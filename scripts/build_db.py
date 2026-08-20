#!/usr/bin/env python3
"""
MRTIS build script -- ingest raw "Zone Report" CSV exports and (re)build the
DuckDB warehouse (dim_vessel, dim_agent, dim_zone, fact_zone_event).

Usage:
    python3 scripts/build_db.py
    python3 scripts/build_db.py --source-dir . --db-path data/db/mrtis.duckdb

Safe to re-run: it fully rebuilds the tables from the current set of source
CSVs each time (drop + recreate), so the database always reflects exactly
what's on disk in --source-dir. It does not mutate or move your source CSVs.

See docs/BUILD.md for the full pipeline explanation and docs/DATA_QUALITY.md
for the report this script produces on its most recent run.
"""

import argparse
import csv
import glob
import os
import sys
from datetime import datetime

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.parse import (
    canonical_imo,
    parse_draft,
    parse_mile,
    natural_vessel_key,
    classify_zone_group,
    normalize_vessel_name,
    imo_check_digit_valid,
    build_imo_repair_map,
)

RAW_COLUMNS = ["IMO", "Name", "Action", "Time", "Zone", "Agent", "Type", "Draft", "Mile"]

# Agency fee in USD, accrued on sailing (Depart) from a facility berth.
# Driven by the vessel, not the berth -- confirmed by William 2026-08-19 after
# comparing coverage: vessel-based reaches 90.5% of berth departures against
# 82.1% for berth-based, and follows the ship being agented rather than the
# dock. Bulk carriers are the higher tier; everything else (tanker, gas,
# container, cruise, reefer, and vessels with no recorded type) is the lower.
AGENCY_FEE_BULK = 10_500.0
AGENCY_FEE_OTHER = 3_500.0

# William's revised fee schedule (2026-08-19, §12 of OPEN_QUESTIONS.md) --
# six rules layered on top of the two-tier schedule above. R1-R4 key off the
# ships register's own ship_type, not vessel_type_canonical (§12.3.1: the
# register is authoritative -- these are register vocabulary values, and
# Ro-Ro Cargo Ship / Vehicles Carrier have no equivalent in the 7-value
# canonical vocabulary at all). R5 is priced by the leg's first berth, the
# only berth-dependent rule (§12.3.3), and outranks R1-R4 if a vessel could
# ever satisfy both (§12.3.3.3), though no vessel can today.
SHIP_TYPE_FEE_TIERS = {
    "Passenger/Cruise": 2_500.0,                                 # R1
    "Ro-Ro Cargo Ship": 1_000.0,                                 # R2
    "Vehicles Carrier": 1_000.0,                                 # R2
    # General Cargo Ship (with Ro-Ro facility) -- the nearest thing actually
    # in MRTIS traffic to R2's named types, neither of which appear (§12.3.2).
    # RULED, William, 2026-08-19: "a roro is a port call" -- covered by R2, not
    # left at the base tier. 6 vessels, 5 chargeable legs today ($52,500 at
    # the old $10,500 base tier -> $5,000 at R2).
    "General Cargo Ship (with Ro-Ro facility)": 1_000.0,         # R2
    "Container Ship (Fully Cellular)": 750.0,                    # R3
    "Container Ship (Fully Cellular/Ro-Ro Facility)": 750.0,     # R3
    "Refrigerated Cargo Ship": 5_000.0,                          # R4
}
# Consulted only when the vessel has no register row at all (§12.3.1's
# approved hybrid) -- the canonical equivalent for the three rules that have
# one. Ro-Ro Cargo Ship / Vehicles Carrier have no canonical equivalent and
# stay unreachable for a vessel with no register row, by design.
CANONICAL_FEE_FALLBACK = {
    "Passenger": 2_500.0,   # R1
    "Container": 750.0,     # R3
    "Reefer": 5_000.0,      # R4
}
AGENCY_FEE_BULK_GENERAL_CARGO = 5_000.0  # R5

# Facility types that are NOT a berth -- a vessel departing these has not
# worked cargo, so no agency fee accrues. Everything else in the zone
# dictionary is a real berth of some kind.
NON_BERTH_FACILITY_TYPES = {"Anchorage", "Pilot Station"}


