# Writing a held-out set of pipeline incidents

You are describing failed pipeline runs the way the on-call data engineer sees them, and the real cause. The
set tests whether the platform's failure triage names the right cause and routes the incident to the team that
can fix it. Do not open anything in this repository except this file.

A set is a folder `heldout/<set-name>/` with one file, `incidents.yaml`.

## How the pipelines work

Every night each data product runs for the previous day. A source product reads that day's file from its
operational system in the landing zone (S3), checks it against the product's contract (field names, types),
writes it to a staging area, runs the contract's quality checks, and publishes it. A derived product runs its
SQL over the published partitions of its inputs, then checks and publishes the same way. If an input has no
published partition for the day, the derived product does not run: it is "upstream_failed" and blocked by that
input. Failures can come from the source system, the data, a team's change to its contract or SQL, or the
platform itself (permissions, memory, AWS).

Run statuses: `failed` (an error in a step), `quality_failed` (checks failed, partition quarantined),
`upstream_failed` (blocked by an input). Steps: `ingest`, `transform`, `quality`, `sensor`.

A log line looks like `2026-09-11T02:00:04Z ERROR [leads.leads.ingest] <message>`; write messages in your own
words, the way AWS, Athena, DuckDB, Airflow or a Python job would phrase them. Vary them.

## Causes, and who must act

| Category | What happened | Team that acts |
| --- | --- | --- |
| late_drop | the source system had not delivered the day's file | owner of the source product |
| schema_drift | the source system renamed or dropped a field without a contract change | owner of the source product |
| type_drift | the source sent values of the wrong type (text in a number field) | owner of the source product |
| null_spike | a key column is suddenly empty on many rows | owner of the product |
| duplicates | duplicate keys in the day's data | owner of the product |
| volume_drop | far fewer rows than usual (a tracking outage) | owner of the product |
| upstream_contract_change | an input's team changed its contract incompatibly (renamed or removed a column the consumer reads) | owner of the input that changed |
| code_change | the product's own SQL was changed just before the failure | owner of the product |
| access_denied | the pipeline role lost a permission (Lake Formation, S3) | team-platform |
| resource_exhausted | the worker or query ran out of memory | team-platform |
| transient_infra | AWS throttled or failed briefly, beyond the retries | team-platform |

## Format

```yaml
incidents:
  - id: i01
    product: pricing.price_ratings
    dt: "2026-09-10"
    status: failed               # failed | quality_failed | upstream_failed
    step: transform
    error_type: BinderException  # a short class name, as the job would report it
    error: 'column "mileage_km" not found ...'
    log:                         # the lines on-call would see, 3 to 8, ERROR and WARN mostly
      - "2026-09-11T02:00:03Z INFO  [pricing.price_ratings.transform] running price_ratings.sql"
      - "2026-09-11T02:00:15Z ERROR [pricing.price_ratings.transform] ..."
    recent_changes:              # optional: changes merged shortly before (they show in the contract history)
      - product: listings.listing_events
        rename_columns: {mileage_km: mileage}    # a contract change renaming columns
        days_before: 1
        note: "rename mileage_km to mileage"
      # or a change to a product's SQL: sql_replace: {"old text": "new text"} (old text must appear in that SQL)
    blocked_by: []               # for upstream_failed: the inputs that were not published
    upstream:                    # for upstream_failed: the input's own failed run that day
      - product: dealers.dealers
        status: failed
        step: ingest
        error_type: ...
        error: ...
        log: [...]
    expected_category: upstream_contract_change
    expected_team: team-listings
```

Write 24 incidents: two of each category, plus two more blocked (`upstream_failed`) runs whose root is one of
the others. Owners: listings.* is team-listings, dealers.* team-dealers, leads.* team-leads, pricing.*
team-pricing. Make some hard: an error message that does not name the cause, a recent change that is
unrelated to the failure, a platform error that looks like a data problem.

## The data catalog

### `dealers.dealers` (owner team-dealers)

Dealers registered on the marketplace with their subscription tier and contact details. One row per dealer per day (a daily snapshot from the dealer CRM).

Partitioned by `dt` (DATE, one partition per day). Source: dealer-crm.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `dealer_id` | string | internal | Id of the dealer |
| `dealer_name` | string | internal | Trading name |
| `country` | string | public | ISO country code |
| `city` | string | internal | City of the main branch |
| `tier` | string | internal | Subscription tier basic, pro or premium |
| `contact_email` | string | pii | Email of the dealer's contact person |
| `contact_phone` | string | pii | Phone of the dealer's contact person |
| `vat_id` | string | confidential | VAT identification number |
| `onboarded_on` | date | internal | Date the dealer joined |

### `leads.dealer_lead_performance` (owner team-leads)

