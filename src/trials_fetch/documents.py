"""Download the documents attached to trial records: protocols, statistical analysis plans, consent forms."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from .parse import Study

log = logging.getLogger(__name__)

# Document kinds and the record flag that marks them. A file can hold several (e.g. "Prot_SAP").
DOCUMENT_TYPES = {"protocol": "has_protocol", "sap": "has_sap", "icf": "has_icf"}


def wanted(document: dict, types: list[str]) -> bool:
    return any(document.get(DOCUMENT_TYPES[t]) for t in types)


def download_documents(
    session: requests.Session,
    studies: list[Study],
    directory: str | Path,
    types: list[str] | None = None,
    timeout: float = 120,
) -> tuple[int, int]:
    """
    Download documents of the given ``types`` (default: all) to ``directory/<NCT ID>/<filename>``
    and set each document's ``path``. Files already present with the expected size are kept.
    Returns (downloaded, failed).
    """
    types = types or list(DOCUMENT_TYPES)
    if bad := set(types) - set(DOCUMENT_TYPES):
        raise ValueError(f"Unknown document type(s): {', '.join(sorted(bad))}")
    downloaded = failed = 0
    for study in studies:
        for document in study.get("documents") or []:
            if not wanted(document, types):
                continue
            target = Path(directory) / study["nct_id"] / document["filename"]
            if target.exists() and (document.get("size") is None or target.stat().st_size == document["size"]):
                document["path"] = str(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            try:
                with session.get(document["url"], stream=True, timeout=timeout) as response:
                    response.raise_for_status()
                    with open(partial, "wb") as f:
                        for chunk in response.iter_content(chunk_size=1 << 16):
                            f.write(chunk)
                os.replace(partial, target)
            except (requests.RequestException, OSError) as e:
                failed += 1
                partial.unlink(missing_ok=True)
                log.warning("Could not download %s: %s", document["url"], e)
                continue
            document["path"] = str(target)
            downloaded += 1
    if downloaded:
        log.info("Downloaded %d document(s) to %s", downloaded, directory)
    return downloaded, failed
