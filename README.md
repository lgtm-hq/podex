# podex

Podex is a podcast media-index platform. It discovers podcast episodes,
transcribes and cleans them, and runs an LLM extraction pipeline to identify the
media, people, and works mentioned across episodes — surfacing them in a
searchable public catalog.

- **Backend** — FastAPI + SQLAlchemy, an `/api/v2` REST surface, and an LLM
  ingestion pipeline (transcription → cleanup → mention extraction → review).
- **Frontend** — an Astro/React/Tailwind discovery site.

## License

Podex is licensed under the GNU Affero General Public License v3.0
([AGPL-3.0-only](LICENSE)).

Prior commits were released under the MIT License; AGPL-3.0-only applies from
this relicense forward. See [NOTICE](NOTICE).