Leads each dealer received per day, with the dealer's tier and the number of listings it had online, so sales and dealer success can compare lead performance across dealers and tiers.

Partitioned by `dt` (DATE, one partition per day). Built from leads.leads, dealers.dealers, listings.listing_events.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `dealer_id` | string | internal | Id of the dealer |
| `country` | string | public | ISO country code of the dealer |
| `tier` | string | internal | Subscription tier |
| `leads` | bigint | internal | Leads received that day |
| `listings_online` | bigint | internal | Listings the dealer had online that day |
| `leads_per_listing` | double | internal | leads divided by listings_online |

### `leads.leads` (owner team-leads)

Buyer enquiries sent to a seller about a listing, by email, phone call or chat. One row per lead. Contains the buyer's contact details.

Partitioned by `dt` (DATE, one partition per day). Source: lead-service.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `lead_id` | string | internal | Id of the lead |
| `listing_id` | string | internal | Listing the buyer asked about |
| `dealer_id` | string | internal | Dealer who received the lead; empty for private sellers |
| `channel` | string | public | email, call or chat |
| `country` | string | public | ISO country code of the listing |
| `buyer_email` | string | pii | Buyer's email address |
| `buyer_phone` | string | pii | Buyer's phone number |
| `created_ts` | timestamp | internal | When the lead was sent (UTC) |

### `listings.active_listings_daily` (owner team-listings)

Stock on the marketplace at the end of each day: number of active listings and median asking price by country and make. Use it for supply and price-level questions.

Partitioned by `dt` (DATE, one partition per day). Built from listings.listing_events.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `country` | string | public | ISO country code |
| `make` | string | public | Manufacturer |
| `active_listings` | bigint | public | Listings online at the end of the day |
| `median_price_eur` | double | public | Median asking price of active listings |

### `listings.listing_events` (owner team-listings)

Every change to a car listing on the marketplace: created, price changed, sold or removed. One row per event. Source-aligned: written as the listing service emits it, cleaned and typed.

Partitioned by `dt` (DATE, one partition per day). Source: listing-service.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `event_id` | string | internal | Unique id of the event |
| `listing_id` | string | internal | Id of the listing |
| `dealer_id` | string | internal | Dealer who owns the listing; empty for private sellers |
| `seller_type` | string | public | dealer or private |
| `make` | string | public | Manufacturer, e.g. Volkswagen |
| `model` | string | public | Model name, e.g. Golf |
| `first_registration_year` | int | public | Year of first registration |
| `mileage_km` | int | public | Odometer reading in kilometres |
| `fuel` | string | public | petrol, diesel, electric, hybrid or lpg |
| `price_eur` | double | public | Asking price in euros |
| `country` | string | public | ISO country code of the listing |
| `event_type` | string | public | created, price_change, sold or removed |
| `event_ts` | timestamp | internal | When the event happened (UTC) |

### `pricing.price_ratings` (owner team-pricing)

The price rating shown on each listing created that day (great, good, fair or high), from the asking price compared with similar cars: same make, model, registration year band and mileage band.

Partitioned by `dt` (DATE, one partition per day). Built from listings.listing_events.

| Column | Type | Classification | Description |
| --- | --- | --- | --- |
| `listing_id` | string | internal | Id of the listing |
| `country` | string | public | ISO country code |
| `make` | string | public | Manufacturer |
| `model` | string | public | Model |
| `price_eur` | double | public | Asking price |
| `reference_price_eur` | double | internal | Median price of comparable listings |
| `rating` | string | public | great, good, fair or high |

## Who can see what

Access follows two tags: the dataset's domain (listings, dealers, leads, pricing) and each column's classification. A person sees a column when one of their grants covers both. Some grants also restrict rows.

| User | Group | What the group may read |
| --- | --- | --- |
| `anna.analyst` | analysts-marketplace (Central analytics and business intelligence) | all domains: public, internal |
| `lukas.leads` | team-leads (Leads domain team) | leads: all classifications; listings, dealers, pricing: public, internal |
| `dana.dealers` | team-dealers (Dealers domain team) | dealers: all classifications; listings, leads, pricing: public, internal |
| `lena.listings` | team-listings (Listings domain team) | listings: all classifications; dealers, leads, pricing: public, internal |
| `piet.pricing` | team-pricing (Pricing data science) | pricing: all classifications; listings, dealers, leads: public, internal |
| `sofia.success` | dealer-success-de (Dealer success managers for Germany; may contact dealers and see their leads, German rows only) | dealers, leads: public, internal, pii (only rows where country = 'DE'); listings: public |
| `max.partner` | partner-reporting (Reporting for an external market-data partner; public columns of listings only) | listings: public |

## Business glossary

