"""Command-line interface."""

from __future__ import annotations

import argparse
import configparser
import csv
import logging
import os
import re
import sqlite3
import sys
from datetime import date
from typing import Any

from ._meta import TrialsError, __version__
from .documents import DOCUMENT_TYPES
from .outputs import OUTPUT_FORMATS
from .pipeline import EXIT_ERROR, Options, count_matches, run
from .query import (
    AGE_GROUPS,
    DATE_FIELDS,
    FUNDER_TYPES,
    NCT_ID,
    PHASES,
    SEARCH_FIELDS,
    SEXES,
    SITE_STATUSES,
    STATUSES,
    STUDY_TYPES,
    QuerySpec,
)

log = logging.getLogger("trials_fetch")

FILTER_KEYS = ("statuses", "phases", "study_types", "countries", "site_statuses", "funder_types", "age_groups")
DEFAULTS: dict[str, Any] = {
    "term": None,
    **{key: [] for key in SEARCH_FIELDS},
    **{key: [] for key in FILTER_KEYS},
    "sex": None,
    "age": None,
    "healthy_volunteers": None,
    "has_results": None,
    "near": None,
    "advanced": None,
    "date_field": None,
    "days": None,
    "date_from": None,
    "date_to": None,
    "max_results": 1000,
    "sort": None,
    "nct_ids": [],
    "from_raw": None,
    "raw": False,
    "fields": [],
    "output_file": None,
    "format": None,
    "merge": False,
    "resume": False,
    "summary": None,
    "download_dir": None,
    "document_types": list(DOCUMENT_TYPES),
    "since_last_run": None,
}

LIST_KEYS = (*SEARCH_FIELDS, *FILTER_KEYS, "nct_ids", "fields", "document_types")
INT_KEYS = ("days", "age")
DATE_KEYS = ("date_from", "date_to")
BOOL_KEYS = ("healthy_volunteers", "has_results", "raw", "merge", "resume")
STR_KEYS = tuple(k for k in DEFAULTS if k not in (*LIST_KEYS, *INT_KEYS, *DATE_KEYS, *BOOL_KEYS))
CHOICES = {
    "format": OUTPUT_FORMATS,
    "date_field": tuple(DATE_FIELDS),
    "sex": SEXES,
    "document_types": tuple(DOCUMENT_TYPES),
}


def parse_list(value: str) -> list[str]:
    """
    Parse a config list: comma-separated and/or one item per line. Quote items that contain
    commas, e.g. ``"Korea, Republic of", Japan``.
    """
    items: list[str] = []
    for line in value.splitlines():
        for row in csv.reader([line], skipinitialspace=True):
            items.extend(item.strip() for item in row if item.strip())
    return items


def _choice(value: str, key: str) -> str:
    value = value.strip().lower()
    if value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}, got {value!r}")
    return value


def _max_results(value: str) -> int | str:
    """A positive number of studies, or "all"."""
    if value.strip().lower() == "all":
        return "all"
    number = int(value)
    if number <= 0:
        raise ValueError(f"must be a positive integer or 'all', got {value}")
    return number


def load_config(config_file: str) -> dict[str, Any]:
    """
    Load settings from the [search] section of an INI file. Only keys present in the file are
    returned, so callers can layer CLI > config > defaults.
    """
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_file)
    if "search" not in config:
        return {}
    section = config["search"]

    settings: dict[str, Any] = {}
    for key in section:
        if key not in DEFAULTS:
            log.warning("Ignoring unknown setting %r in %s", key, config_file)
    for key in LIST_KEYS:
        if key in section:
            settings[key] = parse_list(section[key])
    for key in INT_KEYS:
        if section.get(key, "").strip():
            settings[key] = section.getint(key)
    for key in BOOL_KEYS:
        if section.get(key, "").strip():
            settings[key] = section.getboolean(key)
    for key in DATE_KEYS:
        if section.get(key, "").strip():
            settings[key] = date.fromisoformat(section[key].strip())
    for key in STR_KEYS:
        if section.get(key, "").strip():
            settings[key] = section[key].strip()
    for key in CHOICES:
        if isinstance(settings.get(key), str):
            settings[key] = _choice(settings[key], key)
    if "document_types" in settings:
        settings["document_types"] = [_choice(t, "document_types") for t in settings["document_types"]]
    if "max_results" in settings:
        settings["max_results"] = _max_results(settings["max_results"])
    return settings


