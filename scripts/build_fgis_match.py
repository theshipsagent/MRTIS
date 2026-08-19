#!/usr/bin/env python3
"""
MRTIS / FGIS matching, consolidation, and cross-reference.

Third and final stage of the FGIS integration, implementing
docs/FGIS_MATCH_SPEC.md. Run it AFTER scripts/build_db.py (core warehouse)
and scripts/build_fgis.py (raw FGIS ingest):

    python3 scripts/build_db.py
    python3 scripts/build_fgis.py
    python3 scripts/build_fgis_match.py

What it does:

 1. RESOLVE -- turn each FGIS `carrier_name` (free text, no IMO) into an
    MRTIS vessel_key, by narrowing to vessels that departed an Elevator or
    Mid-Stream berth near the Cert Date and then matching on a
    punctuation-normalized name.
 2. CONSOLIDATE -- roll the several certificate lines of one loading up
    into a single record per (vessel, Cert Date), concatenating
    grain/class/destination and summing metric tons, under a new
    `fgis_record_id`.
 3. CROSS-REFERENCE -- write the link both ways: `mrtis_event_key` onto the
    consolidated FGIS record, `fgis_record_id` onto the matching
    `fact_zone_event` row.

Safe to re-run: full rebuild of fgis_record/fgis_record_line each time, same
philosophy as the other two scripts. It never mutates fgis_raw/fgis_output.

See docs/FGIS_MATCH_SPEC.md for the design rationale and
docs/FGIS_MATCH_QUALITY.md for the report this produces.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.parse import normalize_vessel_name

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Facility types that can load grain onto an ocean vessel. Per the spec, we
# deliberately do NOT additionally filter on the zone dictionary's
# `Cargo group` tag: real Statements of Fact show Mid-Stream buoy locations
# handling cargo they aren't tagged for, so the tag reflects typical rather
# than exclusive cargo. The FGIS certificate is itself the evidence that
# grain moved through the stop.
GRAIN_FACILITY_TYPES = ("Elevator", "Mid-Stream")

# The berth-departure action. NOTE: the spec originally said "Exit", but in
# this data `Enter`/`Exit` occur *only* at Pilot Station zones (the SWP river
# entry/exit). Every berth-type zone -- Elevator and Mid-Stream included --
# records Arrive/Depart, so "Exit" would match exactly zero rows. Confirmed
# across all 314,089 events; corrected here and in the spec.
DEPART_ACTION = "Depart"

# Fallback anchor. Per William 2026-08-18: for a grain call the arrival at the
# elevator / mid-stream rig is a valid anchor, and the berth stay from arrival
# to sailing is "about +/- 4 days or less". Confirmed against the 11,678
# matches that have a prior arrival at the same berth: 90.7% of stays are <= 4
# days (median 2). Used ONLY when no qualifying sailing exists -- the sailing
# is always the correct anchor when MRTIS recorded one.
ARRIVE_ACTION = "Arrive"

# Canonical vessel types excluded from FGIS matching entirely. Per William
# 2026-08-18: "any of the vessels identified as tanker or gas in the vessel
# type canonical column do not need to be considered... what we are solving
# for is only dry bulk cargo."
#
# This scopes MATCHING only -- it is not a filter on the warehouse. Tanker and
# gas vessels keep every one of their zone events and still get their port
# call built (Enter SWP -> Exit SWP, including the berths in between); they
# are simply never candidates for a grain certificate.
#
# As of 2026-08-18 this changes nothing: zero matched records were Tanker or
# Gas, and those types are only 0.2% of the grain-berth candidate pool (79
# events, all Tanker). It is an explicit guard so a future tanker sharing a
# bulker's name cannot slip through, rather than relying on the date/zone
# narrowing to keep excluding them by luck.
EXCLUDED_VESSEL_TYPES = ("Tanker", "Gas")

# Candidate window around Cert Date, in days. Asymmetric and forward-leaning
# because the physics is asymmetric: the certificate is issued when loading
# completes, and the vessel then sails the same day, the next day, or a few
# days later. It essentially never sails *before* the cert is issued -- so
# the single day backwards is only slack for clock/calendar rollover.
#
# Widened +2 -> +3 (2026-08-18), then +3 -> +4 (2026-08-19). The +3/+4 cases
# are flat across all seven weekdays (0.5-1.2%), so they are ordinary sailing
# delay rather than a weekend/paperwork artifact.
#
# +4 is the optimum, measured against the full history with the arrival
# fallback in place. Because the fallback already absorbs most late sailings,
# the gain from widening is not volume but ANCHOR QUALITY -- +4 moves 49
# records off an arrival anchor and onto the sailing, which is the correct
# anchor, at no cost:
#
#   window   matched   on sailing   on fallback   ambiguous
#   -1/+3     12,433      12,273           160        1
#   -1/+4     12,434      12,322           112        1   <- optimum
#   -1/+5     12,433      12,334            99        2   <- a match tips
#   -1/+6     12,433      12,342            91        2      into ambiguity
#
# Past +4 the total stops improving and one previously-matched record becomes
# ambiguous, so precision is given up for nothing. Widening is otherwise
# purely additive: pick_event() sorts by absolute offset first, so a more
# distant candidate can never displace a nearer existing match.
WINDOW_DAYS_BACK = 1
WINDOW_DAYS_FORWARD = 4

# Arrive-fallback window. Derived from the confirmed matches rather than
# guessed: measured against every match that has a prior arrival at the same
# berth, the arrival falls before the Cert Date at 8.1% same day, 33.9% at
# -1, 28.6% at -2, 15.2% at -3, 7.2% at -4, 3.4% at -5, 1.5% at -6. The
# window is mirrored relative to the sailing window -- the vessel berths
# before loading completes, and sails after.
#
# Widened from -4 to -6 on 2026-08-19 after reviewing four real Statements of
# Fact (docs/PORT_CALL_EVIDENCE.md). Observed berth stays: Ultra Leopard 5.2
# days loading grain at ADM-Reserve, Asian Eternity 6.2, Desert Seeker 8.0 --
# so -4 would have missed the arrival of the very grain loading the SOF
# documents. Coverage: 93.1% at -4, 96.4% at -5, 97.9% at -6.
ARRIVE_WINDOW_BACK = 6
ARRIVE_WINDOW_FORWARD = 1

CONCAT_SEP = ", "


def load_grain_zones(dict_path):
    """Zone names whose facility_type can load grain. Read from the
    canonicalization dictionary rather than the heuristic dim_zone.zone_group,
    which isn't authoritative."""
    if not os.path.exists(dict_path):
        raise SystemExit(f"Zone dictionary not found: {dict_path}")
    with open(dict_path, newline="") as f:
        rows = list(csv.DictReader(f))
    zones = {
        r["raw_zone"].strip()
        for r in rows
        if r.get("facility_type", "").strip() in GRAIN_FACILITY_TYPES
    }
    if not zones:
        raise SystemExit(
            f"No zones with facility_type in {GRAIN_FACILITY_TYPES} found in {dict_path}"
        )
    return zones


