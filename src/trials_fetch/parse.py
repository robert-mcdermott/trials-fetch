"""Convert ClinicalTrials.gov API study objects into flat, schema-versioned trial records."""

from __future__ import annotations

from typing import Any

# A normalized trial record (see schema/output.schema.json, $defs/study), or a raw API study.
Study = dict[str, Any]

STUDY_URL = "https://clinicaltrials.gov/study/{nct_id}"
# Protocols, statistical analysis plans and consent forms uploaded with a record.
DOCUMENT_URL = "https://cdn.clinicaltrials.gov/large-docs/{suffix}/{nct_id}/{filename}"


def opt(value: Any) -> Any:
    """``value``, or None if it is missing or an empty string."""
    return None if value in (None, "") else value


def record_id(record: Study) -> str:
    """The NCT ID of a normalized record or a raw API study."""
    if "nct_id" in record:
        return record["nct_id"]
    return record["protocolSection"]["identificationModule"]["nctId"]


def _date(struct: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """A ``{date, type}`` struct as (date, type); type is ACTUAL or ESTIMATED."""
    struct = struct or {}
    return opt(struct.get("date")), opt(struct.get("type"))


def _org(org: dict[str, Any] | None) -> dict[str, Any] | None:
    if not org or not org.get("name"):
        return None
    return {"name": org["name"], "class": opt(org.get("class"))}


def _outcomes(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "measure": opt(o.get("measure")),
            "description": opt(o.get("description")),
            "time_frame": opt(o.get("timeFrame")),
        }
        for o in items or []
    ]


def _meshes(module: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"id": m.get("id"), "term": m.get("term")} for m in (module or {}).get("meshes") or []]


def document_url(nct_id: str, filename: str) -> str:
    return DOCUMENT_URL.format(suffix=nct_id[-2:], nct_id=nct_id, filename=filename)


