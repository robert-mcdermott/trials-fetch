from conftest import synthetic_study

from trials_fetch import parse_study, record_id


def test_completed_trial_with_aliases_and_references(records):
    study = records["NCT00000620"]
    assert study["url"] == "https://clinicaltrials.gov/study/NCT00000620"
    assert study["nct_id_aliases"] == ["NCT00182910"]
    assert (study["acronym"], study["overall_status"], study["phases"]) == ("ACCORD", "COMPLETED", ["PHASE3"])
    assert (study["enrollment"], study["enrollment_type"]) == (10251, "ACTUAL")
    assert (study["start_date"], study["start_date_type"]) == ("1999-09", None)
    assert study["has_results"] is True and study["results_first_posted"] == "2014-09-15"
    assert study["lead_sponsor"] == {"name": "National Heart, Lung, and Blood Institute (NHLBI)", "class": "NIH"}
    assert study["countries"] == ["Canada", "United States"]
    assert study["condition_mesh"][0] == {"id": "D050197", "term": "Atherosclerosis"}
    assert len(study["references"]) == 128 and all(r["pmid"] for r in study["references"][:5])
    assert (study["sex"], study["minimum_age"], study["maximum_age"]) == ("ALL", "40 Years", "79 Years")
    assert study["secondary_ids"][0]["type"] == "NIH"


def test_documents_get_download_urls(records):
    [doc] = records["NCT03405792"]["documents"]
    assert doc["url"] == "https://cdn.clinicaltrials.gov/large-docs/92/NCT03405792/Prot_SAP_000.pdf"
    assert (doc["has_protocol"], doc["has_sap"], doc["has_icf"], doc["path"]) == (True, True, False, None)
    types = [(d["type"], d["has_icf"]) for d in records["NCT02743078"]["documents"]]
    assert types == [("Prot_SAP", False), ("ICF", True)]


def test_recruiting_trial_with_contacts(records):
    study = records["NCT06419946"]
    assert study["overall_status"] == "RECRUITING"
    assert study["countries"] == ["Austria", "Denmark", "Norway", "Sweden"]
    assert len(study["locations"]) == 20 and study["locations"][0]["latitude"] is not None
    assert study["central_contacts"][0]["email"] == "contact@example.org"
    assert study["ipd_sharing"] == "YES"


def test_other_record_kinds(records):
    expanded = records["NCT06404034"]
    assert (expanded["study_type"], expanded["phases"], expanded["enrollment"]) == ("EXPANDED_ACCESS", [], None)
    unknown = records["NCT04007185"]
    assert (unknown["overall_status"], unknown["last_known_status"]) == ("UNKNOWN", "ACTIVE_NOT_RECRUITING")
    assert unknown["study_type"] == "OBSERVATIONAL"
    terminated = records["NCT02743078"]
    assert terminated["why_stopped"] == "Treatment now available commercially" and terminated["has_results"]
    withdrawn = records["NCT01788280"]
    assert (withdrawn["overall_status"], withdrawn["enrollment"]) == ("WITHDRAWN", 0)


def test_minimal_study_has_every_field():
    full = parse_study(synthetic_study(0))
    sparse = parse_study({"protocolSection": {"identificationModule": {"nctId": "NCT90000001"}}})
    assert sparse.keys() == full.keys()
    assert sparse["brief_title"] is None and sparse["lead_sponsor"] is None and sparse["locations"] == []
    assert sparse["has_results"] is False


def test_empty_strings_become_null(raw_studies):
    raw = raw_studies[0]
    raw["protocolSection"]["identificationModule"]["acronym"] = ""
    assert parse_study(raw)["acronym"] is None


def test_record_id(raw_studies, records):
    assert record_id(raw_studies[0]) == "NCT00000620"
    assert record_id(records["NCT00000620"]) == "NCT00000620"