- **listing** (Inserat, ad, Anzeige, car for sale): a car offered for sale. Datasets: listings.listing_events, listings.active_listings_daily.
- **active stock** (Bestand, inventory, supply, online listings): listings online at the end of a day. Datasets: listings.active_listings_daily.
- **dealer** (Händler, Haendler, customer, professional seller): a business selling cars on the marketplace. Datasets: dealers.dealers.
- **lead** (Anfrage, enquiry, inquiry, contact request): a buyer contacting a seller about a listing. Datasets: leads.leads, leads.dealer_lead_performance.
- **leads per listing** (lead rate, LPL): leads a dealer received divided by its listings online. Datasets: leads.dealer_lead_performance.
- **price rating** (Preisbewertung, deal rating, price label): great, good, fair or high, from the asking price against comparable cars. Datasets: pricing.price_ratings.
- **tier** (package, subscription, Paket): the dealer's subscription: basic, pro or premium. Datasets: dealers.dealers, leads.dealer_lead_performance.
## How the derived datasets are computed (Athena SQL; `{{ dt }}` is the day being built)

### `leads.dealer_lead_performance`

```sql
-- Leads per dealer and day, against the listings the dealer had online that day.
WITH day_leads AS (
  SELECT dealer_id, count(*) AS leads
  FROM leads.leads
  WHERE dt = DATE '{{ dt }}' AND dealer_id IS NOT NULL
  GROUP BY dealer_id
),
last_event AS (
  SELECT
    listing_id, dealer_id, event_type,
    row_number() OVER (PARTITION BY listing_id ORDER BY event_ts DESC, event_id DESC) AS rn
  FROM listings.listing_events
  WHERE dt <= DATE '{{ dt }}'
),
online AS (
  SELECT dealer_id, count(*) AS listings_online
  FROM last_event
  WHERE rn = 1 AND event_type IN ('created', 'price_change') AND dealer_id IS NOT NULL
  GROUP BY dealer_id
),
dealer_day AS (
  SELECT dealer_id, country, tier
  FROM dealers.dealers
  WHERE dt = DATE '{{ dt }}'
)
SELECT
  d.dealer_id,
  d.country,
  d.tier,
  coalesce(l.leads, 0) AS leads,
  coalesce(o.listings_online, 0) AS listings_online,
  CASE
    WHEN coalesce(o.listings_online, 0) = 0 THEN 0.0
    ELSE CAST(coalesce(l.leads, 0) AS DOUBLE) / o.listings_online
  END AS leads_per_listing
FROM dealer_day AS d
LEFT JOIN day_leads AS l ON l.dealer_id = d.dealer_id
LEFT JOIN online AS o ON o.dealer_id = d.dealer_id
```

### `listings.active_listings_daily`

```sql
-- Active stock at the end of the day: listings whose latest event is not "sold" or "removed".
WITH last_event AS (
  SELECT
    listing_id, country, make, price_eur, event_type,
    row_number() OVER (PARTITION BY listing_id ORDER BY event_ts DESC, event_id DESC) AS rn
  FROM listings.listing_events
  WHERE dt <= DATE '{{ dt }}'
)
SELECT
  country,
  make,
  count(*) AS active_listings,
  approx_percentile(price_eur, 0.5) AS median_price_eur
FROM last_event
WHERE rn = 1 AND event_type IN ('created', 'price_change')
GROUP BY country, make
```

### `pricing.price_ratings`

```sql
-- Rate each listing created today against comparable listings created in the last 90 days:
-- same make, model and registration year, mileage in the same 20,000 km band.
WITH created AS (
  SELECT listing_id, country, make, model, first_registration_year, mileage_km, price_eur, dt
  FROM listings.listing_events
  WHERE event_type = 'created' AND dt BETWEEN DATE '{{ dt }}' - INTERVAL '90' DAY AND DATE '{{ dt }}'
),
reference AS (
  SELECT
    make, model, first_registration_year,
    CAST(floor(mileage_km / 20000) AS INTEGER) AS mileage_band,
    approx_percentile(price_eur, 0.5) AS reference_price_eur
  FROM created
  GROUP BY make, model, first_registration_year, CAST(floor(mileage_km / 20000) AS INTEGER)
)
SELECT
  c.listing_id, c.country, c.make, c.model, c.price_eur, r.reference_price_eur,
  CASE
    WHEN c.price_eur <= 0.90 * r.reference_price_eur THEN 'great'
    WHEN c.price_eur <= 0.97 * r.reference_price_eur THEN 'good'
    WHEN c.price_eur <= 1.05 * r.reference_price_eur THEN 'fair'
    ELSE 'high'
  END AS rating
FROM created AS c
JOIN reference AS r
  ON r.make = c.make AND r.model = c.model AND r.first_registration_year = c.first_registration_year
  AND r.mileage_band = CAST(floor(c.mileage_km / 20000) AS INTEGER)
WHERE c.dt = DATE '{{ dt }}'
```
