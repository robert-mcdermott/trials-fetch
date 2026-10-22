# trials-fetch reference

Output schema version 1. The authoritative JSON Schema is `src/trials_fetch/schema/output.schema.json` in the trials-fetch repository (study records are `$defs/study`). Missing values are `null`; missing lists are empty. Enumerated values are upper-case API codes.

## Study record

| Field | Type | Notes |
| --- | --- | --- |
| `nct_id` | string | `NCT` + 8 digits |
| `url` | string | ClinicalTrials.gov page |
| `nct_id_aliases` | list of strings | Former NCT IDs merged into this record |
| `org_study_id`, `secondary_ids` | string, list of `{id, type}` | Sponsor's ID; other registry/grant IDs |
| `brief_title`, `official_title`, `acronym` | string \| null | |
| `overall_status` | string | `RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `SUSPENDED`, `TERMINATED`, `WITHDRAWN`, `UNKNOWN`, expanded-access statuses |
| `last_known_status` | string \| null | For `UNKNOWN` (not verified in 2+ years) |
| `why_stopped` | string \| null | For terminated/withdrawn/suspended trials |
| `status_verified_date` | date \| null | |
| `start_date`, `primary_completion_date`, `completion_date` | date \| null | Each with `*_type`: `ACTUAL` or `ESTIMATED` |
| `first_submitted`, `first_posted`, `last_update_posted`, `results_first_posted` | date \| null | |
| `has_results` | bool | Results posted on ClinicalTrials.gov |
| `study_type` | string | `INTERVENTIONAL`, `OBSERVATIONAL`, `EXPANDED_ACCESS` |
| `phases` | list | `EARLY_PHASE1`, `PHASE1` ... `PHASE4`, `NA`; empty for observational |
| `allocation`, `intervention_model`, `primary_purpose`, `masking` | string \| null | Interventional design, e.g. `RANDOMIZED`, `PARALLEL`, `TREATMENT`, `DOUBLE` |
| `observational_model`, `time_perspective` | string \| null | Observational design, e.g. `COHORT`, `PROSPECTIVE` |
| `enrollment`, `enrollment_type` | int, `ACTUAL`/`ESTIMATED` | Participants (planned or actual) |
| `conditions`, `keywords` | list of strings | As entered by the sponsor |
| `condition_mesh`, `intervention_mesh` | list of `{id, term}` | Standardized MeSH terms |
| `interventions` | list of `{type, name, description, other_names[], arm_group_labels[]}` | `type`: `DRUG`, `BIOLOGICAL`, `DEVICE`, `PROCEDURE`, `RADIATION`, `BEHAVIORAL`, ... |
| `arms` | list of `{label, type, description, interventions[]}` | `type`: `EXPERIMENTAL`, `PLACEBO_COMPARATOR`, `ACTIVE_COMPARATOR`, ... |
| `primary_outcomes`, `secondary_outcomes`, `other_outcomes` | list of `{measure, description, time_frame}` | |
| `eligibility_criteria` | string \| null | Inclusion/exclusion text (Markdown) |
| `sex` | `ALL` \| `FEMALE` \| `MALE` \| null | |
| `minimum_age`, `maximum_age` | string \| null | e.g. `"18 Years"`, `"6 Months"`; null = no limit |
| `std_ages` | list | `CHILD` (<18), `ADULT` (18-64), `OLDER_ADULT` (65+) |
| `healthy_volunteers` | bool \| null | |
| `lead_sponsor` | `{name, class}` \| null | `class`: `INDUSTRY`, `NIH`, `FED`, `OTHER_GOV`, `NETWORK`, `INDIV`, `OTHER`, ... |
| `collaborators` | list of `{name, class}` | |
| `officials` | list of `{name, affiliation, role}` | `role`: `PRINCIPAL_INVESTIGATOR`, `STUDY_CHAIR`, `STUDY_DIRECTOR` |
| `central_contacts` | list of `{name, role, phone, email}` | |
| `locations` | list of `{facility, status, city, state, zip, country, latitude, longitude}` | `status` is the site's own recruitment status |
| `countries` | list of strings | Distinct site countries, sorted |
| `brief_summary`, `detailed_description` | string \| null | |
| `references` | list of `{pmid, type, citation}` | `type`: `RESULT` and `DERIVED` (the trial's publications), `BACKGROUND` (cited) |
| `documents` | list of `{type, label, date, has_protocol, has_sap, has_icf, filename, size, url, path}` | Uploaded protocol/SAP/ICF; `path` set by `--download-documents` |
| `is_fda_regulated_drug`, `is_fda_regulated_device` | bool \| null | |
| `ipd_sharing` | string \| null | `YES`, `NO`, `UNDECIDED` |

With `--raw`, records are the API's nested objects instead (`protocolSection`, `resultsSection`, `derivedSection`, `documentSection`, `hasResults`); see the [API data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure).

## search_info (run metadata)

`tool` (name, version), `generated_on` (UTC), `api_version`, `data_timestamp` (ClinicalTrials.gov data refresh), `source` (`search`, `ids` or `raw`), `query` (API parameters sent), `filters` (all search options, with the resolved date range), `sort`, `raw`, `max_results` (null = all), `total_found` (matches, or IDs requested), `expected`, `retrieved`, `truncated` (capped by `--max-results`), `complete` (no page failed), `failed_batches`, `missing_ids` (`--nct-ids` only), `skipped_records`, `skipped_existing` (`--resume`), `downloads`, `total_studies` (trials in the output file or database).

## SQLite tables

| Table | One row per | Columns |
| --- | --- | --- |
| `studies` | trial (PK `nct_id`) | `brief_title`, `official_title`, `acronym`, `overall_status`, `last_known_status`, `why_stopped`, `study_type`, `phases` (joined with `; `), `allocation`, `intervention_model`, `primary_purpose`, `masking`, `observational_model`, `time_perspective`, `enrollment`, `enrollment_type`, `start_date`, `start_date_type`, `primary_completion_date`, `primary_completion_date_type`, `completion_date`, `completion_date_type`, `first_submitted`, `first_posted`, `last_update_posted`, `results_first_posted`, `has_results`, `sex`, `minimum_age`, `maximum_age`, `healthy_volunteers`, `lead_sponsor`, `lead_sponsor_class`, `is_fda_regulated_drug`, `is_fda_regulated_device`, `ipd_sharing`, `brief_summary`, `eligibility_criteria`, `url`, `record_json`, `updated_at` |
| `conditions` | condition | `nct_id`, `condition` |
| `keywords` | keyword | `nct_id`, `keyword` |
| `phases` | phase | `nct_id`, `phase` |
| `mesh` | MeSH term | `nct_id`, `kind` (`condition`/`intervention`), `mesh_id`, `term` |
| `interventions` | intervention | `nct_id`, `type`, `name`, `description`, `other_names` |
| `sponsors` | sponsor | `nct_id`, `role` (`lead`/`collaborator`), `name`, `class` |
| `officials` | investigator | `nct_id`, `name`, `affiliation`, `role` |
| `locations` | site | `nct_id`, `facility`, `status`, `city`, `state`, `zip`, `country`, `latitude`, `longitude` |
| `outcomes` | outcome | `nct_id`, `kind` (`primary`/`secondary`/`other`), `measure`, `time_frame`, `description` |
| `study_references` | reference | `nct_id`, `pmid`, `type`, `citation` |
| `documents` | document | `nct_id`, `type`, `label`, `date`, `has_protocol`, `has_sap`, `has_icf`, `filename`, `size`, `url`, `path` |
| `runs` | run | `run_id`, `finished_at`, `search_info` (JSON) |

Dates are text (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`), so compare them as strings (`start_date >= '2020'`) and use `substr(start_date, 1, 4)` for the year. Booleans are 0/1. Fields that aren't columns can be read from `record_json`, e.g. `json_extract(record_json, '$.arms')`.

