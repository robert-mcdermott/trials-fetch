---
name: trials-fetch
description: >-
  Search ClinicalTrials.gov and collect clinical trial data with the trials-fetch CLI: find trials by
  condition, intervention, sponsor, location, title, outcome or free text; filter by status, phase, study
  type, country, recruiting sites near a place, funder type, age, sex, results and dates; fetch known NCT
  IDs; get eligibility, sites, investigators, outcomes, linked PubMed IDs and protocol/SAP documents;
  export JSON/JSONL/CSV/SQLite; monitor trials over time. Use when the user asks about clinical trials,
  ClinicalTrials.gov, NCT numbers, recruiting or enrolling studies, trial landscapes, sponsors or
  pipelines, trial results reporting, or building a clinical trials dataset.
---

# trials-fetch

`trials-fetch` searches ClinicalTrials.gov (API v2) and writes structured, schema-versioned trial records. Use it instead of scraping the website or calling the API by hand: it handles paging, rate limits, retries, query syntax and NCT ID aliases.

Detailed field and database reference: [reference.md](reference.md).

## 1. Run the tool

Pick the first that works:

| Situation | Command prefix |
| --- | --- |
| Inside the trials-fetch repository | `uv run trials-fetch` |
| Installed (`uv tool install`) | `trials-fetch` |
| Anywhere else (no install needed) | `uvx --from git+https://github.com/robert-mcdermott/trials-fetch trials-fetch` |

Below, `trials-fetch` stands for whichever prefix applies. Check it works with `trials-fetch --version` (expect 0.2.0 or later). No API key or email is needed.

## 2. Workflow

1. **Translate the request into search fields and filters** (section 3). Use `--condition` for diseases and `--intervention` for treatments rather than the positional free-text term, which matches every field and is noisier.
2. **Count first** when the scope is uncertain. It costs one request:
   ```bash
   trials-fetch --condition "ovarian cancer" --phase 3 --status RECRUITING --count
   ```
   If the count is in the thousands, confirm the scope with the user or narrow it. If it is 0, loosen the search (fewer filters, broader terms); an invalid expression fails with an error instead of returning 0.
3. **Collect** into a file. Choose the format by purpose (section 4) and set `--max-results` deliberately (default 1000; `all` for every match).
4. **Check completeness before using the data.** Exit code `0` means complete, `2` means partial (a page failed; data is still written), `1` means error. `search_info.complete`, `search_info.truncated` (capped by `--max-results`) and `search_info.missing_ids` (for `--nct-ids`) say the same in the output.
5. **Read results with `jq` or `sqlite3`.** Never `cat` a large output file into the conversation; records include long eligibility text and site lists. Extract only the fields you need (section 6).
6. **Report the scope** when answering: the filters used, `total_found` versus retrieved, whether results were capped, and `search_info.data_timestamp` (when ClinicalTrials.gov last refreshed).

## 3. Building searches

Values within one option are ORed; different options are ANDed. Search fields use Essie syntax (`OR`, `AND`, `NOT`, quotes for phrases, `AREA[Field]value`). Put the positional term first.

