# podex

Podex is a podcast media index: a catalog of podcasts and episodes and the
media, people and works mentioned in them.

## What works today

- **Backend** — a FastAPI + SQLAlchemy REST API under `/api/v2`:
  - public read routes for podcasts, episodes, media, their mentions, and
    catalog stats;
  - passwordless accounts (email magic link, optionally WorkOS AuthKit) with
    saved media, followed podcasts, alert rules, email digests, preferences,
    data export and account deletion;
  - paid-tier checkout (off by default) and Paddle subscription webhooks;
  - public takedown requests;
  - an ops API, gated by an ops key, for managing podcasts, transcript retention
    and takedown decisions, plus a Prometheus `/metrics` endpoint.
- **Scheduler** — a separate process (`python -m podex.scheduler_runner`) that
  discovers new episodes from podscripts.co, RSS feeds and Spotify, delivers
  digests, and runs transcript retention sweeps.
- **Frontend** — an Astro/React/Tailwind site with the podcast catalog, podcast
  pages, account pages and an ops console.

Each external provider is optional.
[docs/configuration.md](docs/configuration.md) lists the settings that enable
each one and what happens without them.

## Not yet working

- Episodes do not get transcripts automatically: no transcript acquisition
  provider is implemented.
- Media enrichment from external catalogs, LLM transcript cleanup, LLM mention
  extraction and extraction review exist as backend services, but nothing in the
  API or scheduler runs them yet, so mentions are not produced automatically.
  Tracked in [#59](https://github.com/lgtm-hq/podex/issues/59).

See [CONTRIBUTING.md](CONTRIBUTING.md) to set up and run both apps.

## License

Podex is licensed under the GNU Affero General Public License v3.0
([AGPL-3.0-only](LICENSE)).

Prior commits were released under the MIT License; AGPL-3.0-only applies from
this relicense forward. See [NOTICE](NOTICE).