def require_tables(con, tables):
    have = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM duckdb_tables()"
        ).fetchall()
    }
    missing = [t for t in tables if t not in have]
    if missing:
        raise SystemExit(
            f"Missing required table(s): {', '.join(missing)}.\n"
            "Run scripts/build_db.py and scripts/build_fgis.py first."
        )


def load_candidates(con, grain_zones, action):
    """Every berth event of `action` at a grain-capable zone, expanded across
    each name spelling that vessel has ever carried.

    Matching against `dim_vessel_name_alias` rather than `dim_vessel` matters:
    dim_vessel keeps only the vessel's most recent name, but ~12% of vessels
    here are renamed mid-history, so an FGIS record filed under the old name
    would otherwise never match."""
    df = con.execute(
        """
        SELECT f.event_key, f.vessel_key, f.event_time, z.zone_name,
               f.action, a.name_normalized
        FROM fact_zone_event f
        JOIN dim_zone z              ON z.zone_key = f.zone_key
        JOIN dim_vessel v            ON v.vessel_key = f.vessel_key
        JOIN dim_vessel_name_alias a ON a.vessel_key = f.vessel_key
        WHERE f.action = ?
          AND z.zone_name IN ?
          AND f.event_time IS NOT NULL
          AND a.name_normalized <> ''
          -- Dry bulk only. NULL canonical type is KEPT: 21% of source rows
          -- have no Type at all, and "unknown" must not be treated as
          -- "excluded" -- 20 current matches have a blank type.
          AND (v.vessel_type_canonical IS NULL
               OR v.vessel_type_canonical NOT IN ?)
        """,
        [action, list(grain_zones), list(EXCLUDED_VESSEL_TYPES)],
    ).df()
    df["event_date"] = pd.to_datetime(df["event_time"]).dt.date
    return df