def agency_fee_for(vessel_type_canonical, ship_type_group, imo, ship_type=None, facility_type=None,
                    apply_2026_tiers=False):
    """Agency fee in USD for a sailing by this vessel, or None if no fee accrues.

    `apply_2026_tiers=False` (the default) preserves the pre-§12 two-tier
    schedule exactly, byte-for-byte -- this is what the per-departure basis
    (`fact_zone_event.agency_fee`, built here in build_db.py) uses, frozen per
    William's ruling (2026-08-19, §12.3.4). Only `build_port_calls.py`'s
    leg-level fee passes `apply_2026_tiers=True` (with `ship_type` and
    `facility_type`), to price the six §12 rules first. This is an explicit
    flag rather than inferring "new tiers wanted" from ship_type being present,
    because build_db.py's call never passes ship_type at all -- inferring from
    its absence would silently apply §12's canonical fallback (Passenger /
    Container / Reefer) to the frozen basis too, which is exactly the bug this
    flag exists to prevent.

    Priority when apply_2026_tiers=True:
      0. R5: a dry bulk vessel calling a General Cargo facility at the leg's
         first berth. $5,000.
      1. R1-R4: the register's own ship_type against SHIP_TYPE_FEE_TIERS.
      1b. R1-R4 fallback (only when ship_type is blank -- no register row at
          all): the canonical equivalent, where one exists, against
          CANONICAL_FEE_FALLBACK.

    Priority always (the pre-§12 schedule, and the fallback below §12's rules):
      2. The Zone Report's own Type, canonicalized -- 'Bulk' is the higher tier,
         every other known type (tanker, gas, container, cruise, reefer) the lower.
      3. Where Type was never recorded for the vessel, fall back to the ships
         register: a 'Bulk Carrier-*' group is the higher tier. This recovers
         real Capesize/Kamsarmax bulkers that would otherwise be under-billed --
         the register knows them even though the Zone Report Type is blank.
      4. A vessel with NO usable IMO and no type from either source is not an
         ocean vessel being agented -- it is a tug, workboat, naval or
         government craft (observed: 'Usace Mat Sink Unit', 'French Warship',
         'Cg Eagle', 'Shop'). No fee accrues; agency_fee stays NULL.

    A vessel whose IMO merely fails its check digit still bills at the lower
    tier -- a corrupted IMO is a typo on a real ship, not the absence of one.
    """
    if apply_2026_tiers:
        has_ship_type = isinstance(ship_type, str) and bool(ship_type)
        if vessel_type_canonical == "Bulk" and facility_type == "General Cargo":
            return AGENCY_FEE_BULK_GENERAL_CARGO
        if has_ship_type and ship_type in SHIP_TYPE_FEE_TIERS:
            return SHIP_TYPE_FEE_TIERS[ship_type]
        if not has_ship_type and vessel_type_canonical in CANONICAL_FEE_FALLBACK:
            return CANONICAL_FEE_FALLBACK[vessel_type_canonical]

    if vessel_type_canonical == "Bulk":
        return AGENCY_FEE_BULK
    if isinstance(vessel_type_canonical, str) and vessel_type_canonical:
        return AGENCY_FEE_OTHER
    if isinstance(ship_type_group, str) and ship_type_group:
        return (AGENCY_FEE_BULK if ship_type_group.startswith("Bulk Carrier")
                else AGENCY_FEE_OTHER)
    if not isinstance(imo, str) or not imo:
        return None
    return AGENCY_FEE_OTHER
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


def find_source_files(source_dir: str, pattern: str):
    files = sorted(glob.glob(os.path.join(source_dir, pattern)))
    if not files:
        raise SystemExit(
            f"No files matching '{pattern}' found in {source_dir}. "
            "Nothing to build."
        )
    return files


def load_raw(files):
    """Load and concatenate all source CSVs, tagging each row with its
    originating file for lineage."""
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        missing = set(RAW_COLUMNS) - set(df.columns)
        if missing:
            raise SystemExit(f"{f}: missing expected columns {missing}")
        df["source_file"] = os.path.basename(f)
        frames.append(df[RAW_COLUMNS + ["source_file"]])
    return pd.concat(frames, ignore_index=True)