## Example queries

```sql
-- Status overview
SELECT overall_status, COUNT(*) AS trials FROM studies GROUP BY overall_status ORDER BY trials DESC;

-- Trials started per year, by phase
SELECT substr(start_date, 1, 4) AS year, phase, COUNT(*) AS trials
FROM studies JOIN phases USING (nct_id) WHERE start_date >= '2020'
GROUP BY year, phase ORDER BY year, phase;

-- Most-studied interventions (MeSH groups spelling variants)
SELECT term, COUNT(DISTINCT nct_id) AS trials FROM mesh WHERE kind = 'intervention'
GROUP BY term ORDER BY trials DESC LIMIT 15;

-- Industry sponsors
SELECT lead_sponsor, COUNT(*) AS trials FROM studies WHERE lead_sponsor_class = 'INDUSTRY'
GROUP BY lead_sponsor ORDER BY trials DESC LIMIT 10;

-- Results reporting by sponsor type, for finished trials
SELECT lead_sponsor_class, COUNT(*) AS trials, ROUND(100.0 * SUM(has_results) / COUNT(*), 1) AS pct_results
FROM studies WHERE overall_status IN ('COMPLETED', 'TERMINATED') GROUP BY lead_sponsor_class ORDER BY trials DESC;

-- Recruiting sites by country
SELECT country, COUNT(*) AS sites, COUNT(DISTINCT nct_id) AS trials FROM locations
WHERE status = 'RECRUITING' GROUP BY country ORDER BY sites DESC LIMIT 10;

-- Recruiting trials in a city
SELECT DISTINCT s.nct_id, s.brief_title, l.facility FROM studies s JOIN locations l USING (nct_id)
WHERE l.city = 'Boston' AND l.status = 'RECRUITING';

-- Why trials stopped
SELECT why_stopped, COUNT(*) AS trials FROM studies
WHERE overall_status IN ('TERMINATED', 'WITHDRAWN', 'SUSPENDED') AND why_stopped IS NOT NULL
GROUP BY lower(why_stopped) ORDER BY trials DESC LIMIT 10;

-- Principal investigators
SELECT name, affiliation, COUNT(*) AS trials FROM officials WHERE role = 'PRINCIPAL_INVESTIGATOR'
GROUP BY name ORDER BY trials DESC LIMIT 10;

-- Completed trials with neither posted results nor a publication
SELECT nct_id, completion_date, lead_sponsor FROM studies s
WHERE overall_status = 'COMPLETED' AND NOT has_results AND completion_date < '2023'
  AND NOT EXISTS (SELECT 1 FROM study_references r WHERE r.nct_id = s.nct_id AND r.type IN ('RESULT', 'DERIVED'));

-- PMIDs of the trials' publications (feed to pubmed-fetch --pmids -)
SELECT DISTINCT pmid FROM study_references WHERE type IN ('RESULT', 'DERIVED') AND pmid IS NOT NULL;

-- Arms of one trial
SELECT json_extract(a.value, '$.label') AS arm, json_extract(a.value, '$.type') AS type
FROM studies, json_each(record_json, '$.arms') AS a WHERE nct_id = 'NCT00000620';

-- Runs that built the database
SELECT run_id, finished_at, json_extract(search_info, '$.total_found') AS found,
       json_extract(search_info, '$.data_timestamp') AS data_as_of FROM runs;
```