| User wants | Option |
| --- | --- |
| A disease or condition | `--condition "type 2 diabetes"` (synonyms included) |
| A drug, device or procedure | `--intervention semaglutide "tirzepatide"` |
| A sponsor or collaborator | `--sponsor "Merck"`; by type: `--funder-type industry` / `nih` / `fed` / `other` |
| Words in the title | `--title vaccine` |
| An outcome measure | `--outcome "overall survival"` |
| A place (text) | `--location "Seattle"`; country: `--country "United States"`; radius: `--near 47.61,-122.33,25mi` |
| Recruiting now | `--status RECRUITING NOT_YET_RECRUITING` (overall status) |
| Recruiting *in a country* | `--country Sweden --site-status RECRUITING` (that country's site must be recruiting) |
| Phase | `--phase 2 3` (`0` = early phase 1; `NA` = not applicable, e.g. device/behavioral) |
| Study type | `--study-type interventional` / `observational` / `expanded_access` |
| Eligible patients | `--age 12`, `--age-group child` / `adult` / `older_adult`, `--sex female`, `--healthy-volunteers` |
| Has posted results | `--has-results` (or `--no-results`) |
| Anything else | `--advanced 'AREA[EnrollmentCount]RANGE[500, MAX]'` |

**Dates:** `--date-from YYYY-MM-DD`, `--date-to YYYY-MM-DD`, or `--days N`, applied to `--date-field`:
- `start` (default): trial start, often an estimate for future trials
- `primary_completion`, `completion`
- `first_posted`: registered on ClinicalTrials.gov ("new trials")
- `last_update`: record updated ("what changed")
- `results_posted`

**Inputs other than a search:** `--nct-ids NCT01234567 NCT07654321` (or a file of IDs, or `-` for stdin). Former IDs resolve to the current record.

**Ordering:** `--sort LastUpdatePostDate` (newest first), `--sort StartDate:asc`, `--sort EnrollmentCount:desc`. Without it, the order is arbitrary, so combine `--sort` with a small `--max-results` for "the latest N" (the "Only N of M matching studies" warning is expected then).

## 4. Choosing an output

| Purpose | Use |
| --- | --- |
| Quick look, piping to jq | `--output - --format jsonl -q` (one trial per line on stdout) |
| Analysis, many trials, repeated runs | `--output trials.sqlite` (normalized tables; reruns upsert) |
| Spreadsheet for the user | `--output trials.csv` (lists joined with `; `) |
| Results data (outcomes, adverse events) | `--raw --fields NCTId BriefTitle ResultsSection --output results.jsonl` |

Add `-q` to suppress the human-readable listing, and `--summary FILE` (or `--summary -`) to get `search_info` separately. Interrupted? Rerun the same command with `--resume`.

## 5. Worked examples

**"Which trials are recruiting for glioblastoma in Sweden?"**
```bash
trials-fetch --condition glioblastoma --country Sweden --site-status RECRUITING -q --output /tmp/gbm.jsonl
jq -c '{nct_id, brief_title, phases, sponsor: .lead_sponsor.name, sites: [.locations[] | select(.country == "Sweden") | "\(.facility), \(.city) (\(.status))"]}' /tmp/gbm.jsonl
```

**"Find trials my 12-year-old with type 1 diabetes could join near Seattle"**
```bash
trials-fetch --condition "type 1 diabetes" --age 12 --site-status RECRUITING --near 47.61,-122.33,50mi -q --output /tmp/t1d.jsonl
jq -c '{nct_id, brief_title, ages: "\(.minimum_age) - \(.maximum_age)", contact: .central_contacts[0]}' /tmp/t1d.jsonl
```
Then show the eligibility criteria for the most relevant ones: `jq -r 'select(.nct_id == "NCT...") | .eligibility_criteria' /tmp/t1d.jsonl`. Remind the user to discuss trials with their doctor; you can't determine eligibility.

**"What happened to NCT04280705?"**
```bash
trials-fetch --nct-ids NCT04280705 -q --output - --format jsonl \
  | jq '{nct_id, brief_title, overall_status, why_stopped, start_date, completion_date, enrollment, has_results, results_first_posted, publications: [.references[] | select(.type != "BACKGROUND") | .pmid]}'
```
If there are publications and the pubmed-fetch skill or tool is available, fetch them with `pubmed-fetch --pmids -`.

**"Map the phase 3 obesity drug landscape since 2020"**
```bash
trials-fetch --condition obesity --phase 3 --study-type interventional --date-from 2020-01-01 --max-results all \
  -q --output /tmp/obesity.sqlite --summary - | jq '{total_found, total_studies, complete}'
sqlite3 -header -column /tmp/obesity.sqlite \
  "SELECT term, COUNT(DISTINCT nct_id) AS trials FROM mesh WHERE kind = 'intervention' GROUP BY term ORDER BY trials DESC LIMIT 15;"
sqlite3 -header -column /tmp/obesity.sqlite \
  "SELECT lead_sponsor, COUNT(*) AS trials FROM studies WHERE lead_sponsor_class = 'INDUSTRY' GROUP BY lead_sponsor ORDER BY trials DESC LIMIT 10;"
```

**"Do sponsors report results for completed Alzheimer's trials?"**
```bash
trials-fetch --condition "Alzheimer Disease" --study-type interventional --status COMPLETED --max-results all \
  -q --output /tmp/alz.sqlite --summary - | jq '{total_found, total_studies, complete}'
sqlite3 -header -column /tmp/alz.sqlite "SELECT lead_sponsor_class, COUNT(*) AS trials, ROUND(100.0 * SUM(has_results) / COUNT(*), 1) AS pct_results FROM studies GROUP BY lead_sponsor_class ORDER BY trials DESC;"
```

**"Get the protocol so we can check the primary endpoint definition"**
```bash
trials-fetch --nct-ids NCT03405792 --download-documents /tmp/docs --document-types protocol sap -q --output /tmp/doc.jsonl
jq -r '.documents[] | select(.path) | .path' /tmp/doc.jsonl
```
Documents are usually PDFs; many trials have none (`documents` is empty).

**"Track new and changed recruiting melanoma trials every week"**
```bash
trials-fetch --condition melanoma --max-results all --since-last-run ~/trials/melanoma.state.json \
  --output ~/trials/melanoma.sqlite -q --summary - | jq '{since: .filters.date_from, retrieved, total_studies, complete}'
sqlite3 -header -column ~/trials/melanoma.sqlite \
  "SELECT nct_id, overall_status, last_update_posted, brief_title FROM studies
   WHERE overall_status IN ('RECRUITING', 'NOT_YET_RECRUITING') ORDER BY last_update_posted DESC LIMIT 20;"
```
- The first run collects every current match as a baseline. Later runs fetch only trials updated since the last run (`last_update`), with a one-day overlap, and SQLite replaces their old records.
- **Filter on status in SQL, not in the monitored search.** A monitored search with `--status`, `--site-status`, `--has-results` or `--no-results` never refetches trials that stop matching, so a trial that completes or posts results keeps its old values in the database.
- Use `--max-results all`: the state only advances after a complete, uncapped run. Use SQLite or `--merge`, never `--resume`, which would drop the updates.
- To also limit by another date, use `--advanced 'AREA[StartDate]RANGE[2020-01-01, MAX]'` (`--date-from` can't be combined with `--since-last-run`).

## 6. Reading results efficiently

```bash
# Compact listing
jq -c '{nct_id, overall_status, phases, brief_title}' out.jsonl
# Counts by status / phase / sponsor type / country
jq -r '.overall_status' out.jsonl | sort | uniq -c | sort -rn
jq -r '.phases | join("/")' out.jsonl | sort | uniq -c | sort -rn
jq -r '.lead_sponsor.class' out.jsonl | sort | uniq -c | sort -rn
jq -r '.countries[]' out.jsonl | sort | uniq -c | sort -rn | head
# Interventions
jq -r '.interventions[] | "\(.type): \(.name)"' out.jsonl | sort | uniq -c | sort -rn | head
# Recruiting sites in one country
jq -c '.nct_id as $id | .locations[] | select(.country == "Canada" and .status == "RECRUITING") | {$id, facility, city}' out.jsonl
# JSON documents (not JSONL): iterate .studies[]
jq -c '.studies[] | {nct_id, brief_title}' out.json
```
For SQLite, see the tables and example queries in [reference.md](reference.md). Child tables have one row per item, so use `COUNT(DISTINCT nct_id)`.

## 7. Pitfalls

- **Values within one option are ORed.** To require two conditions, put one in `--condition` and the other in `--advanced 'AREA[Condition]...'`, or use the positional term with `AND`.
- `--status RECRUITING --country X` doesn't mean the site in X is recruiting; use `--site-status`.
- With `--since-last-run`, don't filter on status or results in the search (the database goes stale as trials stop matching); filter in SQL instead.
- Dates in the future are estimates (`*_date_type: ESTIMATED`), and some dates are only `YYYY-MM`.
- Free-text searches match synonyms and related text broadly (e.g. "ovarian cancer" also matches breast cancer trials mentioning "ovarian function suppression"). Check `conditions`/`condition_mesh` before drawing conclusions, and filter further if needed.
- Drug names vary in spelling and brand; aggregate with `intervention_mesh` / the `mesh` table.
- `--max-results` defaults to 1000; mention it when `search_info.truncated` is true.
- Normalized records don't include the results section; use `--raw --fields ResultsSection`.
- `--days`/`--date-from` can't be combined; `--since-last-run` can't be used with `--date-from` or `--nct-ids`; `--raw` works only with JSON/JSONL; `--output -` can't be combined with SQLite, `--merge` or `--resume`.
- Don't run many fetches in parallel: they share ClinicalTrials.gov's per-IP rate limit (commonly cited as about 50 requests per minute).