def load_zone_facility(path):
    """raw zone name -> facility_type, from William's hand-built zone
    dictionary. Authoritative, unlike the classify_zone_group() heuristic.
    Returns {} if absent so the build still runs without it."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {
            r["raw_zone"].strip(): r["facility_type"].strip()
            for r in csv.DictReader(f)
            if r.get("raw_zone", "").strip() and r.get("facility_type", "").strip()
        }


def load_dredge_exclusions(path):
    """Vessels marked exclude_as_dredge=Y -- dredges and high-frequency
    workboats whose movements are noise rather than cargo traffic.

    Returns (imos, names_without_imo). Matching by IMO wherever the dictionary
    supplies one matters: 'Texas Star' is BOTH a dredge (imo_raw 311000000)
    and, separately, a real tanker (9256860). Excluding on name alone deletes
    the tanker too. Names are used only for the entries that genuinely have no
    IMO (Allisonk, Allins K, Keeneland, Ginny Lab), and even then only against
    rows that carry no valid IMO themselves -- so a real IMO-bearing vessel
    sharing one of those names still survives."""
    imos, names = set(), set()
    if not os.path.exists(path):
        return imos, names
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("exclude_as_dredge", "").strip().upper() != "Y":
                continue
            name = r.get("vessel_name", "").strip()
            if not name:
                continue
            imo = canonical_imo(r.get("imo_raw", "").strip())
            if imo:
                imos.add(imo)
            else:
                names.add(normalize_vessel_name(name))
    return imos, names


def transform(raw: pd.DataFrame, vessel_type_map: dict = None, dredge_exclusions=None):
    """Parse raw string fields into typed columns and dedupe."""
    df = raw.copy()

    df["event_time"] = pd.to_datetime(df["Time"], format="%m/%d/%Y %H:%M", errors="coerce")
    bad_time = df["event_time"].isna().sum()

    df["draft_ft"] = df["Draft"].apply(parse_draft)
    df["mile_marker"] = df["Mile"].apply(parse_mile)
    df["imo_raw"] = df["IMO"].str.strip()
    df["imo_canonical"] = df["imo_raw"].apply(canonical_imo)
    df["imo_valid"] = df["imo_canonical"].notna()
    df["vessel_name"] = df["Name"].str.strip()
    df["name_normalized"] = df["vessel_name"].apply(normalize_vessel_name)

    # Drop dredge/workboat noise entirely rather than flagging it. These are
    # high-frequency non-cargo movers (Mack B alone has ~9,700 events) that
    # crowd out real cargo traffic in every rollup. Removing them at ingest
    # focuses the table; the per-vessel counts are reported so it stays
    # visible, and --keep-dredges restores them without editing the dictionary.
    dredge_imos, dredge_names = dredge_exclusions or (set(), set())
    dredge_dropped = {}
    name_only_review = []
    if dredge_imos or dredge_names:
        # By IMO where the dictionary has one; by name ONLY for entries with no
        # IMO, and then only against rows that themselves carry no valid IMO.
        is_dredge_by_name = df["imo_canonical"].isna() & df["name_normalized"].isin(dredge_names)
        is_dredge = df["imo_canonical"].isin(dredge_imos) | is_dredge_by_name
        if is_dredge.any():
            dredge_dropped = (
                df.loc[is_dredge, "vessel_name"].value_counts().to_dict()
            )
            # The name-only path is the one with real ambiguity risk (William,
            # 2026-08-19, OPEN_QUESTIONS §7.3): a genuinely different real
            # vessel can share an exact name with a no-IMO dredge entry -- T
            # Jungfrau, Heino and Corinthian all turned out to be real river
            # transits (Enter -> berth -> Exit), not noise, deleted only
            # because their name collided with one. Rather than delete these
            # silently, summarise what was dropped per name so a false
            # positive can be spotted and pulled back in by hand -- see
            # dictionaries/dredge_name_only_review.csv. The IMO-matched path
            # is not reviewed here: IMO is a deterministic key, so it carries
            # none of this ambiguity.
            if is_dredge_by_name.any():
                nb = df.loc[is_dredge_by_name, ["vessel_name", "IMO", "Action", "event_time"]]
                for name, g in nb.groupby("vessel_name"):
                    name_only_review.append({
                        "vessel_name": name,
                        "record_count": len(g),
                        "first_seen": g["event_time"].min(),
                        "last_seen": g["event_time"].max(),
                        "distinct_imo_raw_values": ", ".join(
                            sorted(v for v in g["IMO"].astype(str).unique() if v)) or None,
                        "forms_complete_enter_exit_transit": bool(
                            (g["Action"] == "Enter").any() and (g["Action"] == "Exit").any()),
                    })
            df = df[~is_dredge]

    # Repair IMOs corrupted in the source before they become vessel identity.
    # A bad check digit is not a real vessel: left alone it forks one ship into
    # two (Spring Aura 9991064 stole two events from the middle of 9991082's
    # single Zen-Noh loading, making one ship look like two at the same
    # elevator on the same day). Guarded against merging genuinely different
    # vessels that share a name -- see parse.build_imo_repair_map.
    vessel_type_map = vessel_type_map or {}
    # raw "Type" column -- df["vessel_type"] isn't derived until further down
    df["_canon_type"] = df["Type"].map(
        lambda t: vessel_type_map.get(str(t).strip()) if t and str(t).strip() else None
    )
    imo_repair = build_imo_repair_map(
        zip(df["imo_canonical"], df["name_normalized"], df["_canon_type"]),
        excluded_names=dredge_names,
    )
    repaired_rows = int(df["imo_canonical"].isin(imo_repair).sum())
    df["imo_canonical"] = df["imo_canonical"].map(
        lambda i: imo_repair.get(i, i) if isinstance(i, str) and i else i
    )
    df = df.drop(columns=["_canon_type"])

    # NB: guard with isinstance, not truthiness. pandas hands NaN through for
    # missing values, and bool(nan) is True -- an unguarded truth test lets NaN
    # become the vessel key, which groupby() then silently DROPS, orphaning
    # those rows from dim_vessel with a NULL vessel_key.
    df["natural_vessel_key"] = df.apply(
        lambda r: r["imo_canonical"]
        if isinstance(r["imo_canonical"], str) and r["imo_canonical"]
        else "NONAME:" + (r["vessel_name"] or "").strip().upper(),
        axis=1,
    )
    df["agent_name"] = df["Agent"].str.strip()
    df["agent_name"] = df["agent_name"].replace("", None)
    df["zone_name"] = df["Zone"].str.strip()
    df["action"] = df["Action"].str.strip()
    df["vessel_type"] = df["Type"].str.strip().replace("", None)

    before = len(df)
    dedup_cols = ["natural_vessel_key", "action", "Time", "zone_name"]
    df = df.drop_duplicates(subset=dedup_cols, keep="first")
    dupes_dropped = before - len(df)

    stats = {
        "rows_in": before,
        "rows_after_dedup": len(df),
        "duplicates_dropped": dupes_dropped,
        "bad_timestamps": int(bad_time),
        "dredge_rows_dropped": sum(dredge_dropped.values()),
        "dredge_dropped_detail": dredge_dropped,
        "name_only_dropped_review": name_only_review,
        "imo_repairs": len(imo_repair),
        "imo_repaired_rows": repaired_rows,
        "imo_repair_map": imo_repair,
    }
    return df, stats


def load_ships_register(path):
    """Reference fleet data (Sea-web, via the William's separate Ships_Register
    project) to enrich dim_vessel by canonical IMO. Returns an empty frame
    with the right columns if the dictionary file isn't present, so the
    build still runs without it."""
    cols = ["imo", "ship_type", "ship_type_group", "dwt", "tpc"]
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    reg = pd.read_csv(path, dtype=str, keep_default_na=False)
    reg = reg.rename(columns={"ship_type_ref": "ship_type"})
    reg = reg[["imo", "ship_type", "ship_type_group", "dwt", "tpc"]].copy()
    reg["dwt"] = pd.to_numeric(reg["dwt"], errors="coerce")
    reg["tpc"] = pd.to_numeric(reg["tpc"], errors="coerce")
    reg["ship_type"] = reg["ship_type"].replace("", None)
    reg["ship_type_group"] = reg["ship_type_group"].replace("", None)
    # William, 2026-08-19: some register families carry no size vocabulary
    # (Cement Carrier, Aggregates Carrier, self-discharging Lakers, ...), so
    # ship_type_group comes back blank even though the vessel's type is known.
    # Carry ship_type over into the gap rather than leave it NULL -- a gap is
    # worse than a variance in convention. ship_type itself is untouched.
    reg["ship_type_group"] = reg["ship_type_group"].fillna(reg["ship_type"])
    # imo is the join key -- fleet_joined has no duplicate IMOs (verified),
    # but guard anyway: keep first if a future export ever has one.
    reg = reg.drop_duplicates(subset=["imo"], keep="first")
    return reg


def load_vessel_type_dict(path):
    """raw Type -> canonical vessel_type (dictionaries/vessel_type.csv).
    Returns {} if absent so the build still runs without it."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {
            r["raw_type"].strip(): r["vessel_type"].strip()
            for r in csv.DictReader(f)
            if r.get("raw_type", "").strip() and r.get("vessel_type", "").strip()
        }