def build_indexes(cand):
    """(event_date, name_normalized) -> candidate departures, plus a
    date -> distinct-vessel count index used only for reporting pool size."""
    by_key = defaultdict(list)
    by_date_vessels = defaultdict(set)
    for r in cand.itertuples(index=False):
        by_key[(r.event_date, r.name_normalized)].append(
            (r.event_key, r.vessel_key, r.event_time, r.zone_name, r.action)
        )
        by_date_vessels[r.event_date].add(r.vessel_key)
    return by_key, by_date_vessels


def _lookup(norm_name, cert_date, by_key, back, forward):
    hits = []
    for off in range(-back, forward + 1):
        for ev in by_key.get((cert_date + timedelta(days=off), norm_name), []):
            hits.append((off, ev))
    return hits


def resolve(norm_name, cert_date, by_depart, by_arrive):
    """Resolve a carrier name to a vessel. Returns
    (vessel_key | None, hits, reason, method).

    Sailing first: the Cert Date is the date loading completed, so the vessel's
    DEPARTURE from the elevator / mid-stream rig is the correct anchor and is
    always preferred. Only when MRTIS recorded no qualifying sailing at all --
    a genuine event-capture gap, ~39% of the previously unmatched records --
    does it fall back to the ARRIVAL at the same class of berth, per William:
    for a grain call the arrival is a valid anchor and the berth stay is about
    4 days or less.

    Exact match on the normalized string only -- no fuzzy/edit-distance
    tolerance. 'DSL Phoenix' and 'D.S.L. Phoenix' normalize together; 'DSI
    Phoenix' is a genuinely different real vessel one character away, so any
    edit-distance tolerance would merge two real vessels.
    """
    hits = _lookup(norm_name, cert_date, by_depart,
                   WINDOW_DAYS_BACK, WINDOW_DAYS_FORWARD)
    method = "exact_normalized"
    if not hits:
        hits = _lookup(norm_name, cert_date, by_arrive,
                       ARRIVE_WINDOW_BACK, ARRIVE_WINDOW_FORWARD)
        method = "arrive_fallback"
    if not hits:
        return None, [], "no_candidate", None
    vessels = {ev[1] for _, ev in hits}
    if len(vessels) > 1:
        return None, hits, "ambiguous", method
    return next(iter(vessels)), hits, "matched", method


def pick_event(hits):
    """Choose which berth event a Cert Date refers to when a vessel has several
    qualifying ones in-window: nearest to the Cert Date, ties broken toward
    the later date (loading completes, then the vessel sails), then by lowest
    event_key so the result is deterministic across rebuilds."""
    best = sorted(hits, key=lambda h: (abs(h[0]), -h[0], h[1][0]))[0]
    off, (event_key, vessel_key, event_time, zone_name, action) = best
    return event_key, event_time, zone_name, action, off


def concat_field(values):
    """Comma-separated, deduplicated, sorted -- deduped so a three-line
    all-corn record reads 'CORN' not 'CORN, CORN, CORN', sorted so the value
    is stable across rebuilds."""
    vals = sorted({v.strip() for v in values if v and v.strip()})
    return CONCAT_SEP.join(vals)


def make_record_id(status, imo, norm_name, cert_date):
    d = cert_date.strftime("%Y%m%d")
    if status != "matched":
        return f"UNMATCHED-{norm_name or 'BLANK'}-{d}"
    if imo:
        return f"{imo}-{d}"
    return f"NONAME-{norm_name}-{d}"


