import json

import pytest
from conftest import FakeResponse, FakeSession, fake_api, nct, studies_handler, synthetic_study

from trials_fetch import QuerySpec, TrialsClient, TrialsError, normalize_ids
from trials_fetch.client import RECORD_FIELDS, USER_AGENT, load_raw_studies, make_session

SPEC = QuerySpec(conditions=["glioblastoma"])


def client_for(session, page_size=None):
    client = TrialsClient(session=session)
    if page_size:
        client.page_size = page_size
    return client


def test_search_pages_through_results():
    session = fake_api(studies=[synthetic_study(i) for i in range(5)])
    client = client_for(session, page_size=2)
    batches = list(client.iter_search(SPEC, max_results=None))
    assert [len(b) for b in batches] == [2, 2, 1]
    calls = session.calls_to("/studies")
    assert calls[0]["countTotal"] == "true" and "pageToken" not in calls[0]
    assert "countTotal" not in calls[1] and calls[1]["pageToken"] == "2"
    assert calls[0]["fields"] == RECORD_FIELDS and calls[0]["query.cond"] == "glioblastoma"
    info = client.search_info
    assert (info["total_found"], info["expected"], info["retrieved"], info["truncated"]) == (5, 5, 5, False)


def test_max_results_limits_pages_and_marks_truncation(caplog):
    session = fake_api(studies=[synthetic_study(i) for i in range(5)])
    client = client_for(session, page_size=2)
    studies = [s for b in client.iter_search(SPEC, max_results=3) for s in b]
    assert len(studies) == 3
    # The last page asks for only what's still needed.
    assert [c["pageSize"] for c in session.calls_to("/studies")] == [2, 1]
    assert (client.search_info["expected"], client.search_info["truncated"]) == (3, True)
    assert "Only 3 of 5 matching studies" in caplog.text


def test_search_error_explains_the_query_problem():
    def bad_query(url, params):
        return FakeResponse(status=400, text="Error parsing query in Other terms: missing ')' at '<EOF>'")

    client = client_for(FakeSession({"/studies": bad_query}))
    with pytest.raises(TrialsError, match="missing '\\)'"):
        list(client.iter_search(QuerySpec(term="(glioma")))


def test_failed_later_page_is_recorded():
    session = fake_api(studies=[synthetic_study(i) for i in range(5)], fail_pages=(2,))
    client = client_for(session, page_size=2)
    studies = [s for b in client.iter_search(SPEC) for s in b]
    assert len(studies) == 2
    [failure] = client.search_info["failed_batches"]
    assert (failure["page"], failure["after"]) == (2, 2)


def test_page_retried_after_dropped_connection():
    handler = studies_handler(studies=[synthetic_study(0)])
    attempts = []

    def flaky(url, params):
        attempts.append(1)
        if len(attempts) == 1:
            raise __import__("requests").ConnectionError("connection reset")
        return handler(url, params)

    client = client_for(FakeSession({"/studies": flaky}))
    assert len(client.search_studies(SPEC)) == 1 and len(attempts) == 2


def test_raw_mode_returns_api_objects_and_keeps_nct_id_field(raw_studies):
    session = fake_api()
    client = client_for(session)
    studies = client.search_studies(SPEC, raw=True, fields=["BriefTitle"])
    assert studies == raw_studies
    assert session.calls_to("/studies")[0]["fields"] == "NCTId,BriefTitle"
    client.search_studies(SPEC, raw=True)
    assert "fields" not in session.calls_to("/studies")[1]


def test_search_studies_builds_spec_from_keywords():
    session = fake_api()
    client_for(session).search_studies(conditions=["glioblastoma"], statuses=["recruiting"], sort="StartDate")
    params = session.calls_to("/studies")[0]
    assert (params["query.cond"], params["filter.overallStatus"], params["sort"]) == (
        "glioblastoma",
        "RECRUITING",
        "StartDate",
    )


def test_empty_search_is_refused():
    with pytest.raises(TrialsError, match="No search"):
        list(client_for(fake_api()).iter_search(QuerySpec()))


def test_unparseable_studies_are_skipped():
    session = fake_api(studies=[synthetic_study(0), {"protocolSection": {}}])
    client = client_for(session)
    assert len(client.search_studies(SPEC)) == 1
    assert client.search_info["skipped_records"] == 1


