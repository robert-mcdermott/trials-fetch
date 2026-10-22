"""The output must match the published JSON Schema, so changes to it are deliberate."""

import json

import jsonschema
import pytest
from conftest import FakeResponse, fake_api

from trials_fetch import Options, QuerySpec, parse_study, run
from trials_fetch.schema import load_schema, study_schema


@pytest.fixture(scope="module")
def validator():
    jsonschema.Draft202012Validator.check_schema(load_schema())
    return jsonschema.Draft202012Validator(study_schema())


def test_every_fixture_record_matches_schema(validator, raw_studies):
    for raw in raw_studies:
        validator.validate(parse_study(raw))


def test_full_output_document_matches_schema(tmp_path):
    session = fake_api()
    session.routes["cdn.clinicaltrials.gov"] = lambda url, params: FakeResponse(content=b"%PDF")
    out = tmp_path / "out.json"
    opts = Options(
        spec=QuerySpec(conditions=["x"], phases=["2"], date_from=__import__("datetime").date(2024, 1, 1)),
        display=False,
        output_file=str(out),
        download_dir=str(tmp_path / "docs"),
    )
    run(opts, session=session)
    jsonschema.validate(json.loads(out.read_text()), load_schema())


def test_ids_output_matches_schema(tmp_path):
    out = tmp_path / "out.json"
    opts = Options(nct_ids=["NCT00000620", "NCT99999999"], display=False, output_file=str(out))
    run(opts, session=fake_api())
    jsonschema.validate(json.loads(out.read_text()), load_schema())


def test_schema_rejects_unexpected_fields(validator, records):
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**records["NCT00000620"], "surprise": 1})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**records["NCT00000620"], "start_date": "Sept 1999"})