def build_dims_and_fact(df: pd.DataFrame, ships_register: pd.DataFrame,
                        vessel_type_map: dict, zone_facility: dict = None):
    # --- dim_vessel ---
    vessel_groups = df.groupby("natural_vessel_key")
    vessel_rows = []
    for key, g in vessel_groups:
        g_sorted = g.sort_values("event_time")
        most_common_type = (
            g["vessel_type"].dropna().mode().iloc[0]
            if not g["vessel_type"].dropna().empty
            else None
        )
        vessel_rows.append(
            {
                "natural_key": key,
                "imo_raw": g_sorted["imo_raw"].iloc[-1],
                "imo": g_sorted["imo_canonical"].iloc[-1],
                "imo_valid": bool(g_sorted["imo_valid"].iloc[-1]),
                "vessel_name": g_sorted["vessel_name"].iloc[-1],
                "first_seen": g["event_time"].min(),
                "last_seen": g["event_time"].max(),
                "most_common_type": most_common_type,
            }
        )
    dim_vessel = pd.DataFrame(vessel_rows).reset_index(drop=True)

    # Enrich by canonical IMO from the ships register. Left join: no match
    # -> ship_type_group/dwt/tpc stay NULL, per spec ("if no match, ignore").
    dim_vessel = dim_vessel.merge(
        ships_register, on="imo", how="left", validate="many_to_one"
    )

    # Canonical vessel type from the dictionary. Blank source Type stays NULL
    # rather than being guessed -- 21% of raw rows have no Type at all, and an
    # unknown type must not be mistaken for a known-safe one downstream.
    dim_vessel["vessel_type_canonical"] = dim_vessel["most_common_type"].map(
        lambda t: vessel_type_map.get(str(t).strip()) if t and str(t).strip() else None
    )

    dim_vessel["imo_check_valid"] = dim_vessel["imo"].apply(
        lambda i: imo_check_digit_valid(i) if i else None
    )

    dim_vessel.insert(0, "vessel_key", dim_vessel.index + 1)

    # --- dim_agent ---
    agent_names = sorted(df["agent_name"].dropna().unique())
    agent_rows = []
    for name in agent_names:
        g = df[df["agent_name"] == name]
        agent_rows.append(
            {
                "agent_name": name,
                "first_seen": g["event_time"].min(),
                "last_seen": g["event_time"].max(),
            }
        )
    dim_agent = pd.DataFrame(agent_rows).reset_index(drop=True)
    dim_agent.insert(0, "agent_key", dim_agent.index + 1)

    # --- dim_zone ---
    zone_names = sorted(df["zone_name"].dropna().unique())
    zone_facility = zone_facility or {}
    dim_zone = pd.DataFrame(
        {
            "zone_name": zone_names,
            "zone_group": [classify_zone_group(z) for z in zone_names],
            "facility_type": [zone_facility.get(z) for z in zone_names],
        }
    ).reset_index(drop=True)
    dim_zone.insert(0, "zone_key", dim_zone.index + 1)

    # --- fact_zone_event ---
    fact = df.merge(
        dim_vessel[["vessel_key", "natural_key"]],
        left_on="natural_vessel_key",
        right_on="natural_key",
        how="left",
    )
    fact = fact.merge(
        dim_agent[["agent_key", "agent_name"]], on="agent_name", how="left"
    )
    fact = fact.merge(
        dim_zone[["zone_key", "zone_name", "facility_type"]], on="zone_name", how="left"
    )
    fact = fact.merge(
        dim_vessel[["vessel_key", "vessel_type_canonical", "ship_type_group", "imo"]],
        on="vessel_key", how="left",
    )

    # --- agency fee ---
    # Accrues on SAILING from a facility berth only. NULL elsewhere (arrivals,
    # anchorage/pilot-station departures) so SUM(agency_fee) over any slice is
    # the fee earned on it without further filtering.
    is_berth_sailing = (
        (fact["action"] == "Depart")
        & fact["facility_type"].notna()
        & ~fact["facility_type"].isin(NON_BERTH_FACILITY_TYPES)
    )
    fact["agency_fee"] = pd.Series(
        [
            agency_fee_for(vt, stg, imo)
            for vt, stg, imo in zip(
                fact["vessel_type_canonical"], fact["ship_type_group"], fact["imo"]
            )
        ],
        index=fact.index,
    ).where(is_berth_sailing)

    fact = fact.reset_index(drop=True)
    fact.insert(0, "event_key", fact.index + 1)
    fact_cols = [
        "event_key",
        "vessel_key",
        "agent_key",
        "zone_key",
        "action",
        "event_time",
        "vessel_type",
        "draft_ft",
        "mile_marker",
        "source_file",
        "agency_fee",
    ]
    fact_zone_event = fact[fact_cols]

    # --- dim_vessel_name_alias ---
    # Every distinct name spelling ever seen for each vessel, not just the
    # latest. dim_vessel keeps only the most recent name, but ~12% of vessels
    # here are renamed mid-history (IMO 9397456: 'Hellas Explorer' 2019 ->
    # 'Alithini II' 2022), so any by-name match against dim_vessel alone would
    # silently miss every record predating the rename. FGIS matching
    # (scripts/build_fgis_match.py) resolves against this table instead.
    alias = (
        df.groupby(["natural_vessel_key", "vessel_name"])
        .agg(
            first_seen=("event_time", "min"),
            last_seen=("event_time", "max"),
            event_count=("event_time", "size"),
        )
        .reset_index()
    )
    alias = alias.merge(
        dim_vessel[["vessel_key", "natural_key"]],
        left_on="natural_vessel_key",
        right_on="natural_key",
        how="left",
    )
    alias["name_normalized"] = alias["vessel_name"].apply(normalize_vessel_name)
    # A vessel whose name only varied by punctuation/spacing collapses to one
    # normalized alias -- keep the most-seen spelling as its representative.
    alias = alias.sort_values(
        ["vessel_key", "name_normalized", "event_count"], ascending=[True, True, False]
    )
    alias = alias.drop_duplicates(subset=["vessel_key", "name_normalized"], keep="first")
    alias = alias[alias["name_normalized"] != ""]
    alias = alias.reset_index(drop=True)
    alias.insert(0, "alias_key", alias.index + 1)
    dim_vessel_name_alias = alias[
        [
            "alias_key",
            "vessel_key",
            "vessel_name",
            "name_normalized",
            "first_seen",
            "last_seen",
            "event_count",
        ]
    ]

    return dim_vessel, dim_agent, dim_zone, fact_zone_event, dim_vessel_name_alias


