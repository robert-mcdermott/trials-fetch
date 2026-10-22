"""
Regenerate studies_sample.json from the live API: uv run python tests/fixtures/make_fixtures.py

The studies cover edge cases: former NCT IDs (aliases), references and posted results
(NCT00000620), attached documents (NCT03405792), a recruiting multi-country trial with
contacts (NCT06419946), expanded access (NCT06404034), an observational study with an
UNKNOWN status (NCT04007185), a terminated trial with results (NCT02743078) and a withdrawn
trial without sites (NCT01788280). Contact emails and phone numbers are replaced with
placeholders.
"""

import json
from pathlib import Path

from trials_fetch.client import RECORD_FIELDS, TrialsClient

IDS = ["NCT00000620", "NCT03405792", "NCT06419946", "NCT06404034", "NCT04007185", "NCT02743078", "NCT01788280"]


def scrub(value):
    if isinstance(value, dict):
        return {
            k: "contact@example.org" if k == "email" else "555-0100" if k == "phone" else scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def main() -> None:
    client = TrialsClient()
    page = client._get("studies", {"filter.ids": ",".join(IDS), "fields": RECORD_FIELDS, "pageSize": 100})
    studies = sorted(page["studies"], key=lambda s: IDS.index(s["protocolSection"]["identificationModule"]["nctId"]))
    target = Path(__file__).with_name("studies_sample.json")
    target.write_text(json.dumps({"studies": scrub(studies)}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(studies)} studies to {target}")


if __name__ == "__main__":
    main()
