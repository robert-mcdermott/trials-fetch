"""Run a complete fetch: search or load records, download documents, and stream them to an output."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import requests

from ._meta import SCHEMA_VERSION, TOOL_NAME, TrialsError, __version__, utc_now
from .client import TrialsClient
from .documents import DOCUMENT_TYPES, download_documents
from .outputs import RAW_FORMATS, STDOUT, Sink, open_sink, print_studies, resolve_output, write_summary
from .parse import Study, record_id
from .query import QuerySpec, resolve_date_range
from .state import load_state, save_state, start_from_state

log = logging.getLogger(__name__)

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL = 0, 1, 2


@dataclass
class Options:
    """Everything that controls a run. See ``trials-fetch --help`` for descriptions."""

    spec: QuerySpec = field(default_factory=QuerySpec)
    days: int | None = None
    max_results: int | None = 1000  # None: no limit
    sort: str | None = None
    nct_ids: list[str] | None = None
    from_raw: str | None = None
    raw: bool = False
    fields: list[str] | None = None
    output_file: str | None = None
    format: str | None = None
    merge: bool = False
    resume: bool = False
    summary: str | None = None
    download_dir: str | None = None
    document_types: list[str] = field(default_factory=lambda: list(DOCUMENT_TYPES))
    since_last_run: str | None = None
    display: bool = True

    @property
    def mode(self) -> str:
        return "ids" if self.nct_ids is not None else "raw" if self.from_raw else "search"

    def resolved_date_field(self) -> str:
        return self.spec.date_field or ("last_update" if self.since_last_run else "start")

    def validate(self) -> None:
        """Raise ValueError for invalid or conflicting options (before any network access)."""
        searching = not self.spec.is_empty() or self.days is not None or bool(self.since_last_run)
        modes = [searching, self.nct_ids is not None, bool(self.from_raw)]
        if not any(modes):
            raise ValueError(
                "No search specified. Give a search term, a filter such as --condition, --intervention, --sponsor "
                "or --status, --nct-ids, --from-raw, or a config file."
            )
        if sum(modes) > 1:
            raise ValueError("Use only one of: a search, --nct-ids, or --from-raw.")
        if self.mode != "search" and self.sort:
            raise ValueError("--sort applies to searches only.")
        if self.since_last_run and (self.mode != "search" or self.spec.date_from):
            raise ValueError("--since-last-run works with searches only and can't be combined with --date-from.")
        if self.days is not None and self.spec.date_from:
            raise ValueError("--days and --date-from can't be used together.")
        if self.max_results is not None and self.max_results <= 0:
            raise ValueError("max_results must be a positive number (or None for no limit).")
        if self.fields and not self.raw:
            raise ValueError("--fields only applies to --raw output.")
        if self.raw and self.download_dir:
            raise ValueError("--download-documents can't be combined with --raw.")
        if bad := set(self.document_types) - set(DOCUMENT_TYPES):
            raise ValueError(f"Unknown document type(s): {', '.join(sorted(bad))}")
        path, fmt = resolve_output(self.output_file, self.format)
        if self.raw and fmt not in RAW_FORMATS:
            raise ValueError("--raw output must be JSON or JSONL (CSV and SQLite need normalized records).")
        if path == STDOUT and (fmt == "sqlite" or self.merge or self.resume):
            raise ValueError("Writing to stdout ('-') can't be combined with sqlite, --merge or --resume.")
        if self.merge and self.resume:
            raise ValueError("--merge and --resume can't be used together.")


@dataclass
class RunResult:
    exit_code: int
    search_info: dict[str, Any]
    output: str


def resolve_search(options: Options, today: date | None = None) -> tuple[QuerySpec, date]:
    """
    The search to run, with the date range resolved from ``days`` and the ``since_last_run``
    state, and the date to record as the end of this run.
    """
    spec = options.spec
    today = today or date.today()
    date_from = spec.date_from
    if options.since_last_run:
        state = load_state(options.since_last_run)
        date_from = start_from_state(state, _state_query(options), options.resolved_date_field())
        if date_from is None:
            log.info("No state file yet at %s; starting a new incremental series.", options.since_last_run)
    date_from, date_to = resolve_date_range(options.days, date_from, spec.date_to, today)
    if date_from or date_to:
        spec = replace(spec, date_field=options.resolved_date_field(), date_from=date_from, date_to=date_to)
    return spec, date_to or today


def _state_query(options: Options) -> dict[str, str]:
    """The search an incremental series is for, without its moving date range."""
    return replace(options.spec, date_from=None, date_to=None).build_params()


def count_matches(options: Options, session: requests.Session | None = None) -> int:
    """The number of studies the search in ``options`` matches, without fetching them."""
    options.validate()
    if options.mode != "search":
        raise ValueError("Counting applies to searches only.")
    spec, _ = resolve_search(options)
    return TrialsClient(session=session).count(spec)


def run(options: Options, session: requests.Session | None = None) -> RunResult:
    """
    Execute a run. Returns a RunResult whose exit_code is 0 (complete) or 2 (some batches failed).
    Raises ValueError for invalid options and TrialsError if nothing could be fetched.
    """
    options.validate()
    client = TrialsClient(session=session)
    out_path, fmt = resolve_output(options.output_file, options.format)
    # The human-readable listing goes to stdout, so it's off when the data itself goes there.
    display = options.display and out_path != STDOUT

    data_version: dict[str, Any] = {}
    if options.mode != "raw":
        try:
            data_version = client.version()
        except (TrialsError, requests.RequestException, ValueError) as e:
            log.debug("Could not get the API version: %s", e)

    end: date | None = None
    batches: Iterator[list[Study]]
    if options.mode == "search":
        spec, end = resolve_search(options)
        batches = client.iter_search(spec, options.max_results, options.sort, options.raw, options.fields)
    elif options.mode == "ids":
        batches = client.iter_ids(options.nct_ids or [], options.raw, options.fields)
    else:
        batches = client.iter_raw(options.from_raw or "", options.raw)

    stats = {"skipped_existing": 0}
    downloads = {"downloaded": 0, "failed": 0}
    sink: Sink | None = None
    shown = 0
    for batch in batches:
        # Open the output only once records arrive, so a failed search leaves no empty file behind.
        sink = sink or open_sink(out_path, fmt, merge=options.merge, resume=options.resume)
        if sink.existing_ids:
            kept = [s for s in batch if record_id(s) not in sink.existing_ids]
            stats["skipped_existing"] += len(batch) - len(kept)
            batch = kept
        if not batch:
            continue
        if options.download_dir:
            done, failed = download_documents(
                client.session, batch, options.download_dir, options.document_types, client.timeout
            )
            downloads["downloaded"] += done
            downloads["failed"] += failed
        if display:
            print_studies(batch, start=shown + 1)
            shown += len(batch)
        sink.write(batch)

    info: dict[str, Any] = {
        "tool": {"name": TOOL_NAME, "version": __version__},
        "schema_version": SCHEMA_VERSION,
        "generated_on": utc_now(),
        "api_version": data_version.get("apiVersion"),
        "data_timestamp": data_version.get("dataTimestamp"),
        **client.search_info,
        **stats,
    }
    if options.download_dir:
        info["downloads"] = downloads
    info["complete"] = not info["failed_batches"]
    if info["failed_batches"] and not info["retrieved"]:
        raise TrialsError(f"Could not fetch any studies: {info['failed_batches'][0]['error']}")
    if display and not shown:
        print("No new studies." if stats["skipped_existing"] else "No studies found.")

    sink = sink or open_sink(out_path, fmt, merge=options.merge, resume=options.resume)
    info["total_studies"] = sink.close(info)
    if out_path != STDOUT:
        log.info("Results saved to: %s (%d studies)", out_path, info["total_studies"])
    if options.summary:
        write_summary(options.summary, info)
    if options.since_last_run and end is not None:
        if not info["complete"]:
            log.warning("Not updating %s because some batches failed; the next run will retry.", options.since_last_run)
        elif info["truncated"]:
            log.warning(
                "Not updating %s because --max-results cut the results; raise it so the next run doesn't skip studies.",
                options.since_last_run,
            )
        else:
            date_field = options.resolved_date_field()
            save_state(options.since_last_run, _state_query(options), date_field, end, info["total_found"])

    if not info["complete"]:
        log.warning("Run incomplete: %d batch(es) failed. See search_info.failed_batches.", len(info["failed_batches"]))
    return RunResult(EXIT_OK if info["complete"] else EXIT_PARTIAL, info, out_path)
