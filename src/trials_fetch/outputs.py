"""Output sinks (JSON, JSONL, CSV, SQLite), the human-readable listing and the run summary."""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from ._meta import SCHEMA_VERSION, utc_now
from .parse import Study, parse_study, record_id

log = logging.getLogger(__name__)

OUTPUT_FORMATS = ("json", "jsonl", "csv", "sqlite")
# Formats that can hold raw API study objects (--raw).
RAW_FORMATS = ("json", "jsonl")
EXTENSIONS = {
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".csv": "csv",
    ".sqlite": "sqlite",
    ".db": "sqlite",
}
STDOUT = "-"

CSV_FIELDS = [
    "nct_id",
    "url",
    "brief_title",
    "official_title",
    "acronym",
    "overall_status",
    "why_stopped",
    "study_type",
    "phases",
    "conditions",
    "keywords",
    "condition_mesh_terms",
    "interventions",
    "intervention_types",
    "intervention_mesh_terms",
    "lead_sponsor",
    "lead_sponsor_class",
    "collaborators",
    "start_date",
    "start_date_type",
    "primary_completion_date",
    "primary_completion_date_type",
    "completion_date",
    "completion_date_type",
    "first_posted",
    "last_update_posted",
    "results_first_posted",
    "has_results",
    "enrollment",
    "enrollment_type",
    "allocation",
    "intervention_model",
    "masking",
    "primary_purpose",
    "observational_model",
    "sex",
    "minimum_age",
    "maximum_age",
    "std_ages",
    "healthy_volunteers",
    "countries",
    "location_count",
    "officials",
    "primary_outcomes",
    "pmids",
    "document_urls",
    "is_fda_regulated_drug",
    "is_fda_regulated_device",
    "ipd_sharing",
    "brief_summary",
]


def resolve_output(filename: str | None = None, fmt: str | None = None) -> tuple[str, str]:
    """Return (filename, format). The format comes from ``fmt``, else the file extension, else JSON."""
    ext_fmt = EXTENSIONS.get(Path(filename).suffix.lower()) if filename and filename != STDOUT else None
    fmt = fmt or ext_fmt or "json"
    if fmt not in OUTPUT_FORMATS:
        raise ValueError(f"Unknown output format: {fmt}")
    return filename or f"trials.{fmt}", fmt


def _join(values: Iterable[Any]) -> str:
    return "; ".join(str(v) for v in values if v not in (None, ""))


def csv_row(study: Study) -> dict[str, Any]:
    lead = study.get("lead_sponsor") or {}
    row = {key: study.get(key) for key in CSV_FIELDS}
    row.update(
        phases=_join(study["phases"]),
        conditions=_join(study["conditions"]),
        keywords=_join(study["keywords"]),
        condition_mesh_terms=_join(m["term"] for m in study["condition_mesh"]),
        interventions=_join(f"{i['type']}: {i['name']}" if i["type"] else i["name"] for i in study["interventions"]),
        intervention_types=_join(dict.fromkeys(i["type"] for i in study["interventions"])),
        intervention_mesh_terms=_join(m["term"] for m in study["intervention_mesh"]),
        lead_sponsor=lead.get("name"),
        lead_sponsor_class=lead.get("class"),
        collaborators=_join(c["name"] for c in study["collaborators"]),
        std_ages=_join(study["std_ages"]),
        countries=_join(study["countries"]),
        location_count=len(study["locations"]),
        officials=_join(
            f"{o['name']} ({o['affiliation']})" if o["affiliation"] else o["name"] for o in study["officials"]
        ),
        primary_outcomes=_join(o["measure"] for o in study["primary_outcomes"]),
        pmids=_join(r["pmid"] for r in study["references"]),
        document_urls=_join(d["url"] for d in study["documents"]),
    )
    return {k: "" if v is None else v for k, v in row.items()}


def _atomic_write(path: str, write) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        write(f)
    os.replace(tmp, path)


class Sink:
    """
    Receives batches of records as they are fetched. ``existing_ids`` lists records already in
    the output that ``resume`` mode skips. ``close`` finalizes the output and returns the
    number of records it contains.
    """

    streaming = False

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        self.path = path
        self.merge = merge
        self.resume = resume
        self.existing_ids: set[str] = set()

    def write(self, batch: list[Study]) -> None:
        raise NotImplementedError

    def close(self, search_info: dict[str, Any]) -> int:
        raise NotImplementedError


