# Changelog

## 0.2.0

trials-fetch is now a package that can be imported by other scripts, as well as a command-line tool.

### Breaking changes
- The code is now the `trials_fetch` package (in `src/`). Run it with `uv run trials-fetch` or `uv run -m trials_fetch`; `python fetch_trials.py` no longer works. The 0.1 command-line form still does: `trials-fetch "term" -o out.json --status RECRUITING --country Sweden`.
- JSON output is a document with `schema_version`, `search_info` and `studies`, where each study is a flat, normalized record (schema version 1) instead of the API's nested object. Use `--raw` for the API's objects (as a `studies` list in the document, or one per line with JSONL), and `--from-raw` to convert files written by 0.1.
- At most 1,000 trials are fetched by default (0.1 fetched every match). Use `--max-results all` for every match; a warning says when results were cut.
- Requires Python 3.10+ (was 3.12+).

### Fixed
- Country filters applied to only part of a search term that contained `OR`: `"glioblastoma OR glioma" --country Sweden` returned 2,489 trials instead of 45. Search terms and filters are now sent as separate API parameters.
- Requests had no timeout, retries or rate limiting; a transient error or rate limit (429) lost the whole run.
- Invalid `--status` values were sent to the API unchecked. Values are now validated (case-insensitively) before any request, and API errors show the API's explanation.

### Added
- Search fields: `--condition`, `--intervention`, `--title`, `--outcome`, `--sponsor`, `--location`.
- Filters: `--phase`, `--study-type`, `--site-status` (a site with this status, in the `--country` countries), `--near LAT,LON[,DIST]`, `--funder-type`, `--age-group`, `--age`, `--sex`, `--healthy-volunteers`, `--has-results`/`--no-results`, `--advanced` (any Essie expression).
- Date ranges on start, completion, first-posted, last-update or results-posted dates (`--date-field`, `--date-from`, `--date-to`, `--days`).
- `--max-results`, `--sort`, `--count`.
- `--nct-ids` to fetch specific trials (former IDs resolve; missing IDs are reported).
- Output as JSON, JSONL, CSV or a normalized SQLite database; `--output -` for stdout; `--merge`, `--resume`, `--summary`.
- `search_info` provenance and completeness: tool version, API version, data timestamp, API parameters, counts, `truncated`, `complete`, `failed_batches`, `missing_ids`. Exit code 2 for partial results.
- `--download-documents` for uploaded protocols, statistical analysis plans and consent forms.
- `--since-last-run STATE` for incremental monitoring.
- INI config files (`--config`, `--create-config`).
- A library API (`TrialsClient`, `QuerySpec`, `run`, `Options`, `parse_study`), a published JSON Schema (`trials_fetch.schema`), type hints checked with mypy, offline tests on real API responses, weekly live API tests in CI.
- An agent skill for AI coding agents in `skills/trials-fetch/`.

## 0.1.0
- Search ClinicalTrials.gov by term, filter by overall status and country, and save the API's study objects as JSON.
