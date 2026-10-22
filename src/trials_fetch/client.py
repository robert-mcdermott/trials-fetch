"""ClinicalTrials.gov API v2 client: search studies and fetch records page by page."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ._meta import TOOL_NAME, TrialsError, __version__
from .parse import Study, parse_study, record_id
from .query import NCT_ID, QuerySpec

log = logging.getLogger(__name__)

API_URL = "https://clinicaltrials.gov/api/v2/"
# The API returns at most 1,000 studies per page.
PAGE_SIZE_LIMIT = 1000
# What normalized records are built from; leaves out the (large) resultsSection.
RECORD_FIELDS = "ProtocolSection,DerivedSection,DocumentSection,HasResults"
USER_AGENT = f"{TOOL_NAME}/{__version__} (+https://github.com/robert-mcdermott/trials-fetch)"


def make_session(pool_size: int = 4) -> requests.Session:
    """A requests session that retries transient failures (429/5xx) with backoff."""
    retry = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=pool_size))
    return session


def normalize_ids(ids: Iterable[str]) -> list[str]:
    """Upper-case and de-duplicate NCT IDs, keeping their order. Raises ValueError for malformed IDs."""
    ids = [i.strip().upper() for i in ids if i.strip()]
    if bad := [i for i in ids if not NCT_ID.match(i)]:
        raise ValueError(f"Invalid NCT ID(s): {', '.join(bad[:5])} (expected NCT followed by 8 digits)")
    return list(dict.fromkeys(ids))


class TrialsClient:
    """
    Search ClinicalTrials.gov and fetch study records. Batches are yielded as lists of records:
    normalized dicts (see ``parse_study``) or, with ``raw=True``, the API's own study objects.
    Details of the run (query, counts, failed batches) are collected in ``self.search_info``.
    """

    page_size = PAGE_SIZE_LIMIT
    id_batch_size = 100
    page_attempts = 2
    # The API limits each client to about 50 requests per minute.
    min_interval = 1.2

    def __init__(self, session: requests.Session | None = None, timeout: float = 120, base_url: str = API_URL):
        self.base_url = base_url
        self.timeout = timeout
        self.session = session or make_session()
        self.search_info: dict[str, Any] = {}
        self._last_request = 0.0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a rate-limited GET request and return the decoded JSON."""
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        finally:
            self._last_request = time.monotonic()
        if response.status_code in (400, 404):
            # The API explains bad queries in plain text, e.g. "Unknown area name: `Bogus`".
            raise TrialsError(response.text.strip() or f"HTTP {response.status_code}")
        response.raise_for_status()
        return response.json()

    def _get_page(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """``_get`` for one page of results, retried once for connections dropped mid-download."""
        for attempt in range(1, self.page_attempts + 1):
            try:
                return self._get(path, params)
            except (requests.RequestException, ValueError) as e:
                if attempt == self.page_attempts:
                    raise
                log.debug("Request failed (%s); retrying.", e)
                time.sleep(5)
        raise AssertionError("unreachable")

    # -- metadata --------------------------------------------------------------------------

    def version(self) -> dict[str, Any]:
        """The API version and when its data was last refreshed: ``{"apiVersion", "dataTimestamp"}``."""
        return self._get("version")

    def count(self, spec: QuerySpec) -> int:
        """The number of studies matching a search."""
        data = self._get("studies", {**spec.build_params(), "countTotal": "true", "pageSize": 1, "fields": "NCTId"})
        return int(data.get("totalCount") or 0)

    def get_study(self, nct_id: str, raw: bool = False) -> Study | None:
        """One study by NCT ID (or a former ID that is now an alias), or None if it doesn't exist."""
        [nct_id] = normalize_ids([nct_id])
        try:
            study = self._get(f"studies/{nct_id}")
        except TrialsError as e:
            if "not found" in str(e).lower():
                return None
            raise
        return study if raw else parse_study(study)

    # -- searching -------------------------------------------------------------------------

    @staticmethod
    def _fields(raw: bool, fields: Iterable[str] | None) -> str | None:
        if fields:
            # The NCT ID is needed to identify records (for --merge, --resume and listings).
            return ",".join(dict.fromkeys(["NCTId", *fields]))
        return None if raw else RECORD_FIELDS

    def _convert(self, studies: list[Study], raw: bool) -> list[Study]:
        if raw:
            return studies
        records = []
        for study in studies:
            try:
                records.append(parse_study(study))
            except (KeyError, TypeError, AttributeError) as e:
                self.search_info["skipped_records"] += 1
                log.warning("Skipping a study that could not be parsed: %r", e)
        return records

    def iter_search(
        self,
        spec: QuerySpec,
        max_results: int | None = 1000,
        sort: str | None = None,
        raw: bool = False,
        fields: Iterable[str] | None = None,
    ) -> Iterator[list[Study]]:
        """
        Search and yield batches (pages) of records; ``max_results=None`` fetches every match.
        Raises TrialsError if the search itself fails. A later page that fails ends the run
        early and is recorded in ``search_info["failed_batches"]``.
        """
        params = spec.build_params()
        if not params:
            raise TrialsError("No search terms or filters provided.")
        if sort:
            params["sort"] = sort
        if field_list := self._fields(raw, fields):
            params["fields"] = field_list
        log.info("Search: %s", {k: v for k, v in params.items() if k != "fields"})

        self._init_info(
            source="search",
            query={k: v for k, v in params.items() if k not in ("fields", "sort")},
            filters=spec.filters(),
            sort=sort,
            raw=raw,
            max_results=max_results,
            total_found=None,
            expected=None,
            truncated=False,
        )
        remaining = max_results
        token = None
        page = 0
        while remaining is None or remaining > 0:
            page += 1
            page_params = {**params, "pageSize": min(self.page_size, remaining or self.page_size)}
            if token:
                page_params["pageToken"] = token
            else:
                page_params["countTotal"] = "true"
            try:
                data = self._get_page("studies", page_params)
            except (TrialsError, requests.RequestException, ValueError) as e:
                if page == 1:
                    raise TrialsError(f"Error searching ClinicalTrials.gov: {e}") from e
                self._record_failure({"page": page, "after": self.search_info["retrieved"]}, e)
                break
            if page == 1:
                self._found(int(data.get("totalCount") or 0), max_results)
            studies = data.get("studies") or []
            if remaining is not None:
                studies = studies[:remaining]
                remaining -= len(studies)
            log.info("Fetched page %d (%d studies)", page, len(studies))
            records = self._convert(studies, raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records
            token = data.get("nextPageToken")
            if not token:
                break
        log.info("Total studies retrieved: %d", self.search_info["retrieved"])

    def _found(self, total: int, max_results: int | None) -> None:
        expected = total if max_results is None else min(total, max_results)
        self.search_info.update(total_found=total, expected=expected, truncated=expected < total)
        log.info("Found %d studies", total)
        if expected < total:
            log.warning(
                "Only %d of %d matching studies will be retrieved (raise --max-results, use --max-results all, "
                "or narrow the search).",
                expected,
                total,
            )

    def iter_ids(
        self, ids: Iterable[str], raw: bool = False, fields: Iterable[str] | None = None
    ) -> Iterator[list[Study]]:
        """
        Fetch specific studies by NCT ID in batches. Former IDs (aliases) resolve to their current
        record. IDs that don't exist are listed in ``search_info["missing_ids"]``.
        """
        ids = normalize_ids(ids)
        self._init_info(source="ids", raw=raw, total_found=len(ids), expected=len(ids), truncated=False)
        self.search_info["missing_ids"] = []
        log.info("Fetching %d studies by NCT ID", len(ids))
        seen: set[str] = set()
        field_list = self._fields(raw, fields)
        if field_list and raw:
            field_list += ",NCTIdAlias"
        for start in range(0, len(ids), self.id_batch_size):
            chunk = ids[start : start + self.id_batch_size]
            params: dict[str, Any] = {"filter.ids": ",".join(chunk), "pageSize": PAGE_SIZE_LIMIT}
            if field_list:
                params["fields"] = field_list
            studies: list[Study] = []
            try:
                while True:
                    data = self._get_page("studies", params)
                    studies.extend(data.get("studies") or [])
                    if not data.get("nextPageToken"):
                        break
                    params["pageToken"] = data["nextPageToken"]
            except (TrialsError, requests.RequestException, ValueError) as e:
                self._record_failure({"ids": [chunk[0], chunk[-1]], "count": len(chunk)}, e)
                continue
            found = set()
            batch = []
            for study in studies:
                nct_id = record_id(study)
                aliases = (study.get("protocolSection") or {}).get("identificationModule", {}).get("nctIdAliases")
                found.update([nct_id, *(aliases or [])])
                if nct_id not in seen:
                    seen.add(nct_id)
                    batch.append(study)
            self.search_info["missing_ids"].extend(i for i in chunk if i not in found)
            records = self._convert(batch, raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records
        if missing := self.search_info["missing_ids"]:
            log.warning("%d NCT ID(s) not found: %s", len(missing), ", ".join(missing[:10]))
        log.info("Total studies retrieved: %d", self.search_info["retrieved"])

    def iter_raw(self, path: str | Path, raw: bool = False) -> Iterator[list[Study]]:
        """
        Read raw API study objects saved earlier and yield them (normalized unless ``raw``)
        without contacting ClinicalTrials.gov. Accepts JSON (a list of studies, as written by
        trials-fetch 0.1, or a document with a ``studies`` list, as written with ``--raw``) and
        JSONL (one study per line).
        """
        self._init_info(source="raw", raw_file=str(path), raw=raw, total_found=None, expected=None, truncated=False)
        studies = load_raw_studies(path)
        for start in range(0, len(studies), self.page_size):
            records = self._convert(studies[start : start + self.page_size], raw)
            self.search_info["retrieved"] += len(records)
            if records:
                yield records

    def search_studies(self, spec: QuerySpec | None = None, max_results: int | None = 1000, **kwargs) -> list[Study]:
        """
        Convenience wrapper: run a search and return all records as a list. Keyword arguments
        other than ``sort``, ``raw`` and ``fields`` build the QuerySpec, e.g.
        ``search_studies(conditions=["glioblastoma"], statuses=["RECRUITING"])``.
        """
        options = {k: kwargs.pop(k) for k in ("sort", "raw", "fields") if k in kwargs}
        spec = spec or QuerySpec(**kwargs)
        return [s for batch in self.iter_search(spec, max_results, **options) for s in batch]

    def fetch_studies(self, ids: Iterable[str], raw: bool = False) -> list[Study]:
        """Convenience wrapper: fetch studies by NCT ID and return them as a list."""
        return [s for batch in self.iter_ids(ids, raw=raw) for s in batch]

    # -- bookkeeping -----------------------------------------------------------------------

    def _init_info(self, **info: Any) -> None:
        self.search_info = {**info, "retrieved": 0, "skipped_records": 0, "failed_batches": []}

    def _record_failure(self, where: dict[str, Any], error: Exception) -> None:
        self.search_info["failed_batches"].append({**where, "error": str(error)})
        log.warning("Batch %s failed: %s", where, error)


def load_raw_studies(path: str | Path) -> list[Study]:
    """Load raw API study objects from a JSON or JSONL file (see ``TrialsClient.iter_raw``)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as e:
            raise TrialsError(f"{path} is neither JSON nor JSONL: {e}") from e
    if isinstance(data, dict):
        data = data.get("studies", [data] if "protocolSection" in data else [])
    if not isinstance(data, list):
        raise TrialsError(f"No studies found in {path}")
    if data and "protocolSection" not in data[0]:
        raise TrialsError(
            f"{path} doesn't contain raw API studies (no protocolSection); "
            "only output written with --raw (or by trials-fetch 0.1) can be re-read."
        )
    return data
