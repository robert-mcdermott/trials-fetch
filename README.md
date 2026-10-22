# Trials Fetch

This Python utility fetches clinical trials from the ClinicalTrials.gov API based on search terms and saves them in JSON format.

## Setup

This project uses UV for dependency management. Ensure UV is installed.

## Installation

Activate the virtual environment if needed, or use `uv` directly.

Dependencies are managed via `uv add`.

## Usage

Run the script using UV:

```bash
uv run python trials-fetch.py "search term" -o output.json --status STATUS
```

- `search term`: The term to search for (e.g., "diabetes").
- `-o output.json`: Optional output file (default: trials.json).
- `--status STATUS`: Filter by overall status (e.g., --status RECRUITING). Can be used multiple times for multiple statuses. Allowed values: ACTIVE_NOT_RECRUITING, COMPLETED, etc.
- `--country COUNTRY`: Filter by country of facilities (e.g., --country Sweden --country "United States"). Can be used multiple times.

For help:

```bash
uv run python trials-fetch.py --help
```

The API used is from [ClinicalTrials.gov Data API](https://clinicaltrials.gov/data-api/api).
