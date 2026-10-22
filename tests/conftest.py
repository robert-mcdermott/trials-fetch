import copy
import json
import os
from pathlib import Path

import pytest
import requests

import trials_fetch.client

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_STUDIES = FIXTURES / "studies_sample.json"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TRIALS_FETCH_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live API test; set TRIALS_FETCH_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch, request):
    if "live" not in request.keywords:
        monkeypatch.setattr(trials_fetch.client.time, "sleep", lambda seconds: None)


class FakeResponse:
    def __init__(self, json_data=None, status=200, text=None, content=b""):
        self._json = json_data
        self.status_code = status
        self.text = text if text is not None else (json.dumps(json_data) if json_data is not None else "")
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._json

    def iter_content(self, chunk_size=1):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Routes requests to handlers keyed by URL substring (first match wins) and records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append((url, params))
        for key, handler in self.routes.items():
            if key in url:
                return handler(url, params)
        raise AssertionError(f"Unexpected request: {url}")

    def calls_to(self, fragment):
        return [params for url, params in self.calls if fragment in url]


def sample_studies():
    """The real API study objects in the fixture (fresh copies)."""
    return copy.deepcopy(json.loads(SAMPLE_STUDIES.read_text(encoding="utf-8"))["studies"])


def nct(study):
    return study["protocolSection"]["identificationModule"]["nctId"]


def synthetic_study(n):
    """A minimal valid study with NCT ID NCT9000000n."""
    return {"protocolSection": {"identificationModule": {"nctId": f"NCT{90000000 + n}", "briefTitle": f"Trial {n}"}}}


def studies_handler(studies=None, total=None, fail_pages=()):
    """
    Serve /studies like the API: pages of ``pageSize`` with offset page tokens, ``totalCount``
    when ``countTotal`` is set, ``filter.ids`` matching NCT IDs and aliases. Pages whose number
    (1-based) is in ``fail_pages`` answer 500.
    """
    studies = sample_studies() if studies is None else studies

    def handler(url, params):
        pool = studies
        if "filter.ids" in params:
            wanted = set(params["filter.ids"].split(","))
            pool = [
                s
                for s in studies
                if wanted & {nct(s), *s["protocolSection"]["identificationModule"].get("nctIdAliases", [])}
            ]
        offset = int(params.get("pageToken", 0))
        size = int(params.get("pageSize") or 10)
        if offset // size + 1 in fail_pages:
            return FakeResponse(status=500, text="server error")
        body = {"studies": pool[offset : offset + size]}
        if offset + size < len(pool):
            body["nextPageToken"] = str(offset + size)
        if params.get("countTotal") == "true":
            body["totalCount"] = len(pool) if total is None else total
        return FakeResponse(body)

    return handler


def version_handler(url, params):
    return FakeResponse({"apiVersion": "2.0.5", "dataTimestamp": "2026-10-21T09:00:05"})


def fake_api(**kwargs):
    return FakeSession({"/version": version_handler, "/studies": studies_handler(**kwargs)})


@pytest.fixture
def raw_studies():
    return sample_studies()


@pytest.fixture
def records(raw_studies):
    from trials_fetch import parse_study

    return {r["nct_id"]: r for r in map(parse_study, raw_studies)}
