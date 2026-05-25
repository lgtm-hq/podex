# Load Testing

Podex includes a bounded-concurrency HTTP load harness for the public grouped
search path and operator ingestion visibility path:

```bash
cd backend
uv run python scripts/load_test_core.py \
  --base-url https://staging-api.podex.example \
  --api-key "$API_KEY" \
  --search-requests 500 \
  --pipeline-reads 100 \
  --concurrency 20
```

The command prints JSON metrics and fails when its default thresholds are
exceeded: p95 latency above `750ms` or an error rate above `1%`. Adjust these
with `--max-p95-ms` and `--max-error-rate` for an agreed service-level target.
Raise staging rate limits for intentional load runs so `429` responses measure
the desired policy rather than accidentally dominating the test.

## Ingestion Triggers

The default workload reads pipeline state and does not create work. To exercise
the ingestion trigger write path, run only in a disposable or intentionally
seeded staging environment:

```bash
uv run python scripts/load_test_core.py \
  --base-url https://staging-api.podex.example \
  --api-key "$API_KEY" \
  --search-requests 100 \
  --pipeline-reads 20 \
  --ingestion-triggers 10
```

Every `--ingestion-triggers` request creates a durable ingestion run and audit
record. Review pipeline queues and clean test data according to the staging
operational policy after the run.
