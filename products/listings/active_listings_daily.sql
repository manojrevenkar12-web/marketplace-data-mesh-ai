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
