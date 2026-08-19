---
name: mrtis-rebuild-db
description: Rebuild/refresh the MRTIS vessel zone-traffic DuckDB warehouse from the Zone Report CSV exports in the project folder. Use when the user drops new Zone Report CSVs into MRTIS and wants the database refreshed, asks to "rebuild the database", "refresh MRTIS", "reload the zone reports", or "update the vessel traffic warehouse".
---

# MRTIS: Rebuild the database

## When to use this skill

Trigger this skill when the user:
- Adds new or updated "Zone Report*.csv" files to the MRTIS project folder
  and wants the warehouse refreshed, or
- Explicitly asks to rebuild/refresh/reload the MRTIS database, or
- Asks a question that requires up-to-date data and you're not sure the
  database reflects the CSVs currently on disk.

## What it does

Runs the project's own build pipeline (`scripts/build_db.py`) against every
`Zone Report*.csv` file in the MRTIS project root, and regenerates:

- `data/db/mrtis.duckdb` -- the full warehouse (dim_vessel, dim_vessel_name_alias,
  dim_agent, dim_zone, fact_zone_event), rebuilt from scratch each run so it
  always exactly reflects what's on disk.
- `docs/DATA_QUALITY.md` -- a fresh data quality report (row counts, date
  range, duplicate/blank stats).

This is idempotent and safe to run repeatedly. It never modifies or deletes
the source CSVs.

**Rebuilding invalidates every layer built on top of the core tables.**
`event_key` and `vessel_key` are assigned by row index, so a rebuild reassigns
them and any existing link would end up pointing at the wrong rows.
`build_db.py` therefore drops both downstream layers -- the FGIS match
(`fgis_record`, `fgis_record_line`) and the port call assembly (`port_call`,
`port_call_leg`, `port_call_event`) -- rather than leaving them silently wrong,
and prints a reminder naming what to re-run. Steps 4 and 5 below restore them.

## Steps

1. Confirm you're operating in the MRTIS project root (it contains
   `scripts/build_db.py`, `sql/schema.sql`, and one or more
   `Zone Report*.csv` files).
2. Ensure dependencies are installed: `pip install -r requirements.txt`
   (duckdb, pandas) if not already available.
3. Run the build:
   ```bash
   python3 scripts/build_db.py
   ```
4. If the database has the FGIS tables loaded (it will say so in the output),
   restore the cross-reference -- this is fast and needs no network:
   ```bash
   python3 scripts/build_fgis_match.py
   ```
   Only re-run `scripts/build_fgis.py` as well if the user also wants fresh
   FGIS data pulled from USDA (it updates weekly, on Thursdays).
5. Rebuild the port call assembly -- also fast, also no network:
   ```bash
   python3 scripts/build_port_calls.py
   ```
   This one refuses to write anything if a hard guardrail fails, so a
   non-zero exit means the output was rejected, not half-written. Read
   `docs/PORT_CALL_QUALITY.md` for the result.

6. Read the command output and the regenerated `docs/DATA_QUALITY.md`.
   Report back to the user in plain language: how many source files were
   processed, how many events/vessels/agents/zones resulted, and flag
   anything that changed meaningfully from before (e.g. a sudden spike in
   blank agents or duplicate rows) as it may indicate a malformed export.
7. If the user has git set up for this project, remind them the change to
   `docs/DATA_QUALITY.md` (and any code changes) is worth a commit -- but do
   not commit or push on their behalf unless they ask.

## If the build fails

- **Missing/renamed columns**: `build_db.py` raises immediately naming the
  missing column(s). The Zone Report export format changed; check the new
  file's header against the expected columns in `docs/BUILD.md`.
- **No files found**: confirm the CSVs are actually named `Zone Report*.csv`
  and located in the project root (or pass `--source-dir`/`--pattern` to
  point at wherever they actually are).
- For anything else, surface the full error to the user rather than
  guessing -- this pipeline is deliberately simple and a failure usually
  means the source data shape changed.