def parse_study(raw: Study) -> Study:
    """Normalize one study object from the API. Missing values are None (or empty lists)."""
    protocol = raw.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    sponsors = protocol.get("sponsorCollaboratorsModule") or {}
    oversight = protocol.get("oversightModule") or {}
    description = protocol.get("descriptionModule") or {}
    conditions = protocol.get("conditionsModule") or {}
    design = protocol.get("designModule") or {}
    design_info = design.get("designInfo") or {}
    enrollment = design.get("enrollmentInfo") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    outcomes = protocol.get("outcomesModule") or {}
    eligibility = protocol.get("eligibilityModule") or {}
    contacts = protocol.get("contactsLocationsModule") or {}
    references = protocol.get("referencesModule") or {}
    derived = raw.get("derivedSection") or {}
    documents = ((raw.get("documentSection") or {}).get("largeDocumentModule") or {}).get("largeDocs") or []

    nct_id = ident["nctId"]
    start_date, start_date_type = _date(status.get("startDateStruct"))
    primary_completion_date, primary_completion_date_type = _date(status.get("primaryCompletionDateStruct"))
    completion_date, completion_date_type = _date(status.get("completionDateStruct"))
    locations = [
        {
            "facility": opt(loc.get("facility")),
            "status": opt(loc.get("status")),
            "city": opt(loc.get("city")),
            "state": opt(loc.get("state")),
            "zip": opt(loc.get("zip")),
            "country": opt(loc.get("country")),
            "latitude": (loc.get("geoPoint") or {}).get("lat"),
            "longitude": (loc.get("geoPoint") or {}).get("lon"),
        }
        for loc in contacts.get("locations") or []
    ]

    return {
        "nct_id": nct_id,
        "url": STUDY_URL.format(nct_id=nct_id),
        "nct_id_aliases": list(ident.get("nctIdAliases") or []),
        "org_study_id": opt((ident.get("orgStudyIdInfo") or {}).get("id")),
        "secondary_ids": [{"id": s.get("id"), "type": opt(s.get("type"))} for s in ident.get("secondaryIdInfos") or []],
        "brief_title": opt(ident.get("briefTitle")),
        "official_title": opt(ident.get("officialTitle")),
        "acronym": opt(ident.get("acronym")),
        "overall_status": opt(status.get("overallStatus")),
        "last_known_status": opt(status.get("lastKnownStatus")),
        "why_stopped": opt(status.get("whyStopped")),
        "status_verified_date": opt(status.get("statusVerifiedDate")),
        "start_date": start_date,
        "start_date_type": start_date_type,
        "primary_completion_date": primary_completion_date,
        "primary_completion_date_type": primary_completion_date_type,
        "completion_date": completion_date,
        "completion_date_type": completion_date_type,
        "first_submitted": opt(status.get("studyFirstSubmitDate")),
        "first_posted": _date(status.get("studyFirstPostDateStruct"))[0],
        "last_update_posted": _date(status.get("lastUpdatePostDateStruct"))[0],
        "results_first_posted": _date(status.get("resultsFirstPostDateStruct"))[0],
        "has_results": bool(raw.get("hasResults")),
        "study_type": opt(design.get("studyType")),
        "phases": list(design.get("phases") or []),
        "allocation": opt(design_info.get("allocation")),
        "intervention_model": opt(design_info.get("interventionModel")),
        "primary_purpose": opt(design_info.get("primaryPurpose")),
        "masking": opt((design_info.get("maskingInfo") or {}).get("masking")),
        "observational_model": opt(design_info.get("observationalModel")),
        "time_perspective": opt(design_info.get("timePerspective")),
        "enrollment": enrollment.get("count"),
        "enrollment_type": opt(enrollment.get("type")),
        "conditions": list(conditions.get("conditions") or []),
        "keywords": list(conditions.get("keywords") or []),
        "condition_mesh": _meshes(derived.get("conditionBrowseModule")),
        "interventions": [
            {
                "type": opt(i.get("type")),
                "name": opt(i.get("name")),
                "description": opt(i.get("description")),
                "other_names": list(i.get("otherNames") or []),
                "arm_group_labels": list(i.get("armGroupLabels") or []),
            }
            for i in arms.get("interventions") or []
        ],
        "intervention_mesh": _meshes(derived.get("interventionBrowseModule")),
        "arms": [
            {
                "label": opt(a.get("label")),
                "type": opt(a.get("type")),
                "description": opt(a.get("description")),
                "interventions": list(a.get("interventionNames") or []),
            }
            for a in arms.get("armGroups") or []
        ],
        "primary_outcomes": _outcomes(outcomes.get("primaryOutcomes")),
        "secondary_outcomes": _outcomes(outcomes.get("secondaryOutcomes")),
        "other_outcomes": _outcomes(outcomes.get("otherOutcomes")),
        "eligibility_criteria": opt(eligibility.get("eligibilityCriteria")),
        "sex": opt(eligibility.get("sex")),
        "minimum_age": opt(eligibility.get("minimumAge")),
        "maximum_age": opt(eligibility.get("maximumAge")),
        "std_ages": list(eligibility.get("stdAges") or []),
        "healthy_volunteers": eligibility.get("healthyVolunteers"),
        "lead_sponsor": _org(sponsors.get("leadSponsor")),
        "collaborators": [org for org in map(_org, sponsors.get("collaborators") or []) if org],
        "officials": [
            {"name": opt(o.get("name")), "affiliation": opt(o.get("affiliation")), "role": opt(o.get("role"))}
            for o in contacts.get("overallOfficials") or []
        ],
        "central_contacts": [
            {
                "name": opt(c.get("name")),
                "role": opt(c.get("role")),
                "phone": opt(c.get("phone")),
                "email": opt(c.get("email")),
            }
            for c in contacts.get("centralContacts") or []
        ],
        "locations": locations,
        "countries": sorted({loc["country"] for loc in locations if loc["country"]}),
        "brief_summary": opt(description.get("briefSummary")),
        "detailed_description": opt(description.get("detailedDescription")),
        "references": [
            {"pmid": opt(r.get("pmid")), "type": opt(r.get("type")), "citation": opt(r.get("citation"))}
            for r in references.get("references") or []
        ],
        "documents": [
            {
                "type": opt(d.get("typeAbbrev")),
                "label": opt(d.get("label")),
                "date": opt(d.get("date")),
                "has_protocol": bool(d.get("hasProtocol")),
                "has_sap": bool(d.get("hasSap")),
                "has_icf": bool(d.get("hasIcf")),
                "filename": d["filename"],
                "size": d.get("size"),
                "url": document_url(nct_id, d["filename"]),
                "path": None,
            }
            for d in documents
            if d.get("filename")
        ],
        "is_fda_regulated_drug": oversight.get("isFdaRegulatedDrug"),
        "is_fda_regulated_device": oversight.get("isFdaRegulatedDevice"),
        "ipd_sharing": opt((protocol.get("ipdSharingStatementModule") or {}).get("ipdSharing")),
    }
