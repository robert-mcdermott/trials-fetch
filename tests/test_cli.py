import io
import json
from datetime import date

import pytest
from conftest import fake_api

from trials_fetch import cli
from trials_fetch.cli import build_options, load_config, parse_arguments, parse_list, read_ids, resolve_settings


def test_parse_arguments():
    args = parse_arguments(
        ["glioblastoma", "--condition", "brain tumor", "--phase", "2", "3", "--status", "RECRUITING"]
        + ["--status", "NOT_YET_RECRUITING", "--country", "Sweden", "--country", "United States", "--has-results"]
        + ["--max-results", "all", "--sex", "female", "--age", "12", "-o", "out.csv"]
    )
    assert args.term == "glioblastoma"
    assert args.conditions == ["brain tumor"] and args.phases == ["2", "3"]
    # Repeating a list option adds to it, so the 0.1 style (--status A --status B) still works.
    assert args.statuses == ["RECRUITING", "NOT_YET_RECRUITING"]
    assert args.countries == ["Sweden", "United States"]
    assert (args.has_results, args.max_results, args.sex, args.age, args.output_file) == (
        True,
        "all",
        "female",
        12,
        "out.csv",
    )
    assert (args.merge, args.raw, args.healthy_volunteers) == (None, None, None)


@pytest.mark.parametrize(
    "bad",
    [
        ["--days", "0"],
        ["--date-from", "01/02/2024"],
        ["--days", "5", "--date-from", "2024-01-01"],
        ["--format", "xlsx"],
        ["--date-field", "posted"],
        ["--max-results", "lots"],
        ["--has-results", "--no-results"],
        ["--sex", "other"],
    ],
)
def test_parse_arguments_rejects_invalid_values(bad):
    with pytest.raises(SystemExit):
        parse_arguments(["x", *bad])


def test_parse_list_supports_quotes_and_lines():
    assert parse_list('"Korea, Republic of", Japan') == ["Korea, Republic of", "Japan"]
    assert parse_list("\n  A\n  B, C\n") == ["A", "B", "C"]


def test_load_config(tmp_path, caplog):
    config_file = tmp_path / "c.ini"
    config_file.write_text(
        "[search]\n"
        "term = glioblastoma\n"
        'countries = "Korea, Republic of", Japan\n'
        "phases =\n  2\n  3\n"
        "days = 60\nmax_results = all\nage = 30\n"
        "date_from = 2024-01-01\ndate_to = 2024-12-31\nformat = CSV\ndate_field = LAST_UPDATE\n"
        "download_dir = docs\ndocument_types = protocol, SAP\nhas_results = yes\nraw = no\n"
        "advanced = AREA[EnrollmentCount]RANGE[100, MAX]\nsex = Female\ntypo_key = 1\n"
    )
    config = load_config(str(config_file))
    assert config == {
        "term": "glioblastoma",
        "countries": ["Korea, Republic of", "Japan"],
        "phases": ["2", "3"],
        "document_types": ["protocol", "sap"],
        "days": 60,
        "age": 30,
        "has_results": True,
        "raw": False,
        "date_from": date(2024, 1, 1),
        "date_to": date(2024, 12, 31),
        "sex": "female",
        "advanced": "AREA[EnrollmentCount]RANGE[100, MAX]",
        "date_field": "last_update",
        "max_results": "all",
        "format": "csv",
        "download_dir": "docs",
    }
    assert "Ignoring unknown setting 'typo_key'" in caplog.text

    config_file.write_text("[search]\nformat = xlsx\n")
    with pytest.raises(ValueError, match="format"):
        load_config(str(config_file))


def test_settings_precedence():
    config = {"conditions": ["A"], "days": 60, "max_results": 5, "has_results": True}
    settings = resolve_settings(parse_arguments(["--days", "30", "--no-results"]), config)
    assert (settings["days"], settings["max_results"], settings["conditions"]) == (30, 5, ["A"])
    assert settings["has_results"] is False
    assert settings["output_file"] is None and settings["document_types"] == ["protocol", "sap", "icf"]


