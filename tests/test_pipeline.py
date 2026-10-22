import csv
import json
from datetime import date, timedelta

import pytest
from conftest import FakeResponse, FakeSession, fake_api, nct, synthetic_study, version_handler

from trials_fetch import Options, QuerySpec, TrialsClient, TrialsError, count_matches, run
from trials_fetch.pipeline import EXIT_OK, EXIT_PARTIAL


def options(tmp_path, **kwargs):
    defaults = {"spec": QuerySpec(conditions=["glioblastoma"]), "display": False}
    return Options(**{**defaults, "output_file": str(tmp_path / "out.json"), **kwargs})


def test_run_writes_complete_output(tmp_path):
    result = run(options(tmp_path), session=fake_api())
    assert result.exit_code == EXIT_OK
    doc = json.loads((tmp_path / "out.json").read_text())
    info = doc["search_info"]
    assert info["tool"]["name"] == "trials-fetch" and info["generated_on"].endswith("Z")
    assert (info["api_version"], info["data_timestamp"]) == ("2.0.5", "2026-10-21T09:00:05")
    assert (info["complete"], info["total_studies"], info["retrieved"], info["total_found"]) == (True, 7, 7, 7)
    assert doc["studies"][0]["nct_id"] == "NCT00000620"


def test_version_failure_does_not_stop_the_run(tmp_path):
    session = fake_api()
    session.routes["/version"] = lambda url, params: FakeResponse(status=503)
    result = run(options(tmp_path), session=session)
    assert result.search_info["api_version"] is None and result.exit_code == EXIT_OK


def test_run_reports_partial_results(tmp_path, monkeypatch):
    monkeypatch.setattr(TrialsClient, "page_size", 3)
    result = run(options(tmp_path), session=fake_api(fail_pages=(2,)))
    assert result.exit_code == EXIT_PARTIAL
    assert result.search_info["complete"] is False and result.search_info["total_studies"] == 3


def test_run_fails_when_nothing_could_be_fetched(tmp_path):
    session = FakeSession({"/version": version_handler, "/studies": lambda u, p: FakeResponse(status=503)})
    with pytest.raises(TrialsError, match="Error searching"):
        run(options(tmp_path), session=session)
    assert not (tmp_path / "out.json").exists()


def test_run_writes_empty_output_for_no_matches(tmp_path, capsys):
    run(options(tmp_path, display=True), session=fake_api(studies=[]))
    doc = json.loads((tmp_path / "out.json").read_text())
    assert doc["studies"] == [] and doc["search_info"]["total_studies"] == 0
    assert "No studies found." in capsys.readouterr().out


def test_days_become_a_start_date_range(tmp_path):
    session = fake_api()
    run(options(tmp_path, days=30), session=session)
    today = date.today()
    expected = f"AREA[StartDate]RANGE[{today - timedelta(days=30)}, {today}]"
    assert session.calls_to("/studies")[0]["filter.advanced"] == expected


def test_since_last_run_uses_and_updates_state(tmp_path):
    state = tmp_path / "state.json"
    session = fake_api()
    opts = options(tmp_path, since_last_run=str(state), output_file=str(tmp_path / "out.jsonl"))

    # The first run has no date range: it collects everything that currently matches.
    run(opts, session=session)
    assert "filter.advanced" not in session.calls_to("/studies")[0]
    saved = json.loads(state.read_text())
    assert (saved["last_end_date"], saved["date_field"]) == (date.today().isoformat(), "last_update")
    assert saved["query"] == {"query.cond": "glioblastoma"}

    state.write_text(json.dumps({**saved, "last_end_date": "2024-05-01"}))
    run(
        options(tmp_path, since_last_run=str(state), output_file=str(tmp_path / "out.jsonl"), merge=True),
        session=session,
    )
    assert session.calls_to("/studies")[1]["filter.advanced"] == "AREA[LastUpdatePostDate]RANGE[2024-05-01, MAX]"
    assert json.loads(state.read_text())["last_end_date"] == date.today().isoformat()


def test_since_last_run_state_kept_on_partial_or_truncated_run(tmp_path, monkeypatch, caplog):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_end_date": "2024-05-01", "query": {}, "date_field": "last_update"}))
    monkeypatch.setattr(TrialsClient, "page_size", 3)
    result = run(options(tmp_path, since_last_run=str(state)), session=fake_api(fail_pages=(2,)))
    assert result.exit_code == EXIT_PARTIAL
    assert json.loads(state.read_text())["last_end_date"] == "2024-05-01"

    result = run(options(tmp_path, since_last_run=str(state), max_results=2), session=fake_api())
    assert result.search_info["truncated"] and result.exit_code == EXIT_OK
    assert json.loads(state.read_text())["last_end_date"] == "2024-05-01"
    assert "--max-results cut the results" in caplog.text


