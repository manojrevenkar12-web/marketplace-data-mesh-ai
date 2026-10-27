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
