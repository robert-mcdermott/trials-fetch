"""JSON Schema for trials-fetch output (schema_version 1)."""

import json
from functools import cache
from importlib.resources import files
from typing import Any


@cache
def load_schema() -> dict[str, Any]:
    """The JSON Schema for a JSON output document. Study records are under ``$defs/study``."""
    return json.loads(files(__package__).joinpath("output.schema.json").read_text(encoding="utf-8"))


def study_schema() -> dict[str, Any]:
    """A standalone schema for one study record (e.g. a JSONL line)."""
    schema = load_schema()
    return {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/study"}
