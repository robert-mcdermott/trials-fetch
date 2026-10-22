"""Build ClinicalTrials.gov API search parameters from a QuerySpec."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from ._meta import TrialsError

# Free-text search categories and the API parameters they map to, in Essie expression syntax
# (https://clinicaltrials.gov/find-studies/constructing-complex-search-queries).
SEARCH_FIELDS = {
    "conditions": "query.cond",
    "interventions": "query.intr",
    "titles": "query.titles",
    "outcomes": "query.outc",
    "sponsors": "query.spons",
    "locations": "query.locn",
}

STATUSES = (
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "ENROLLING_BY_INVITATION",
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
    "AVAILABLE",
    "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING",
    "WITHHELD",
    "UNKNOWN",
)
# Statuses an individual site (location) can have.
SITE_STATUSES = STATUSES[:9]
PHASES = ("EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA")
STUDY_TYPES = ("INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS")
AGE_GROUPS = ("CHILD", "ADULT", "OLDER_ADULT")
FUNDER_TYPES = ("NIH", "FED", "OTHER_GOV", "INDUSTRY", "NETWORK", "INDIV", "AMBIG", "OTHER", "UNKNOWN")
SEXES = ("female", "male")

# Date filters: option value -> API field.
DATE_FIELDS = {
    "start": "StartDate",
    "primary_completion": "PrimaryCompletionDate",
    "completion": "CompletionDate",
    "first_posted": "StudyFirstPostDate",
    "last_update": "LastUpdatePostDate",
    "results_posted": "ResultsFirstPostDate",
}

# Shorthands accepted for enum values (case-insensitive), e.g. --phase 2 3.
ALIASES = {
    "phases": {"0": "EARLY_PHASE1", "EARLY1": "EARLY_PHASE1", "1": "PHASE1", "2": "PHASE2", "3": "PHASE3"}
    | {"4": "PHASE4", "N/A": "NA", "NONE": "NA"},
    "study_types": {"EXPANDED": "EXPANDED_ACCESS"},
    "age_groups": {"OLDER": "OLDER_ADULT"},
}

NCT_ID = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
_NEAR = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(\d+(?:\.\d+)?)\s*(mi|km)?\s*)?$", re.I)


def normalize_enum(
    values: list[str], allowed: tuple[str, ...], name: str, aliases: dict[str, str] | None = None
) -> list[str]:
    """Upper-case values, apply shorthands and check them against ``allowed``. Raises ValueError."""
    result = []
    for value in values:
        key = value.strip().upper().replace("-", "_").replace(" ", "_")
        key = (aliases or {}).get(key, key)
        if key not in allowed:
            raise ValueError(f"Unknown {name} {value!r}; use one of {', '.join(allowed)}")
        result.append(key)
    return list(dict.fromkeys(result))


def parse_near(value: str) -> str:
    """
    Convert "LAT,LON[,DISTANCE[mi|km]]" to the API's geo filter, e.g. "47.6,-122.3,25" ->
    "distance(47.6,-122.3,25mi)". The distance defaults to 50 miles.
    """
    match = _NEAR.match(value)
    if not match:
        raise ValueError(f"Expected LAT,LON[,DISTANCE[mi|km]] such as 47.61,-122.33,25mi; got {value!r}")
    lat, lon, distance, unit = match.groups()
    if not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
        raise ValueError(f"Latitude/longitude out of range: {value!r}")
    return f"distance({lat},{lon},{distance or 50}{(unit or 'mi').lower()})"


def _quote(value: str) -> str:
    return '"' + value.replace('"', "").strip() + '"'


def _any_of(area: str, values: list[str]) -> str:
    return f"AREA[{area}]" + (values[0] if len(values) == 1 else "(" + " OR ".join(values) + ")")


def _either(values: list[str]) -> str:
    """OR several Essie expressions, keeping each one's grouping."""
    values = [v.strip() for v in values if v.strip()]
    return values[0] if len(values) == 1 else " OR ".join(f"({v})" for v in values)


