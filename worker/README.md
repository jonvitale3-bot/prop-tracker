# worker

Python worker for prop-tracker. Owns scraping, parsing, and grading.

## Setup

```bash
cd worker
uv sync                # creates .venv and installs deps from pyproject.toml
cp ../.env.example ../.env   # then fill in the secrets
```

## Run commands

```bash
uv run migrate           # apply SQL migrations from ../shared/sql
uv run ingest-reddit     # pull Reddit posts \u2192 mentions table
uv run ingest-twitter    # pull tweets \u2192 mentions table
uv run parse-mentions    # mentions \u2192 plays via Claude Haiku
uv run grade-results     # plays \u2192 results via ESPN box scores
```