def test_resume_skips_existing_records(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    run(options(tmp_path, output_file=str(out)), session=fake_api())
    result = run(options(tmp_path, output_file=str(out), resume=True, display=True), session=fake_api())
    assert (result.search_info["skipped_existing"], result.search_info["total_studies"]) == (7, 7)
    assert len(out.read_text().splitlines()) == 7
    assert "No new studies." in capsys.readouterr().out


def test_ids_mode(tmp_path):
    opts = options(tmp_path, spec=QuerySpec(), nct_ids=["NCT03405792", "NCT99999999"])
    info = run(opts, session=fake_api()).search_info
    assert (info["source"], info["retrieved"], info["missing_ids"]) == ("ids", 1, ["NCT99999999"])


def test_raw_output_and_reconversion(tmp_path, raw_studies):
    raw_path = tmp_path / "raw.jsonl"
    run(options(tmp_path, raw=True, output_file=str(raw_path)), session=fake_api())
    assert [json.loads(line) for line in raw_path.read_text().splitlines()] == raw_studies

    # Raw output can be converted later without the network, e.g. into SQLite.
    db = tmp_path / "trials.sqlite"
    result = run(options(tmp_path, spec=QuerySpec(), from_raw=str(raw_path), output_file=str(db)))
    assert (result.search_info["source"], result.search_info["total_studies"]) == ("raw", 7)
    assert result.search_info["api_version"] is None


def test_old_output_converts(tmp_path, raw_studies):
    old = tmp_path / "trials.json"
    old.write_text(json.dumps(raw_studies, indent=4))  # what trials-fetch 0.1 wrote
    out = tmp_path / "new.csv"
    run(options(tmp_path, spec=QuerySpec(), from_raw=str(old), output_file=str(out)))
    with open(out, newline="", encoding="utf-8") as f:
        assert [row["nct_id"] for row in csv.DictReader(f)] == [nct(s) for s in raw_studies]


def test_documents_are_downloaded(tmp_path):
    session = fake_api()
    session.routes["cdn.clinicaltrials.gov"] = lambda url, params: FakeResponse(content=b"%PDF-1.7")
    opts = options(tmp_path, download_dir=str(tmp_path / "docs"), document_types=["protocol"])
    info = run(opts, session=session).search_info
    assert info["downloads"] == {"downloaded": 2, "failed": 0}
    doc = json.loads((tmp_path / "out.json").read_text())
    paths = [d["path"] for s in doc["studies"] for d in s["documents"] if d["path"]]
    assert sorted(paths) == sorted(
        [
            str(tmp_path / "docs" / "NCT03405792" / "Prot_SAP_000.pdf"),
            str(tmp_path / "docs" / "NCT02743078" / "Prot_SAP_000.pdf"),
        ]
    )


def test_listing_is_printed_unless_output_is_stdout(tmp_path, capsys):
    run(options(tmp_path, display=True), session=fake_api())
    out = capsys.readouterr().out
    # The acronym is added only when the title doesn't already include it.
    assert "1. Action to Control Cardiovascular Risk in Diabetes (ACCORD)\n" in out

    run(
        options(tmp_path, display=True, output_file="-", format="jsonl"), session=fake_api(studies=[synthetic_study(1)])
    )
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["nct_id"] == "NCT90000001"


def test_count_matches():
    session = fake_api(total=42)
    assert count_matches(Options(spec=QuerySpec(conditions=["x"]), days=7), session=session) == 42
    assert "AREA[StartDate]RANGE" in session.calls_to("/studies")[0]["filter.advanced"]
    with pytest.raises(ValueError):
        count_matches(Options(nct_ids=["NCT00000620"]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"spec": QuerySpec()},
        {"nct_ids": ["NCT00000620"]},
        {"from_raw": "raw.json"},
        {"nct_ids": ["NCT00000620"], "spec": QuerySpec(), "sort": "StartDate"},
        {"since_last_run": "s.json", "spec": QuerySpec(conditions=["x"], date_from=date(2024, 1, 1))},
        {"days": 5, "spec": QuerySpec(conditions=["x"], date_from=date(2024, 1, 1))},
        {"max_results": 0},
        {"fields": ["BriefTitle"]},
        {"raw": True, "output_file": "x.csv"},
        {"raw": True, "download_dir": "docs"},
        {"document_types": ["consent"]},
        {"output_file": "-", "format": "sqlite"},
        {"merge": True, "resume": True},
    ],
)
def test_invalid_options(kwargs):
    defaults = {"spec": QuerySpec(conditions=["x"])}
    with pytest.raises(ValueError):
        Options(**{**defaults, **kwargs}).validate()


def test_fixture_order_is_stable(raw_studies):
    # Tests above rely on the fixture's order.
    assert [nct(s) for s in raw_studies][:2] == ["NCT00000620", "NCT03405792"]
