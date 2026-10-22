# AGENTS.md

- Use `uv run` to run anything (never bare `python`), and `uv add` for dependencies (never `pip`).
- Package code is in `src/trials_fetch/`: `query` (QuerySpec -> API parameters, Essie filters, dates), `client` (API v2 paging, NCT ID lookup, raw files), `parse` (API study -> record), `documents` (protocol/SAP/ICF downloads), `outputs` (sinks), `state`, `pipeline` (`run()`), `cli`.
- Verify changes with:
  - `uv run -m pytest -q` (offline; must not touch the network)
  - `uv run ruff check . && uv run ruff format --check .`
  - `uv run mypy`
  - `TRIALS_FETCH_LIVE=1 uv run -m pytest -q -m live` when changing anything that talks to ClinicalTrials.gov, especially query building: the API rejects unknown areas and values, and `test_every_filter_is_accepted` checks each filter against it.
- Record changes to the output must update `src/trials_fetch/schema/output.schema.json` (tests validate against it), the SQLite schema and CSV columns in `outputs.py` if relevant, and the CHANGELOG; bump `SCHEMA_VERSION` in `_meta.py` for breaking changes.
- `sample_config.ini` must equal `cli.SAMPLE_CONFIG` (a test checks this).
- The agent skill in `skills/trials-fetch/` (`SKILL.md`, `reference.md`) documents the CLI for other agents; update it when options or output fields change. Keep its frontmatter portable: only `name` (must match the folder name) and `description` (at most 1024 characters), with no tool-specific fields.
- `tests/fixtures/studies_sample.json` holds real API responses covering edge cases (aliases, results, documents, expanded access, UNKNOWN status, terminated, withdrawn), with contact emails and phones replaced by placeholders. Regenerate it with `uv run python tests/fixtures/make_fixtures.py`; tests rely on its order and contents.