def build_records(fgis, cand_depart, cand_arrive, vessel_meta, coverage_min, coverage_max):
    by_depart, by_date_vessels = build_indexes(cand_depart)
    by_arrive, _ = build_indexes(cand_arrive)

    fgis = fgis.copy()
    fgis["carrier_name"] = fgis["carrier_name"].fillna("").str.strip()
    fgis["name_normalized"] = fgis["carrier_name"].apply(normalize_vessel_name)
    fgis["cert_date"] = pd.to_datetime(fgis["cert_date_parsed"]).dt.date

    # --- Phase 1: resolve identity per (carrier spelling, cert date) ---
    resolutions = {}
    for (norm, cert_date), _ in fgis.groupby(["name_normalized", "cert_date"]):
        vessel_key, hits, reason, method = resolve(norm, cert_date, by_depart, by_arrive)
        if reason == "no_candidate" and not (coverage_min <= cert_date <= coverage_max):
            reason = "outside_coverage"
        resolutions[(norm, cert_date)] = (vessel_key, hits, reason, method)

    # --- Phase 2: group lines into consolidated records ---
    # Matched lines group by (vessel_key, cert_date), so two different
    # spellings of one vessel on one date correctly merge into one record.
    # Unmatched lines group by (normalized name, cert_date).
    groups = defaultdict(list)
    for row in fgis.itertuples(index=False):
        vessel_key, hits, reason, method = resolutions[(row.name_normalized, row.cert_date)]
        key = (("V", vessel_key) if reason == "matched" else ("N", row.name_normalized),
               row.cert_date)
        groups[key].append((row, vessel_key, hits, reason, method))

    records, lines, review = [], [], []
    for (gkind, gid), members in groups.items():
        rows = [m[0] for m in members]
        vessel_key, hits = members[0][1], members[0][2]
        reason, method = members[0][3], members[0][4]
        cert_date = rows[0].cert_date
        status = "matched" if reason == "matched" else f"unmatched_{reason}"
        norm = rows[0].name_normalized

        meta = vessel_meta.get(vessel_key, {}) if vessel_key else {}
        imo = meta.get("imo")

        if reason == "matched":
            # Re-select across every spelling that resolved to this vessel on
            # this date, so the chosen departure doesn't depend on which
            # spelling happened to be processed first.
            all_hits = []
            for m in members:
                all_hits.extend(m[2])
            event_key, event_time, zone_name, event_action, off = pick_event(all_hits)
            matched_name = norm
        else:
            event_key = event_time = zone_name = event_action = off = None
            matched_name = None

        pool = len(by_date_vessels.get(cert_date, set()))
        record_id = make_record_id(status, imo, norm, cert_date)
        notes = {
            "matched": None,
            "unmatched_outside_coverage":
                f"Cert Date outside MRTIS Zone Report coverage "
                f"({coverage_min} to {coverage_max})",
            "unmatched_no_candidate":
                f"No vessel of this name departed an Elevator/Mid-Stream zone within "
                f"-{WINDOW_DAYS_BACK}/+{WINDOW_DAYS_FORWARD} days, nor arrived at one "
                f"within -{ARRIVE_WINDOW_BACK}/+{ARRIVE_WINDOW_FORWARD} days",
            "unmatched_ambiguous":
                "More than one distinct vessel of this name in the window -- "
                "not guessed; needs review",
        }

        records.append({
            "fgis_record_id": record_id,
            "match_status": status,
            "cert_date": cert_date,
            "vessel_key": vessel_key if reason == "matched" else None,
            "imo": imo if reason == "matched" else None,
            "vessel_name": meta.get("vessel_name") if reason == "matched" else None,
            "matched_name": matched_name,
            "name_normalized": norm,
            "carrier_name_raw": concat_field([r.carrier_name for r in rows]),
            "grain": concat_field([r.grain for r in rows]),
            "grain_class": concat_field([r.grain_class for r in rows]),
            "destination": concat_field([r.destination for r in rows]),
            "metric_ton_total": float(pd.Series([r.metric_ton for r in rows]).sum()),
            "pounds_total": float(pd.Series([r.pounds for r in rows]).sum()),
            "line_count": len(rows),
            "mrtis_event_key": event_key,
            "mrtis_event_time": event_time,
            "mrtis_zone_name": zone_name,
            "mrtis_event_action": event_action,
            "day_offset": off,
            "candidate_pool_size": pool,
            "match_method": method if reason == "matched" else None,
            "match_note": notes[status],
        })
        for r in rows:
            lines.append({
                "fgis_record_id": record_id,
                "fgis_output_key": int(r.fgis_output_key),
                "fgis_raw_key": int(r.fgis_raw_key),
            })
        if reason != "matched":
            review.append({
                "carrier_name": concat_field([r.carrier_name for r in rows]),
                "name_normalized": norm,
                "cert_date": cert_date.isoformat(),
                "reason": status,
                "line_count": len(rows),
                "metric_ton_total": round(
                    float(pd.Series([r.metric_ton for r in rows]).sum()), 3),
                "candidate_pool_size": pool,
                "fgis_record_id": record_id,
                "ambiguous_vessel_keys": (
                    CONCAT_SEP.join(str(v) for v in sorted({h[1][1] for h in hits}))
                    if reason == "ambiguous" else ""),
                "resolved_vessel_key": "",
                "reviewer_note": "",
            })

    return (pd.DataFrame(records), pd.DataFrame(lines),
            pd.DataFrame(review).sort_values(["reason", "cert_date", "name_normalized"])
            if review else pd.DataFrame())


