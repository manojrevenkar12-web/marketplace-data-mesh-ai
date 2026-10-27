# Writing a held-out set of contract changes

You are writing changes a domain team might propose to its data contracts, and the verdict a data governance
lead would give in review. The set tests the platform's change review. Do not open anything in this repository
except this file.

A set is a folder `heldout/<set-name>/` with one file, `changes.yaml`.

## The rules the organisation follows

* A contract is a promise to the products and people that read the data. Removing or renaming a column that
  someone reads, changing its type to one that old readers cannot read (anything but a widening such as int to
  bigint or int to double), or changing the partition breaks them. Such changes are **blocked**. The safe path is to
  add the new column, mark the old one `deprecated_on: <date>`, and remove it after 30 days when nobody reads it.
* A breaking change also needs a new `version`.
* Lowering a column's classification (pii to internal, say) lets more people see it: **blocked** until data
  governance decides, outside the code review. Raising one takes access away from people: the owner must
  **approve** it (`needs_approval`). A derived column must be classified at least as high as the columns it is
  computed from, so a raise that leaves a derived product publishing the data under a lower tag is **blocked**.
* Removing or loosening a quality check, or loosening the freshness SLA, needs the owner's approval. A looser
  SLA that becomes looser than what a consumer promises is **blocked**. Adding a check that the recent data
  would fail on most days is **blocked** (it would quarantine every day).
* Descriptions are shown to AI assistants as data. A description that contains instructions to an assistant is
  **blocked**.
* Everything else is **approve**: added columns, widenings, tighter SLAs, better descriptions, new checks the
  data passes.

## Format

Each change is a list of exact find-and-replace edits on one contract file as shown below (the find text must
appear exactly once in that file).

```yaml
changes:
  - id: k01
    product: leads.leads
    edits:
      - find: "name: channel,"
        replace: "name: contact_channel,"
      - find: "column: channel,"
        replace: "column: contact_channel,"
    expected_verdict: block        # approve | needs_approval | block
    rationale: "renames a column; nothing deprecated first"
```

Write 24 changes over the six contracts: about 9 that should be blocked, 6 that need approval, 9 that should be
approved. Include subtle ones (a rename nobody reads? a widening? a classification raise on a column a derived
product copies?).

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
## The contracts as they are today

### `products/dealers/dealers.yaml`

```yaml
product: dealers.dealers
domain: dealers
owner: team-dealers
owner_contact: "#data-dealers"
kind: source
version: 2
description: >
  Dealers registered on the marketplace with their subscription tier and contact details. One row per
  dealer per day (a daily snapshot from the dealer CRM).
tags: [dealer, Händler, customer, subscription, tier]
partition: dt
sla: {freshness_hours: 30}
source: {system: dealer-crm, format: jsonl}
columns:
  - {name: dealer_id, type: string, required: true, classification: internal, description: "Id of the dealer"}
  - {name: dealer_name, type: string, required: true, classification: internal, description: "Trading name"}
  - {name: country, type: string, required: true, classification: public, description: "ISO country code"}
  - {name: city, type: string, required: true, classification: internal, description: "City of the main branch"}
  - {name: tier, type: string, required: true, classification: internal, description: "Subscription tier basic, pro or premium"}
  - {name: contact_email, type: string, required: false, classification: pii, description: "Email of the dealer's contact person"}
  - {name: contact_phone, type: string, required: false, classification: pii, description: "Phone of the dealer's contact person"}
  - {name: vat_id, type: string, required: false, classification: confidential, description: "VAT identification number"}
  - {name: onboarded_on, type: date, required: true, classification: internal, description: "Date the dealer joined"}
quality:
  - {check: not_null, column: dealer_id, severity: error}
  - {check: unique, column: dealer_id, severity: error}
  - {check: accepted_values, column: tier, values: [basic, pro, premium], severity: error}
  - {check: volume, min_ratio: 0.8, max_ratio: 1.25, severity: error}
```

### `products/leads/dealer_lead_performance.yaml`

