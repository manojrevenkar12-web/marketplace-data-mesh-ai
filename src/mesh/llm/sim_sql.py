"""The simulated model's question-to-SQL reader: a deterministic semantic parser over the prompt's datasets.

It knows what any model would know (country names, car makes, what "last week" means) and nothing about
the platform except what is in the prompt: the question, the latest day and the datasets JSON. It covers the
common shapes of analytics questions (a measure, an optional breakdown, filters, a period, a top N) and says
"not answerable" otherwise. It is deliberately not tuned to the held-out questions; how it does on those is
reported as it is.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

COUNTRIES = {
    "germany": "DE", "deutschland": "DE", "german": "DE", "italy": "IT", "italia": "IT", "italian": "IT", "netherlands": "NL",
    "dutch": "NL", "holland": "NL", "belgium": "BE", "belgian": "BE", "austria": "AT", "österreich": "AT", "austrian": "AT",
    "france": "FR", "french": "FR", "spain": "ES", "spanish": "ES", "luxembourg": "LU",
}
CODES = {"DE", "IT", "NL", "BE", "AT", "FR", "ES", "LU"}
MAKES = ["Volkswagen", "BMW", "Mercedes-Benz", "Audi", "Fiat", "Renault", "Tesla", "Skoda", "Toyota"]
MAKE_ALIASES = {"vw": "Volkswagen", "mercedes": "Mercedes-Benz", "benz": "Mercedes-Benz", "škoda": "Skoda"}
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"])}
CHANNELS = {"email": "email", "e-mail": "email", "call": "call", "phone call": "call", "calls": "call", "chat": "chat"}
FUELS = {"electric": "electric", "ev": "electric", "evs": "electric", "diesel": "diesel", "petrol": "petrol", "gasoline": "petrol",
         "hybrid": "hybrid", "lpg": "lpg"}
TIERS = ["basic", "pro", "premium"]
RATINGS = ["great", "good", "fair", "high"]


@dataclass
class Draft:
    answerable: bool
    sql: str = ""
    tables: list[str] | None = None
    explanation: str = ""
    missing: str = ""


def parse_prompt(text: str) -> tuple[str, str, list[dict[str, Any]]]:
    q = re.search(r"Question:\s*(.*)", text)
    latest = re.search(r"Latest day with data:\s*(\S+)", text)
    ds = re.search(r"<datasets>\s*(.*?)\s*</datasets>", text, re.S)
    datasets = json.loads(ds.group(1)) if ds else []
    return (q.group(1).strip() if q else ""), (latest.group(1) if latest else ""), datasets


def draft(prompt_text: str) -> Draft:
    question, latest, datasets = parse_prompt(prompt_text)
    if not latest or latest == "no" or not datasets:
        return Draft(False, explanation="No data to answer from.", missing="a dataset with data")
    tables = {d["table"]: {c["name"] for c in d["columns"]} for d in datasets}
    latest_of = {d["table"]: (re.search(r"latest (\d{4}-\d{2}-\d{2})", d.get("partition", "")) or [None, latest])[1] for d in datasets}
    q = question.lower()
    period = _period(q, latest)
    filters = _filters(q, question)
    group = _group(q)
    top = re.search(r"\btop\s+(\d+)", q)
    limit = int(top.group(1)) if top else None

    def has(table: str, *cols: str) -> bool:
        return table in tables and all(c in tables[table] for c in cols)

    measure: tuple[str, str, str, list[str]] | None = None  # (table, select expression, label, extra where)
    if re.search(r"leads?\s+per\s+listing|lead rate|leads/listing", q) and has("leads.dealer_lead_performance", "leads_per_listing"):
        measure = ("leads.dealer_lead_performance", "round(avg(leads_per_listing), 3)", "avg_leads_per_listing", [])
    elif re.search(r"\b(leads?|enquir|inquir|anfrage|contact requests?)", q):
        if has("leads.leads", "lead_id"):
            measure = ("leads.leads", "count(*)", "leads", [])
        elif has("leads.dealer_lead_performance", "leads"):
            measure = ("leads.dealer_lead_performance", "sum(leads)", "leads", [])
    elif re.search(r"active (listings|stock|cars)|\bstock\b|inventory|bestand|online listings|listings online|supply", q) and has(
            "listings.active_listings_daily", "active_listings"):
        measure = ("listings.active_listings_daily", "sum(active_listings)", "active_listings", [])
    elif re.search(r"price rating|rated|rating|great deal|good deal|preisbewertung", q) and has("pricing.price_ratings", "rating"):
        if group is None and not any(r in q for r in RATINGS):
            group = "rating"
        measure = ("pricing.price_ratings", "count(*)", "listings", [])
    elif re.search(r"(median|average|avg|mean|typical)\s+(asking\s+)?price|price level", q):
        if has("listings.listing_events", "price_eur", "event_type"):
            fn = "approx_percentile(price_eur, 0.5)" if "median" in q or "typical" in q else "round(avg(price_eur), 2)"
            measure = ("listings.listing_events", fn, "price_eur", ["event_type = 'created'"])
        elif has("listings.active_listings_daily", "median_price_eur"):
            measure = ("listings.active_listings_daily", "round(avg(median_price_eur), 2)", "median_price_eur", [])
    elif re.search(r"\bsold\b|sales of cars|cars sold", q) and has("listings.listing_events", "event_type"):
        measure = ("listings.listing_events", "count(*)", "sold", ["event_type = 'sold'"])
    elif re.search(r"new listings|listings (were )?(created|added|posted)|newly listed|new ads|listed", q) and has("listings.listing_events", "event_type"):
        measure = ("listings.listing_events", "count(*)", "new_listings", ["event_type = 'created'"])
    elif re.search(r"\bdealers?\b|händler|haendler", q) and has("dealers.dealers", "dealer_id"):
        if re.search(r"e-?mail|phone|contact", q) and not has("dealers.dealers", "contact_email"):
            return Draft(False, explanation="Dealer contact details are not among the columns you can read.",
                         missing="contact columns of dealers.dealers")
        if re.search(r"e-?mail|phone|contact", q) and has("dealers.dealers", "contact_email"):
            cols = [c for c in ("dealer_id", "dealer_name", "contact_email", "contact_phone") if has("dealers.dealers", c)]
            where = [f"dt = DATE '{latest_of['dealers.dealers']}'", *_where(filters, tables["dealers.dealers"])]
            sql = f"SELECT {', '.join(cols)} FROM dealers.dealers WHERE {' AND '.join(where)} ORDER BY dealer_id"
            return Draft(True, sql, ["dealers.dealers"], "Dealer contact details on the latest snapshot.")
        measure = ("dealers.dealers", "count(DISTINCT dealer_id)", "dealers", [])
        period = (latest_of["dealers.dealers"], latest_of["dealers.dealers"])  # a daily snapshot: count on one day
    if measure is None:
        return Draft(False, explanation="I could not map this question onto the datasets shown.",
                     missing="a measure I recognise in the datasets you can read")
    table, expr, label, extra = measure
    cols = tables[table]
    if latest_of.get(table) and latest_of[table] != latest and period == (latest, latest):
        period = (latest_of[table], latest_of[table])
    if group and group not in cols and not (group == "dealer_id" and "dealer_id" in cols):
        return Draft(False, explanation=f"{table} has no {group} to break down by.", missing=f"{group} in {table}")
    where = [_dt_clause(period), *extra, *_where(filters, cols)]
    if top and group is None:
        group = "dealer_id" if "dealer" in q and "dealer_id" in cols else ("make" if "make" in cols else None)
    select = [*([group] if group else []), f"{expr} AS {label}"]
    sql = f"SELECT {', '.join(select)} FROM {table} WHERE {' AND '.join(w for w in where if w)}"
    if group:
        sql += f" GROUP BY {group} ORDER BY {label if limit else group} {'DESC' if limit else ''}".rstrip()
    if limit:
        sql += f" LIMIT {limit}"
    expl = f"{label.replace('_', ' ')} from {table}" + (f" by {group}" if group else "") + f", {period[0]} to {period[1]}"
    return Draft(True, sql, [table], expl[0].upper() + expl[1:] + ".")


def _period(q: str, latest: str) -> tuple[str, str]:
    end = date.fromisoformat(latest)
    m = re.search(r"(last|past|previous)\s+(\d+)\s+days?", q)
    if m:
        return (end - timedelta(int(m.group(2)) - 1)).isoformat(), latest
    if re.search(r"(last|past|previous)\s+(week|7 days)", q) or "this week" in q:
        return (end - timedelta(6)).isoformat(), latest
    if re.search(r"(last|past|previous)\s+(month|30 days)", q):
        return (end - timedelta(29)).isoformat(), latest
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|and|-|until|through)\s*(\d{4}-\d{2}-\d{2})", q)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"since\s+(\d{4}-\d{2}-\d{2})", q)
    if m:
        return m.group(1), latest
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if m:
        return m.group(1), m.group(1)
    for name, num in MONTHS.items():
        if re.search(rf"\b{name}\b", q):
            year = end.year if num <= end.month else end.year - 1
            first = date(year, num, 1)
            last = min(date(year, num, monthrange(year, num)[1]), end)
            return first.isoformat(), last.isoformat()
    if "yesterday" in q:
        return latest, latest
    return latest, latest


def _filters(q: str, original: str) -> dict[str, str]:
    f: dict[str, str] = {}
    for name, code in COUNTRIES.items():
        if re.search(rf"\b{name}\b", q):
            f["country"] = code
    for code in CODES:
        if re.search(rf"\b{code}\b", original):
            f["country"] = code
    for make in MAKES:
        if make.lower() in q:
            f["make"] = make
    for alias, make in MAKE_ALIASES.items():
        if re.search(rf"\b{alias}\b", q):
            f["make"] = make
    for word, ch in CHANNELS.items():
        if re.search(rf"\b(by|via|through|over)\s+{re.escape(word)}\b", q) or re.search(rf"\b{re.escape(word)}\s+leads\b", q):
            f["channel"] = ch
    for word, fuel in FUELS.items():
        if re.search(rf"\b{word}\b", q):
            f["fuel"] = fuel
    for t in TIERS:
        if re.search(rf"\b{t}\b\s+(dealers?|tier|package)", q):
            f["tier"] = t
    for r in RATINGS:
        if re.search(rf"\b{r}\b\s+(deals?|rating|price rating)|rated\s+{r}", q):
            f["rating"] = r
    return f


def _group(q: str) -> str | None:
    pairs = [
        (r"(by|per|for each|each|across)\s+(country|countries|market|markets)", "country"),
        (r"(by|per|for each|each|across)\s+(make|makes|brand|brands|manufacturer)", "make"),
        (r"(by|per|for each|each)\s+(tier|tiers|package|subscription)", "tier"),
        (r"(by|per|for each|each)\s+(channel|channels)", "channel"),
        (r"(by|per|for each|each)\s+(fuel|fuel type)", "fuel"),
        (r"(by|per|for each|each)\s+(rating|ratings)", "rating"),
        (r"(by|per|for each|each)\s+(model|models)", "model"),
        (r"(by|per|for each|each)\s+(day|date)|daily|day by day|trend", "dt"),
        (r"(by|per|for each|each)\s+dealers?|top\s+\d+\s+dealers?", "dealer_id"),
    ]
    for pat, col in pairs:
        if re.search(pat, q):
            return col
    return None


def _dt_clause(period: tuple[str, str]) -> str:
    a, b = period
    return f"dt = DATE '{a}'" if a == b else f"dt BETWEEN DATE '{a}' AND DATE '{b}'"


def _where(filters: dict[str, str], cols: set[str]) -> list[str]:
    return [f"{k} = '{v}'" for k, v in sorted(filters.items()) if k in cols]