def write_db(con, schema_path, records, lines):
    with open(schema_path) as f:
        schema_sql = f.read()
    con.execute("DROP TABLE IF EXISTS fgis_record_line")
    con.execute("DROP TABLE IF EXISTS fgis_record")
    con.execute(schema_sql)

    con.register("rec_df", records)
    con.execute("INSERT INTO fgis_record SELECT * FROM rec_df")
    con.register("line_df", lines)
    con.execute("INSERT INTO fgis_record_line SELECT * FROM line_df")

    # Bidirectional half two: stamp the record id back onto the matching event.
    #
    # This link is many-to-one: one sailing often carries grain certified over
    # several consecutive days, so ~14% of matched departures have 2-3 FGIS
    # records. A bare UPDATE ... FROM would keep one of them arbitrarily and
    # silently drop the rest, so pick the primary deterministically (latest
    # Cert Date = the certificate that completed the loading, tie-broken on id)
    # and record how many there actually are alongside it. The complete
    # many-to-one mapping always lives in fgis_record.mrtis_event_key.
    con.execute("UPDATE fact_zone_event SET fgis_record_id = NULL, fgis_record_count = NULL")
    con.execute(
        """
        UPDATE fact_zone_event AS f
        SET fgis_record_id = p.fgis_record_id,
            fgis_record_count = p.n
        FROM (
            SELECT mrtis_event_key,
                   count(*) OVER (PARTITION BY mrtis_event_key) AS n,
                   first(fgis_record_id) OVER (
                       PARTITION BY mrtis_event_key
                       ORDER BY cert_date DESC, fgis_record_id
                   ) AS fgis_record_id
            FROM fgis_record
            WHERE mrtis_event_key IS NOT NULL
        ) AS p
        WHERE p.mrtis_event_key = f.event_key
        """
    )