class JsonSink(Sink):
    """A single JSON document. Held in memory and written atomically on close."""

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self.studies: dict[str, Study] = {}
        if (merge or resume) and path != STDOUT and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict) or "studies" not in doc:
                raise ValueError(f"{path} isn't trials-fetch JSON output (no 'studies' list); can't merge or resume.")
            self.studies = {record_id(s): s for s in doc["studies"]}
            if resume:
                self.existing_ids = set(self.studies)

    def write(self, batch: list[Study]) -> None:
        self.studies.update((record_id(s), s) for s in batch)

    def close(self, search_info: dict[str, Any]) -> int:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "search_info": {**search_info, "total_studies": len(self.studies)},
            "studies": list(self.studies.values()),
        }
        if self.path == STDOUT:
            json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            _atomic_write(self.path, lambda f: json.dump(doc, f, indent=2, ensure_ascii=False))
        return len(self.studies)


class _LineSink(Sink):
    """
    Base for line-oriented formats (JSONL, CSV). Batches are written and flushed as they arrive;
    ``resume`` appends to an existing file. ``merge`` rewrites the file at close, replacing
    existing records with the same NCT ID.
    """

    streaming = True

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self._file: IO[str] | None = None
        self._count = 0
        self._previous: dict[str, Any] = {}
        exists = path != STDOUT and os.path.exists(path) and os.path.getsize(path) > 0
        if exists and resume:
            self._drop_partial_line()
        if exists and (merge or resume):
            self._previous = self._read_existing()
            if resume:
                self.existing_ids = set(self._previous)
                self._count = len(self._previous)
        self._append = resume and exists

    def _drop_partial_line(self) -> None:
        """Remove a trailing partial line left by an interrupted run, so appended records stay valid."""
        with open(self.path, "rb+") as f:
            data = f.read()
            end = data.rfind(b"\n") + 1
            if end < len(data):
                log.warning("Removing an incomplete last line from %s (likely from an interrupted run)", self.path)
                f.truncate(end)

    def _read_existing(self) -> dict[str, Any]:
        raise NotImplementedError

    def _write_header(self, f: IO[str]) -> None:
        return None

    def _write_records(self, f: IO[str], batch: list[Study]) -> None:
        raise NotImplementedError

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        raise NotImplementedError

    def _open(self) -> IO[str]:
        if self._file is None:
            if self.path == STDOUT:
                self._file = sys.stdout
            else:
                target = f"{self.path}.tmp" if self.merge else self.path
                self._file = open(target, "a" if self._append else "w", encoding="utf-8", newline="")
            if not self._append:
                self._write_header(self._file)
        return self._file

    def write(self, batch: list[Study]) -> None:
        f = self._open()
        self._write_records(f, batch)
        f.flush()
        self._count += len(batch)
        if self.merge:
            for study in batch:
                self._previous.pop(record_id(study), None)

    def close(self, search_info: dict[str, Any]) -> int:
        f = self._open()
        if self.merge:
            self._write_previous(f, self._previous.values())
            self._count += len(self._previous)
        if f is not sys.stdout:
            f.close()
            if self.merge:
                os.replace(f"{self.path}.tmp", self.path)
        return self._count


