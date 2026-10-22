from datetime import date

import pytest

from trials_fetch import QuerySpec, TrialsError, resolve_date_range
from trials_fetch.query import parse_near


def test_search_fields_map_to_api_parameters():
    spec = QuerySpec(
        term="glioblastoma OR glioma",
        conditions=["breast cancer", "DCIS"],
        interventions=["pembrolizumab"],
        sponsors=["  "],
    )
    assert spec.build_params() == {
        # The term stays as typed; it's never combined with filters in the same parameter.
        "query.term": "glioblastoma OR glioma",
        "query.cond": "(breast cancer) OR (DCIS)",
        "query.intr": "pembrolizumab",
    }


def test_filters_are_anded_and_grouped():
    spec = QuerySpec(
        statuses=["recruiting", "not_yet_recruiting"],
        phases=["2", "phase3"],
        study_types=["interventional"],
        countries=["Sweden", "United States"],
        funder_types=["industry"],
        has_results=False,
        advanced="AREA[EnrollmentCount]RANGE[100, MAX]",
    )
    params = spec.build_params()
    assert params["filter.overallStatus"] == "RECRUITING,NOT_YET_RECRUITING"
    assert params["filter.advanced"] == (
        "(AREA[Phase](PHASE2 OR PHASE3)) AND (AREA[StudyType]INTERVENTIONAL) AND "
        '((AREA[LocationCountry]"Sweden") OR (AREA[LocationCountry]"United States")) AND '
        "(AREA[LeadSponsorClass]INDUSTRY) AND (NOT AREA[HasResults]true) AND "
        "(AREA[EnrollmentCount]RANGE[100, MAX])"
    )


def test_site_status_applies_to_the_same_site_as_the_country():
    spec = QuerySpec(countries=["Sweden"], site_statuses=["recruiting"])
    assert spec.advanced_filter() == (
        'SEARCH[Location]((AREA[LocationCountry]"Sweden") AND (AREA[LocationStatus]RECRUITING))'
    )
    assert (
        QuerySpec(site_statuses=["RECRUITING"]).advanced_filter() == "SEARCH[Location](AREA[LocationStatus]RECRUITING)"
    )


def test_eligibility_filters():
    assert QuerySpec(sex="Female").advanced_filter() == "NOT AREA[Sex]MALE"
    assert QuerySpec(sex="male").advanced_filter() == "NOT AREA[Sex]FEMALE"
    assert QuerySpec(age_groups=["child", "older"]).advanced_filter() == "AREA[StdAge](CHILD OR OLDER_ADULT)"
    assert QuerySpec(healthy_volunteers=True).advanced_filter() == "AREA[HealthyVolunteers]true"
    assert QuerySpec(age=12).advanced_filter() == (
        "(AREA[MinimumAge]RANGE[MIN, 12 years] OR AREA[MinimumAge]MISSING) AND "
        "(AREA[MaximumAge]RANGE[12 years, MAX] OR AREA[MaximumAge]MISSING)"
    )


def test_date_range():
    spec = QuerySpec(date_from=date(2024, 1, 1), date_to=date(2024, 12, 31))
    assert spec.advanced_filter() == "AREA[StartDate]RANGE[2024-01-01, 2024-12-31]"
    spec = QuerySpec(date_field="last_update", date_from=date(2024, 5, 1))
    assert spec.advanced_filter() == "AREA[LastUpdatePostDate]RANGE[2024-05-01, MAX]"


def test_near():
    assert parse_near("47.61, -122.33") == "distance(47.61,-122.33,50mi)"
    assert parse_near("59.3,18.07,25KM") == "distance(59.3,18.07,25km)"
    assert QuerySpec(near="1,2,10").build_params() == {"filter.geo": "distance(1,2,10mi)"}
    for bad in ("Seattle", "91,0", "1,2,3,4"):
        with pytest.raises(ValueError):
            parse_near(bad)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"statuses": ["OPEN"]},
        {"phases": ["5"]},
        {"study_types": ["clinical"]},
        {"site_statuses": ["WITHHELD"]},
        {"funder_types": ["pharma"]},
        {"sex": "other"},
        {"date_field": "posted"},
        {"age": -1},
        {"date_from": date(2024, 2, 1), "date_to": date(2024, 1, 1)},
    ],
)
def test_invalid_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        QuerySpec(**kwargs)


def test_empty_spec_and_filters():
    assert QuerySpec(conditions=[" "]).is_empty()
    spec = QuerySpec(phases=["3", "PHASE3"], date_from=date(2024, 1, 1))
    assert not spec.is_empty()
    filters = spec.filters()
    assert filters["phases"] == ["PHASE3"] and filters["date_from"] == "2024-01-01"


def test_resolve_date_range():
    today = date(2024, 3, 31)
    assert resolve_date_range(None, None, None, today) == (None, None)
    assert resolve_date_range(30, None, None, today) == (date(2024, 3, 1), today)
    assert resolve_date_range(10, None, date(2024, 1, 11), today) == (date(2024, 1, 1), date(2024, 1, 11))
    assert resolve_date_range(None, date(2024, 1, 1), None, today) == (date(2024, 1, 1), None)
    with pytest.raises(TrialsError):
        resolve_date_range(None, date(2024, 2, 1), date(2024, 1, 1), today)