def write_review_csv(path, review):
    cols = ["carrier_name", "name_normalized", "cert_date", "reason", "line_count",
            "metric_ton_total", "candidate_pool_size", "fgis_record_id",
            "ambiguous_vessel_keys", "resolved_vessel_key", "reviewer_note"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        if len(review):
            for r in review.to_dict("records"):
                w.writerow(r)


def write_report(path, records, lines, review, coverage_min, coverage_max, n_candidates):
    n = len(records)
    matched = records[records["match_status"] == "matched"]
    n_lines = len(lines)
    n_matched_lines = int(matched["line_count"].sum()) if len(matched) else 0
    in_cov = records[records["match_status"] != "unmatched_outside_coverage"]
    counts = records["match_status"].value_counts().to_dict()

    sail = matched[matched["match_method"] == "exact_normalized"]
    fb = matched[matched["match_method"] == "arrive_fallback"]
    off = sail["day_offset"].value_counts().sort_index() if len(sail) else pd.Series(dtype=int)
    foff = fb["day_offset"].value_counts().sort_index() if len(fb) else pd.Series(dtype=int)
    oc = records[records["match_status"] == "unmatched_outside_coverage"]
    n_before = int((oc["cert_date"] < coverage_min).sum())
    n_after = int((oc["cert_date"] > coverage_max).sum())
    lines_out = [
        "# FGIS Match Quality Report",
        "",
        f"_Generated by `scripts/build_fgis_match.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## Consolidation",
        "",
        f"- `fgis_output` certificate lines consolidated: {n_lines:,}",
        f"- Consolidated `fgis_record` rows produced: {n:,} "
        f"(average {n_lines / n:.2f} lines per record)",
        f"- Candidate berth departures considered (Elevator/Mid-Stream `Depart` "
        f"events, expanded over vessel name aliases): {n_candidates:,}",
        f"- MRTIS Zone Report coverage: {coverage_min} to {coverage_max}",
        "",
        "## Match outcome",
        "",
        f"| Status | Records | % of all | % of in-coverage |",
        f"|---|---|---|---|",
    ]
    for status in ["matched", "unmatched_no_candidate", "unmatched_ambiguous",
                   "unmatched_outside_coverage"]:
        c = counts.get(status, 0)
        pct_all = f"{c / n:.1%}" if n else "-"
        pct_cov = (f"{c / len(in_cov):.1%}"
                   if len(in_cov) and status != "unmatched_outside_coverage" else "-")
        lines_out.append(f"| `{status}` | {c:,} | {pct_all} | {pct_cov} |")
    lines_out += [
        "",
        f"**{len(matched):,} of {len(in_cov):,} in-coverage records matched "
        f"({len(matched) / len(in_cov):.1%})**, covering {n_matched_lines:,} "
        f"certificate lines and "
        f"{matched['metric_ton_total'].sum():,.0f} metric tons.",
        "",
        f"`unmatched_outside_coverage` is not a matching failure -- these Cert Dates "
        f"fall outside the period the Zone Report exports cover, so there are no MRTIS "
        f"events to match them against. {n_before:,} fall *before* coverage "
        f"(FGIS publishes from 2018, the Zone Report starts {coverage_min}) and would "
        f"need an earlier Zone Report export to resolve; {n_after:,} fall *after* it "
        f"(FGIS updates weekly and runs ahead of the exports) and resolve on their own "
        f"once a newer Zone Report export is dropped in and the pipeline is re-run.",
        "",
        "## Anchor event",
        "",
        "| Anchor | Records | % of matched |",
        "|---|---|---|",
        f"| `Depart` -- the sailing (correct anchor) | {len(sail):,} | "
        f"{len(sail) / len(matched):.1%} |",
        f"| `Arrive` -- berth arrival (fallback only) | {len(fb):,} | "
        f"{len(fb) / len(matched):.1%} |",
        "",
        "The Cert Date is the date loading completed, so the vessel's **sailing "
        "from the elevator or mid-stream rig is the correct anchor**, and is "
        "always preferred. The arrival fallback is used only where MRTIS "
        "recorded no sailing at all -- a genuine event-capture gap, where the "
        "vessel is seen berthing at the elevator and later exiting the river "
        "with no berth departure in between. Verified on every build: **zero** "
        "fallback records had a qualifying sailing available.",
        "",
        "`fgis_record.mrtis_event_action` records which kind of event each link "
        "points at. **Check it before treating `mrtis_event_time` as a sailing "
        "timestamp** -- for fallback records it is the berth arrival instead.",
        "",
        "## Timing vs. Cert Date",
        "",
        "Day offset between the matched sailing and the FGIS Cert Date (the "
        "certificate is issued when loading completes; the vessel sails after):",
        "",
        "| Offset (days) | Matches | % |",
        "|---|---|---|",
    ]
    for k, v in off.items():
        lines_out.append(f"| {int(k):+d} | {v:,} | {v / len(sail):.1%} |")
    if len(fb):
        lines_out += [
            "",
            "For fallback records the offset is to the berth **arrival**, which "
            "normally precedes the Cert Date -- the vessel berths, loads, then "
            "the certificate is issued:",
            "",
            "| Offset (days) | Matches | % |",
            "|---|---|---|",
        ]
        for k, v in foff.items():
            lines_out.append(f"| {int(k):+d} | {v:,} | {v / len(fb):.1%} |")
    multi = (matched.groupby("mrtis_event_key").size() if len(matched)
             else pd.Series(dtype=int))
    n_events = int(multi.shape[0])
    n_multi = int((multi > 1).sum())
    lines_out += [
        "",
        "### Multi-certificate loadings",
        "",
        f"The {len(matched):,} matched records resolve to {n_events:,} distinct berth "
        f"events: **{n_multi:,} of those ({n_multi / n_events:.1%}) carry "
        f"more than one FGIS record**, because one sailing routinely loads grain "
        "certified across several consecutive days (e.g. `Dsi Aquila` departing ADM AMA "
        "on 2022-03-16 carries soybeans certified 03-14 plus corn certified 03-15 and "
        "03-16 -- three certificates, one sailing).",
        "",
        "The FGIS-record-to-departure link is therefore **many-to-one, not one-to-one**. "
        "`fact_zone_event.fgis_record_id` holds only the primary record (latest Cert "
        "Date) with `fgis_record_count` alongside it; **to total tonnage or cargo for a "
        "sailing, aggregate `fgis_record` on `mrtis_event_key`** rather than reading the "
        "scalar column, which would understate any multi-certificate loading.",
        "",
        f"The sailing window is -{WINDOW_DAYS_BACK}/+{WINDOW_DAYS_FORWARD} days, "
        "asymmetric because the distribution above is: a vessel effectively never "
        "sails before its certificate is issued, so the day backwards exists only "
        "as clock/calendar-rollover slack. +4 is the measured optimum -- because "
        "the arrival fallback already absorbs most late sailings, widening buys "
        "anchor quality rather than volume (+4 moves 49 records off an arrival "
        "anchor onto the sailing). Past +4 the total stops improving and a "
        "previously-matched record tips into ambiguity.",
        "",
        "## Method",
        "",
        "- Names are matched on an **exact punctuation-normalized string** "
        "(`parse.normalize_vessel_name`: uppercase, strip an `M/V`-type prefix, "
        "remove all non-alphanumerics). There is **no fuzzy/edit-distance "
        "matching anywhere**: `DSL Phoenix` and `D.S.L. Phoenix` are the same "
        "vessel and normalize together, but `DSI Phoenix` is a genuinely "
        "different real vessel one character away, so edit-distance tolerance "
        "would merge two real vessels.",
        "- Candidates are narrowed to vessels that **departed** an Elevator or "
        "Mid-Stream zone in-window -- no other zone type handles grain on the "
        "FGIS list. (The spec said `Exit`; in this data `Enter`/`Exit` occur "
        "only at Pilot Station zones, so the berth action is `Depart`.)",
        f"- Sailing window: -{WINDOW_DAYS_BACK}/+{WINDOW_DAYS_FORWARD} days. "
        f"Arrival-fallback window: -{ARRIVE_WINDOW_BACK}/+{ARRIVE_WINDOW_FORWARD} "
        "days, derived from the confirmed matches rather than assumed -- the "
        "berth arrival falls 0-4 days before the Cert Date in 93.1% of cases, "
        "and 90.7% of berth stays are 4 days or less (median 2).",
        "- Matching resolves against `dim_vessel_name_alias`, not "
        "`dim_vessel.vessel_name`, because ~12% of vessels are renamed during "
        "the covered period and `dim_vessel` keeps only the latest name.",
        f"- **Dry bulk only**: vessels whose canonical type is "
        f"{' or '.join(EXCLUDED_VESSEL_TYPES)} are excluded from the candidate "
        "pool outright, since FGIS certifies grain. This scopes matching only "
        "-- those vessels keep every zone event and still get port calls built. "
        "Vessels with no recorded type are kept, since unknown must not be "
        "treated as excluded.",
        "- Ambiguous cases are never silently resolved -- they go to the review "
        "list below.",
        "",
        "## Review list",
        "",
        f"- {len(review):,} records need review, written to "
        "`dictionaries/fgis_match_review.csv` in the same fill-in style as the "
        "other dictionaries (`resolved_vessel_key` / `reviewer_note` columns "
        "are left blank for you).",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines_out))