```yaml
product: leads.dealer_lead_performance
domain: leads
owner: team-leads
owner_contact: "#data-leads"
kind: derived
version: 1
description: >
  Leads each dealer received per day, with the dealer's tier and the number of listings it had online,
  so sales and dealer success can compare lead performance across dealers and tiers.
tags: [dealer performance, leads per listing, KPI, dealer success]
partition: dt
sla: {freshness_hours: 34}
inputs: [leads.leads, dealers.dealers, listings.listing_events]
transform: dealer_lead_performance.sql
columns:
  - {name: dealer_id, type: string, required: true, classification: internal, description: "Id of the dealer"}
  - {name: country, type: string, required: true, classification: public, description: "ISO country code of the dealer"}
  - {name: tier, type: string, required: true, classification: internal, description: "Subscription tier"}
  - {name: leads, type: bigint, required: true, classification: internal, description: "Leads received that day"}
  - {name: listings_online, type: bigint, required: true, classification: internal, description: "Listings the dealer had online that day"}
  - {name: leads_per_listing, type: double, required: true, classification: internal, description: "leads divided by listings_online"}
quality:
  - {check: not_null, column: dealer_id, severity: error}
  - {check: range, column: leads_per_listing, min: 0, max: 100, severity: error}
  - {check: volume, min_ratio: 0.5, max_ratio: 2.0, severity: warn}
```

### `products/leads/leads.yaml`

```yaml
product: leads.leads
domain: leads
owner: team-leads
owner_contact: "#data-leads"
kind: source
version: 2
description: >
  Buyer enquiries sent to a seller about a listing, by email, phone call or chat. One row per lead.
  Contains the buyer's contact details.
tags: [lead, Anfrage, enquiry, contact request, buyer]
partition: dt
sla: {freshness_hours: 30}
source: {system: lead-service, format: jsonl}
columns:
  - {name: lead_id, type: string, required: true, classification: internal, description: "Id of the lead"}
  - {name: listing_id, type: string, required: true, classification: internal, description: "Listing the buyer asked about"}
  - {name: dealer_id, type: string, required: false, classification: internal, description: "Dealer who received the lead; empty for private sellers"}
  - {name: channel, type: string, required: true, classification: public, description: "email, call or chat"}
  - {name: country, type: string, required: true, classification: public, description: "ISO country code of the listing"}
  - {name: buyer_email, type: string, required: false, classification: pii, description: "Buyer's email address"}
  - {name: buyer_phone, type: string, required: false, classification: pii, description: "Buyer's phone number"}
  - {name: created_ts, type: timestamp, required: true, classification: internal, description: "When the lead was sent (UTC)"}
quality:
  - {check: not_null, column: lead_id, severity: error}
  - {check: unique, column: lead_id, severity: error}
  - {check: not_null, column: listing_id, severity: error}
  - {check: accepted_values, column: channel, values: [email, call, chat], severity: error}
  - {check: null_share, column: dealer_id, max: 0.6, severity: error}
  - {check: volume, min_ratio: 0.5, max_ratio: 2.0, severity: error}
```

### `products/listings/active_listings_daily.yaml`

```yaml
product: listings.active_listings_daily
domain: listings
owner: team-listings
owner_contact: "#data-listings"
kind: derived
version: 1
description: >
  Stock on the marketplace at the end of each day: number of active listings and median asking price by
  country and make. Use it for supply and price-level questions.
tags: [stock, supply, Bestand, inventory, median price]
partition: dt
sla: {freshness_hours: 32}
inputs: [listings.listing_events]
transform: active_listings_daily.sql
columns:
  - {name: country, type: string, required: true, classification: public, description: "ISO country code"}
  - {name: make, type: string, required: true, classification: public, description: "Manufacturer"}
  - {name: active_listings, type: bigint, required: true, classification: public, description: "Listings online at the end of the day"}
  - {name: median_price_eur, type: double, required: true, classification: public, description: "Median asking price of active listings"}
quality:
  - {check: not_null, column: country, severity: error}
  - {check: range, column: active_listings, min: 0, max: 10000000, severity: error}
  - {check: volume, min_ratio: 0.5, max_ratio: 2.0, severity: warn}
```