def test_fetch_by_ids_resolves_aliases_and_reports_missing(caplog):
    session = fake_api()
    client = client_for(session)
    client.id_batch_size = 2
    studies = client.fetch_studies(["nct00182910", "NCT00000620", "NCT03405792", "NCT99999999"])
    # The alias and the current ID are the same study, returned once.
    assert [s["nct_id"] for s in studies] == ["NCT00000620", "NCT03405792"]
    assert [c["filter.ids"] for c in session.calls_to("/studies")] == [
        "NCT00182910,NCT00000620",
        "NCT03405792,NCT99999999",
    ]
    assert client.search_info["missing_ids"] == ["NCT99999999"]
    assert (client.search_info["source"], client.search_info["total_found"]) == ("ids", 4)
    assert "1 NCT ID(s) not found" in caplog.text


def test_failed_id_batch_is_skipped():
    handler = studies_handler()

    def flaky(url, params):
        return FakeResponse(status=500) if "NCT00000620" in params["filter.ids"] else handler(url, params)

    client = client_for(FakeSession({"/studies": flaky}))
    client.id_batch_size = 1
    studies = client.fetch_studies(["NCT00000620", "NCT03405792"])
    assert [s["nct_id"] for s in studies] == ["NCT03405792"]
    assert client.search_info["failed_batches"][0]["ids"] == ["NCT00000620", "NCT00000620"]
    assert client.search_info["missing_ids"] == []


def test_normalize_ids():
    assert normalize_ids([" nct00000620", "NCT00000620", ""]) == ["NCT00000620"]
    with pytest.raises(ValueError, match="NCT123"):
        normalize_ids(["NCT123"])


def test_get_study(raw_studies):
    def single(url, params):
        nct_id = url.rsplit("/", 1)[1]
        match = [s for s in raw_studies if nct(s) == nct_id]
        return FakeResponse(match[0]) if match else FakeResponse(status=404, text=f"NCT number {nct_id} not found")

    client = client_for(FakeSession({"/studies/": single}))
    assert client.get_study("nct03405792")["brief_title"]
    assert client.get_study("NCT99999999") is None
    assert nct(client.get_study("NCT03405792", raw=True)) == "NCT03405792"


def test_count_and_version():
    session = fake_api(total=2276)
    client = client_for(session)
    assert client.count(SPEC) == 2276
    assert session.calls_to("/studies")[0]["pageSize"] == 1
    assert client.version()["apiVersion"] == "2.0.5"


def test_requests_are_rate_limited(monkeypatch):
    sleeps = []
    monkeypatch.setattr("trials_fetch.client.time.sleep", sleeps.append)
    client = client_for(fake_api())
    client.version()
    client.version()
    assert sleeps and 0 < sleeps[0] <= client.min_interval


@pytest.mark.parametrize("layout", ["list", "document", "jsonl"])
def test_iter_raw_reads_saved_studies(tmp_path, raw_studies, layout):
    path = tmp_path / "raw.json"
    if layout == "list":  # trials-fetch 0.1 output
        path.write_text(json.dumps(raw_studies))
    elif layout == "document":  # --raw JSON output
        path.write_text(json.dumps({"schema_version": 1, "search_info": {}, "studies": raw_studies}))
    else:
        path.write_text("".join(json.dumps(s) + "\n" for s in raw_studies))
    client = TrialsClient(session=FakeSession({}))
    studies = [s for b in client.iter_raw(path) for s in b]
    assert [s["nct_id"] for s in studies] == [nct(s) for s in raw_studies]
    assert (client.search_info["source"], client.search_info["retrieved"]) == ("raw", 7)


def test_load_raw_studies_rejects_normalized_records(tmp_path, records):
    path = tmp_path / "normalized.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records.values()))
    with pytest.raises(TrialsError, match="raw API studies"):
        load_raw_studies(path)
    path.write_text("not json\n")
    with pytest.raises(TrialsError, match="neither JSON nor JSONL"):
        load_raw_studies(path)


def test_session_identifies_the_tool():
    assert make_session().headers["User-Agent"] == USER_AGENT