def write_db(db_path, schema_sql_path, dim_vessel, dim_agent, dim_zone, fact_zone_event,
             dim_vessel_name_alias):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    with open(schema_sql_path) as f:
        schema_sql = f.read()

    # Rebuilding the core tables reassigns every event_key/vessel_key (they're
    # row-index based), which invalidates any FGIS cross-reference built off
    # the previous build. Drop the FGIS match layer here rather than leaving
    # it silently pointing at keys that have moved -- re-run
    # scripts/build_fgis_match.py afterwards to rebuild it. fgis_raw/
    # fgis_output are untouched: they key off FGIS's own data, not ours.
    #
    # The port call assembly layer is built off the same keys and is dropped for
    # exactly the same reason -- a stale port_call_event pointing at reassigned
    # event_keys would look fine and be wrong in every row.
    had_fgis_match = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'fgis_record'"
    ).fetchone()[0] > 0
    had_port_calls = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'port_call'"
    ).fetchone()[0] > 0
    con.execute("DROP TABLE IF EXISTS port_call_event")
    con.execute("DROP TABLE IF EXISTS port_call_leg")
    con.execute("DROP TABLE IF EXISTS port_call")
    con.execute("DROP TABLE IF EXISTS fgis_record_line")
    con.execute("DROP TABLE IF EXISTS fgis_record")

    con.execute("DROP TABLE IF EXISTS fact_zone_event")
    con.execute("DROP TABLE IF EXISTS dim_vessel_name_alias")
    con.execute("DROP TABLE IF EXISTS dim_vessel")
    con.execute("DROP TABLE IF EXISTS dim_agent")
    con.execute("DROP TABLE IF EXISTS dim_zone")
    con.execute(schema_sql)

    con.register("dim_vessel_df", dim_vessel)
    con.execute(
        "INSERT INTO dim_vessel SELECT vessel_key, imo_raw, imo, imo_valid, vessel_name, "
        "natural_key, first_seen, last_seen, most_common_type, vessel_type_canonical, "
        "imo_check_valid, ship_type, ship_type_group, dwt, tpc FROM dim_vessel_df"
    )
    con.register("dim_agent_df", dim_agent)
    con.execute(
        "INSERT INTO dim_agent SELECT agent_key, agent_name, first_seen, last_seen FROM dim_agent_df"
    )
    con.register("dim_zone_df", dim_zone)
    con.execute("INSERT INTO dim_zone SELECT zone_key, zone_name, zone_group, facility_type FROM dim_zone_df")
    con.register("fact_df", fact_zone_event)
    con.execute(
        "INSERT INTO fact_zone_event SELECT event_key, vessel_key, agent_key, zone_key, "
        "action, event_time, vessel_type, draft_ft, mile_marker, source_file, "
        "NULL AS fgis_record_id, NULL AS fgis_record_count, agency_fee FROM fact_df"
    )
    con.register("alias_df", dim_vessel_name_alias)
    con.execute(
        "INSERT INTO dim_vessel_name_alias SELECT alias_key, vessel_key, vessel_name, "
        "name_normalized, first_seen, last_seen, event_count FROM alias_df"
    )
    con.close()
    return had_fgis_match, had_port_calls


