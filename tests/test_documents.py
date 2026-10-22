import pytest
import requests
from conftest import FakeResponse, FakeSession

from trials_fetch import download_documents


def pdf_server(fail=()):
    def handler(url, params):
        if any(f in url for f in fail):
            raise requests.ConnectionError("unreachable")
        return FakeResponse(content=b"%PDF-" + url.encode())

    return FakeSession({"cdn.clinicaltrials.gov": handler})


def test_downloads_documents_and_sets_paths(tmp_path, records):
    study = records["NCT02743078"]
    for doc in study["documents"]:
        doc["size"] = None
    session = pdf_server()
    assert download_documents(session, [study], tmp_path) == (2, 0)
    paths = [doc["path"] for doc in study["documents"]]
    assert paths == [str(tmp_path / "NCT02743078" / "Prot_SAP_000.pdf"), str(tmp_path / "NCT02743078" / "ICF_001.pdf")]
    assert (tmp_path / "NCT02743078" / "ICF_001.pdf").read_bytes().startswith(b"%PDF-")

    # Files that are already there aren't downloaded again.
    assert download_documents(session, [study], tmp_path) == (0, 0)
    assert len(session.calls) == 2


def test_document_types_filter(tmp_path, records):
    study = records["NCT02743078"]
    assert download_documents(pdf_server(), [study], tmp_path, ["icf"]) == (1, 0)
    assert [doc["path"] is not None for doc in study["documents"]] == [False, True]
    with pytest.raises(ValueError, match="consent"):
        download_documents(pdf_server(), [study], tmp_path, ["consent"])


def test_failed_downloads_are_counted(tmp_path, records, caplog):
    study = records["NCT02743078"]
    assert download_documents(pdf_server(fail=["ICF"]), [study], tmp_path) == (1, 1)
    assert study["documents"][1]["path"] is None
    assert not list((tmp_path / "NCT02743078").glob("*.part"))
    assert "Could not download" in caplog.text


def test_wrong_size_file_is_replaced(tmp_path, records):
    study = records["NCT03405792"]
    target = tmp_path / "NCT03405792" / "Prot_SAP_000.pdf"
    target.parent.mkdir()
    target.write_bytes(b"truncated")
    assert download_documents(pdf_server(), [study], tmp_path) == (1, 0)
    assert target.read_bytes().startswith(b"%PDF-")