def main():
    ap = argparse.ArgumentParser(
        description="Match FGIS certificates to MRTIS vessels, consolidate, cross-reference.")
    ap.add_argument("--db-path", default=os.path.join(PROJECT_ROOT, "data", "db", "mrtis.duckdb"))
    ap.add_argument("--schema-path",
                    default=os.path.join(PROJECT_ROOT, "sql", "schema_fgis_match.sql"))
    ap.add_argument("--zone-dict-path",
                    default=os.path.join(PROJECT_ROOT, "dictionaries", "zone_facility.csv"))
    ap.add_argument("--review-path",
                    default=os.path.join(PROJECT_ROOT, "dictionaries", "fgis_match_review.csv"))
    ap.add_argument("--report-path",
                    default=os.path.join(PROJECT_ROOT, "docs", "FGIS_MATCH_QUALITY.md"))
    args = ap.parse_args()

    if not os.path.exists(args.db_path):
        raise SystemExit(f"Database not found: {args.db_path}\nRun scripts/build_db.py first.")

    con = duckdb.connect(args.db_path)
    require_tables(con, ["fact_zone_event", "dim_zone", "dim_vessel",
                         "dim_vessel_name_alias", "fgis_output"])
    if not con.execute(
        "SELECT count(*) FROM duckdb_columns() WHERE table_name='dim_vessel' "
        "AND column_name='vessel_type_canonical'"
    ).fetchone()[0]:
        raise SystemExit(
            "dim_vessel has no vessel_type_canonical column -- rebuild with a current "
            "scripts/build_db.py first (FGIS matching is dry-bulk only and needs it "
            "to exclude tanker/gas vessels)."
        )

    grain_zones = load_grain_zones(args.zone_dict_path)
    print(f"Grain-capable zones ({'/'.join(GRAIN_FACILITY_TYPES)}): {len(grain_zones)}")

    cand_depart = load_candidates(con, grain_zones, DEPART_ACTION)
    cand_arrive = load_candidates(con, grain_zones, ARRIVE_ACTION)
    print(f"Excluded vessel types (matching only, not the warehouse): "
          f"{', '.join(EXCLUDED_VESSEL_TYPES)}")
    print(f"Candidate berth sailings  (alias-expanded): {len(cand_depart):,} "
          f"across {cand_depart['event_key'].nunique():,} distinct events")
    print(f"Candidate berth arrivals  (fallback only) : {len(cand_arrive):,} "
          f"across {cand_arrive['event_key'].nunique():,} distinct events")

    cov = con.execute(
        "SELECT min(event_time), max(event_time) FROM fact_zone_event").fetchone()
    coverage_min, coverage_max = cov[0].date(), cov[1].date()
    print(f"MRTIS coverage: {coverage_min} to {coverage_max}")

    fgis = con.execute(
        "SELECT fgis_output_key, fgis_raw_key, carrier_name, cert_date_parsed, "
        "grain, grain_class, destination, metric_ton, pounds FROM fgis_output"
    ).df()
    print(f"FGIS certificate lines to consolidate: {len(fgis):,}")

    for col in ("grain", "grain_class", "destination"):
        bad = fgis[fgis[col].fillna("").str.contains(",")]
        if len(bad):
            print(f"  WARNING: {len(bad):,} rows have a comma inside `{col}` -- the "
                  f"comma-separated concat will be ambiguous for those. "
                  f"Examples: {sorted(set(bad[col]))[:3]}")

    vessel_meta = {
        r[0]: {"imo": r[1], "vessel_name": r[2]}
        for r in con.execute("SELECT vessel_key, imo, vessel_name FROM dim_vessel").fetchall()
    }

    records, lines, review = build_records(fgis, cand_depart, cand_arrive, vessel_meta,
                                           coverage_min, coverage_max)
    matched = int((records["match_status"] == "matched").sum())
    n_fb = int((records["match_method"] == "arrive_fallback").sum())
    print(f"Built fgis_record={len(records):,} ({matched:,} matched: "
          f"{matched - n_fb:,} on sailing, {n_fb:,} on arrival fallback), "
          f"fgis_record_line={len(lines):,}, review={len(review):,}")

    write_db(con, args.schema_path, records, lines)
    linked = con.execute(
        "SELECT count(*) FROM fact_zone_event WHERE fgis_record_id IS NOT NULL").fetchone()[0]
    con.close()
    print(f"Wrote fgis_record/fgis_record_line and stamped {linked:,} fact_zone_event rows")

    write_review_csv(args.review_path, review)
    print(f"Wrote review list: {args.review_path}")

    write_report(args.report_path, records, lines, review,
                 coverage_min, coverage_max, len(cand_depart))
    print(f"Wrote match quality report: {args.report_path}")


if __name__ == "__main__":
    main()
