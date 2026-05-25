# Operational Playbooks

The private endpoint `GET /api/v2/ops/alerts` evaluates current operational
metrics using configurable thresholds. The ops dashboard should be checked
before and after mitigation to confirm the alert clears.

## Review Backlog

Alert key: `review_backlog`

1. Open the review queue and identify high-priority or high-confidence pending
   candidates first.
2. Check recent pipeline runs for a sudden extraction-volume increase or a
   source producing unusually noisy candidates.
3. Assign additional review capacity or pause the problematic source while the
   backlog is reduced.
4. Confirm pending review items fall below
   `OPS_REVIEW_PENDING_ALERT_THRESHOLD`.

## Projection Lag

Alert keys: `projection_backlog`, `projection_age`, `projection_failures`

1. Inspect Search Operations for failed repairs and error messages.
2. Confirm Meilisearch connectivity and index health before replaying work.
3. Queue a scoped reindex for failed or stale records; avoid full rebuilds
   until the cause is understood.
4. Confirm failed repairs return to zero and oldest pending age falls below
   `OPS_PROJECTION_OLDEST_PENDING_MINUTES`.

## Notification Delivery

Alert key: `delivery_backlog`

1. Verify SMTP configuration and provider delivery availability.
2. Review generated events versus delivered digest counts to determine whether
   creation or transport is failing.
3. Restore delivery configuration and invoke normal digest processing; do not
   manually duplicate consumed events.
4. Confirm pending events fall below
   `OPS_ALERT_DELIVERY_PENDING_THRESHOLD` and a delivery record is persisted.

## Threshold Configuration

- `OPS_REVIEW_PENDING_ALERT_THRESHOLD=50`
- `OPS_PROJECTION_PENDING_ALERT_THRESHOLD=25`
- `OPS_PROJECTION_OLDEST_PENDING_MINUTES=60`
- `OPS_ALERT_DELIVERY_PENDING_THRESHOLD=25`

Tune staging thresholds from observed load and workflow volume, then document
production changes through the normal configuration review process.