class JsonlSink(_LineSink):
    """One record per line."""

    def _read_existing(self) -> dict[str, Any]:
        records = {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Ignoring an incomplete line in %s (likely from an interrupted run)", self.path)
                    continue
                records[record_id(record)] = record
        return records

    def _write_records(self, f: IO[str], batch: list[Study]) -> None:
        f.writelines(json.dumps(s, ensure_ascii=False) + "\n" for s in batch)

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        self._write_records(f, list(previous))


class CsvSink(_LineSink):
    """One flattened row per trial; list fields are joined with "; "."""

    def _read_existing(self) -> dict[str, Any]:
        with open(self.path, encoding="utf-8", newline="") as f:
            return {row["nct_id"]: row for row in csv.DictReader(f) if row.get("nct_id")}

    def _writer(self, f: IO[str]) -> csv.DictWriter:
        return csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")

    def _write_header(self, f: IO[str]) -> None:
        self._writer(f).writeheader()

    def _write_records(self, f: IO[str], batch: list[Study]) -> None:
        self._writer(f).writerows(map(csv_row, batch))

    def _write_previous(self, f: IO[str], previous: Iterable[Any]) -> None:
        self._writer(f).writerows({k: row.get(k, "") for k in CSV_FIELDS} for row in previous)


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
    nct_id TEXT PRIMARY KEY, brief_title TEXT, official_title TEXT, acronym TEXT,
    overall_status TEXT, last_known_status TEXT, why_stopped TEXT,
    study_type TEXT, phases TEXT, allocation TEXT, intervention_model TEXT, primary_purpose TEXT, masking TEXT,
    observational_model TEXT, time_perspective TEXT, enrollment INTEGER, enrollment_type TEXT,
    start_date TEXT, start_date_type TEXT, primary_completion_date TEXT, primary_completion_date_type TEXT,
    completion_date TEXT, completion_date_type TEXT, first_submitted TEXT, first_posted TEXT,
    last_update_posted TEXT, results_first_posted TEXT, has_results INTEGER,
    sex TEXT, minimum_age TEXT, maximum_age TEXT, healthy_volunteers INTEGER,
    lead_sponsor TEXT, lead_sponsor_class TEXT, is_fda_regulated_drug INTEGER, is_fda_regulated_device INTEGER,
    ipd_sharing TEXT, brief_summary TEXT, eligibility_criteria TEXT, url TEXT,
    record_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conditions (nct_id TEXT NOT NULL, condition TEXT);
CREATE TABLE IF NOT EXISTS keywords (nct_id TEXT NOT NULL, keyword TEXT);
CREATE TABLE IF NOT EXISTS phases (nct_id TEXT NOT NULL, phase TEXT);
CREATE TABLE IF NOT EXISTS mesh (nct_id TEXT NOT NULL, kind TEXT, mesh_id TEXT, term TEXT);
CREATE TABLE IF NOT EXISTS interventions (
    nct_id TEXT NOT NULL, type TEXT, name TEXT, description TEXT, other_names TEXT
);
CREATE TABLE IF NOT EXISTS sponsors (nct_id TEXT NOT NULL, role TEXT, name TEXT, class TEXT);
CREATE TABLE IF NOT EXISTS officials (nct_id TEXT NOT NULL, name TEXT, affiliation TEXT, role TEXT);
CREATE TABLE IF NOT EXISTS locations (
    nct_id TEXT NOT NULL, facility TEXT, status TEXT, city TEXT, state TEXT, zip TEXT, country TEXT,
    latitude REAL, longitude REAL
);
CREATE TABLE IF NOT EXISTS outcomes (nct_id TEXT NOT NULL, kind TEXT, measure TEXT, time_frame TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS study_references (nct_id TEXT NOT NULL, pmid TEXT, type TEXT, citation TEXT);
CREATE TABLE IF NOT EXISTS documents (
    nct_id TEXT NOT NULL, type TEXT, label TEXT, date TEXT, has_protocol INTEGER, has_sap INTEGER, has_icf INTEGER,
    filename TEXT, size INTEGER, url TEXT, path TEXT
);
CREATE TABLE IF NOT EXISTS runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, finished_at TEXT, search_info TEXT);
CREATE INDEX IF NOT EXISTS idx_conditions_nct_id ON conditions(nct_id);
CREATE INDEX IF NOT EXISTS idx_conditions_condition ON conditions(condition);
CREATE INDEX IF NOT EXISTS idx_keywords_nct_id ON keywords(nct_id);
CREATE INDEX IF NOT EXISTS idx_phases_nct_id ON phases(nct_id);
CREATE INDEX IF NOT EXISTS idx_mesh_nct_id ON mesh(nct_id);
CREATE INDEX IF NOT EXISTS idx_mesh_term ON mesh(term);
CREATE INDEX IF NOT EXISTS idx_interventions_nct_id ON interventions(nct_id);
CREATE INDEX IF NOT EXISTS idx_sponsors_nct_id ON sponsors(nct_id);
CREATE INDEX IF NOT EXISTS idx_sponsors_name ON sponsors(name);
CREATE INDEX IF NOT EXISTS idx_officials_nct_id ON officials(nct_id);
CREATE INDEX IF NOT EXISTS idx_locations_nct_id ON locations(nct_id);
CREATE INDEX IF NOT EXISTS idx_locations_country ON locations(country);
CREATE INDEX IF NOT EXISTS idx_outcomes_nct_id ON outcomes(nct_id);
CREATE INDEX IF NOT EXISTS idx_study_references_nct_id ON study_references(nct_id);
CREATE INDEX IF NOT EXISTS idx_study_references_pmid ON study_references(pmid);
CREATE INDEX IF NOT EXISTS idx_documents_nct_id ON documents(nct_id);
"""
SQLITE_CHILD_TABLES = (
    "conditions",
    "keywords",
    "phases",
    "mesh",
    "interventions",
    "sponsors",
    "officials",
    "locations",
    "outcomes",
    "study_references",
    "documents",
)
SQLITE_STUDY_COLUMNS = [
    "nct_id",
    "brief_title",
    "official_title",
    "acronym",
    "overall_status",
    "last_known_status",
    "why_stopped",
    "study_type",
    "allocation",
    "intervention_model",
    "primary_purpose",
    "masking",
    "observational_model",
    "time_perspective",
    "enrollment",
    "enrollment_type",
    "start_date",
    "start_date_type",
    "primary_completion_date",
    "primary_completion_date_type",
    "completion_date",
    "completion_date_type",
    "first_submitted",
    "first_posted",
    "last_update_posted",
    "results_first_posted",
    "has_results",
    "sex",
    "minimum_age",
    "maximum_age",
    "healthy_volunteers",
    "is_fda_regulated_drug",
    "is_fda_regulated_device",
    "ipd_sharing",
    "brief_summary",
    "eligibility_criteria",
    "url",
]


def _child_rows(study: Study) -> dict[str, list[tuple]]:
    """Rows for each child table, without the leading nct_id."""
    lead = study["lead_sponsor"]
    return {
        "conditions": [(c,) for c in study["conditions"]],
        "keywords": [(k,) for k in study["keywords"]],
        "phases": [(p,) for p in study["phases"]],
        "mesh": [("condition", m["id"], m["term"]) for m in study["condition_mesh"]]
        + [("intervention", m["id"], m["term"]) for m in study["intervention_mesh"]],
        "interventions": [
            (i["type"], i["name"], i["description"], _join(i["other_names"]) or None) for i in study["interventions"]
        ],
        "sponsors": ([("lead", lead["name"], lead["class"])] if lead else [])
        + [("collaborator", c["name"], c["class"]) for c in study["collaborators"]],
        "officials": [(o["name"], o["affiliation"], o["role"]) for o in study["officials"]],
        "locations": [
            (loc["facility"], loc["status"], loc["city"], loc["state"], loc["zip"], loc["country"])
            + (loc["latitude"], loc["longitude"])
            for loc in study["locations"]
        ],
        "outcomes": [
            (kind, o["measure"], o["time_frame"], o["description"])
            for kind in ("primary", "secondary", "other")
            for o in study[f"{kind}_outcomes"]
        ],
        "study_references": [(r["pmid"], r["type"], r["citation"]) for r in study["references"]],
        "documents": [
            (d["type"], d["label"], d["date"], d["has_protocol"], d["has_sap"], d["has_icf"])
            + (d["filename"], d["size"], d["url"], d["path"])
            for d in study["documents"]
        ],
    }


class SqliteSink(Sink):
    """
    A normalized SQLite database. Records are always upserted by NCT ID, so repeated runs
    accumulate into the same database. Each batch is committed as it arrives.
    """

    streaming = True

    def __init__(self, path: str, merge: bool = False, resume: bool = False):
        super().__init__(path, merge, resume)
        self.db = sqlite3.connect(path)
        self.db.executescript(SQLITE_SCHEMA)
        if resume:
            self.existing_ids = {row[0] for row in self.db.execute("SELECT nct_id FROM studies")}

    def write(self, batch: list[Study]) -> None:
        now = utc_now()
        ids = [(s["nct_id"],) for s in batch]
        columns = [*SQLITE_STUDY_COLUMNS, "phases", "lead_sponsor", "lead_sponsor_class", "record_json", "updated_at"]
        with self.db:
            for table in SQLITE_CHILD_TABLES:
                self.db.executemany(f"DELETE FROM {table} WHERE nct_id = ?", ids)
            self.db.executemany(
                f"INSERT OR REPLACE INTO studies ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                [self._study_row(s, now) for s in batch],
            )
            rows: dict[str, list[tuple]] = {table: [] for table in SQLITE_CHILD_TABLES}
            for study in batch:
                for table, table_rows in _child_rows(study).items():
                    rows[table].extend((study["nct_id"], *row) for row in table_rows)
            for table, table_rows in rows.items():
                if table_rows:
                    placeholders = ", ".join("?" * len(table_rows[0]))
                    self.db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", table_rows)

    @staticmethod
    def _study_row(study: Study, now: str) -> tuple:
        lead = study.get("lead_sponsor") or {}
        return (
            *(study.get(c) for c in SQLITE_STUDY_COLUMNS),
            _join(study["phases"]) or None,
            lead.get("name"),
            lead.get("class"),
            json.dumps(study, ensure_ascii=False),
            now,
        )

    def close(self, search_info: dict[str, Any]) -> int:
        with self.db:
            total = self.db.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
            self.db.execute(
                "INSERT INTO runs (finished_at, search_info) VALUES (?, ?)",
                (utc_now(), json.dumps({**search_info, "total_studies": total}, ensure_ascii=False)),
            )
        self.db.close()
        return total


SINKS: dict[str, type[Sink]] = {"json": JsonSink, "jsonl": JsonlSink, "csv": CsvSink, "sqlite": SqliteSink}


def open_sink(path: str, fmt: str, merge: bool = False, resume: bool = False) -> Sink:
    if path == STDOUT and (fmt == "sqlite" or merge or resume):
        raise ValueError("Writing to stdout ('-') can't be combined with sqlite, --merge or --resume.")
    if merge and resume:
        raise ValueError("--merge and --resume can't be used together.")
    return SINKS[fmt](path, merge=merge, resume=resume)


def save_results(studies: list[Study], search_info: dict[str, Any], filename=None, fmt=None, merge=False) -> int:
    """Write a complete list of records in one go. Returns the number of records in the output."""
    path, fmt = resolve_output(filename, fmt)
    sink = open_sink(path, fmt, merge=merge)
    sink.write(studies)
    return sink.close(search_info)


def write_summary(path: str, summary: dict[str, Any]) -> None:
    """Write the run summary (search_info) as JSON; ``-`` means stdout."""
    if path == STDOUT:
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _atomic_write(path, lambda f: json.dump(summary, f, indent=2, ensure_ascii=False))


def _clip(values: list[str], n: int = 5) -> str:
    return ", ".join(values[:n]) + (f" (+{len(values) - n} more)" if len(values) > n else "")


def print_studies(studies: list[Study], start: int = 1, out: IO[str] | None = None) -> None:
    """Print a human-readable listing of records (normalized or raw)."""
    out = out or sys.stdout
    for i, study in enumerate(studies, start):
        if "nct_id" not in study:
            try:
                study = parse_study(study)
            except (KeyError, TypeError, AttributeError):
                print(f"\n{i}. {record_id(study)}\n" + "-" * 40, file=out)
                continue
        title = study["brief_title"] or study["nct_id"]
        if study.get("acronym") and study["acronym"] not in title:
            title += f" ({study['acronym']})"
        design = [study["overall_status"], _join(study["phases"]) or None, study["study_type"]]
        lines = [
            f"\n{i}. {title}",
            f"   {study['nct_id']} | " + " | ".join(d for d in design if d),
        ]
        if study["conditions"]:
            lines.append(f"   Conditions: {_clip(study['conditions'])}")
        if study["interventions"]:
            names = [f"{x['type']}: {x['name']}" if x["type"] else x["name"] for x in study["interventions"]]
            lines.append(f"   Interventions: {_clip(names, 4)}")
        if lead := study.get("lead_sponsor"):
            lines.append(f"   Sponsor: {lead['name']}" + (f" ({lead['class']})" if lead.get("class") else ""))
        dates = [
            f"{label} {study[key]}" + (f" ({study[key + '_type'].lower()})" if study.get(key + "_type") else "")
            for label, key in (("start", "start_date"), ("primary completion", "primary_completion_date"))
            if study.get(key)
        ]
        if dates:
            lines.append(f"   Dates: {', '.join(dates)}")
        if study.get("enrollment") is not None:
            kind = f" ({study['enrollment_type'].lower()})" if study.get("enrollment_type") else ""
            lines.append(f"   Enrollment: {study['enrollment']}{kind}")
        if study["locations"]:
            lines.append(f"   Sites: {len(study['locations'])} in {_clip(study['countries'], 4)}")
        if study["has_results"]:
            lines.append(f"   Results posted: {study.get('results_first_posted') or 'yes'}")
        if study.get("why_stopped"):
            lines.append(f"   Why stopped: {study['why_stopped']}")
        if study["documents"]:
            lines.append(f"   Documents: {', '.join(d['label'] or d['filename'] for d in study['documents'])}")
        lines.append(f"   URL: {study['url']}")
        lines.append("-" * 40)
        print("\n".join(lines), file=out)