def test_cli_dates_replace_config_dates():
    config = {"date_from": date(2024, 1, 1), "date_to": date(2024, 12, 31), "days": 30}
    settings = resolve_settings(parse_arguments(["--days", "7"]), config)
    assert (settings["date_from"], settings["date_to"], settings["days"]) == (None, date(2024, 12, 31), 7)
    settings = resolve_settings(parse_arguments(["--date-from", "2025-01-01"]), config)
    assert (settings["date_from"], settings["days"]) == (date(2025, 1, 1), None)


def test_build_options(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("# my list\nNCT00000620, nct03405792\n")
    args = parse_arguments(
        ["--nct-ids", "NCT06419946", str(ids), "--output", "x.csv", "--merge", "--max-results", "all"]
    )
    options = build_options(resolve_settings(args, {}), quiet=True)
    assert options.nct_ids == ["NCT06419946", "NCT00000620", "nct03405792"]
    assert (options.mode, options.output_file, options.merge, options.display) == ("ids", "x.csv", True, False)
    assert options.max_results is None


def test_build_options_validates_filter_values():
    with pytest.raises(ValueError, match="Unknown phase"):
        build_options(resolve_settings(parse_arguments(["x", "--phase", "7"]), {}))


def test_read_ids_from_stdin_and_rejects_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("NCT00000620\nNCT03405792\n"))
    assert read_ids(["-"]) == ["NCT00000620", "NCT03405792"]
    bad = tmp_path / "bad.txt"
    bad.write_text("NCT00000620 NCT1")
    with pytest.raises(ValueError, match="NCT1"):
        read_ids([str(bad)])


def test_create_sample_config(tmp_path):
    target = tmp_path / "sample.ini"
    assert cli.create_sample_config(str(target))
    assert load_config(str(target))["conditions"] == ["Glioblastoma"]
    assert not cli.create_sample_config(str(target))


def test_repo_sample_config_matches_template():
    with open("sample_config.ini", encoding="utf-8") as f:
        assert f.read() == cli.SAMPLE_CONFIG
    config = load_config("sample_config.ini")
    assert config["statuses"] == ["RECRUITING", "NOT_YET_RECRUITING"] and config["max_results"] == 1000
    build_options(resolve_settings(parse_arguments([]), config))


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--condition", "X", "--nct-ids", "NCT00000620"],
        ["--condition", "X", "--raw", "--output", "x.csv"],
        ["--condition", "X", "--output", "-", "--format", "sqlite"],
        ["--condition", "X", "--merge", "--resume"],
        ["--condition", "X", "--fields", "BriefTitle"],
        ["--nct-ids", "NCT00000620", "--since-last-run", "s.json"],
        ["--nct-ids", "NCT00000620", "--count"],
        ["--condition", "X", "--date-from", "2024-01-01", "--since-last-run", "s.json"],
        ["--condition", "X", "--near", "Seattle"],
        ["--nct-ids", "missing-file.txt"],
        ["--config", "missing.ini"],
    ],
)
def test_main_rejects_invalid_usage(argv):
    assert cli.main([*argv, "-q"]) == 1


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("trials-fetch ")


def test_count(monkeypatch, capsys):
    monkeypatch.setattr("trials_fetch.client.make_session", lambda: fake_api(total=2276))
    assert cli.main(["glioblastoma", "--count", "-q"]) == 0
    assert capsys.readouterr().out == "2276\n"


def test_summary_written(tmp_path, monkeypatch):
    monkeypatch.setattr("trials_fetch.client.make_session", lambda: fake_api())
    out, summary = tmp_path / "out.jsonl", tmp_path / "summary.json"
    code = cli.main(["glioblastoma", "--status", "recruiting", "-q", "-o", str(out), "--summary", str(summary)])
    assert code == 0
    info = json.loads(summary.read_text())
    assert (info["total_studies"], info["complete"], info["source"]) == (7, True, "search")
    assert info["query"] == {"query.term": "glioblastoma", "filter.overallStatus": "RECRUITING"}
