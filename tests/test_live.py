"""
Contract tests against the real ClinicalTrials.gov API, to catch upstream changes in the API,
its query syntax or its data. Skipped unless TRIALS_FETCH_LIVE=1; CI runs them weekly.
"""

import jsonschema
import pytest

from trials_fetch import QuerySpec, TrialsClient, TrialsError
from trials_fetch.schema import study_schema

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    return TrialsClient()


@pytest.fixture(scope="module")
def validator():
    return jsonschema.Draft202012Validator(study_schema())


def test_search_records_match_schema(client, validator):
    spec = QuerySpec(conditions=["glioblastoma"], phases=["2"], study_types=["interventional"])
    studies = [s for batch in client.iter_search(spec, max_results=25) for s in batch]
    assert len(studies) == 25 and client.search_info["total_found"] > 25
    for study in studies:
        validator.validate(study)
        assert "PHASE2" in study["phases"] and study["study_type"] == "INTERVENTIONAL"


def test_paging_and_count_agree(client):
    spec = QuerySpec(conditions=["glioblastoma"], countries=["Sweden"])
    client.page_size = 10
    try:
        studies = [s for batch in client.iter_search(spec, max_results=None) for s in batch]
    finally:
        client.page_size = TrialsClient.page_size
    assert len(studies) == client.count(spec) == client.search_info["total_found"]
    assert len({s["nct_id"] for s in studies}) == len(studies)
    assert all("Sweden" in s["countries"] for s in studies)


def test_every_filter_is_accepted(client):
    """Each filter must parse and narrow the search (the API rejects unknown areas or values)."""
    base = QuerySpec(conditions=["cancer"])
    total = client.count(base)
    for kwargs in [
        {"statuses": ["RECRUITING"]},
        {"phases": ["3"]},
        {"study_types": ["observational"]},
        {"countries": ["Korea, Republic of"]},
        {"countries": ["Sweden"], "site_statuses": ["RECRUITING"]},
        {"site_statuses": ["NOT_YET_RECRUITING"]},
        {"funder_types": ["NIH"]},
        {"age_groups": ["CHILD"]},
        {"sex": "male"},
        {"age": 10},
        {"healthy_volunteers": True},
        {"has_results": True},
        {"has_results": False},
        {"near": "47.61,-122.33,25mi"},
        {"date_field": "last_update", "date_from": __import__("datetime").date(2026, 1, 1)},
        {"locations": ["Seattle"]},
    ]:
        count = client.count(QuerySpec(conditions=["cancer"], **kwargs))
        assert 0 < count < total, kwargs


def test_sort(client):
    studies = client.search_studies(QuerySpec(conditions=["glioblastoma"]), max_results=5, sort="LastUpdatePostDate")
    dates = [s["last_update_posted"] for s in studies]
    assert dates == sorted(dates, reverse=True)


def test_ids_aliases_and_missing(client, validator):
    studies = client.fetch_studies(["NCT00182910", "NCT03405792", "NCT99999999"])
    assert sorted(s["nct_id"] for s in studies) == ["NCT00000620", "NCT03405792"]
    assert client.search_info["missing_ids"] == ["NCT99999999"]
    for study in studies:
        validator.validate(study)


def test_document_links_resolve(client):
    study = client.get_study("NCT03405792")
    [doc] = study["documents"]
    response = client.session.head(doc["url"], timeout=60)
    assert response.status_code == 200 and int(response.headers["content-length"]) == doc["size"]


def test_query_errors_are_explained(client):
    with pytest.raises(TrialsError, match="Unknown area name"):
        client.count(QuerySpec(advanced="AREA[Bogus]x"))
    assert client.get_study("NCT99999999") is None


def test_version(client):
    version = client.version()
    assert version["apiVersion"].startswith("2.") and version["dataTimestamp"]
