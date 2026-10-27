"""A car marketplace that produces data every day, and breaks the way real data platforms break.

The world sends daily drops from three operational systems (listing service, dealer CRM, lead service) to the
landing zone and lets the platform's runner process them. On some days it injects an incident: a source
renames a field, sends prices as text, delivers late, loses tracking, duplicates events; a domain team
changes its contract without warning its consumers, or breaks its own transform; the platform loses a
permission, runs out of memory or meets S3 throttling. Each incident is fixed a day or two later, the way an
on-call engineer would, and the affected days are re-run.

What really happened is written to ``data/truth/incidents.jsonl`` for the evaluation. Incidents before
``live_from`` also become resolved on-call tickets in ``data/history/tickets.jsonl``, which is what a real
platform team would have and what the triage classifier is trained on (with the mislabels real tickets have).
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from mesh import config
from mesh.contracts import Registry
from mesh.history import Versions
from mesh.lake import Lake
from mesh.pipeline import Runner, StepError, write_jsonl
from mesh.state import Run, State

CITIES = {
    "DE": ["Berlin", "Munich", "Hamburg", "Cologne", "Frankfurt", "Stuttgart", "Leipzig"],
    "IT": ["Milan", "Rome", "Turin", "Bologna", "Padua"],
    "NL": ["Amsterdam", "Rotterdam", "Utrecht"],
    "BE": ["Brussels", "Antwerp", "Ghent"],
    "AT": ["Vienna", "Graz", "Linz"],
    "FR": ["Paris", "Lyon", "Strasbourg"],
    "ES": ["Madrid", "Barcelona", "Valencia"],
    "LU": ["Luxembourg"],
}
SOURCES = {"listings.listing_events": "listing-service", "dealers.dealers": "dealer-crm", "leads.leads": "lead-service"}
OWNERS = {"listings": "team-listings", "dealers": "team-dealers", "leads": "team-leads", "pricing": "team-pricing"}

# field renames a source system might ship, and contract renames a team might publish
SCHEMA_DRIFT = {
    "leads.leads": ("channel", "contact_channel"),
    "dealers.dealers": ("tier", "package"),
    "listings.listing_events": ("fuel", "fuel_type"),
}
CONTRACT_RENAMES = [
    ("listings.listing_events", "mileage_km", "mileage", "Odometer reading"),
    ("listings.listing_events", "first_registration_year", "registration_year", "Year of first registration"),
    ("dealers.dealers", "tier", "subscription_tier", "Subscription tier"),
    ("leads.leads", "dealer_id", "seller_dealer_id", "Dealer who received the lead"),
]
CODE_BUGS = {
    "pricing.price_ratings": ("r.reference_price_eur", "r.reference_price"),
    "leads.dealer_lead_performance": ("o.listings_online", "o.listing_online"),
    "listings.active_listings_daily": ("approx_percentile(price_eur, 0.5)", "approx_percentile(price_eur)"),
}


@dataclass
class Incident:
    id: str
    kind: str
    dt: str
    product: str  # where it was injected
    owner: str  # the team that must act
    detail: str
    fixed_on: str = ""
    affected_runs: list[str] = field(default_factory=list)
    window: str = "live"


def dates(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    return [(d0 + timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)]


class World:
    def __init__(self, data: Path | None = None, cfg: dict[str, Any] | None = None, products: Path | None = None):
        self.cfg = cfg or yaml.safe_load((config.config_dir() / "world.yaml").read_text())
        self.data = data or config.data_dir()
        self.rng = np.random.default_rng(int(self.cfg["seed"]))
        self.registry = Registry.load(products)
        self.days = dates(self.cfg["start"], self.cfg["end"])
        self.live_from = str(self.cfg["live_from"])
        self.countries = list(self.cfg["countries"])
        self.cweights = np.array(list(self.cfg["countries"].values()), dtype=float)
        self.cweights /= self.cweights.sum()
        self.models = [(mk, md, float(p)) for mk, ms in self.cfg["makes"].items() for md, p in ms.items()]
        self.fuels = list(self.cfg["fuels"])
        self.fweights = np.array(list(self.cfg["fuels"].values()), dtype=float)
        self.fweights /= self.fweights.sum()
        self.listings: dict[str, dict[str, Any]] = {}
        self.dealers: list[dict[str, Any]] = []
        self.n_listing = 0
        self.n_event = 0
        self.n_lead = 0
        self.incidents: list[Incident] = []
        self.faults: dict[tuple[str, str, str], tuple[StepError, int]] = {}  # (product, dt, step) -> (error, failing attempts)
        self.pending_fixes: dict[str, list[Any]] = defaultdict(list)  # dt -> fixes to apply before that day's run

    # ---------------------------------------------------------------- build

    def build(self) -> dict[str, Any]:
        for sub in ("landing", "lake", "state", "truth", "history"):
            shutil.rmtree(self.data / sub, ignore_errors=True)
        self.state = State(self.data / "state")
        self.lake = Lake(self.data / "lake")
        self.versions = Versions(self.state, self.registry)
        self.versions.bootstrap(self.days[0])
        self.runner = Runner(self.lake, self.state, self.versions, self.data / "landing", faults=self._fault)
        self._make_dealers()
        # burn-in: the marketplace existed before the platform started recording, so stock and leads are steady
        for _ in range(int(self.cfg.get("burn_in_days", 60))):
            self._generate_day(self._next(self.days[0], -1), keep=False)
        for dt in self.days:
            self._apply_fixes(dt)
            drops = self._generate_day(dt)
            incident = self._maybe_incident(dt, drops)
            self._deliver(dt, drops, skip=incident.product if incident and incident.kind == "late_drop" else None)
            runs = self.runner.run_day(dt)
            if incident:
                incident.affected_runs = self._affected(incident, runs)
        self._write_truth()
        return {"days": len(self.days), "incidents": len(self.incidents), "listings": self.n_listing, "leads": self.n_lead}

    # ---------------------------------------------------------------- data

    def _make_dealers(self) -> None:
        tiers = ["basic", "pro", "premium"]
        for i in range(int(self.cfg["dealers"])):
            country = str(self.rng.choice(self.countries, p=self.cweights))
            city = str(self.rng.choice(CITIES[country]))
            name = f"{['Auto', 'Car', 'Motors', 'Garage', 'Autohaus'][i % 5]} {city} {i + 1}"
            self.dealers.append(
                {
                    "dealer_id": f"D{i + 1:04d}",
                    "dealer_name": name,
                    "country": country,
                    "city": city,
                    "tier": str(self.rng.choice(tiers, p=[0.55, 0.3, 0.15])),
                    "contact_email": f"sales{i + 1}@dealer{i + 1}.example",
                    "contact_phone": f"+{30 + i % 60} {int(self.rng.integers(100, 999))} {int(self.rng.integers(100000, 999999))}",
                    "vat_id": f"{country}{int(self.rng.integers(10**8, 10**9))}",
                    "onboarded_on": (date(2015, 1, 1) + timedelta(int(self.rng.integers(0, 3800)))).isoformat(),
                    "weight": 1.0,
                }
            )
        for d in self.dealers:
            d["weight"] = {"basic": 1.0, "pro": 2.2, "premium": 4.0}[d["tier"]]

    def _generate_day(self, dt: str, keep: bool = True) -> dict[str, list[dict[str, Any]]]:
        day = date.fromisoformat(dt)
        factor = float(self.cfg["weekend_factor"]) if day.weekday() >= 5 else 1.0
        base = datetime.fromisoformat(dt)
        events: list[dict[str, Any]] = []
        dw = np.array([d["weight"] for d in self.dealers])
        dw /= dw.sum()

        def ts() -> str:
            return (base + timedelta(seconds=int(self.rng.integers(0, 86400)))).isoformat()

        def event(lid: str, kind: str) -> dict[str, Any]:
            listing = self.listings[lid]
            self.n_event += 1
            return {
                "event_id": f"E{self.n_event:08d}",
                "listing_id": lid,
                "dealer_id": listing["dealer_id"],
                "seller_type": "dealer" if listing["dealer_id"] else "private",
                "make": listing["make"],
                "model": listing["model"],
                "first_registration_year": listing["year"],
                "mileage_km": listing["mileage"],
                "fuel": listing["fuel"],
                "price_eur": listing["price"],
                "country": listing["country"],
                "event_type": kind,
                "event_ts": ts(),
            }

        # changes to listings already online
        for lid, listing in list(self.listings.items()):
            if not listing["active"]:
                continue
            u = self.rng.random()
            if u < 0.025:
                listing["active"] = False
                events.append(event(lid, "sold"))
            elif u < 0.035:
                listing["active"] = False
                events.append(event(lid, "removed"))
            elif u < 0.065:
                listing["price"] = round(listing["price"] * float(self.rng.uniform(0.94, 0.99)) / 50) * 50
                events.append(event(lid, "price_change"))
        # new listings
        for _ in range(int(self.rng.poisson(float(self.cfg["new_listings_per_day"]) * factor))):
            self.n_listing += 1
            lid = f"L{self.n_listing:07d}"
            private = self.rng.random() < float(self.cfg["private_share"])
            dealer = None if private else self.dealers[int(self.rng.choice(len(self.dealers), p=dw))]
            country = str(self.rng.choice(self.countries, p=self.cweights)) if dealer is None else dealer["country"]
            make, model, base_price = self.models[int(self.rng.integers(len(self.models)))]
            age = int(self.rng.integers(0, 13))
            year = 2026 - age
            mileage = int(max(0, self.rng.normal(14000 * age + 3000, 6000 + 3000 * age)))
            price = base_price * (0.86**age) * float(np.exp(-mileage / 400000)) * float(self.rng.lognormal(0, 0.12))
            self.listings[lid] = {
                "dealer_id": dealer["dealer_id"] if dealer else None,
                "make": make,
                "model": model,
                "year": year,
                "mileage": mileage,
                "fuel": str(self.rng.choice(self.fuels, p=self.fweights)),
                "price": max(500, round(price / 50) * 50),
                "country": country,
                "active": True,
                "base": base_price * (0.86**age),
            }
            events.append(event(lid, "created"))
        if not keep:  # burn-in: only the listings' state matters
            for lid in [lid for lid, x in self.listings.items() if not x["active"]]:
                del self.listings[lid]
            return {}
        # leads on active listings: cheaper than comparable cars get more
        active = [(lid, x) for lid, x in self.listings.items() if x["active"]]
        leads: list[dict[str, Any]] = []
        if active:
            appeal = np.array([np.clip(x["base"] / max(x["price"], 1), 0.5, 2.0) for _, x in active])
            counts = self.rng.poisson(float(self.cfg["leads_per_listing_day"]) * factor * appeal)
            channels = ["email", "call", "chat"]
            for (lid, x), k in zip(active, counts, strict=True):
                for _ in range(int(k)):
                    self.n_lead += 1
                    leads.append(
                        {
                            "lead_id": f"Q{self.n_lead:08d}",
                            "listing_id": lid,
                            "dealer_id": x["dealer_id"],
                            "channel": str(self.rng.choice(channels, p=[0.55, 0.25, 0.2])),
                            "country": x["country"],
                            "buyer_email": f"buyer{self.n_lead}@mail.example",
                            "buyer_phone": f"+49 151 {self.n_lead:08d}",
                            "created_ts": ts(),
                        }
                    )
        dealers = [{k: v for k, v in d.items() if k != "weight"} for d in self.dealers]
        return {"listings.listing_events": events, "dealers.dealers": dealers, "leads.leads": leads}

    def _deliver(self, dt: str, drops: dict[str, list[dict[str, Any]]], skip: str | None = None) -> None:
        for product, rows in drops.items():
            if product == skip:
                continue
            name = product.split(".", 1)[1]
            write_jsonl(self.data / "landing" / SOURCES[product] / name / f"{dt}.jsonl", rows)
        self._drops = getattr(self, "_drops", {})
        self._drops[dt] = drops
        for old in [d for d in self._drops if d < self._next(dt, -3)]:
            del self._drops[old]

    # ---------------------------------------------------------------- incidents

    def _maybe_incident(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident | None:
        live = dt >= self.live_from
        rate = float(self.cfg["incidents"]["rate_live" if live else "rate_history"])
        if dt == self.days[0] or self.rng.random() >= rate:
            return None
        mix = self.cfg["incidents"]["mix"]
        kinds = list(mix)
        p = np.array([float(mix[k]) for k in kinds])
        kind = str(self.rng.choice(kinds, p=p / p.sum()))
        inc = getattr(self, f"_inject_{kind}")(dt, drops)
        if inc is None:
            return None
        inc.window = "live" if live else "history"
        self.incidents.append(inc)
        return inc

    def _new(self, kind: str, dt: str, product: str, owner: str, detail: str) -> Incident:
        return Incident(f"I{len(self.incidents) + 1:03d}", kind, dt, product, owner, detail)

    def _next(self, dt: str, n: int = 1) -> str:
        return (date.fromisoformat(dt) + timedelta(n)).isoformat()

    def _redeliver_and_rerun(self, dt: str, product: str, on: str) -> None:
        self.pending_fixes[on].append(("rerun", dt, product))

    def _pick_source(self) -> str:
        return str(self.rng.choice(list(SOURCES)))

    def _inject_schema_drift(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = self._pick_source()
        old, new = SCHEMA_DRIFT[product]
        clean = [dict(r) for r in drops[product]]
        for r in drops[product]:
            r[new] = r.pop(old)
        inc = self._new(
            "schema_drift", dt, product, OWNERS[product.split(".")[0]], f"{SOURCES[product]} renamed field '{old}' to '{new}'"
        )
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, clean))
        return inc

    def _inject_type_drift(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = "listings.listing_events"
        clean = [dict(r) for r in drops[product]]
        col = str(self.rng.choice(["price_eur", "mileage_km"]))
        for i, r in enumerate(drops[product]):
            if i % 7 == 0:
                v = int(r[col])
                r[col] = f"{v:,}".replace(",", ".") + (" €" if col == "price_eur" else " km")
            else:
                r[col] = str(r[col])
        inc = self._new("type_drift", dt, product, "team-listings", f"listing-service sent {col} as formatted text")
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, clean))
        return inc

    def _inject_late_drop(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = self._pick_source()
        inc = self._new(
            "late_drop", dt, product, OWNERS[product.split(".")[0]], f"{SOURCES[product]} delivered the {dt} drop late"
        )
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, [dict(r) for r in drops[product]]))
        return inc

    def _inject_null_spike(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = "leads.leads"
        clean = [dict(r) for r in drops[product]]
        for r in drops[product]:
            if self.rng.random() < 0.85:
                r["dealer_id"] = None
        inc = self._new("null_spike", dt, product, "team-leads", "lead-service lost the dealer id on most leads (tracking bug)")
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, clean))
        return inc

    def _inject_duplicates(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = "listings.listing_events"
        clean = [dict(r) for r in drops[product]]
        rows = drops[product]
        rows.extend(dict(r) for r in rows[: max(1, len(rows) // 12)])
        inc = self._new("duplicates", dt, product, "team-listings", "listing-service replayed part of its outbox")
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, clean))
        return inc

    def _inject_volume_drop(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = "leads.leads"
        clean = [dict(r) for r in drops[product]]
        drops[product][:] = drops[product][: int(len(drops[product]) * 0.3)]
        inc = self._new("volume_drop", dt, product, "team-leads", "lead tracking outage: 70% of leads missing")
        self.pending_fixes[self._next(dt)].append(("redeliver", dt, product, clean))
        return inc

    def _inject_upstream_contract_change(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident | None:
        product, old, new, _desc = CONTRACT_RENAMES[int(self.rng.integers(len(CONTRACT_RENAMES)))]
        current = self.versions.in_force(product, dt)
        assert current is not None
        data = yaml.safe_load(current.contract)
        if not any(c["name"] == old for c in data["columns"]):
            return None
        data["version"] = int(data["version"]) + 1
        for c in data["columns"]:
            if c["name"] == old:
                c["name"] = new
        for q in data.get("quality", []):
            if q.get("column") == old:
                q["column"] = new
        self.versions.publish(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            current.sql,
            dt,
            OWNERS[product.split(".")[0]],
            f"rename {old} to {new}",
            published=f"{dt}T15:00:00Z",
        )
        for r in drops[product]:
            r[new] = r.pop(old)
        inc = self._new(
            "upstream_contract_change",
            dt,
            product,
            OWNERS[product.split(".")[0]],
            f"contract v{data['version']} renamed '{old}' to '{new}' without deprecating it",
        )
        self.pending_fixes[self._next(dt)].append(("revert_contract", product, current, dt, old, new))
        return inc

    def _inject_code_change(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = str(self.rng.choice(list(CODE_BUGS)))
        current = self.versions.in_force(product, dt)
        assert current is not None and current.sql is not None
        old, new = CODE_BUGS[product]
        self.versions.publish(
            current.contract,
            current.sql.replace(old, new, 1),
            dt,
            OWNERS[product.split(".")[0]],
            "refactor transform",
            published=f"{dt}T15:00:00Z",
        )
        inc = self._new("code_change", dt, product, OWNERS[product.split(".")[0]], f"transform edited: '{old}' became '{new}'")
        self.pending_fixes[self._next(dt)].append(("revert_sql", product, current, dt))
        return inc

    def _env_fault(self, kind: str, dt: str, product: str, step: str, err: StepError, attempts: int, detail: str) -> Incident:
        self.faults[(product, dt, step)] = (err, attempts)
        inc = self._new(kind, dt, product, "team-platform", detail)
        self.pending_fixes[self._next(dt)].append(("rerun", dt, product))
        return inc

    def _inject_access_denied(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = str(
            self.rng.choice(
                ["leads.dealer_lead_performance", "pricing.price_ratings", "listings.active_listings_daily", "dealers.dealers"]
            )
        )
        c = self.registry[product]
        if c.kind == "derived":
            target = c.inputs[0]
            msg = (
                f"An error occurred (AccessDeniedException) when calling the GetTable operation: Insufficient Lake Formation "
                f"permission(s): Required SELECT on {target.replace('.', '_')}.{target.split('.')[1]} for principal "
                f"arn:aws:iam::123456789012:role/mesh-mwaa-execution"
            )
            step = "transform"
        else:
            msg = (
                f"An error occurred (AccessDenied) when calling the PutObject operation: User arn:aws:sts::123456789012:assumed-role/"
                f"mesh-mwaa-execution is not authorized to perform s3:PutObject on arn:aws:s3:::mesh-lake-{c.domain}/{c.name}/dt={dt}/"
            )
            step = "ingest"
        return self._env_fault(
            "access_denied",
            dt,
            product,
            step,
            StepError("AccessDeniedException", msg),
            99,
            "a Terraform change removed a grant from the pipeline role",
        )

    def _inject_resource_exhausted(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = str(
            self.rng.choice(["leads.dealer_lead_performance", "pricing.price_ratings", "listings.active_listings_daily"])
        )
        msg = str(
            self.rng.choice(
                [
                    "Out of Memory Error: failed to allocate data of size 2.0 GiB (6.1 GiB/6.2 GiB used)",
                    "Query exhausted resources at this scale factor (worker mesh-mwaa-worker-3 killed by the OOM killer)",
                ]
            )
        )
        return self._env_fault(
            "resource_exhausted",
            dt,
            product,
            "transform",
            StepError("ResourceExhausted", msg),
            99,
            "the worker ran out of memory",
        )

    def _inject_transient_infra(self, dt: str, drops: dict[str, list[dict[str, Any]]]) -> Incident:
        product = str(self.rng.choice(list(self.registry.contracts)))
        c = self.registry[product]
        msg = str(
            self.rng.choice(
                [
                    "An error occurred (SlowDown) when calling the PutObject operation (reached max retries: 4): Please reduce your request rate.",
                    "An error occurred (ServiceUnavailable) when calling the GetPartitions operation: Glue is temporarily unavailable",
                    "Could not connect to the endpoint URL: https://s3.eu-central-1.amazonaws.com/ (read timeout=60)",
                ]
            )
        )
        step = "ingest" if c.kind == "source" else "transform"
        return self._env_fault(
            "transient_infra",
            dt,
            product,
            step,
            StepError("ServiceError", msg, retryable=True),
            3,
            "AWS throttled or briefly failed beyond the retries",
        )

    def _fault(self, product: str, dt: str, step: str, attempt: int) -> StepError | None:
        f = self.faults.get((product, dt, step))
        if f and attempt <= f[1]:
            return f[0]
        return None

    # ---------------------------------------------------------------- fixes and re-runs

    def _apply_fixes(self, dt: str) -> None:
        for fix in self.pending_fixes.pop(dt, []):
            if fix[0] == "redeliver":
                _, day, product, rows = fix
                name = product.split(".", 1)[1]
                write_jsonl(self.data / "landing" / SOURCES[product] / name / f"{day}.jsonl", rows)
                self._rerun_from(day, product)
            elif fix[0] == "rerun":
                _, day, product = fix
                for k in [k for k in self.faults if k[0] == product and k[1] == day]:
                    del self.faults[k]
                self._rerun_from(day, product)
            elif fix[0] == "revert_contract":
                _, product, previous, since, old, new = fix
                data = yaml.safe_load(previous.contract)
                cur = self.versions.in_force(product, dt)
                assert cur is not None
                data["version"] = int(yaml.safe_load(cur.contract)["version"]) + 1
                self.versions.publish(
                    yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                    previous.sql,
                    since,
                    previous.author,
                    "revert rename (consumers broke)",
                    published=f"{dt}T10:00:00Z",
                )
                fixed = [{(old if k == new else k): v for k, v in r.items()} for r in self._drops[since][product]]
                write_jsonl(self.data / "landing" / SOURCES[product] / product.split(".", 1)[1] / f"{since}.jsonl", fixed)
                self._rerun_from(since, product)
            elif fix[0] == "revert_sql":
                _, product, previous, since = fix
                self.versions.publish(
                    previous.contract,
                    previous.sql,
                    since,
                    previous.author,
                    "revert transform change",
                    published=f"{dt}T10:00:00Z",
                )
                self._rerun_from(since, product)

    def _rerun_from(self, day: str, product: str) -> None:
        """Re-run a product for a day and everything downstream of it that day (a backfill)."""
        registry = self.versions.registry_at(day)
        affected = {product}
        for p in registry.order():
            if p == product or any(i in affected for i in registry[p].inputs):
                affected.add(p)
                self.runner.run_product(p, day, registry)

    # ---------------------------------------------------------------- truth

    def _affected(self, inc: Incident, runs: list[Run]) -> list[str]:
        failed = {r.product: r for r in runs if not r.ok}
        if inc.product not in failed and inc.kind != "upstream_contract_change":
            return []
        roots = {inc.product} if inc.product in failed else set()
        if inc.kind == "upstream_contract_change":
            roots = {r.product for r in runs if not r.ok and inc.product in self.registry_inputs(r.product)}
            roots |= {inc.product} if inc.product in failed else set()
        affected = set(roots)
        changed = True
        while changed:
            changed = False
            for r in failed.values():
                if r.product not in affected and any(b in affected for b in r.blocked_by):
                    affected.add(r.product)
                    changed = True
        return sorted(failed[p].id for p in affected if p in failed)

    def registry_inputs(self, product: str) -> list[str]:
        return self.registry[product].inputs if product in self.registry else []

    def _write_truth(self) -> None:
        truth = self.data / "truth"
        truth.mkdir(parents=True, exist_ok=True)
        with (truth / "incidents.jsonl").open("w") as f:
            for inc in self.incidents:
                f.write(json.dumps(inc.__dict__) + "\n")
        # resolved on-call tickets for history incidents: what the platform team would have written down
        hist = self.data / "history"
        hist.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(int(self.cfg["seed"]) + 1)
        kinds = list(self.cfg["incidents"]["mix"])
        with (hist / "tickets.jsonl").open("w") as f:
            for inc in self.incidents:
                if inc.window != "history":
                    continue
                for run_id in inc.affected_runs:
                    label = inc.kind if rng.random() > 0.08 else str(rng.choice(kinds))  # on-call labels are not perfect
                    f.write(
                        json.dumps(
                            {
                                "run_id": run_id,
                                "root_cause": label,
                                "routed_to": inc.owner,
                                "resolution": f"{inc.detail}; fixed and re-run",
                            }
                        )
                        + "\n"
                    )
