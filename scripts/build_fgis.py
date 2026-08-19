#!/usr/bin/env python3
"""
MRTIS / FGIS raw ingest -- pull USDA Federal Grain Inspection Service export
certification data and load it into two new tables in the MRTIS warehouse:
`fgis_raw` (every column, every carrier type, every port, 2018-YTD) and
`fgis_output` (the 14 columns William specified, filtered to ocean vessels
calling the Mississippi River -- Type Carrier = 1 AND Port = "MISSISSIPPI R.").

Source: https://fgisonline.ams.usda.gov/ExportGrainReport/default.aspx
        one plain CSV per calendar year at a predictable URL:
        https://fgisonline.ams.usda.gov/ExportGrainReport/CY{year}.csv
        Confirmed 2026-08-18 by inspecting the page directly -- no scraping,
        no form, no auth wall. Files are public USDA open data, ~110 columns
        of grain-grading detail per certificate line.

This script deliberately does NOT do any of the following -- they're the
harder, ambiguity-laden part of the FGIS integration and are being spec'd
separately (see docs/FGIS_MATCH_SPEC.md) for a dedicated build pass:
    - matching FGIS Carrier Name to an MRTIS vessel (no IMO on this side)
    - rolling multiple FGIS lines up into one record per (vessel, Cert Date)
    - the bidirectional fgis_record_id <-> MRTIS cross-reference

Usage:
    python3 scripts/build_fgis.py
    python3 scripts/build_fgis.py --start-year 2018 --end-year 2026
    python3 scripts/build_fgis.py --use-cache   # skip download, read fgis_source/ as-is

Safe to re-run: full rebuild each time (drop + recreate fgis_raw/fgis_output
from whatever's in fgis_source/), same philosophy as scripts/build_db.py.
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime

import duckdb
import pandas as pd
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SOURCE_URL_TEMPLATE = "https://fgisonline.ams.usda.gov/ExportGrainReport/CY{year}.csv"

# The 14 columns William specified for the filtered output table, in his
# order, mapped raw-header -> sanitized column name.
OUTPUT_COLUMNS_RAW = [
    "Thursday", "Type Shipm", "Cert Date", "Type Carrier", "Carrier Name",
    "Grain", "Class", "Pounds", "Destination", "Field Office", "Port",
    "AMS Reg", "FGIS Reg", "Metric Ton",
]


def sanitize_column(name: str) -> str:
    """Turn a raw FGIS header like 'M LD %' or '1000 Bushels' into a safe
    snake_case SQL column name. A couple of names collide with SQL keywords
    ('Class', 'Def') so those get an explicit, more descriptive rename."""
    overrides = {
        "Class": "grain_class",
        "DEF": "def_pct",
    }
    stripped = name.strip()
    if stripped in overrides:
        return overrides[stripped]
    n = stripped.lower()
    n = re.sub(r"[^a-z0-9]+", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    if re.match(r"^[0-9]", n):
        n = "n_" + n
    return n


def fetch_year(year: int, source_dir: str, use_cache: bool) -> str:
    """Download CY{year}.csv into source_dir unless --use-cache and it
    already exists. Returns the local path, or None if the year isn't
    available yet (e.g. asking for a future year)."""
    os.makedirs(source_dir, exist_ok=True)
    local_path = os.path.join(source_dir, f"CY{year}.csv")

    if use_cache and os.path.exists(local_path):
        print(f"  {year}: using cached {local_path}")
        return local_path

    url = SOURCE_URL_TEMPLATE.format(year=year)
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        print(f"  {year}: not available (404) -- skipping")
        return None
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    print(f"  {year}: downloaded {len(resp.content):,} bytes -> {local_path}")
    return local_path


def load_year(path: str, year: int) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [sanitize_column(c) for c in df.columns]
    df["source_year"] = year
    df["source_file"] = os.path.basename(path)
    return df


def build_raw(paths_by_year: dict) -> pd.DataFrame:
    frames = []
    all_cols = None
    for year, path in sorted(paths_by_year.items()):
        if path is None:
            continue
        df = load_year(path, year)
        if all_cols is None:
            all_cols = list(df.columns)
        else:
            # FGIS columns have been stable since 2021 per the site's own
            # note, but don't silently drop anything if an older/newer year
            # has extra columns -- union them in rather than erroring.
            for c in df.columns:
                if c not in all_cols:
                    all_cols.append(c)
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.reindex(columns=all_cols)
    raw.insert(0, "fgis_raw_key", raw.index + 1)
    return raw


def build_output(raw: pd.DataFrame) -> pd.DataFrame:
    cols = [sanitize_column(c) for c in OUTPUT_COLUMNS_RAW]
    out = raw[raw["type_carrier"] == "1"]
    out = out[out["port"] == "MISSISSIPPI R."]
    out = out[["fgis_raw_key"] + cols].copy()

    out["thursday_date"] = pd.to_datetime(out["thursday"], format="%Y%m%d", errors="coerce")
    out["cert_date_parsed"] = pd.to_datetime(out["cert_date"], format="%Y%m%d", errors="coerce")
    out["metric_ton"] = pd.to_numeric(out["metric_ton"], errors="coerce")
    out["pounds"] = pd.to_numeric(out["pounds"], errors="coerce")

    out = out.reset_index(drop=True)
    out.insert(0, "fgis_output_key", out.index + 1)
    return out


def write_db(db_path, raw: pd.DataFrame, output: pd.DataFrame):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)

    con.execute("DROP TABLE IF EXISTS fgis_output")
    con.execute("DROP TABLE IF EXISTS fgis_raw")

    # fgis_raw is ~110 raw-fidelity columns that can vary slightly by year --
    # CREATE TABLE AS SELECT from the dataframe rather than hand-maintained
    # DDL, so this doesn't need updating if FGIS adds/renames a grading
    # column in a future year. All raw values stay VARCHAR (as exported);
    # no typing/parsing/guessing happens in this table.
    con.register("raw_df", raw)
    con.execute("CREATE TABLE fgis_raw AS SELECT * FROM raw_df")

    con.register("output_df", output)
    con.execute("CREATE TABLE fgis_output AS SELECT * FROM output_df")

    con.execute("CREATE INDEX IF NOT EXISTS idx_fgis_raw_year ON fgis_raw(source_year)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fgis_output_certdate ON fgis_output(cert_date_parsed)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fgis_output_carrier ON fgis_output(carrier_name)")

    con.close()


def write_report(report_path, raw: pd.DataFrame, output: pd.DataFrame, years):
    n_raw = len(raw)
    n_out = len(output)
    date_min = output["cert_date_parsed"].min()
    date_max = output["cert_date_parsed"].max()
    n_carriers = output["carrier_name"].nunique()
    blank_class = (output["grain_class"] == "").sum()

    lines = [
        "# FGIS Data Quality Report",
        "",
        f"_Generated by `scripts/build_fgis.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        f"- Years pulled: {min(years)}-{max(years)} ({len(years)} yearly files from "
        "fgisonline.ams.usda.gov)",
        f"- Raw rows loaded (`fgis_raw`, all carriers/all ports/all years): {n_raw:,}",
        f"- Output rows (`fgis_output`, Type Carrier=1 AND Port=MISSISSIPPI R.): {n_out:,}",
        f"- Output date range (Cert Date): {date_min} to {date_max}",
        f"- Distinct Carrier Name values in output: {n_carriers:,} (raw text, not yet "
        "resolved to MRTIS vessel identity -- see docs/FGIS_MATCH_SPEC.md)",
        f"- Output rows with blank Class: {blank_class:,}",
        "",
        "## Notes",
        "",
        "- `fgis_raw` keeps every column FGIS publishes (grading/quality detail "
        "included) for every carrier type and port nationally -- not just the "
        "Mississippi River ocean-vessel slice. Kept per William's instruction "
        "to store the full raw feed for other future uses.",
        "- `fgis_output` is the 14-column, Mississippi-River/ocean-vessel-only "
        "slice, with `thursday_date`/`cert_date_parsed` (typed dates) and "
        "numeric `metric_ton`/`pounds` added alongside the raw string columns.",
        "- No vessel matching, rollup/consolidation, or MRTIS cross-reference "
        "has happened yet -- `fgis_output` is one row per FGIS certificate "
        "line, exactly as published.",
        "",
    ]
    with open(report_path, "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Fetch and load FGIS export grain inspection data.")
    ap.add_argument("--start-year", type=int, default=2018)
    ap.add_argument("--end-year", type=int, default=datetime.now().year)
    ap.add_argument("--source-dir", default=os.path.join(PROJECT_ROOT, "fgis_source"))
    ap.add_argument("--db-path", default=os.path.join(PROJECT_ROOT, "data", "db", "mrtis.duckdb"))
    ap.add_argument("--report-path", default=os.path.join(PROJECT_ROOT, "docs", "FGIS_DATA_QUALITY.md"))
    ap.add_argument("--use-cache", action="store_true", help="Skip download; use files already in --source-dir")
    args = ap.parse_args()

    years = list(range(args.start_year, args.end_year + 1))
    print(f"Fetching FGIS export data for {years[0]}-{years[-1]}...")
    paths_by_year = {y: fetch_year(y, args.source_dir, args.use_cache) for y in years}

    print("Loading and sanitizing...")
    raw = build_raw(paths_by_year)
    output = build_output(raw)
    print(f"Built fgis_raw={len(raw):,} rows, fgis_output={len(output):,} rows")

    write_db(args.db_path, raw, output)
    print(f"Wrote tables to: {args.db_path}")

    write_report(args.report_path, raw, output, [y for y in years if paths_by_year[y]])
    print(f"Wrote data quality report: {args.report_path}")


if __name__ == "__main__":
    main()