@dataclass
class QuerySpec:
    """
    A ClinicalTrials.gov search. Values within one list are ORed; different fields and filters
    are ANDed. Free-text values (``term``, ``conditions`` ... ``locations``, ``advanced``) use
    Essie expression syntax, so ``"breast cancer" OR "DCIS"`` and ``AREA[...]`` work.
    """

    term: str | None = None
    conditions: list[str] = field(default_factory=list)
    interventions: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    sponsors: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    study_types: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    site_statuses: list[str] = field(default_factory=list)
    funder_types: list[str] = field(default_factory=list)
    age_groups: list[str] = field(default_factory=list)
    sex: str | None = None
    age: int | None = None
    healthy_volunteers: bool | None = None
    has_results: bool | None = None
    near: str | None = None
    date_field: str | None = None  # key of DATE_FIELDS; default "start"
    date_from: date | None = None
    date_to: date | None = None
    advanced: str | None = None

    def __post_init__(self) -> None:
        self.statuses = normalize_enum(self.statuses, STATUSES, "status")
        self.site_statuses = normalize_enum(self.site_statuses, SITE_STATUSES, "site status")
        self.phases = normalize_enum(self.phases, PHASES, "phase", ALIASES["phases"])
        self.study_types = normalize_enum(self.study_types, STUDY_TYPES, "study type", ALIASES["study_types"])
        self.funder_types = normalize_enum(self.funder_types, FUNDER_TYPES, "funder type")
        self.age_groups = normalize_enum(self.age_groups, AGE_GROUPS, "age group", ALIASES["age_groups"])
        if self.sex is not None:
            self.sex = self.sex.strip().lower()
            if self.sex not in SEXES:
                raise ValueError(f"sex must be one of {', '.join(SEXES)}, got {self.sex!r}")
        if self.date_field is not None and self.date_field not in DATE_FIELDS:
            raise ValueError(f"date_field must be one of {', '.join(DATE_FIELDS)}, got {self.date_field!r}")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError(f"Start of the date range ({self.date_from}) is after its end ({self.date_to}).")
        if self.age is not None and not 0 <= self.age <= 150:
            raise ValueError(f"age must be between 0 and 150, got {self.age}")
        if self.near:
            parse_near(self.near)

    def advanced_filter(self) -> str:
        """The structured filters as one Essie expression for ``filter.advanced``."""
        parts = []
        if self.phases:
            parts.append(_any_of("Phase", self.phases))
        if self.study_types:
            parts.append(_any_of("StudyType", self.study_types))
        countries = [f"AREA[LocationCountry]{_quote(c)}" for c in self.countries if c.strip()]
        if self.site_statuses:
            # SEARCH[Location] requires the country and status to belong to the same site.
            site = [_either(countries)] if countries else []
            site.append(_any_of("LocationStatus", self.site_statuses))
            parts.append(
                "SEARCH[Location](" + (site[0] if len(site) == 1 else " AND ".join(f"({s})" for s in site)) + ")"
            )
        elif countries:
            parts.append(_either(countries))
        if self.funder_types:
            parts.append(_any_of("LeadSponsorClass", self.funder_types))
        if self.age_groups:
            parts.append(_any_of("StdAge", self.age_groups))
        if self.sex:
            # Studies open to this sex: single-sex studies of the other sex are excluded, "ALL" kept.
            parts.append(f"NOT AREA[Sex]{'MALE' if self.sex == 'female' else 'FEMALE'}")
        if self.age is not None:
            # A missing minimum or maximum age means no limit.
            parts.append(
                f"(AREA[MinimumAge]RANGE[MIN, {self.age} years] OR AREA[MinimumAge]MISSING) AND "
                f"(AREA[MaximumAge]RANGE[{self.age} years, MAX] OR AREA[MaximumAge]MISSING)"
            )
        if self.healthy_volunteers is not None:
            parts.append(f"AREA[HealthyVolunteers]{str(self.healthy_volunteers).lower()}")
        if self.has_results is not None:
            parts.append("AREA[HasResults]true" if self.has_results else "NOT AREA[HasResults]true")
        if self.date_from or self.date_to:
            start = self.date_from.isoformat() if self.date_from else "MIN"
            end = self.date_to.isoformat() if self.date_to else "MAX"
            parts.append(f"AREA[{DATE_FIELDS[self.date_field or 'start']}]RANGE[{start}, {end}]")
        if self.advanced and self.advanced.strip():
            parts.append(self.advanced.strip())
        return parts[0] if len(parts) == 1 else " AND ".join(f"({p})" for p in parts)

    def build_params(self) -> dict[str, str]:
        """API query parameters (without paging) for /api/v2/studies."""
        params: dict[str, str] = {}
        if self.term and self.term.strip():
            params["query.term"] = self.term.strip()
        for key, param in SEARCH_FIELDS.items():
            values = [v for v in getattr(self, key) if v.strip()]
            if values:
                params[param] = _either(values)
        if self.statuses:
            params["filter.overallStatus"] = ",".join(self.statuses)
        if self.near:
            params["filter.geo"] = parse_near(self.near)
        if advanced := self.advanced_filter():
            params["filter.advanced"] = advanced
        return params

    def is_empty(self) -> bool:
        return not self.build_params()

    def filters(self) -> dict[str, Any]:
        """The spec as JSON-serializable data (dates as ISO strings)."""
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()}


def resolve_date_range(
    days: int | None = None, date_from: date | None = None, date_to: date | None = None, today: date | None = None
) -> tuple[date | None, date | None]:
    """
    Return (from, to). Explicit dates win; ``days`` means the ``days`` days up to ``date_to``
    (default today). With neither, the range stays open (None).
    """
    if days is not None and date_from is None:
        date_to = date_to or today or date.today()
        date_from = date_to - timedelta(days=days)
    if date_from and date_to and date_from > date_to:
        raise TrialsError(f"Start of the date range ({date_from}) is after its end ({date_to}).")
    return date_from, date_to
