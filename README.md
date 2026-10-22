# Trials Fetch

A CLI and Python library that searches [ClinicalTrials.gov](https://clinicaltrials.gov) (API v2) and exports structured, schema-versioned trial records as JSON, JSONL, CSV or SQLite, ready for downstream analysis. Each record includes status and dates, phases and design, conditions and interventions with MeSH terms, eligibility, sponsors, investigators, sites with coordinates, outcomes, linked PubMed IDs, and links to uploaded protocols and analysis plans.

## Requirements
- Python 3.10+ (development uses 3.13, see .python-version)
- uv package manager

## Install
```bash
uv sync
```
This installs the `trials-fetch` command. Run it with `uv run trials-fetch ...` (or `uv run -m trials_fetch ...`). To install it globally, run `uv tool install .`; to run it without installing, use `uvx --from git+https://github.com/robert-mcdermott/trials-fetch trials-fetch ...`.

To use it as a library from another project: `uv add git+https://github.com/robert-mcdermott/trials-fetch` (see [Library use](#library-use)).

## Quick start
```bash
# Recruiting glioblastoma trials with a site in Sweden (same as trials-fetch 0.1)
uv run trials-fetch glioblastoma --status RECRUITING --country Sweden -o gbm.json

# How many trials match, without fetching them
uv run trials-fetch --condition "breast cancer" --phase 3 --count

# Phase 2/3 interventional breast cancer trials started since 2023, as CSV
uv run trials-fetch --condition "breast cancer" --phase 2 3 --study-type interventional --date-from 2023-01-01 -o breast.csv

# Every industry-sponsored pembrolizumab trial with posted results, into SQLite
uv run trials-fetch --intervention pembrolizumab --funder-type industry --has-results --max-results all -o pembro.sqlite

# Trials a 12-year-old could join, at a recruiting site within 50 miles of Seattle
uv run trials-fetch --condition "type 1 diabetes" --age 12 --site-status RECRUITING --near 47.61,-122.33,50mi

# Specific trials by NCT ID, piped to another tool
uv run trials-fetch --nct-ids NCT04280705 NCT00000620 -o - -q | jq '.studies[] | {nct_id, brief_title, overall_status}'

# Weekly monitoring into a SQLite database (only trials updated since the last run)
uv run trials-fetch --condition melanoma --status RECRUITING --since-last-run melanoma.state.json -o melanoma.sqlite

# Download protocols and statistical analysis plans
uv run trials-fetch --condition glioblastoma --has-results --download-documents docs --document-types protocol sap
```

## Searching
Search fields are free text, matched the same way as the ClinicalTrials.gov website (including synonyms):

| Option | Searches | API parameter |
| --- | --- | --- |
| `TERM` (positional) | all fields | `query.term` |
| `--condition` | conditions or diseases | `query.cond` |
| `--intervention` | interventions or treatments | `query.intr` |
| `--title` | titles and acronyms | `query.titles` |
| `--outcome` | outcome measures | `query.outc` |
| `--sponsor` | sponsors and collaborators | `query.spons` |
| `--location` | facility, city, state or country text | `query.locn` |

Several values for one option are ORed (`--condition "breast cancer" DCIS`); different options are ANDed. Values use [Essie expression syntax](https://clinicaltrials.gov/find-studies/constructing-complex-search-queries), so `'"heart attack" OR "myocardial infarction"'` and `'AREA[InterventionName]aspirin'` work. Put the positional search term first, before options that take several values. The exact API parameters sent are recorded in `search_info.query`.

## Filters
Values are case-insensitive. Several values for one filter are ORed; different filters are ANDed.

| Option | Values |
| --- | --- |
| `--status` | `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `SUSPENDED`, `TERMINATED`, `WITHDRAWN`, `UNKNOWN`, and the expanded-access statuses `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING`, `WITHHELD` |
| `--phase` | `EARLY_PHASE1`, `PHASE1` ... `PHASE4`, `NA` (not applicable); shorthands `0` (early phase 1), `1`-`4` |
| `--study-type` | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `--country` | countries with at least one site, e.g. `"United States"`, `"Korea, Republic of"` |
| `--site-status` | require a site with this status (`RECRUITING` etc.), in one of the `--country` countries if given |
| `--near LAT,LON[,DIST]` | a site within a distance of a point (default `50mi`; `mi` or `km`) |
| `--funder-type` | lead sponsor class: `INDUSTRY`, `NIH`, `FED` (other U.S. federal), `OTHER_GOV`, `NETWORK`, `INDIV`, `OTHER`, ... |
| `--age-group` | `CHILD` (under 18), `ADULT` (18-64), `OLDER_ADULT` (65+) |
| `--age YEARS` | trials whose minimum and maximum ages include this age |
| `--sex` | `female` or `male`: trials open to that sex (single-sex trials of the other sex are excluded) |
| `--healthy-volunteers` | trials that accept healthy volunteers |
| `--has-results` / `--no-results` | results posted on ClinicalTrials.gov, or not |
| `--advanced EXPR` | any Essie expression, e.g. `'AREA[EnrollmentCount]RANGE[100, MAX]'` |

**`--status` vs. `--site-status`.** `--status` is the trial's overall status. Combined with `--country`, it finds recruiting trials that have a site in that country, but that site may have finished or not opened yet. `--site-status RECRUITING --country Sweden` requires a Swedish site that is itself recruiting. In October 2026, the first query found 7 glioblastoma trials, the second 6. `--near` is matched separately from `--site-status`.

**Dates.** `--date-from` and `--date-to` (YYYY-MM-DD; either can be left open) or `--days N` (the N days up to `--date-to`, default today) filter on one date, chosen with `--date-field`:
- `start` (default): when the trial started or is expected to start
- `primary_completion`, `completion`
- `first_posted`: when the record first appeared on ClinicalTrials.gov
- `last_update`: when the record was last updated (best for monitoring; the default with `--since-last-run`)
- `results_posted`: when results were first posted

Many dates are estimates for trials that haven't reached them (`start_date_type` is `ESTIMATED`), and some are only a month (`2024-03`).

**How many.** `--max-results` (default 1000) caps the number of trials fetched; `--max-results all` removes the cap. When more match, a warning says so and `search_info.truncated` is true. `--count` prints the number of matches and exits, without fetching them. `--sort` orders the results by a date or number field, e.g. `LastUpdatePostDate` (dates sort newest first unless you add `:asc`), `EnrollmentCount:desc`, or `@relevance`. Without `--sort`, the order is unspecified.

## Inputs other than a search
- `--nct-ids ID|FILE ...`: fetch specific trials. Give NCT IDs, files listing them (whitespace/comma separated, `#` comments allowed), or `-` for stdin. Former NCT IDs that were merged into another record return the current record; IDs that don't exist are listed in `search_info.missing_ids`.
- `--from-raw FILE`: convert raw studies saved earlier with `--raw` (or the JSON written by trials-fetch 0.1) without contacting ClinicalTrials.gov, e.g. `--from-raw trials.json -o trials.sqlite`.

## Output
`-o/--output FILE` (or `-` for stdout); the format comes from `--format` or the extension:

| Format | Extension | Notes |
| --- | --- | --- |
| `json` | `.json` | One document: `schema_version`, `search_info`, `studies`. Written atomically at the end. |
| `jsonl` | `.jsonl`, `.ndjson` | One trial per line, written as pages arrive. |
| `csv` | `.csv` | One flattened row per trial; lists joined with `; `. Written as pages arrive. |
| `sqlite` | `.sqlite`, `.db` | Normalized tables (below). Records are upserted by NCT ID, so repeated runs accumulate. |

- `--raw` writes the API's own study objects unchanged (JSON or JSONL), including the full results section, which normalized records leave out. `--fields` limits raw output to some API fields or sections (e.g. `--fields BriefTitle OverallStatus ResultsSection`; field names are listed in the [data structure reference](https://clinicaltrials.gov/data-api/about-api/study-data-structure)).
- `--merge` updates an existing file, replacing records with the same NCT ID (json, jsonl, csv).
- `--resume` continues an interrupted run, skipping NCT IDs already in the output and appending the rest (jsonl, csv, sqlite, json).
- `--summary FILE` writes the run's `search_info` as JSON (`-` for stdout), which is handy for orchestration tools.

Progress and diagnostics go to stderr (`-q` for warnings and errors only, `-v` for debug output). The human-readable listing goes to stdout unless `-q` is given or the data itself is written to stdout.

### Exit codes
| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Error (bad options, invalid query, search failed, nothing could be fetched) |
| 2 | Partial results: a page or batch failed after retries. Output is written with `search_info.complete = false` and details in `search_info.failed_batches`. `--since-last-run` state is not advanced, so the next run retries. |

### Record format (schema version 1)
The JSON Schema is in [`src/trials_fetch/schema/output.schema.json`](src/trials_fetch/schema/output.schema.json) (study records are `$defs/study`) and is available in Python via `trials_fetch.schema.load_schema()`. Missing values are `null` and missing lists are empty, never placeholder strings. Enumerated values (statuses, phases, ...) are the API's upper-case codes.

Each study includes:
- Identification: `nct_id`, `url`, `nct_id_aliases` (former IDs), `org_study_id`, `secondary_ids`, `brief_title`, `official_title`, `acronym`
- Status: `overall_status`, `last_known_status` (for `UNKNOWN`), `why_stopped`, `status_verified_date`, `has_results`
- Dates: `start_date`, `primary_completion_date`, `completion_date` (each with a `_type` of `ACTUAL` or `ESTIMATED`), `first_submitted`, `first_posted`, `last_update_posted`, `results_first_posted`
- Design: `study_type`, `phases`, `allocation`, `intervention_model`, `primary_purpose`, `masking`, `observational_model`, `time_perspective`, `enrollment`, `enrollment_type`, `arms` (`label`, `type`, `description`, `interventions`)
- Topic: `conditions`, `keywords`, `condition_mesh` and `intervention_mesh` (`id`, `term`), `interventions` (`type`, `name`, `description`, `other_names`, `arm_group_labels`)
- Outcomes: `primary_outcomes`, `secondary_outcomes`, `other_outcomes` (`measure`, `description`, `time_frame`)
- Eligibility: `eligibility_criteria` (text), `sex`, `minimum_age`, `maximum_age` (e.g. `"18 Years"`), `std_ages`, `healthy_volunteers`
- People: `lead_sponsor` and `collaborators` (`name`, `class`), `officials` (`name`, `affiliation`, `role` such as `PRINCIPAL_INVESTIGATOR`), `central_contacts` (`name`, `role`, `phone`, `email`)
- Sites: `locations` (`facility`, `status`, `city`, `state`, `zip`, `country`, `latitude`, `longitude`), `countries` (distinct, sorted)
- Text: `brief_summary`, `detailed_description`
- `references` (`pmid`, `type`, `citation`): `RESULT` and `DERIVED` references are publications of the trial, `BACKGROUND` ones are cited by it
- `documents` (`type`, `label`, `date`, `has_protocol`, `has_sap`, `has_icf`, `filename`, `size`, `url`, `path`): uploaded protocols, statistical analysis plans and informed consent forms; `path` is set by `--download-documents`
- Oversight: `is_fda_regulated_drug`, `is_fda_regulated_device`, `ipd_sharing` (plan to share individual participant data)

`search_info` records provenance and completeness: `tool` (name, version), `generated_on` (UTC), `api_version` and `data_timestamp` (when ClinicalTrials.gov last refreshed its data), `source` (`search`, `ids` or `raw`), `query` (API parameters), `filters`, `sort`, `raw`, `total_found`, `max_results`, `expected`, `retrieved`, `truncated`, `complete`, `failed_batches`, `missing_ids`, `skipped_records`, `skipped_existing`, `downloads`, and `total_studies`.

### SQLite tables
| Table | One row per | Columns |
| --- | --- | --- |
| `studies` | trial (primary key `nct_id`) | `brief_title`, `official_title`, `acronym`, `overall_status`, `last_known_status`, `why_stopped`, `study_type`, `phases` (joined), `allocation`, `intervention_model`, `primary_purpose`, `masking`, `observational_model`, `time_perspective`, `enrollment`, `enrollment_type`, the dates and date types, `has_results`, `sex`, `minimum_age`, `maximum_age`, `healthy_volunteers`, `lead_sponsor`, `lead_sponsor_class`, `is_fda_regulated_drug`, `is_fda_regulated_device`, `ipd_sharing`, `brief_summary`, `eligibility_criteria`, `url`, `record_json` (the full record), `updated_at` |
| `conditions`, `keywords`, `phases` | condition / keyword / phase | `nct_id`, `condition` / `keyword` / `phase` |
| `mesh` | MeSH term | `nct_id`, `kind` (`condition` or `intervention`), `mesh_id`, `term` |
| `interventions` | intervention | `nct_id`, `type`, `name`, `description`, `other_names` |
| `sponsors` | sponsor | `nct_id`, `role` (`lead` or `collaborator`), `name`, `class` |
| `officials` | investigator | `nct_id`, `name`, `affiliation`, `role` |
| `locations` | site | `nct_id`, `facility`, `status`, `city`, `state`, `zip`, `country`, `latitude`, `longitude` |
| `outcomes` | outcome measure | `nct_id`, `kind` (`primary`, `secondary`, `other`), `measure`, `time_frame`, `description` |
| `study_references` | reference | `nct_id`, `pmid`, `type`, `citation` |
| `documents` | uploaded document | `nct_id`, `type`, `label`, `date`, `has_protocol`, `has_sap`, `has_icf`, `filename`, `size`, `url`, `path` |
| `runs` | run of trials-fetch | `finished_at`, `search_info` (JSON) |

Child tables have one row per item, so use `COUNT(DISTINCT nct_id)` when counting trials.

### Example: build and query a trials database
Collect every interventional ovarian cancer trial that started (or is expected to start) since 2015. This took 8 seconds for about 2,500 trials and a 70 MB database:
```bash
uv run trials-fetch --condition "ovarian cancer" --study-type interventional --date-from 2015-01-01 \
  --max-results all -o ovarian.sqlite
```

Then query it with the `sqlite3` command-line tool (preinstalled on macOS and most Linux systems), e.g. `sqlite3 -header -column ovarian.sqlite "<query>"`:

```sql
-- Overview
SELECT COUNT(*) AS trials, SUM(overall_status = 'RECRUITING') AS recruiting,
       SUM(has_results) AS with_results, SUM(enrollment) AS participants
FROM studies;

-- Trials started per year, by phase
SELECT substr(start_date, 1, 4) AS year, phase, COUNT(*) AS trials
FROM studies JOIN phases USING (nct_id)
WHERE start_date >= '2020' AND start_date < '2026'
GROUP BY year, phase ORDER BY year, phase;

-- Most-studied drugs, by MeSH term (which groups spelling variants and brand names)
SELECT term, COUNT(DISTINCT nct_id) AS trials
FROM mesh WHERE kind = 'intervention'
GROUP BY term ORDER BY trials DESC LIMIT 10;

-- Results reporting by sponsor type, for finished trials
SELECT lead_sponsor_class, COUNT(*) AS trials,
       ROUND(100.0 * SUM(has_results) / COUNT(*), 1) AS pct_with_results
FROM studies WHERE overall_status IN ('COMPLETED', 'TERMINATED')
GROUP BY lead_sponsor_class ORDER BY trials DESC;

-- Top industry sponsors
SELECT lead_sponsor, COUNT(*) AS trials
FROM studies WHERE lead_sponsor_class = 'INDUSTRY'
GROUP BY lead_sponsor ORDER BY trials DESC LIMIT 10;

-- Countries with the most recruiting sites
SELECT country, COUNT(*) AS recruiting_sites, COUNT(DISTINCT nct_id) AS trials
FROM locations WHERE status = 'RECRUITING'
GROUP BY country ORDER BY recruiting_sites DESC LIMIT 10;

-- Recruiting trials at sites in a city
SELECT DISTINCT s.nct_id, s.brief_title, l.facility
FROM studies s JOIN locations l USING (nct_id)
WHERE l.city = 'Seattle' AND l.status = 'RECRUITING';

-- Why trials stopped early
SELECT why_stopped, COUNT(*) AS trials
FROM studies WHERE overall_status IN ('TERMINATED', 'WITHDRAWN', 'SUSPENDED') AND why_stopped IS NOT NULL
GROUP BY lower(why_stopped) ORDER BY trials DESC LIMIT 10;

-- Most common primary outcomes
SELECT measure, COUNT(*) AS trials
FROM outcomes WHERE kind = 'primary'
GROUP BY lower(measure) ORDER BY trials DESC LIMIT 10;

-- Principal investigators with the most trials
SELECT name, affiliation, COUNT(*) AS trials
FROM officials WHERE role = 'PRINCIPAL_INVESTIGATOR'
GROUP BY name ORDER BY trials DESC LIMIT 10;

-- Completed trials with neither posted results nor a results publication
SELECT nct_id, completion_date, lead_sponsor FROM studies s
WHERE overall_status = 'COMPLETED' AND NOT has_results AND completion_date < '2023'
  AND NOT EXISTS (SELECT 1 FROM study_references r WHERE r.nct_id = s.nct_id AND r.type IN ('RESULT', 'DERIVED'))
ORDER BY completion_date DESC;

-- Protocols and analysis plans available for download
SELECT nct_id, label, url FROM documents WHERE has_protocol OR has_sap;

-- Arms of a trial, from record_json
SELECT json_extract(a.value, '$.label') AS arm, json_extract(a.value, '$.type') AS type
FROM studies, json_each(record_json, '$.arms') AS a WHERE nct_id = 'NCT03127098';

-- History of the runs that built the database
SELECT run_id, finished_at,
       json_extract(search_info, '$.total_found') AS found,
       json_extract(search_info, '$.total_studies') AS in_database,
       json_extract(search_info, '$.data_timestamp') AS data_as_of
FROM runs;
```
Fields that aren't columns can be read from `record_json` with SQLite's JSON functions, as in the arms query.

The same database loads straight into pandas (here run ad hoc with `uv run --with pandas`, without adding it to the project):
```bash
uv run --with pandas python - <<'EOF'
import sqlite3
import pandas as pd

db = sqlite3.connect("ovarian.sqlite")
trials = pd.read_sql("SELECT nct_id, start_date, lead_sponsor_class FROM studies", db)
trials["start_year"] = trials["start_date"].str[:4]
print(trials.pivot_table(index="start_year", columns="lead_sponsor_class", values="nct_id", aggfunc="count"))
EOF
```

To keep the database current, run the same search on a schedule with `--since-last-run` (see [Incremental runs](#incremental-runs)).

### Linking to PubMed
`references[].pmid` holds the PubMed IDs of a trial's publications. They can be fetched with [pubmed-fetch](https://github.com/robert-mcdermott/pubmed-fetch), e.g. for abstracts and citation metrics of results papers:
```bash
uv run trials-fetch --nct-ids NCT00000620 -o - -q \
  | jq -r '.studies[].references[] | select(.type == "RESULT" or .type == "DERIVED") | .pmid' \
  | pubmed-fetch --pmids - --icite --output accord-papers.jsonl
```

## Documents
`--download-documents DIR` downloads the protocols (`protocol`), statistical analysis plans (`sap`) and informed consent forms (`icf`) that sponsors uploaded, into `DIR/<NCT ID>/<filename>` (usually PDF). `--document-types` picks which ones; a file containing several (e.g. `Prot_SAP_000.pdf`) is downloaded if any of them is wanted. Files already present with the expected size are skipped, so reruns are cheap. Each downloaded document's `path` is recorded in the output. About three quarters of the trials with documents have posted results (`--has-results` finds them), and files can be several MB each, so start with a small `--max-results`.

## Incremental runs
`--since-last-run STATE` fetches only trials updated (`last_update`, unless `--date-field` says otherwise) since the end date stored in the STATE file, then updates the file after a complete run. The first run has no date limit (unless you give `--days`), so it collects everything that currently matches as a baseline. Runs overlap by one day so nothing posted late is missed; use SQLite output, `--merge` or `--resume` to de-duplicate. The state is not advanced when a page failed or when `--max-results` cut the results, since that would skip trials.

## Using a config file (INI)
All options can go in a `[search]` section; see [sample_config.ini](sample_config.ini), or generate one with `uv run trials-fetch --create-config my_config.ini` (it won't overwrite an existing file). Lists are comma-separated and/or one per line; quote values that contain commas:
```ini
[search]
conditions = Glioblastoma
statuses = RECRUITING, NOT_YET_RECRUITING
countries = "Korea, Republic of", Japan
phases =
    2
    3
max_results = all
output_file = gbm.sqlite
```
Run it with `uv run trials-fetch --config my_config.ini`. Unknown keys are reported, so typos don't go unnoticed. Command-line options override the config file, e.g. `--config my_config.ini --country Canada`.

## Library use
```python
from trials_fetch import Options, QuerySpec, TrialsClient, run

client = TrialsClient()

# Records in memory
studies = client.search_studies(conditions=["glioblastoma"], statuses=["RECRUITING"], countries=["Sweden"])
for study in studies:
    print(study["nct_id"], study["overall_status"], study["brief_title"])

# Streamed page by page, for large searches
spec = QuerySpec(interventions=["pembrolizumab"], phases=["3"], has_results=True)
print(client.count(spec), "matches")
for page in client.iter_search(spec, max_results=None):
    ...
print(client.search_info["complete"])

# Specific trials, one trial, or the API's raw objects
studies = client.fetch_studies(["NCT04280705", "NCT00000620"])
study = client.get_study("NCT04280705")  # None if it doesn't exist
raw = client.search_studies(conditions=["glioblastoma"], raw=True, max_results=10)

# The full pipeline, same as the CLI: writes the output file and returns the run summary
result = run(Options(spec=QuerySpec(conditions=["melanoma"]), output_file="melanoma.sqlite", display=False))
print(result.exit_code, result.search_info["total_studies"])
```
`QuerySpec` validates and normalizes its values (raising `ValueError`), so `phases=["2"]` and `statuses=["recruiting"]` work. Errors from the API (such as an invalid Essie expression) raise `TrialsError` with the API's explanation. `parse_study()` converts a raw API study into a record, and `trials_fetch.schema.study_schema()` returns the JSON Schema for one record. The package doesn't configure logging; it logs to the `trials_fetch` logger.

## Using trials-fetch from AI coding agents (agent skill)
[`skills/trials-fetch/`](skills/trials-fetch/) is an agent skill in the open [Agent Skills](https://agentskills.io) format, which Claude Code, OpenAI Codex, Gemini CLI, GitHub Copilot, Cursor, Devin and other coding agents support. It teaches an agent to use this tool well:
- run it, with no install needed
- check the match count before collecting
- turn requests into the right search fields and filters
- pick an output format and confirm the results are complete
- read results with `jq`/`sqlite3` instead of loading whole files

`SKILL.md` holds the workflow and worked examples; `reference.md` holds the record fields, SQLite tables and example queries, and is loaded only when needed.

**Requirements:** the agent must be able to run shell commands, with [uv](https://docs.astral.sh/uv/) installed and network access to clinicaltrials.gov. The skill runs the tool with `uvx --from git+https://github.com/robert-mcdermott/trials-fetch trials-fetch` when it isn't installed. `jq` and `sqlite3` are used for reading results.

### Install with a skills installer
Either of these copies the skill into the right folder for the agents you choose, at project or personal scope:
```bash
# skills CLI (Node.js 18+); supports Claude Code, Codex, Cursor, Copilot, Gemini CLI and more
npx skills add robert-mcdermott/trials-fetch

# GitHub CLI (skill support is in preview); see `gh skill install --help` for --agent and --scope
gh skill install robert-mcdermott/trials-fetch trials-fetch
```

### Install manually
Copy (or symlink) the `skills/trials-fetch` folder into a skills folder your agent reads:

| Agent | Project (commit it to share with a team) | Personal (all your projects) |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| OpenAI Codex | `.agents/skills/` | `~/.agents/skills/` |
| Gemini CLI | `.agents/skills/` or `.gemini/skills/` | `~/.agents/skills/` or `~/.gemini/skills/` |
| GitHub Copilot | `.github/skills/`, `.agents/skills/` or `.claude/skills/` | `~/.agents/skills/` or `~/.copilot/skills/` |
| Devin | `.agents/skills/` or `.devin/skills/` | `~/.agents/skills/` or `~/.config/devin/skills/` |
| Cursor, Cline, OpenCode, Amp, Warp and others | `.agents/skills/` | see the agent's documentation |

For example:
```bash
git clone https://github.com/robert-mcdermott/trials-fetch.git
mkdir -p ~/.agents/skills ~/.claude/skills
cp -r trials-fetch/skills/trials-fetch ~/.agents/skills/   # Codex, Gemini CLI, Copilot, Devin, Cursor, ...
cp -r trials-fetch/skills/trials-fetch ~/.claude/skills/   # Claude Code
```
To pick up updates with `git pull`, symlink instead of copying. Restart the agent, or reload its skills, after installing.

### Use it
Ask in plain language, and the agent loads the skill when the request matches its description:
> Find phase 3 trials of GLP-1 drugs for obesity that are recruiting in Canada, and summarize them.

> Build a SQLite database of all interventional Alzheimer's trials since 2015 and show which sponsors post results.

> What happened to NCT04280705? Did it post results or publish?

You can also invoke it by name: `/trials-fetch` in Claude Code and Devin, or `$trials-fetch` in Codex. For an agent without skill support, point it at the file instead, e.g. "Read `skills/trials-fetch/SKILL.md` and follow it to find trials on ...".

## Notes
- No API key is needed. ClinicalTrials.gov doesn't publish a firm rate limit (about 50 requests per minute per IP address is the commonly cited figure), so trials-fetch paces its requests below that and retries rate-limit (429) and server (5xx) errors with backoff. Each request returns up to 1,000 trials, so even large collections take few requests.
- ClinicalTrials.gov refreshes its data once a day; `search_info.data_timestamp` says when.
- Records are as submitted by sponsors, so values vary in spelling and completeness (e.g. drug names, outcome wording). MeSH terms (`condition_mesh`, `intervention_mesh`) are the standardized versions.
- Normalized records leave out the results section (outcome data, adverse events, participant flow). Use `--raw` (optionally with `--fields ResultsSection`) to get it.
- See [CHANGELOG.md](CHANGELOG.md) for changes between versions, including the 0.2.0 changes from the original script.

## Development
```bash
uv sync --extra dev          # pytest, ruff, mypy, jsonschema
uv run -m pytest -q          # unit tests (offline, use real API responses in tests/fixtures)
TRIALS_FETCH_LIVE=1 uv run -m pytest -q -m live   # contract tests against the real API
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
When CLI options or the output change, update the agent skill in `skills/trials-fetch/` to match.

GitHub Actions runs lint, type checks and tests on Python 3.10-3.13 for every push and pull request (`.github/workflows/ci.yml`), and the live API tests weekly (`.github/workflows/live.yml`).
