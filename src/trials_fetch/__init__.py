"""
Search ClinicalTrials.gov and export structured clinical trial records.

Library use::

    from trials_fetch import Options, QuerySpec, run

    result = run(Options(spec=QuerySpec(conditions=["glioblastoma"], statuses=["RECRUITING"]), output_file="gbm.jsonl"))

or, for records in memory::

    from trials_fetch import TrialsClient

    studies = TrialsClient().search_studies(conditions=["glioblastoma"], statuses=["RECRUITING"], countries=["Sweden"])
"""

import logging

from ._meta import SCHEMA_VERSION, TrialsError, __version__
from .client import PAGE_SIZE_LIMIT, TrialsClient, load_raw_studies, make_session, normalize_ids
from .documents import DOCUMENT_TYPES, download_documents
from .outputs import open_sink, print_studies, resolve_output, save_results
from .parse import Study, parse_study, record_id
from .pipeline import Options, RunResult, count_matches, resolve_search, run
from .query import QuerySpec, resolve_date_range

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "DOCUMENT_TYPES",
    "PAGE_SIZE_LIMIT",
    "SCHEMA_VERSION",
    "Options",
    "QuerySpec",
    "RunResult",
    "Study",
    "TrialsClient",
    "TrialsError",
    "__version__",
    "count_matches",
    "download_documents",
    "load_raw_studies",
    "make_session",
    "normalize_ids",
    "open_sink",
    "parse_study",
    "print_studies",
    "record_id",
    "resolve_date_range",
    "resolve_output",
    "resolve_search",
    "run",
    "save_results",
]