def read_ids(items: list[str]) -> list[str]:
    """
    Collect NCT IDs from a mix of IDs, files and '-' (stdin). Files hold IDs separated by
    whitespace or commas; lines starting with # are ignored.
    """
    tokens: list[str] = []
    for item in items:
        if NCT_ID.match(item.strip()):
            tokens.append(item.strip())
            continue
        if item == "-":
            content = sys.stdin.read()
        else:
            with open(item, encoding="utf-8") as f:
                content = f.read()
        tokens.extend(
            t
            for line in content.splitlines()
            if not line.lstrip().startswith("#")
            for t in re.split(r"[\s,]+", line)
            if t
        )
    if bad := [t for t in tokens if not NCT_ID.match(t)]:
        raise ValueError(f"Invalid NCT ID(s): {', '.join(bad[:5])} (expected NCT followed by 8 digits)")
    return tokens


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return number


def _max_results_arg(value: str) -> int | str:
    try:
        return _max_results(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a date as YYYY-MM-DD, got {value!r}") from None


def _flag(group, *names: str, dest: str, const: bool = True, help: str) -> None:
    """A boolean flag that stays None when not given, so config file values can apply."""
    group.add_argument(*names, dest=dest, action="store_const", const=const, default=None, help=help)


def _list(group, *names: str, dest: str, metavar: str, help: str) -> None:
    """A list option: ``--opt A B`` and ``--opt A --opt B`` both collect [A, B]."""
    group.add_argument(*names, dest=dest, metavar=metavar, nargs="+", action="extend", help=help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trials-fetch",
        description="Search ClinicalTrials.gov and export structured trial records as JSON, JSONL, CSV or SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s glioblastoma --status RECRUITING --country Sweden
  %(prog)s --condition "breast cancer" --phase 2 3 --study-type interventional --output breast.csv
  %(prog)s --intervention pembrolizumab --funder-type industry --has-results --max-results all -o pembro.sqlite
  %(prog)s --condition "type 1 diabetes" --site-status RECRUITING --near 47.61,-122.33,50mi --age 12
  %(prog)s --condition melanoma --since-last-run melanoma.state.json --output melanoma.sqlite
  %(prog)s --nct-ids NCT04280705 NCT00000620 --output - | jq '.studies[].brief_title'
  %(prog)s --condition glioblastoma --count
  %(prog)s --config config.ini

Search values use Essie syntax: 'heart attack OR "myocardial infarction"', AREA[Phase]PHASE3, ...
Exit codes: 0 success, 1 error, 2 partial results (some batches failed; see search_info.failed_batches).
        """,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("term", nargs="?", help="Search terms matched against all fields (Essie syntax)")

    search = parser.add_argument_group("search fields (OR within a field, AND across fields)")
    _list(search, "--condition", dest="conditions", metavar="TEXT", help="Conditions or diseases")
    _list(search, "--intervention", dest="interventions", metavar="TEXT", help="Interventions or treatments")
    _list(search, "--title", dest="titles", metavar="TEXT", help="Words in the title or acronym")
    _list(search, "--outcome", dest="outcomes", metavar="TEXT", help="Outcome measures")
    _list(search, "--sponsor", dest="sponsors", metavar="NAME", help="Sponsors or collaborators")
    _list(search, "--location", dest="locations", metavar="TEXT", help="Facility, city, state or country text")

    filters = parser.add_argument_group("filters (OR within a filter, AND across filters; case-insensitive)")
    _list(filters, "--status", dest="statuses", metavar="STATUS", help=f"Overall status: {', '.join(STATUSES)}")
    _list(filters, "--phase", dest="phases", metavar="PHASE", help=f"{', '.join(PHASES)}; or 0 (early 1), 1-4")
    _list(filters, "--study-type", dest="study_types", metavar="TYPE", help=", ".join(STUDY_TYPES))
    _list(filters, "--country", dest="countries", metavar="COUNTRY", help='Countries with a site, e.g. "United States"')
    _list(
        filters,
        "--site-status",
        dest="site_statuses",
        metavar="STATUS",
        help="Require a site with this status, in one of the --country countries if given "
        f"({', '.join(SITE_STATUSES)})",
    )
    filters.add_argument(
        "--near", metavar="LAT,LON[,DIST]", help="A site within DIST of a point (default 50mi; mi or km)"
    )
    _list(
        filters, "--funder-type", dest="funder_types", metavar="TYPE", help=f"Lead sponsor: {', '.join(FUNDER_TYPES)}"
    )
    _list(filters, "--age-group", dest="age_groups", metavar="GROUP", help=", ".join(AGE_GROUPS))
    filters.add_argument("--age", type=int, metavar="YEARS", help="Studies whose age limits include this age")
    filters.add_argument("--sex", choices=SEXES, help="Studies open to this sex (single-sex and all-sex studies)")
    _flag(filters, "--healthy-volunteers", dest="healthy_volunteers", help="Studies accepting healthy volunteers")
    results = filters.add_mutually_exclusive_group()
    _flag(results, "--has-results", dest="has_results", help="Studies with results posted")
    _flag(results, "--no-results", dest="has_results", const=False, help="Studies without results posted")
    filters.add_argument("--advanced", metavar="EXPR", help="An Essie expression ANDed with the filters")

    dates = parser.add_argument_group("dates")
    dates.add_argument(
        "--date-field",
        choices=DATE_FIELDS,
        help="Which date the range applies to (default: start; last_update with --since-last-run)",
    )
    when = dates.add_mutually_exclusive_group()
    when.add_argument("--days", type=_positive_int, metavar="N", help="The N days up to --date-to (default today)")
    when.add_argument("--date-from", type=_iso_date, metavar="DATE", help="Earliest date (YYYY-MM-DD)")
    dates.add_argument("--date-to", type=_iso_date, metavar="DATE", help="Latest date (YYYY-MM-DD)")

    fetching = parser.add_argument_group("results")
    fetching.add_argument(
        "--max-results", type=_max_results_arg, metavar="N", help="Maximum studies to fetch, or 'all' (default: 1000)"
    )
    fetching.add_argument(
        "--sort",
        metavar="FIELD[:asc|desc]",
        help="Sort by a date or number field, e.g. LastUpdatePostDate:desc, EnrollmentCount, @relevance",
    )
    fetching.add_argument("--count", action="store_true", help="Print the number of matching studies and exit")

    inputs = parser.add_argument_group("input (instead of a search)")
    _list(
        inputs,
        "--nct-ids",
        dest="nct_ids",
        metavar="ID|FILE",
        help="Fetch these studies: NCT IDs, files listing them, or '-' for stdin",
    )
    inputs.add_argument(
        "--from-raw", metavar="FILE", help="Re-read raw studies saved with --raw (or trials-fetch 0.1 JSON)"
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o",
        "--output",
        dest="output_file",
        metavar="FILE",
        help="Output file, or '-' for stdout (default: trials.<format>); "
        "the extension (.json, .jsonl, .csv, .sqlite/.db) selects the format",
    )
    output.add_argument("--format", choices=OUTPUT_FORMATS, help="Output format (default: json)")
    _flag(output, "--raw", dest="raw", help="Write the API's study objects unchanged (JSON/JSONL only)")
    _list(output, "--fields", dest="fields", metavar="FIELD", help="With --raw: only these API fields or sections")
    _flag(output, "--merge", dest="merge", help="Update an existing output, replacing records with the same NCT ID")
    _flag(output, "--resume", dest="resume", help="Append to an existing output, skipping NCT IDs already in it")
    output.add_argument("--summary", metavar="FILE", help="Write the run summary (search_info) as JSON ('-' = stdout)")

    documents = parser.add_argument_group("documents")
    documents.add_argument(
        "--download-documents",
        dest="download_dir",
        metavar="DIR",
        help="Download protocols, analysis plans and consent forms into DIR/<NCT ID>/",
    )
    documents.add_argument(
        "--document-types",
        nargs="+",
        choices=DOCUMENT_TYPES,
        help="Document types to download (default: all)",
    )

    other = parser.add_argument_group("incremental runs and configuration")
    other.add_argument(
        "--since-last-run",
        metavar="STATE",
        help="Fetch studies updated since the date stored in STATE and update it after a complete run "
        "(defaults --date-field to last_update)",
    )
    other.add_argument("--config", metavar="FILE", help="Configuration file (INI format)")
    other.add_argument("--create-config", metavar="FILENAME", help="Create a sample configuration file and exit")
    verbosity = other.add_mutually_exclusive_group()
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Only print warnings and errors")
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Print debug output")
    return parser


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments. Unset options are None so config values can apply."""
    return build_parser().parse_args(argv)


def resolve_settings(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Merge settings with precedence: command line > config file > defaults."""
    cli = {key: getattr(args, key, None) for key in DEFAULTS}
    settings = dict(DEFAULTS)
    for layer in (config, cli):
        settings.update({k: v for k, v in layer.items() if v is not None})
    # --days on the command line replaces a date_from from the config file, and vice versa.
    if args.days is not None and args.date_from is None:
        settings["date_from"] = None
    if args.date_from is not None:
        settings["days"] = None
    return settings


def build_options(settings: dict[str, Any], quiet: bool = False) -> Options:
    spec = QuerySpec(
        **{key: settings[key] for key in ("term", *SEARCH_FIELDS, *FILTER_KEYS)},
        **{key: settings[key] for key in ("sex", "age", "healthy_volunteers", "has_results", "near", "advanced")},
        **{key: settings[key] for key in ("date_field", "date_from", "date_to")},
    )
    names = (
        "days sort from_raw raw output_file format merge resume summary download_dir document_types since_last_run"
    ).split()
    return Options(
        spec=spec,
        max_results=None if settings["max_results"] == "all" else settings["max_results"],
        nct_ids=read_ids(settings["nct_ids"]) if settings["nct_ids"] else None,
        fields=settings["fields"] or None,
        display=not quiet,
        **{name: settings[name] for name in names},
    )


SAMPLE_CONFIG = """[search]
# Lists are comma-separated and/or one item per line. Quote items containing commas:
#   countries = "Korea, Republic of", Japan
# Values are ORed within a list and ANDed across settings. Search values use Essie syntax.

# Search terms matched against all fields (the positional argument)
#term = glioblastoma

# Search fields
conditions = Glioblastoma
#interventions = temozolomide, bevacizumab
#titles = vaccine
#outcomes = overall survival
#sponsors = National Cancer Institute
#locations = Seattle

# Filters (case-insensitive)
statuses = RECRUITING, NOT_YET_RECRUITING
#phases = 2, 3
#study_types = INTERVENTIONAL
#countries = United States, Canada
# Require a site with this status (in one of the countries above, if any)
#site_statuses = RECRUITING
# A site within a distance of a point: LAT,LON[,DISTANCE mi|km]
#near = 47.61,-122.33,50mi
#funder_types = NIH, INDUSTRY
#age_groups = CHILD
#age = 12
#sex = female
#healthy_volunteers = yes
#has_results = yes
# Any Essie expression, ANDed with the filters
#advanced = AREA[EnrollmentCount]RANGE[100, MAX]

# Date range: days before date_to (default today), or date_from..date_to (YYYY-MM-DD)
# date_field: start, primary_completion, completion, first_posted, last_update, results_posted
#date_field = start
#days = 365
#date_from = 2024-01-01
#date_to = 2024-12-31

# Maximum number of studies, or "all"
max_results = 1000
#sort = LastUpdatePostDate:desc

# Fetch specific studies instead of searching (IDs and/or files listing them)
#nct_ids = NCT04280705, NCT00000620

# Output file; the extension (.json, .jsonl, .csv, .sqlite) selects the format unless format is set
output_file = trials.json
#format = jsonl
# Write the API's study objects unchanged (JSON/JSONL only), optionally limited to some fields
#raw = no
#fields = NCTId, BriefTitle, OverallStatus
#merge = no
#resume = no
#summary = run_summary.json

# Download protocols (protocol), statistical analysis plans (sap) and consent forms (icf)
#download_dir = documents
#document_types = protocol, sap

# Incremental runs: fetch studies updated since the date stored in this file
#since_last_run = trials_state.json
"""


def create_sample_config(filename: str) -> bool:
    """Create a sample configuration file. Refuses to overwrite an existing file."""
    try:
        with open(filename, "x", encoding="utf-8") as f:
            f.write(SAMPLE_CONFIG)
    except OSError as e:
        log.error("Error creating config file: %s", e)
        return False
    log.info("Sample configuration file created: %s", filename)
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the command line. Returns a process exit code."""
    args = parse_arguments(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    if args.create_config:
        return 0 if create_sample_config(args.create_config) else EXIT_ERROR

    config: dict[str, Any] = {}
    if args.config:
        if not os.path.exists(args.config):
            log.error("Error: Configuration file '%s' not found.", args.config)
            return EXIT_ERROR
        try:
            config = load_config(args.config)
        except (configparser.Error, ValueError) as e:
            log.error("Error reading configuration file '%s': %s", args.config, e)
            return EXIT_ERROR

    try:
        settings = resolve_settings(args, config)
        options = build_options(settings, quiet=args.quiet)
        if args.count:
            print(count_matches(options))
            return 0
        result = run(options)
    except (ValueError, TrialsError, OSError, sqlite3.Error) as e:
        log.error("Error: %s", e)
        return EXIT_ERROR

    info = result.search_info
    log.info("Done. Retrieved %d studies; %d in %s.", info["retrieved"], info["total_studies"], result.output)
    return result.exit_code


def main_cli() -> None:
    sys.exit(main())