### `products/listings/listing_events.yaml`

```yaml
product: listings.listing_events
domain: listings
owner: team-listings
owner_contact: "#data-listings"
kind: source
version: 3
description: >
  Every change to a car listing on the marketplace: created, price changed, sold or removed. One row per
  event. Source-aligned: written as the listing service emits it, cleaned and typed.
tags: [listing, Inserat, ad, vehicle, price]
partition: dt
sla: {freshness_hours: 30}
source: {system: listing-service, format: jsonl}
columns:
  - {name: event_id, type: string, required: true, classification: internal, description: "Unique id of the event"}
  - {name: listing_id, type: string, required: true, classification: internal, description: "Id of the listing"}
  - {name: dealer_id, type: string, required: false, classification: internal, description: "Dealer who owns the listing; empty for private sellers"}
  - {name: seller_type, type: string, required: true, classification: public, description: "dealer or private"}
  - {name: make, type: string, required: true, classification: public, description: "Manufacturer, e.g. Volkswagen"}
  - {name: model, type: string, required: true, classification: public, description: "Model name, e.g. Golf"}
  - {name: first_registration_year, type: int, required: true, classification: public, description: "Year of first registration"}
  - {name: mileage_km, type: int, required: true, classification: public, description: "Odometer reading in kilometres"}
  - {name: fuel, type: string, required: true, classification: public, description: "petrol, diesel, electric, hybrid or lpg"}
  - {name: price_eur, type: double, required: true, classification: public, description: "Asking price in euros"}
  - {name: country, type: string, required: true, classification: public, description: "ISO country code of the listing"}
  - {name: event_type, type: string, required: true, classification: public, description: "created, price_change, sold or removed"}
  - {name: event_ts, type: timestamp, required: true, classification: internal, description: "When the event happened (UTC)"}
quality:
  - {check: not_null, column: event_id, severity: error}
  - {check: unique, column: event_id, severity: error}
  - {check: accepted_values, column: event_type, values: [created, price_change, sold, removed], severity: error}
  - {check: accepted_values, column: fuel, values: [petrol, diesel, electric, hybrid, lpg], severity: warn}
  - {check: range, column: price_eur, min: 100, max: 2000000, severity: error}
  - {check: range, column: mileage_km, min: 0, max: 1500000, severity: warn}
  - {check: volume, min_ratio: 0.5, max_ratio: 2.0, severity: error}
```

### `products/pricing/price_ratings.yaml`

```yaml
product: pricing.price_ratings
domain: pricing
owner: team-pricing
owner_contact: "#data-pricing"
kind: derived
version: 1
description: >
  The price rating shown on each listing created that day (great, good, fair or high), from the asking
  price compared with similar cars: same make, model, registration year band and mileage band.
tags: [price rating, Preisbewertung, market value, deal rating]
partition: dt
sla: {freshness_hours: 34}
inputs: [listings.listing_events]
transform: price_ratings.sql
columns:
  - {name: listing_id, type: string, required: true, classification: internal, description: "Id of the listing"}
  - {name: country, type: string, required: true, classification: public, description: "ISO country code"}
  - {name: make, type: string, required: true, classification: public, description: "Manufacturer"}
  - {name: model, type: string, required: true, classification: public, description: "Model"}
  - {name: price_eur, type: double, required: true, classification: public, description: "Asking price"}
  - {name: reference_price_eur, type: double, required: true, classification: internal, description: "Median price of comparable listings"}
  - {name: rating, type: string, required: true, classification: public, description: "great, good, fair or high"}
quality:
  - {check: not_null, column: listing_id, severity: error}
  - {check: accepted_values, column: rating, values: [great, good, fair, high], severity: error}
  - {check: volume, min_ratio: 0.3, max_ratio: 3.0, severity: warn}
```
