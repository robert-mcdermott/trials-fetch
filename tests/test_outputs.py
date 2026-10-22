import csv
import io
import json
import sqlite3

import pytest

from trials_fetch import open_sink, print_studies, resolve_output, save_results
from trials_fetch.outputs import CSV_FIELDS, csv_row

INFO = {"source": "test"}


def test_resolve_output():
    assert resolve_output(None, None) == ("trials.json", "json")
    assert resolve_output("x.NDJSON", None) == ("x.NDJSON", "jsonl")
    assert resolve_output("x.db", None) == ("x.db", "sqlite")
    assert resolve_output(None, "csv") == ("trials.csv", "csv")
    assert resolve_output("-", None) == ("-", "json")
    with pytest.raises(ValueError):
        resolve_output("x.json", "xlsx")


def test_csv_row_flattens_lists(records):
    row = csv_row(records["NCT06419946"])
    assert set(row) == set(CSV_FIELDS)
    assert row["countries"] == "Austria; Denmark; Norway; Sweden"
    assert row["interventions"] == "DRUG: Temozolomide; DRUG: Lomustine"
    assert (row["lead_sponsor_class"], row["location_count"], row["acronym"]) == ("OTHER_GOV", 20, "")
    assert row["pmids"].count(";") == 6


def test_json_merge_replaces_by_nct_id(tmp_path, records):
    path = str(tmp_path / "out.json")
    first, second = list(records.values())[:2]
    assert save_results([first], INFO, path) == 1
    changed = {**first, "brief_title": "Updated"}
    assert save_results([changed, second], INFO, path, merge=True) == 2
    doc = json.loads((tmp_path / "out.json").read_text())
    assert doc["schema_version"] == 1 and doc["search_info"]["total_studies"] == 2
    assert doc["studies"][0]["brief_title"] == "Updated"


def test_json_merge_refuses_old_format(tmp_path, raw_studies):
    path = tmp_path / "old.json"
    path.write_text(json.dumps(raw_studies))  # trials-fetch 0.1 wrote a bare list
    with pytest.raises(ValueError, match="isn't trials-fetch JSON output"):
        open_sink(str(path), "json", merge=True)


@pytest.mark.parametrize("fmt", ["jsonl", "csv"])
def test_line_formats_merge_and_resume(tmp_path, records, fmt):
    path = str(tmp_path / f"out.{fmt}")
    studies = list(records.values())
    save_results(studies[:3], INFO, path)

    sink = open_sink(path, fmt, resume=True)
    assert sink.existing_ids == {s["nct_id"] for s in studies[:3]}
    sink.write(studies[3:5])
    assert sink.close(INFO) == 5

    changed = {**studies[0], "brief_title": "Updated"}
    assert save_results([changed], INFO, path, merge=True) == 5
    if fmt == "jsonl":
        rows = [json.loads(line) for line in open(path)]
    else:
        rows = list(csv.DictReader(open(newline="", file=path)))
    assert [r["nct_id"] for r in rows][0] == studies[0]["nct_id"] and rows[0]["brief_title"] == "Updated"
    assert len({r["nct_id"] for r in rows}) == 5


def test_resume_drops_partial_last_line(tmp_path, records):
    path = tmp_path / "out.jsonl"
    first, second = list(records.values())[:2]
    path.write_text(json.dumps(first) + "\n" + json.dumps(second)[:50])
    sink = open_sink(str(path), "jsonl", resume=True)
    assert sink.existing_ids == {first["nct_id"]}
    sink.write([second])
    sink.close(INFO)
    assert [json.loads(line)["nct_id"] for line in path.read_text().splitlines()] == [
        first["nct_id"],
        second["nct_id"],
    ]


def test_sqlite_tables(tmp_path, records):
    path = str(tmp_path / "out.sqlite")
    assert save_results(list(records.values()), INFO, path) == 7
    # Upserting again replaces child rows instead of duplicating them.
    assert save_results([records["NCT06419946"]], INFO, path) == 7
    db = sqlite3.connect(path)
    count = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    assert count("SELECT COUNT(*) FROM locations WHERE nct_id = 'NCT06419946'") == 20
    assert count("SELECT COUNT(*) FROM study_references WHERE nct_id = 'NCT00000620'") == 128
    assert count("SELECT COUNT(*) FROM sponsors WHERE role = 'lead'") == 7
    assert count("SELECT COUNT(*) FROM documents") == 3
    assert count("SELECT COUNT(*) FROM runs") == 2
    assert db.execute(
        "SELECT phases, lead_sponsor_class, has_results FROM studies WHERE nct_id = 'NCT02743078'"
    ).fetchone() == (
        "PHASE2",
        "OTHER",
        1,
    )
    assert count("SELECT COUNT(DISTINCT nct_id) FROM mesh WHERE kind = 'condition' AND term = 'Glioblastoma'") == 6
    record = json.loads(db.execute("SELECT record_json FROM studies WHERE nct_id = 'NCT00000620'").fetchone()[0])
    assert record["acronym"] == "ACCORD"
    resumed = open_sink(path, "sqlite", resume=True)
    assert len(resumed.existing_ids) == 7
    resumed.close(INFO)


def test_stdout_restrictions():
    with pytest.raises(ValueError):
        open_sink("-", "sqlite")
    with pytest.raises(ValueError):
        open_sink("-", "jsonl", merge=True)


def test_print_studies(records, raw_studies):
    out = io.StringIO()
    print_studies([records["NCT02743078"]], start=3, out=out)
    text = out.getvalue()
    assert "3. " in text and "NCT02743078 | TERMINATED | PHASE2 | INTERVENTIONAL" in text
    assert "Why stopped: Treatment now available commercially" in text
    assert "Results posted:" in text and "Documents:" in text

    out = io.StringIO()
    print_studies(raw_studies[:1], out=out)  # raw records are listed too
    assert "ACCORD" in out.getvalue()
