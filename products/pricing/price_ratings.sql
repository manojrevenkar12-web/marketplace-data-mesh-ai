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
