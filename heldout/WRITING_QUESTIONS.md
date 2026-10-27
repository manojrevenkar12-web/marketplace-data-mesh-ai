# Writing a held-out set of questions

You are writing questions that people at a car marketplace would ask a data assistant, and the answer a careful
analyst would accept. The set tests the assistant on questions it has never seen. Do not open anything in this
repository except this file: not the code, not the data, not other sets.

A set is a folder `heldout/<set-name>/` with one file, `questions.yaml`.

## What to write

Write 30 questions. Mix:

* 18 to 20 questions the person can answer with the data they may read. Vary them: counts, sums, averages and
  medians, breakdowns by one dimension, filters on country, make, fuel, channel or tier, periods ("yesterday",
  "last 7 days", "last month", a named month), top-N. Phrase them the way people type: short, long, with typos,
  some in German.
* 6 to 8 questions that must be refused: the person asks for data their group may not read (a column or a whole
  dataset), or for something no dataset holds (revenue, staff, the weather).
* 3 or 4 questions where the person may see only some rows (the dealer success group sees only German rows);
  the right answer is the one computed on those rows.

For every question give the user asking it (from the table below) and whether it should be answered or refused.
For answered questions write the gold query in Athena (Trino) SQL over the datasets below, the one a careful
analyst would write. "Yesterday" or "today's data" means the latest day with data; write it as `{latest}`, and
the day N days before it as `{latest-N}` (so "the last 7 days" is `dt BETWEEN DATE '{latest-6}' AND DATE '{latest}'`).
Always filter on `dt`. Keep the gold query's output to what the question asks (the columns' order and names do
not matter, the values do).

```yaml
questions:
  - id: h01
    user: anna.analyst
    question: "How many leads came in by email yesterday?"
    expect: answer            # answer | refuse
    gold_sql: "SELECT count(*) FROM leads.leads WHERE dt = DATE '{latest}' AND channel = 'email'"
    note: "optional: what makes this question interesting"
  - id: h02
    user: max.partner
    question: "Which dealers got the most leads?"
    expect: refuse
```

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