def write_report(report_path, files, stats, dim_vessel, dim_agent, dim_zone, fact_zone_event,
                 dim_vessel_name_alias):
    fee = fact_zone_event[fact_zone_event["agency_fee"].notna()]
    _berth_sailings = fact_zone_event[
        (fact_zone_event["action"] == "Depart")
        & fact_zone_event["agency_fee"].isna()
        & fact_zone_event["zone_key"].isin(
            dim_zone.loc[
                ~dim_zone["facility_type"].isin(NON_BERTH_FACILITY_TYPES)
                & dim_zone["facility_type"].notna(),
                "zone_key",
            ]
        )
    ]
    _unpriced = len(_berth_sailings)
    n_vessels = len(dim_vessel)
    n_invalid_imo = (~dim_vessel["imo_valid"]).sum()
    n_agents = len(dim_agent)
    n_zones = len(dim_zone)
    n_events = len(fact_zone_event)
    n_blank_agent = fact_zone_event["agent_key"].isna().sum()
    date_min = fact_zone_event["event_time"].min()
    date_max = fact_zone_event["event_time"].max()
    n_valid_imo_vessels = dim_vessel["imo_valid"].sum()
    # dwt has 0 nulls in the ships register source itself, so it's a cleaner
    # "did this IMO match at all" signal than ship_type_group (which does
    # have some nulls upstream even for matched IMOs).
    n_register_matched = dim_vessel["dwt"].notna().sum()
    n_renamed = int((dim_vessel_name_alias.groupby("vessel_key").size() > 1).sum())

    lines = [
        "# MRTIS Data Quality Report",
        "",
        f"_Generated by `scripts/build_db.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        f"- Source files processed: {len(files)}",
        f"- Raw rows read: {stats['rows_in']:,}",
        f"- Duplicate rows dropped: {stats['duplicates_dropped']:,}",
        f"- Dredge/noise rows filtered out: {stats['dredge_rows_dropped']:,} "
        f"across {len(stats['dredge_dropped_detail'])} vessels "
        "(`dictionaries/dredge_exclusions.csv`, `exclude_as_dredge=Y`) -- these are "
        "high-frequency workboats/dredges removed at ingest so they don't crowd out "
        "real cargo traffic. Re-run with `--keep-dredges` to retain them."
        + ("".join(f"\n  - {k}: {v:,} rows" for k, v in
                   sorted(stats["dredge_dropped_detail"].items(), key=lambda x: -x[1]))),
        f"- Corrupted IMOs repaired (failed check digit, merged into the one "
        f"same-name valid-IMO vessel): {stats['imo_repairs']}, affecting "
        f"{stats['imo_repaired_rows']:,} rows"
        + ("".join(f"\n  - {b} -> {g}" for b, g in sorted(stats["imo_repair_map"].items()))),
        f"- Rows with unparseable timestamp (dropped by pandas as NaT, still loaded but event_time is NULL): {stats['bad_timestamps']:,}",
        f"- Final fact_zone_event rows: {n_events:,}",
        f"- Date range covered: {date_min} to {date_max}",
        "",
        "## Dimensions",
        "",
        f"- Distinct vessels (dim_vessel): {n_vessels:,}",
        f"  - Of which non-standard/missing IMO (grouped by name instead): {n_invalid_imo:,} ({n_invalid_imo / n_vessels:.1%})",
        f"- Distinct agents (dim_agent): {n_agents:,}",
        f"- Distinct zones (dim_zone): {n_zones:,}",
        f"- Vessel name aliases (dim_vessel_name_alias): {len(dim_vessel_name_alias):,} "
        f"across {n_vessels:,} vessels; {n_renamed:,} vessels ({n_renamed / n_vessels:.1%}) "
        "carry more than one distinct name spelling over the covered period "
        "(genuine renames, e.g. IMO 9397456 'Hellas Explorer' -> 'Alithini II'). "
        "`dim_vessel.vessel_name` holds only the latest, so match external "
        "sources by name against the alias table, not dim_vessel.",
        "",
        "## Known data quality notes",
        "",
        f"- {n_blank_agent:,} events ({n_blank_agent / n_events:.1%}) have no Agent in the source data "
        "and are loaded with agent_key = NULL. **This is not random**: in the raw feed, "
        "rows carrying a clean 7-digit IMO are missing an Agent only 1.8% of the time, "
        "while rows with a malformed IMO (8/9-digit, 3/4-digit or blank) are missing one "
        "~99% of the time. 8/9-digit rows are 4.7% of the feed but carry 49% of every "
        "blank Agent -- IMO, Agent and Type go missing together, which points at one "
        "defective input path rather than scattered omissions. `Nordic Aki`/`Bonita Aki` "
        "(IMO 9505974) is the clean example: 52 rows with the 7-digit IMO all carry "
        "Type=Tank and Agent=General Maritime, while all 713 rows with the 9-digit "
        "variant carry neither.",
        "- Vessels without a standard 7-digit IMO (tugs, barges, and some entry variance) are "
        "identified by name instead of IMO. If the same name is reused for genuinely different "
        "vessels over time, they will be merged into one dim_vessel row -- worth spot-checking "
        "before relying on long-run history for name-only vessels.",
        "- `dim_zone.zone_group` is a heuristic string-match classification (see "
        "`scripts/lib/parse.py::classify_zone_group`), not an authoritative taxonomy. Treat it as "
        "a starting point for rollups, not ground truth.",
        "",
        "## Agency fees",
        "",
        f"- Chargeable sailings (a `Depart` from a facility berth -- not an anchorage "
        f"or pilot station): {len(fee):,}",
        f"- Total agency fees accrued: ${fee['agency_fee'].sum():,.0f}",
        f"- Berth sailings accruing no fee: {_unpriced:,} (see the note below)",
        "- Rate is set by the **vessel**, not the berth: "
        f"`vessel_type_canonical = 'Bulk'` -> ${AGENCY_FEE_BULK:,.0f}, everything else "
        f"-> ${AGENCY_FEE_OTHER:,.0f}. Vessel-based was chosen over berth-based on "
        "coverage (90.5% vs 82.1% of berth departures before the catch-all tier) and "
        "because it follows the ship being agented rather than the dock.",
        "",
        "| Vessel type | Rate | Sailings | Fees |",
        "|---|---|---|---|",
    ]
    _by_type = (
        fee.merge(dim_vessel[["vessel_key", "vessel_type_canonical"]], on="vessel_key", how="left")
        .assign(vt=lambda d: d["vessel_type_canonical"].fillna("(no type recorded)"))
        .groupby("vt")["agency_fee"].agg(["count", "sum"])
        .sort_values("sum", ascending=False)
    )
    for vt, row in _by_type.iterrows():
        lines.append(f"| {vt} | ${row['count'] and fee['agency_fee'].max() if False else ''}"
                     f"{AGENCY_FEE_BULK if vt == 'Bulk' else AGENCY_FEE_OTHER:,.0f} "
                     f"| {int(row['count']):,} | ${row['sum']:,.0f} |")
    lines += [
        "",
        "- The catch-all $3,500 tier absorbs every vessel type that is neither bulk "
        "nor liquid -- container, cruise/passenger, reefer.",
        "- Where the Zone Report never recorded a Type, the rate falls back to the "
        "ships register (`ship_type_group LIKE 'Bulk Carrier%'` -> the higher tier), "
        "which recovers real Capesize/Kamsarmax bulkers that would otherwise be "
        "under-billed.",
        f"- **{_unpriced:,} berth sailings accrue no fee at all** (`agency_fee` NULL): "
        "vessels with no usable IMO and no type from either source. These are tugs, "
        "workboats and government craft -- Dixie Raider, Jesse A Mollineaux, "
        "Usace Mat Sink Unit, French Warship, Cg Eagle, 'Shop' -- not ocean vessels "
        "being agented. NULL here means *no fee accrues*, not *unknown*.",
        "",
        "## Ships register enrichment",
        "",
        f"- {n_valid_imo_vessels:,} of {n_vessels:,} vessels ({n_valid_imo_vessels / n_vessels:.1%}) have a "
        "canonical 7-digit IMO to match against the ships register.",
        f"- {n_register_matched:,} vessels ({n_register_matched / n_vessels:.1%} of all vessels, "
        f"{n_register_matched / n_valid_imo_vessels:.1%} of those with a valid IMO) matched and got "
        "ship_type_group/dwt/tpc filled in. Everyone else has these columns NULL -- no match, "
        "no guess, per spec.",
        "",
    ]
    with open(report_path, "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Build/refresh the MRTIS DuckDB warehouse.")
    ap.add_argument("--source-dir", default=PROJECT_ROOT, help="Directory containing 'Zone Report*.csv' files (default: project root)")
    ap.add_argument("--pattern", default="Zone Report*.csv", help="Glob pattern for source CSVs")
    ap.add_argument("--db-path", default=os.path.join(PROJECT_ROOT, "data", "db", "mrtis.duckdb"))
    ap.add_argument("--report-path", default=os.path.join(PROJECT_ROOT, "docs", "DATA_QUALITY.md"))
    ap.add_argument("--schema-path", default=os.path.join(PROJECT_ROOT, "sql", "schema.sql"))
    ap.add_argument("--ships-register-path", default=os.path.join(PROJECT_ROOT, "dictionaries", "ships_register_fleet.csv"))
    ap.add_argument("--vessel-type-path", default=os.path.join(PROJECT_ROOT, "dictionaries", "vessel_type.csv"))
    ap.add_argument("--dredge-path", default=os.path.join(PROJECT_ROOT, "dictionaries", "dredge_exclusions.csv"))
    ap.add_argument("--dredge-review-path",
                    default=os.path.join(PROJECT_ROOT, "dictionaries", "dredge_name_only_review.csv"))
    ap.add_argument("--zone-facility-path", default=os.path.join(PROJECT_ROOT, "dictionaries", "zone_facility.csv"))
    ap.add_argument("--keep-dredges", action="store_true",
                    help="Keep vessels marked exclude_as_dredge=Y instead of filtering them out")
    args = ap.parse_args()

    files = find_source_files(args.source_dir, args.pattern)
    print(f"Found {len(files)} source file(s) in {args.source_dir}")

    raw = load_raw(files)
    vessel_type_map = load_vessel_type_dict(args.vessel_type_path)
    dredge_exclusions = ((set(), set()) if args.keep_dredges
                         else load_dredge_exclusions(args.dredge_path))
    print(f"Loaded {len(vessel_type_map)} vessel-type mappings, "
          f"{len(dredge_exclusions[0])} dredge exclusions by IMO + "
          f"{len(dredge_exclusions[1])} by name")
    df, stats = transform(raw, vessel_type_map, dredge_exclusions)
    print(f"Loaded {stats['rows_in']:,} rows, {stats['duplicates_dropped']:,} duplicates dropped, "
          f"{stats['bad_timestamps']:,} unparseable timestamps.")
    if stats["dredge_rows_dropped"]:
        print(f"Filtered out {stats['dredge_rows_dropped']:,} dredge/noise rows "
              f"({len(stats['dredge_dropped_detail'])} vessels): "
              + ", ".join(f"{k} ({v:,})" for k, v in
                          sorted(stats["dredge_dropped_detail"].items(),
                                 key=lambda x: -x[1])))
    review = stats["name_only_dropped_review"]
    if review:
        with open(args.dredge_review_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "vessel_name", "record_count", "first_seen", "last_seen",
                "distinct_imo_raw_values", "forms_complete_enter_exit_transit"])
            w.writeheader()
            w.writerows(sorted(review, key=lambda r: -r["record_count"]))
        complete = sum(1 for r in review if r["forms_complete_enter_exit_transit"])
        print(f"Wrote {len(review)} name-only dredge exclusion(s) for review "
              f"({complete} form a complete Enter->Exit transit -- check these first) "
              f"to {args.dredge_review_path}")
    if stats["imo_repairs"]:
        print(f"Repaired {stats['imo_repairs']} corrupted IMO(s) affecting "
              f"{stats['imo_repaired_rows']:,} rows:")
        for bad, good in sorted(stats["imo_repair_map"].items()):
            print(f"    {bad} -> {good}")

    ships_register = load_ships_register(args.ships_register_path)
    print(f"Loaded {len(ships_register):,} ships register reference rows from {args.ships_register_path}")

    (dim_vessel, dim_agent, dim_zone, fact_zone_event,
     dim_vessel_name_alias) = build_dims_and_fact(df, ships_register, vessel_type_map,
                                                  load_zone_facility(args.zone_facility_path))
    print(f"Built dim_vessel={len(dim_vessel):,} dim_agent={len(dim_agent):,} "
          f"dim_zone={len(dim_zone):,} fact_zone_event={len(fact_zone_event):,} "
          f"dim_vessel_name_alias={len(dim_vessel_name_alias):,}")

    had_fgis_match, had_port_calls = write_db(args.db_path, args.schema_path, dim_vessel, dim_agent,
                              dim_zone, fact_zone_event, dim_vessel_name_alias)
    print(f"Wrote database: {args.db_path}")

    write_report(args.report_path, files, stats, dim_vessel, dim_agent, dim_zone,
                 fact_zone_event, dim_vessel_name_alias)
    print(f"Wrote data quality report: {args.report_path}")

    if had_fgis_match or had_port_calls:
        print("\nNOTE: layers built on event_key/vessel_key were dropped -- this rebuild "
              "reassigned those keys.")
        if had_fgis_match:
            print("      Re-run: python3 scripts/build_fgis_match.py   (FGIS cross-reference)")
        if had_port_calls:
            print("      Re-run: python3 scripts/build_port_calls.py   (port call assembly)")


if __name__ == "__main__":
    main()
