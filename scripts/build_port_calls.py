#!/usr/bin/env python3
"""
MRTIS port call assembly -- turn the raw zone-event feed into assembled port
calls, split into operational legs, with the canonical/transformed columns the
analytics need.

Usage:
    python3 scripts/build_port_calls.py
    python3 scripts/build_port_calls.py --sample 6 --sample-out sample_port_calls.csv

Runs after scripts/build_db.py (core warehouse) and scripts/build_fgis_match.py
(grain cross-reference). Rebuilds port_call / port_call_leg / port_call_event
from scratch each run, so the layer always reflects the warehouse as it stands.

THE SPINE RULE (William, 2026-08-19): the raw MRTIS event is the spine. Source
values are preserved in their own `src_*` columns and never overwritten; every
canonical or derived value sits alongside. Anything that cannot be derived from
evidence is NULL with a reason, never guessed.

Nothing is written to the database until every HARD guardrail passes -- see
scripts/lib/guardrails.py. A hard failure aborts with the database untouched.

See docs/PORT_CALL_SPEC.md for the assembly rules and docs/PORT_CALL_QUALITY.md
for the report this script produces.
"""

import argparse
import os
import sys
from datetime import datetime

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.guardrails import Guardrails
# The fee tiers live in build_db.py and are imported rather than restated:
# one definition of the rate, used by both layers.
from build_db import agency_fee_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "db", "mrtis.duckdb")
SCHEMA_PATH = os.path.join(ROOT, "sql", "schema_port_call.sql")
ZONE_DICT = os.path.join(ROOT, "dictionaries", "zone_facility.csv")
AGENT_DICT = os.path.join(ROOT, "dictionaries", "agent_agency.csv")
REPORT_PATH = os.path.join(ROOT, "docs", "PORT_CALL_QUALITY.md")

# Facility types that are not a berth: a vessel at one of these has not worked
# cargo. Same definition build_db.py uses for the agency fee, deliberately.
NON_BERTH = {"Anchorage", "Pilot Station"}

# Canonical action labels (William's own wording in logic.md).
ACTION_BERTH = {"Arrive": "Arrived", "Depart": "Sailed"}
ACTION_ANCHOR = {"Arrive": "Anchor", "Depart": "Weigh Anchor"}


# ---------------------------------------------------------------------------
# dictionaries
# ---------------------------------------------------------------------------

# The dictionaries use the literal phrase "Same as raw ..." to mean "this value
# needs no translation -- keep what the source said". It is a placeholder, not a
# name, and it must never reach the output as one.
SAME_AS_RAW = "same as raw"


def deref(value, raw):
    """Resolve a dictionary cell, honouring the 'Same as raw ...' placeholder."""
    if value is None or pd.isna(value):
        return raw
    v = str(value).strip()
    if not v or v.casefold().startswith(SAME_AS_RAW):
        return raw
    return v


def conventional_casing(text):
    """True for ALLCAPS or Title Case -- used to pick between case-variant
    spellings of the same agency ('HOST' over 'hOST')."""
    return text.isupper() or text == text.title()


def load_zone_dict(path):
    """raw_zone (+ crossing direction) -> canonical berth/facility/mile/ops.

    'SWP Cross' carries two dictionary rows -- one for the inbound crossing and
    one for the outbound -- distinguished by source_category, which is why the
    key is (raw_zone, direction) rather than raw_zone alone. Everywhere else
    direction is None.
    """
    d = pd.read_csv(path).rename(columns={"Cargo group": "cargo_group", "Rule": "rule"})
    out = {}
    for r in d.itertuples():
        direction = None
        cat = (str(r.source_category) or "").strip()
        if cat == "Cross In":
            direction = "Enter"
        elif cat == "Cross Out":
            direction = "Exit"
        raw_zone = str(r.raw_zone).strip()
        facility = deref(r.facility, raw_zone)
        berth = deref(r.berth_label, raw_zone)
        mile = r.canonical_mile if pd.notna(r.canonical_mile) else r.most_common_mile_in_data
        out[(raw_zone, direction)] = {
            "berth": berth,
            "facility": facility,
            "facility_type": str(r.facility_type).strip() if pd.notna(r.facility_type) else None,
            "ops": str(r.ops).strip() if pd.notna(r.ops) else None,
            "cargo_group": str(r.cargo_group).strip() if pd.notna(r.cargo_group) else None,
            "rule": str(r.rule).strip() if pd.notna(r.rule) else None,
            "mile": float(mile) if pd.notna(mile) else None,
        }
    return out


def zone_lookup(zdict, zone_name, action):
    direction = action if action in ("Enter", "Exit") else None
    return zdict.get((zone_name, direction)) or zdict.get((zone_name, None)) or {}


def load_agent_dict(path, guards):
    """raw_agent -> canonical agency.

    Agency spellings are collapsed case-insensitively: the dictionary contains
    both 'hOST' and 'HOST', which are plainly the same agency, and letting them
    through as two would split that agency's numbers in every report. The
    winning spelling is the one covering the most records; the collapse is
    reported so the dictionary itself can be tidied.
    """
    d = pd.read_csv(path)
    resolved = {}
    by_fold = {}
    for r in d.itertuples():
        raw_agent = str(r.raw_agent).strip()
        agency = deref(r.agency, raw_agent)
        n = int(r.occurrence_count) if pd.notna(r.occurrence_count) else 0
        resolved[raw_agent] = agency
        by_fold.setdefault(agency.casefold(), []).append((n, agency))
    canon, collapsed = {}, []
    for fold, variants in by_fold.items():
        spellings = {v for _, v in variants}
        # Prefer a conventionally-cased spelling ('HOST'), then the one covering
        # the most records. 'hOST' is a typo, not a second agency.
        best = sorted(variants, key=lambda t: (conventional_casing(t[1]), t[0]))[-1][1]
        canon[fold] = best
        if len(spellings) > 1:
            collapsed.append(f"{'/'.join(sorted(spellings))} -> {best}")
    out = {raw: canon[ag.casefold()] for raw, ag in resolved.items()}
    guards.soft(
        "agency spellings collapsed case-insensitively",
        "; ".join(collapsed) if collapsed else "none needed",
        passed=not collapsed,
    )
    return out


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def load_events(con):
    return con.execute("""
        select f.event_key, f.vessel_key, f.action, f.event_time,
               f.vessel_type as src_vessel_type, f.draft_ft, f.mile_marker as src_mile,
               f.agency_fee, f.fgis_record_id, f.fgis_record_count,
               z.zone_name as src_zone, z.facility_type,
               a.agent_name as src_agent,
               v.imo_raw as src_imo, v.imo, v.natural_key, v.vessel_name as dim_vessel_name,
               v.vessel_type_canonical, v.ship_type_group, v.dwt, v.tpc
        from fact_zone_event f
        join dim_zone z using (zone_key)
        join dim_vessel v using (vessel_key)
        left join dim_agent a using (agent_key)
        order by f.vessel_key, f.event_time, f.event_key
    """).df()


def load_event_names(con):
    """Name carried on each event. dim_vessel keeps only the latest spelling, so
    the alias table is what tells us what this call was actually called at the
    time -- 12% of vessels are renamed inside the covered period."""
    df = con.execute("""
        select vessel_key, vessel_name, first_seen, last_seen
        from dim_vessel_name_alias order by vessel_key, first_seen
    """).df()
    by_vessel = {}
    for r in df.itertuples():
        by_vessel.setdefault(r.vessel_key, []).append((r.first_seen, r.last_seen, r.vessel_name))
    return by_vessel


def name_at(aliases, vessel_key, when, fallback):
    for first, last, nm in aliases.get(vessel_key, []):
        if first <= when <= last:
            return nm
    return fallback


def load_fgis(con):
    """event_key -> rolled-up grain cargo for that berth event.

    Aggregated, never read scalar: one sailing routinely carries several
    certificates (grain certified across consecutive days), so reading
    fact_zone_event.fgis_record_id alone would understate the tonnage.
    """
    try:
        df = con.execute("""
            select mrtis_event_key as event_key,
                   count(*) as rec_count,
                   sum(metric_ton_total) as metric_tons,
                   string_agg(distinct grain, ', ') as grain,
                   string_agg(distinct grain_class, ', ') as grain_class,
                   string_agg(distinct destination, ', ') as destination
            from fgis_record
            where match_status = 'matched' and mrtis_event_key is not null
            group by 1
        """).df()
    except duckdb.CatalogException:
        return {}
    return {int(r.event_key): {
        "rec_count": int(r.rec_count), "metric_tons": float(r.metric_tons or 0),
        "grain": r.grain, "grain_class": r.grain_class, "destination": r.destination,
    } for r in df.itertuples()}


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def assemble_calls(ev):
    """Split each vessel's event stream into port calls.

    A port call runs from an Enter at the pilot station to the matching Exit --
    "a vessel must enter SWP and exit SWP to become a total voyage" (logic.md).
    Reality is not always that tidy, so three edge cases are handled explicitly
    rather than dropped:

      * events before the first Enter        -> unassigned ('before_first_entry')
      * a second Enter while a call is open  -> the open call is closed as
                                                'open_end' and a new one starts
      * an Exit with no open call            -> those events are unassigned
                                                ('no_open_call')

    Returns (calls, unassigned) where a call is a list of row indices.
    """
    calls, unassigned = [], []
    for _, g in ev.groupby("vessel_key", sort=False):
        idx = list(g.index)
        cur, seen_entry = None, False
        for i in idx:
            action = ev.at[i, "action_src"]
            if action == "Enter":
                if cur:
                    calls.append(cur)
                cur, seen_entry = [i], True
            elif cur is not None:
                cur.append(i)
                if action == "Exit":
                    calls.append(cur)
                    cur = None
            else:
                unassigned.append((i, "before_first_entry" if not seen_entry else "no_open_call"))
        if cur:
            calls.append(cur)
    return calls, unassigned


def berth_stops_of(ev, call):
    """Pair Arrive/Depart at berth zones into stops, in time order.

    A stop with only one half (a berth arrival never followed by a sailing, or
    the reverse) is still a stop -- it is a real gap in the feed, and dropping
    it would silently delete a cargo operation.
    """
    stops, open_stop = [], None
    for i in call:
        if ev.at[i, "facility_type"] in NON_BERTH:
            continue
        action = ev.at[i, "action_src"]
        if action == "Arrive":
            if open_stop is not None:
                stops.append(open_stop)
            open_stop = {"arrive_idx": i, "depart_idx": None, "zone": ev.at[i, "src_zone"]}
        elif action == "Depart":
            if open_stop is None or open_stop["zone"] != ev.at[i, "src_zone"]:
                if open_stop is not None:
                    stops.append(open_stop)
                open_stop = {"arrive_idx": None, "depart_idx": i, "zone": ev.at[i, "src_zone"]}
            else:
                open_stop["depart_idx"] = i
            stops.append(open_stop)
            open_stop = None
    if open_stop is not None:
        stops.append(open_stop)
    return stops


def overlap_hours(t0, t1, w0, w1):
    """Hours of [t0,t1) that fall inside the window [w0,w1)."""
    if t0 is None or t1 is None or w0 is None or w1 is None:
        return 0.0
    lo, hi = max(t0, w0), min(t1, w1)
    return max(0.0, (hi - lo).total_seconds() / 3600.0) if hi > lo else 0.0


def resolve_activity(ev, stop, zinfo, fgis, min_delta=1):
    """Decide what the vessel did at this berth, and say how we know.

    Order of evidence, strongest first:
      1. draft delta -- sailed deeper than it arrived means it loaded; lighter
         means it discharged. Physics, and it needs no dictionary.
      2. FGIS -- a USDA grain certificate issued against this sailing means the
         vessel loaded grain, full stop.
      3. the zone dictionary -- an Elevator can only load; a Layberth never
         works cargo at all.
      4. nothing -- activity stays NULL. Never guessed.
    """
    a_idx, d_idx = stop["arrive_idx"], stop["depart_idx"]
    ad = ev.at[a_idx, "draft_ft"] if a_idx is not None else None
    dd = ev.at[d_idx, "draft_ft"] if d_idx is not None else None
    delta = None
    if pd.notna(ad) and pd.notna(dd):
        delta = int(dd) - int(ad)

    # Both halves of a stop can carry certificates -- 13 stops in this data have
    # grain certified against the berth arrival AND against the sailing. Taking
    # only one of them would quietly drop the other's tonnage, so they merge.
    hits = [fgis[int(ev.at[i, "event_key"])] for i in (d_idx, a_idx)
            if i is not None and int(ev.at[i, "event_key"]) in fgis]
    cargo = None
    if hits:
        cargo = {
            "rec_count": sum(h["rec_count"] for h in hits),
            "metric_tons": sum(h["metric_tons"] for h in hits),
            "grain": ", ".join(sorted({h["grain"] for h in hits if h["grain"]})) or None,
            "grain_class": ", ".join(sorted({h["grain_class"] for h in hits if h["grain_class"]})) or None,
            "destination": ", ".join(sorted({h["destination"] for h in hits if h["destination"]})) or None,
        }

    activity = method = None
    if delta is not None and abs(delta) >= min_delta:
        activity, method = ("Load" if delta > 0 else "Discharge"), "draft_delta"
    elif cargo is not None:
        activity, method = "Load", "fgis"
    else:
        ops = (zinfo.get("ops") or "").strip()
        rule = (zinfo.get("rule") or "").strip().lower()
        if ops == "Load":
            activity, method = "Load", "dictionary"
        elif ops == "Discharge":
            activity, method = "Discharge", "dictionary"
        elif ops == "Layberth" or rule == "no cargo ever takes place":
            activity, method = "No Cargo", "dictionary"
        else:
            method = "unresolved"

    # Evidence disagreeing with evidence is a finding, not something to resolve
    # quietly. Two kinds, both surfaced rather than settled:
    #   'fgis'       -- a grain certificate was issued, but the vessel sailed lighter.
    #   'dictionary' -- the draft shows a real cargo movement in the direction the
    #                   zone dictionary says this berth never works. Where those
    #                   two disagree the DRAFT WINS: 531 legs sail 14ft lighter
    #                   (median) from berths the dictionary marks Load-only, which
    #                   is a 20,000-tonne discharge, not a rounding artefact. The
    #                   dictionary is incomplete there, and the flag is how it gets
    #                   found and corrected.
    conflict_reason = None
    if method == "draft_delta":
        ops_dict = (zinfo.get("ops") or "").strip()
        if cargo is not None and activity == "Discharge":
            conflict_reason = "fgis"
        elif ops_dict in ("Load", "Discharge") and activity != ops_dict:
            conflict_reason = "dictionary"

    return {
        "activity": activity, "method": method, "conflict": conflict_reason is not None,
        "conflict_reason": conflict_reason, "delta": delta, "cargo": cargo,
    }


def split_into_legs(stops):
    """Group consecutive berth stops into legs, starting a new leg only when the
    activity genuinely changes.

    Two Load berths back to back (topping off at a second elevator) are ONE leg
    -- same cargo job. Discharge then Load is the split call: the vessel came in
    laden for one agent, discharged, then loaded a fresh cargo for the outbound
    voyage. A stop whose activity could not be resolved joins the leg in
    progress; an unknown is never allowed to invent a split.
    """
    legs, cur, cur_activity = [], [], None
    for st in stops:
        act = st["activity"]
        if not cur:
            cur, cur_activity = [st], act
            continue
        if act is not None and cur_activity is not None and act != cur_activity:
            legs.append(cur)
            cur, cur_activity = [st], act
        else:
            cur.append(st)
            if cur_activity is None:
                cur_activity = act
    if cur:
        legs.append(cur)
    return legs


def hours(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return round((b - a).total_seconds() / 3600.0, 2)


def build_frames(ev, calls, unassigned, zdict, agent_map, aliases, fgis, guards, min_delta=1):
    """Produce the three output frames. Pure in-memory -- nothing touches the
    database until every guardrail has passed."""
    call_rows, leg_rows, event_rows = [], [], []

    for call in calls:
        first_i, last_i = call[0], call[-1]
        vessel_key = int(ev.at[first_i, "vessel_key"])
        natural_key = ev.at[first_i, "natural_key"]
        start_t, end_t = ev.at[first_i, "event_time"], ev.at[last_i, "event_time"]
        call_id = f"{natural_key}-{pd.Timestamp(start_t).strftime('%Y%m%d%H%M')}"

        has_entry = ev.at[first_i, "action_src"] == "Enter"
        has_exit = ev.at[last_i, "action_src"] == "Exit"
        status = ("complete" if has_entry and has_exit else
                  "open_end" if has_entry else
                  "open_start" if has_exit else "fragment")

        # --- berth stops and what happened at each ---------------------------
        stops = berth_stops_of(ev, call)
        pos = {i: p for p, i in enumerate(call)}
        for st in stops:
            ref = st["arrive_idx"] if st["arrive_idx"] is not None else st["depart_idx"]
            zinfo = zone_lookup(zdict, ev.at[ref, "src_zone"], ev.at[ref, "action_src"])
            st.update(resolve_activity(ev, st, zinfo, fgis, min_delta))
            st["zinfo"] = zinfo
            st["end_pos"] = max(pos[i] for i in (st["arrive_idx"], st["depart_idx"]) if i is not None)
            st["start_pos"] = min(pos[i] for i in (st["arrive_idx"], st["depart_idx"]) if i is not None)

        legs = split_into_legs(stops)
        if not legs:                      # a call that never berthed
            legs = [[]]
        leg_count = len(legs)

        # --- every event of the call lands on exactly one leg -----------------
        # An event belongs to the leg of the NEXT berth stop it is heading for:
        # anchorage dwell before a berth is time spent waiting for THAT berth.
        # Events after the final sailing belong to the final leg (outbound).
        leg_end_pos = [max((st["end_pos"] for st in lg), default=-1) for lg in legs]
        leg_of_pos = []
        for p in range(len(call)):
            j = next((k for k, e in enumerate(leg_end_pos) if p <= e), leg_count - 1)
            leg_of_pos.append(j)

        # --- agency, per leg -------------------------------------------------
        agencies_call = [a for a in (agent_map.get(ev.at[i, "src_agent"], ev.at[i, "src_agent"])
                                     for i in call) if pd.notna(a) and a]
        call_agency_fallback = max(set(agencies_call), key=agencies_call.count) if agencies_call else None

        leg_meta = []
        for j, lg in enumerate(legs):
            members = [call[p] for p in range(len(call)) if leg_of_pos[p] == j]
            first_berth_pos = min((st["start_pos"] for st in lg), default=None)
            inbound = [i for i in members if first_berth_pos is None or pos[i] <= first_berth_pos]
            def first_agency(rows):
                for i in rows:
                    a = agent_map.get(ev.at[i, "src_agent"], ev.at[i, "src_agent"])
                    if pd.notna(a) and a:
                        return a
                return None
            agency, source = first_agency(inbound), "inbound"
            if agency is None:
                agency, source = first_agency(members), "leg"
            if agency is None:
                agency, source = call_agency_fallback, "call"
            if agency is None:
                source = "none"
            distinct = {agent_map.get(ev.at[i, "src_agent"], ev.at[i, "src_agent"])
                        for i in members
                        if pd.notna(ev.at[i, "src_agent"]) and ev.at[i, "src_agent"]}
            leg_meta.append({"members": members, "agency": agency, "agency_source": source,
                             "agent_changed": len(distinct) > 1})

        # --- time analytics, per leg -----------------------------------------
        for j, lg in enumerate(legs):
            m = leg_meta[j]
            members = m["members"]
            b_arr = next((ev.at[st["arrive_idx"], "event_time"] for st in lg
                          if st["arrive_idx"] is not None), None)
            if b_arr is None and lg:
                b_arr = ev.at[lg[0]["depart_idx"], "event_time"] if lg[0]["depart_idx"] is not None else None
            b_dep = next((ev.at[st["depart_idx"], "event_time"] for st in reversed(lg)
                          if st["depart_idx"] is not None), None)

            waiting = inter = outbound = berth_h = 0.0
            anchor_pairs, open_anchor = [], None
            for i in members:
                if ev.at[i, "facility_type"] != "Anchorage":
                    continue
                if ev.at[i, "action_src"] == "Arrive":
                    open_anchor = i
                elif ev.at[i, "action_src"] == "Depart" and open_anchor is not None:
                    anchor_pairs.append((open_anchor, i))
                    open_anchor = None
            # Attribute anchorage dwell by OVERLAP with the leg's windows, not by
            # which side of the berth arrival the anchorage began. The pilot
            # sheets routinely log the anchor as weighed hours or days after the
            # vessel is already alongside -- 90 legs here have an anchorage still
            # "open" past the berth arrival -- and counting that whole dwell as
            # waiting would double-count time the vessel spent working cargo.
            leg_first = ev.at[members[0], "event_time"] if members else None
            leg_last = ev.at[members[-1], "event_time"] if members else None
            waiting_idx = set()
            for a_i, d_i in anchor_pairs:
                t0, t1 = ev.at[a_i, "event_time"], ev.at[d_i, "event_time"]
                if b_arr is None:
                    outbound += (hours(t0, t1) or 0.0)
                    continue
                w = overlap_hours(t0, t1, leg_first, b_arr)
                waiting += w
                if w > 0:
                    waiting_idx.update((a_i, d_i))
                if b_dep is not None:
                    inter += overlap_hours(t0, t1, b_arr, b_dep)
                    outbound += overlap_hours(t0, t1, b_dep, leg_last)
                else:
                    inter += overlap_hours(t0, t1, b_arr, leg_last)
            for st in lg:
                if st["arrive_idx"] is not None and st["depart_idx"] is not None:
                    berth_h += hours(ev.at[st["arrive_idx"], "event_time"],
                                     ev.at[st["depart_idx"], "event_time"]) or 0.0

            head = lg[0] if lg else None
            zinfo = head["zinfo"] if head else {}
            act = next((st["activity"] for st in lg if st["activity"]), None)
            method = next((st["method"] for st in lg if st["activity"]), "no_berth_stop" if not lg else "unresolved")
            conflict = any(st["conflict"] for st in lg)
            conflict_reason = next((st["conflict_reason"] for st in lg if st["conflict_reason"]), None)
            delta = next((st["delta"] for st in lg if st["delta"] not in (None, 0)), None)

            cargo_recs = [st["cargo"] for st in lg if st.get("cargo")]
            if cargo_recs:
                cargo_group, cargo_source = "Grain", "fgis"
                cargo = ", ".join(sorted({c["grain"] for c in cargo_recs if c["grain"]})) or None
                destination = ", ".join(sorted({c["destination"] for c in cargo_recs if c["destination"]})) or None
                tons = sum(c["metric_tons"] for c in cargo_recs) or None
                nrec = sum(c["rec_count"] for c in cargo_recs)
            else:
                cargo_group = zinfo.get("cargo_group")
                cargo_source = "dictionary" if cargo_group else None
                cargo = destination = tons = None
                nrec = 0

            # Fee under William's ruling (2026-08-19): "agency fee is per port
            # call, not per berth except when split discharge then load" -- so
            # ONE fee per leg that reached a berth, priced by the vessel. A leg
            # that never berthed did no cargo work and accrues nothing.
            fee_departures = sum(ev.at[i, "agency_fee"] for i in members
                                 if pd.notna(ev.at[i, "agency_fee"]))
            fee = agency_fee_for(ev.at[first_i, "vessel_type_canonical"],
                                 ev.at[first_i, "ship_type_group"],
                                 ev.at[first_i, "imo"]) if lg else None
            leg_id = f"{call_id}-L{j + 1}"
            leg_rows.append({
                "leg_id": leg_id, "port_call_id": call_id, "leg_seq": j + 1, "leg_count": leg_count,
                "vessel_key": vessel_key,
                "leg_start": ev.at[members[0], "event_time"] if members else None,
                "leg_end": ev.at[members[-1], "event_time"] if members else None,
                "leg_hours": hours(ev.at[members[0], "event_time"], ev.at[members[-1], "event_time"]) if members else None,
                "activity": act, "activity_method": method, "activity_conflict": conflict,
                "activity_conflict_reason": conflict_reason, "draft_delta_ft": delta,
                "berth_stop_count": len(lg),
                "first_berth_zone": head["zone"] if head else None,
                "first_berth_facility": zinfo.get("facility"),
                "facility_type": zinfo.get("facility_type"),
                "berth_arrive_time": b_arr, "berth_depart_time": b_dep,
                "waiting_hours": round(waiting, 2), "inter_berth_idle_hours": round(inter, 2),
                "outbound_idle_hours": round(outbound, 2), "berth_hours": round(berth_h, 2),
                "agency": m["agency"], "agency_source": m["agency_source"],
                "agent_changed_in_leg": m["agent_changed"],
                "cargo_group": cargo_group, "cargo": cargo, "cargo_source": cargo_source,
                "destination": destination, "actual_tons": tons,
                "fgis_record_count": nrec, "agency_fee": fee,
                "agency_fee_departures": fee_departures or None,
            })
            m.update(leg_id=leg_id, activity=act, method=method, cargo_group=cargo_group,
                     cargo=cargo, destination=destination, tons=tons, waiting_idx=waiting_idx)

        # --- the spine: one row per event ------------------------------------
        stop_seq_of = {}
        for n, st in enumerate(stops, start=1):
            for i in (st["arrive_idx"], st["depart_idx"]):
                if i is not None:
                    stop_seq_of[i] = n
        dwell_of = {}
        for st in stops:
            if st["arrive_idx"] is not None and st["depart_idx"] is not None:
                dwell_of[st["arrive_idx"]] = hours(ev.at[st["arrive_idx"], "event_time"],
                                                   ev.at[st["depart_idx"], "event_time"])
        open_anchor = None
        for i in call:
            if ev.at[i, "facility_type"] == "Anchorage":
                if ev.at[i, "action_src"] == "Arrive":
                    open_anchor = i
                elif ev.at[i, "action_src"] == "Depart" and open_anchor is not None:
                    dwell_of[open_anchor] = hours(ev.at[open_anchor, "event_time"], ev.at[i, "event_time"])
                    open_anchor = None

        for p, i in enumerate(call):
            j = leg_of_pos[p]
            m = leg_meta[j]
            action_src = ev.at[i, "action_src"]
            ft = ev.at[i, "facility_type"]
            zinfo = zone_lookup(zdict, ev.at[i, "src_zone"], action_src)
            if ft == "Pilot Station":
                action = action_src
            elif ft == "Anchorage":
                action = ACTION_ANCHOR.get(action_src, action_src)
            else:
                action = ACTION_BERTH.get(action_src, action_src)
            own_agency = agent_map.get(ev.at[i, "src_agent"], ev.at[i, "src_agent"])
            own_agency = own_agency if (pd.notna(own_agency) and own_agency) else None
            event_rows.append({
                "event_key": int(ev.at[i, "event_key"]),
                "port_call_id": call_id, "leg_id": m["leg_id"], "leg_seq": j + 1,
                "event_seq": p + 1, "unassigned_reason": None,
                "vessel_key": vessel_key,
                "src_imo": ev.at[i, "src_imo"], "imo": ev.at[i, "imo"],
                "vessel_name": name_at(aliases, vessel_key, ev.at[i, "event_time"],
                                       ev.at[i, "dim_vessel_name"]),
                "src_vessel_type": ev.at[i, "src_vessel_type"],
                "vessel_type": ev.at[i, "vessel_type_canonical"],
                "ship_type_group": ev.at[i, "ship_type_group"],
                "dwt": ev.at[i, "dwt"], "tpc": ev.at[i, "tpc"],
                "src_action": action_src, "action": action,
                "event_time": ev.at[i, "event_time"],
                "src_zone": ev.at[i, "src_zone"],
                "berth": zinfo.get("berth", ev.at[i, "src_zone"]),
                "facility": zinfo.get("facility"), "facility_type": ft,
                "src_mile": ev.at[i, "src_mile"], "mile": zinfo.get("mile"),
                "draft_ft": ev.at[i, "draft_ft"],
                "src_agent": ev.at[i, "src_agent"], "agency": own_agency,
                "agency_leg": m["agency"],
                "agency_normalized": bool(m["agency"] is not None and own_agency != m["agency"]),
                "berth_stop_seq": stop_seq_of.get(i),
                "is_berth_stop": ft not in NON_BERTH,
                "is_anchorage": ft == "Anchorage",
                "is_waiting_time": i in m["waiting_idx"],
                "dwell_hours": dwell_of.get(i),
                "hours_since_prev": hours(ev.at[call[p - 1], "event_time"], ev.at[i, "event_time"]) if p else None,
                "activity": m["activity"], "activity_method": m["method"],
                "cargo_group": m["cargo_group"], "cargo": m["cargo"],
                "destination": m["destination"], "actual_tons": m["tons"],
                "call_status": status, "is_split_call": leg_count > 1,
                "agency_fee": ev.at[i, "agency_fee"],
            })

        entry_draft = ev.at[first_i, "draft_ft"] if has_entry else None
        exit_draft = ev.at[last_i, "draft_ft"] if has_exit else None
        call_rows.append({
            "port_call_id": call_id, "vessel_key": vessel_key,
            "imo": ev.at[first_i, "imo"], "vessel_name": ev.at[first_i, "dim_vessel_name"],
            "call_name": name_at(aliases, vessel_key, start_t, ev.at[first_i, "dim_vessel_name"]),
            "vessel_type": ev.at[first_i, "vessel_type_canonical"],
            "ship_type_group": ev.at[first_i, "ship_type_group"],
            "dwt": ev.at[first_i, "dwt"], "tpc": ev.at[first_i, "tpc"],
            "call_start": start_t, "call_end": end_t, "call_hours": hours(start_t, end_t),
            "call_status": status, "is_complete": status == "complete",
            "leg_count": leg_count, "is_split": leg_count > 1,
            "berth_stop_count": len(stops),
            "anchorage_stop_count": sum(1 for i in call if ev.at[i, "facility_type"] == "Anchorage"
                                        and ev.at[i, "action_src"] == "Arrive"),
            "event_count": len(call),
            "entry_draft_ft": entry_draft, "exit_draft_ft": exit_draft,
            "agency": leg_meta[0]["agency"] if leg_meta else None,
            "agency_fee_total": sum(lr["agency_fee"] or 0 for lr in leg_rows[-leg_count:]) or None,
            "agency_fee_departures_total": sum(ev.at[i, "agency_fee"] for i in call
                                               if pd.notna(ev.at[i, "agency_fee"])) or None,
            "fgis_record_count": sum(lr["fgis_record_count"] for lr in leg_rows[-leg_count:]),
            "fgis_metric_tons": sum(lr["actual_tons"] or 0 for lr in leg_rows[-leg_count:]) or None,
        })

    for i, reason in unassigned:
        event_rows.append({
            "event_key": int(ev.at[i, "event_key"]), "port_call_id": None, "leg_id": None,
            "leg_seq": None, "event_seq": None, "unassigned_reason": reason,
            "vessel_key": int(ev.at[i, "vessel_key"]),
            "src_imo": ev.at[i, "src_imo"], "imo": ev.at[i, "imo"],
            "vessel_name": name_at(aliases, int(ev.at[i, "vessel_key"]), ev.at[i, "event_time"],
                                   ev.at[i, "dim_vessel_name"]),
            "src_vessel_type": ev.at[i, "src_vessel_type"],
            "vessel_type": ev.at[i, "vessel_type_canonical"],
            "ship_type_group": ev.at[i, "ship_type_group"],
            "dwt": ev.at[i, "dwt"], "tpc": ev.at[i, "tpc"],
            "src_action": ev.at[i, "action_src"],
            "action": (ACTION_ANCHOR if ev.at[i, "facility_type"] == "Anchorage" else ACTION_BERTH)
                      .get(ev.at[i, "action_src"], ev.at[i, "action_src"]),
            "event_time": ev.at[i, "event_time"], "src_zone": ev.at[i, "src_zone"],
            "berth": zone_lookup(zdict, ev.at[i, "src_zone"], ev.at[i, "action_src"]).get("berth", ev.at[i, "src_zone"]),
            "facility": zone_lookup(zdict, ev.at[i, "src_zone"], ev.at[i, "action_src"]).get("facility"),
            "facility_type": ev.at[i, "facility_type"],
            "src_mile": ev.at[i, "src_mile"],
            "mile": zone_lookup(zdict, ev.at[i, "src_zone"], ev.at[i, "action_src"]).get("mile"),
            "draft_ft": ev.at[i, "draft_ft"], "src_agent": ev.at[i, "src_agent"],
            "agency": agent_map.get(ev.at[i, "src_agent"], ev.at[i, "src_agent"]) or None,
            "agency_leg": None, "agency_normalized": False,
            "berth_stop_seq": None, "is_berth_stop": ev.at[i, "facility_type"] not in NON_BERTH,
            "is_anchorage": ev.at[i, "facility_type"] == "Anchorage",
            "is_waiting_time": False, "dwell_hours": None, "hours_since_prev": None,
            "activity": None, "activity_method": None, "cargo_group": None, "cargo": None,
            "destination": None, "actual_tons": None, "call_status": "unassigned",
            "is_split_call": False, "agency_fee": ev.at[i, "agency_fee"],
        })

    return (pd.DataFrame(call_rows), pd.DataFrame(leg_rows), pd.DataFrame(event_rows))


# ---------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------

def validate(con, calls_df, legs_df, events_df, guards):
    """Every check that must hold before this layer is allowed into the database.

    HARD checks are invariants -- if one is false the code is wrong, and the
    build aborts without writing. SOFT checks are health signals about the
    source data; they are reported and watched, never fatal.
    """
    src = con.execute("select event_key, agency_fee from fact_zone_event").df()
    src_keys = set(src.event_key.astype(int))
    out_keys = set(events_df.event_key.astype(int))

    guards.hard("spine has one row per source event",
                len(events_df) == len(src),
                f"{len(events_df):,} rows vs {len(src):,} source events")
    guards.hard("spine event_key is unique",
                events_df.event_key.is_unique,
                f"{len(events_df) - events_df.event_key.nunique():,} duplicates")
    guards.hard("spine covers exactly the source event set",
                out_keys == src_keys,
                f"{len(src_keys - out_keys):,} missing / {len(out_keys - src_keys):,} unknown")

    guards.hard("port_call_id is unique", calls_df.port_call_id.is_unique,
                f"{len(calls_df) - calls_df.port_call_id.nunique():,} duplicates")
    guards.hard("leg_id is unique", legs_df.leg_id.is_unique,
                f"{len(legs_df) - legs_df.leg_id.nunique():,} duplicates")

    assigned = events_df[events_df.port_call_id.notna()]
    guards.hard("every assigned event points at a real call",
                set(assigned.port_call_id) <= set(calls_df.port_call_id),
                f"{len(set(assigned.port_call_id) - set(calls_df.port_call_id)):,} orphans")
    guards.hard("every assigned event points at a real leg",
                set(assigned.leg_id) <= set(legs_df.leg_id),
                f"{len(set(assigned.leg_id) - set(legs_df.leg_id)):,} orphans")
    guards.hard("every event is either assigned to a call or carries a reason why not",
                bool((events_df.port_call_id.notna() | events_df.unassigned_reason.notna()).all()),
                f"{int((events_df.port_call_id.isna() & events_df.unassigned_reason.isna()).sum()):,} silent drops")

    leg_seq_ok = legs_df.groupby("port_call_id").leg_seq.agg(
        lambda s: sorted(s) == list(range(1, len(s) + 1))).all()
    guards.hard("leg_seq is contiguous from 1 within every call", bool(leg_seq_ok))
    guards.hard("leg_count matches the legs actually written",
                bool((legs_df.groupby("port_call_id").size() ==
                      calls_df.set_index("port_call_id").leg_count).all()))

    counted = assigned.groupby("port_call_id").size()
    guards.hard("call event_count matches the spine",
                bool((counted.reindex(calls_df.port_call_id).fillna(0).values ==
                      calls_df.event_count.values).all()))

    guards.hard("no call ends before it starts",
                bool((calls_df.call_end >= calls_df.call_start).all()),
                f"{int((calls_df.call_end < calls_df.call_start).sum()):,} inverted")
    bad_order = assigned.sort_values(["port_call_id", "event_seq"]).groupby("port_call_id").event_time.apply(
        lambda s: s.is_monotonic_increasing).eq(False).sum()
    guards.hard("events within a call are in time order", bad_order == 0, f"{bad_order:,} calls out of order")

    guessed = legs_df[(legs_df.activity.notna()) &
                      (~legs_df.activity_method.isin(["draft_delta", "fgis", "dictionary"]))]
    guards.hard("no activity is asserted without a named method",
                len(guessed) == 0, f"{len(guessed):,} legs")
    guards.hard("no cargo is asserted without a source",
                len(legs_df[(legs_df.cargo_group.notna()) & (legs_df.cargo_source.isna())]) == 0)

    # The billing rule: one fee per leg that reached a berth, none otherwise.
    berthed = legs_df.berth_stop_count > 0
    guards.hard("no fee accrues on a leg that never berthed",
                bool(legs_df.loc[~berthed, "agency_fee"].isna().all()),
                f"{int(legs_df.loc[~berthed, 'agency_fee'].notna().sum()):,} legs")
    guards.hard("call fee total is the sum of its legs",
                abs(float(legs_df.agency_fee.fillna(0).sum()) -
                    float(calls_df.agency_fee_total.fillna(0).sum())) < 0.01)

    fee_src = float(src.agency_fee.fillna(0).sum())
    fee_out = float(events_df.agency_fee.fillna(0).sum())
    guards.hard("agency fees are carried through unchanged",
                abs(fee_src - fee_out) < 0.01, f"${fee_src:,.0f} vs ${fee_out:,.0f}")

    try:
        fgis_tons = con.execute("""select coalesce(sum(metric_ton_total),0) from fgis_record
                                   where match_status='matched' and mrtis_event_key is not null""").fetchone()[0]
        fgis_recs = con.execute("""select count(*) from fgis_record
                                   where match_status='matched' and mrtis_event_key is not null""").fetchone()[0]
        out_tons = float(legs_df.actual_tons.fillna(0).sum())
        out_recs = int(legs_df.fgis_record_count.fillna(0).sum())
        # A certificate can only reach a leg if its berth event was placed in a
        # call. Where the source feed never recorded the SWP entry, the event is
        # unplaced and the certificate has nowhere to land -- that is a gap in
        # the Zone Report, not a matching error, so it is measured and excluded
        # from the invariant rather than allowed to mask a real loss.
        unplaced_keys = set(events_df.loc[events_df.port_call_id.isna(), "event_key"].astype(int))
        orph = con.execute("""select mrtis_event_key k, count(*) n, sum(metric_ton_total) t
                              from fgis_record
                              where match_status='matched' and mrtis_event_key is not null
                              group by 1""").df()
        orph = orph[orph.k.astype(int).isin(unplaced_keys)]
        orph_recs, orph_tons = int(orph.n.sum()), float(orph.t.sum())
        guards.hard("every matched FGIS certificate lands on a leg (or its event was never placed in a call)",
                    out_recs + orph_recs == fgis_recs,
                    f"{out_recs:,} on legs + {orph_recs:,} unplaceable vs {fgis_recs:,} matched")
        guards.hard("FGIS tonnage is neither lost nor double counted",
                    abs(out_tons + orph_tons - fgis_tons) < 1.0,
                    f"{out_tons:,.0f} + {orph_tons:,.0f} vs {fgis_tons:,.0f} t")
        guards.soft("FGIS certificates stranded on unplaced events",
                    f"{orph_recs:,} certificates / {orph_tons:,.0f} t sit on events with no SWP entry "
                    f"in the feed, so they reach no leg", passed=orph_recs == 0)
    except duckdb.CatalogException:
        guards.soft("FGIS cross-reference present", "no fgis_record table -- cargo columns will be empty",
                    passed=False)

    # ---- soft: how healthy is the source data this run ----------------------
    n_ev = len(events_df)
    unas = int(events_df.port_call_id.isna().sum())
    guards.soft("events placed in a call",
                f"{n_ev - unas:,} of {n_ev:,} ({(n_ev - unas) / n_ev:.1%}); {unas:,} unplaced")
    for status, n in calls_df.call_status.value_counts().items():
        guards.soft(f"calls with status '{status}'", f"{n:,} ({n / len(calls_df):.1%})",
                    passed=(status == "complete"))
    unresolved = int((legs_df.activity.isna()).sum())
    guards.soft("legs with a resolved activity",
                f"{len(legs_df) - unresolved:,} of {len(legs_df):,} "
                f"({(len(legs_df) - unresolved) / len(legs_df):.1%}); {unresolved:,} unresolved",
                passed=unresolved == 0)
    conflicts = int(legs_df.activity_conflict.sum())
    guards.soft("evidence agrees on activity (draft vs FGIS vs dictionary)",
                f"{conflicts:,} legs where two sources disagree; the draft wins and the leg is flagged",
                passed=conflicts == 0)
    no_agency = int(legs_df.agency.isna().sum())
    guards.soft("legs with an agency resolved",
                f"{len(legs_df) - no_agency:,} of {len(legs_df):,}; {no_agency:,} with none recorded anywhere",
                passed=no_agency == 0)
    long_calls = int((calls_df.call_hours > 180 * 24).sum())
    guards.soft("calls longer than 180 days", f"{long_calls:,}", passed=long_calls == 0)
    guards.soft("split calls", f"{int(calls_df.is_split.sum()):,} of {len(calls_df):,} "
                               f"({calls_df.is_split.mean():.1%})")
    return guards


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

EVENT_COLUMNS = [
    "event_key", "port_call_id", "leg_id", "leg_seq", "event_seq", "unassigned_reason",
    "vessel_key", "src_imo", "imo", "vessel_name", "src_vessel_type", "vessel_type",
    "ship_type_group", "dwt", "tpc", "src_action", "action", "event_time", "src_zone",
    "berth", "facility", "facility_type", "src_mile", "mile", "draft_ft", "src_agent",
    "agency", "agency_leg", "agency_normalized", "berth_stop_seq", "is_berth_stop",
    "is_anchorage", "is_waiting_time", "dwell_hours", "hours_since_prev", "activity",
    "activity_method", "cargo_group", "cargo", "destination", "actual_tons",
    "call_status", "is_split_call", "agency_fee",
]


def align_to_table(con, table, df):
    """Order a frame's columns to match the table it is about to be inserted
    into, and refuse if the two disagree. Positional INSERT is fast but silently
    shifts every value one column left if the DDL and the builder drift apart --
    this makes that drift an error instead of a corrupted table."""
    cols = [r[0] for r in con.execute(
        "select column_name from information_schema.columns "
        "where table_name = ? order by ordinal_position", [table]).fetchall()]
    missing, extra = set(cols) - set(df.columns), set(df.columns) - set(cols)
    if missing or extra:
        raise AssertionError(
            f"{table}: builder and schema disagree -- missing {sorted(missing)}, extra {sorted(extra)}")
    return df[cols]


def write_db(con, calls_df, legs_df, events_df):
    with open(SCHEMA_PATH) as fh:
        schema_sql = fh.read()
    con.execute("BEGIN TRANSACTION")
    try:
        for t in ("port_call_event", "port_call_leg", "port_call"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute(schema_sql)
        pc = align_to_table(con, "port_call", calls_df)          # noqa: F841 -- read by duckdb
        pl = align_to_table(con, "port_call_leg", legs_df)        # noqa: F841
        pe = align_to_table(con, "port_call_event", events_df)    # noqa: F841
        con.execute("INSERT INTO port_call SELECT * FROM pc")
        con.execute("INSERT INTO port_call_leg SELECT * FROM pl")
        con.execute("INSERT INTO port_call_event SELECT * FROM pe")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def write_report(path, calls_df, legs_df, events_df, guards):
    n_ev, n_call, n_leg = len(events_df), len(calls_df), len(legs_df)
    placed = int(events_df.port_call_id.notna().sum())
    L = []
    A = L.append
    A("# MRTIS Port Call Quality Report\n")
    A(f"_Generated by `scripts/build_port_calls.py` on {datetime.now():%Y-%m-%d %H:%M}_\n")
    A("## Shape\n")
    A(f"- Source events on the spine: **{n_ev:,}** (every `fact_zone_event` row, exactly once)")
    A(f"- Placed in a port call: {placed:,} ({placed / n_ev:.1%}); unplaced: {n_ev - placed:,}")
    A(f"- Port calls assembled: **{n_call:,}**")
    A(f"- Legs: **{n_leg:,}** -- {int(calls_df.is_split.sum()):,} calls "
      f"({calls_df.is_split.mean():.1%}) are split calls\n")

    A("## Call completeness\n")
    A("A call is `complete` when it both enters and exits Southwest Pass. The rest are real "
      "gaps in the source feed, kept and flagged so they can be excluded from duration "
      "analytics deliberately rather than quietly distorting them.\n")
    A("| Status | Calls | % |\n|---|---|---|")
    for s, n in calls_df.call_status.value_counts().items():
        A(f"| `{s}` | {n:,} | {n / n_call:.1%} |")
    A("")
    if (events_df.unassigned_reason.notna()).any():
        A("Unplaced events, by reason:\n")
        A("| Reason | Events |\n|---|---|")
        for r, n in events_df.unassigned_reason.value_counts().items():
            A(f"| `{r}` | {n:,} |")
        A("")

    A("## What the vessel did, and how we know\n")
    A("Evidence order: draft delta (physics) -> FGIS certificate -> zone dictionary -> nothing. "
      "An unresolved leg keeps `activity` NULL; it is never guessed.\n")
    A("| Method | Legs | % | Activity |\n|---|---|---|---|")
    for m, n in legs_df.activity_method.value_counts().items():
        acts = legs_df[legs_df.activity_method == m].activity.value_counts()
        summary = ", ".join(f"{k} {v:,}" for k, v in acts.items()) or "-"
        A(f"| `{m}` | {n:,} | {n / n_leg:.1%} | {summary} |")
    A("")
    A("| Activity | Legs | % |\n|---|---|---|")
    for a, n in legs_df.activity.value_counts(dropna=False).items():
        A(f"| {a if pd.notna(a) else '_(unresolved)_'} | {n:,} | {n / n_leg:.1%} |")
    A("")
    conf = int(legs_df.activity_conflict.sum())
    A(f"- Legs where two pieces of evidence disagree: **{conf:,}** ({conf / n_leg:.2%}). "
      f"The draft wins; the flag (`activity_conflict`, `activity_conflict_reason`) is how the "
      f"disagreement gets found rather than quietly settled.\n")
    if conf:
        A("| Conflict | Legs |\n|---|---|")
        for r, n in legs_df.activity_conflict_reason.value_counts().items():
            A(f"| `{r}` | {n:,} |")
        A("")
        dic = legs_df[legs_df.activity_conflict_reason == "dictionary"]
        if len(dic):
            A("**Zones where the draft repeatedly contradicts the dictionary's `ops`** -- these are "
              "berths `dictionaries/zone_facility.csv` marks as one-directional but which the draft "
              "shows working both ways. Worth correcting in the dictionary:\n")
            A("| Zone | Legs | Median draft change (ft) |\n|---|---|---|")
            g = dic.groupby("first_berth_zone").agg(n=("leg_id", "size"),
                                                    med=("draft_delta_ft", "median"))
            for zone, row in g.sort_values("n", ascending=False).head(10).iterrows():
                A(f"| {zone} | {int(row.n):,} | {row.med:+.0f} |")
            A("")

    A("### Where the unresolved legs are\n")
    A("Filling `ops` in the zone dictionary for these facility types is what would move the "
      "resolved share; nothing else can, because the draft is already flat on them.\n")
    A("| First berth facility type | Unresolved legs |\n|---|---|")
    u = legs_df[legs_df.activity.isna()]
    for ftype, n in u.facility_type.value_counts(dropna=False).items():
        label = ftype if pd.notna(ftype) else "_(the call never berthed)_"
        A(f"| {label} | {n:,} |")
    A("")

    A("## Agency fee\n")
    ruled = float(legs_df.agency_fee.fillna(0).sum())
    dep = float(events_df.agency_fee.fillna(0).sum())
    billable = int(legs_df.agency_fee.notna().sum())
    A("William's ruling, 2026-08-19: *\"agency fee is per port call, not per berth except when "
      "split discharge then load\"* -- so the billing unit is the **leg**. One fee per leg that "
      "reached a berth; a call that discharges and then loads is two.\n")
    A("| Basis | Chargeable units | Total |\n|---|---|---|")
    A(f"| **Per leg with a berth stop (the ruling)** | **{billable:,} legs** | **${ruled:,.0f}** |")
    A(f"| Per berth departure (pre-ruling, kept for comparison) | "
      f"{int(events_df.agency_fee.notna().sum()):,} sailings | ${dep:,.0f} |")
    A(f"| Difference | | ${ruled - dep:,.0f} ({(ruled - dep) / dep:+.1%}) |")
    A("")
    A(f"- Legs that never reached a berth and accrue nothing: "
      f"{int((legs_df.berth_stop_count == 0).sum()):,}")
    nocargo = legs_df[legs_df.activity == "No Cargo"]
    A(f"- `No Cargo` legs that berthed but worked no cargo (bunkers, stores, repair, lay-by): "
      f"{len(nocargo):,}, currently charged ${nocargo.agency_fee.fillna(0).sum():,.0f} -- "
      f"**open**: a ship at a berth is still being agented, but confirm.")
    A(f"- Chargeable legs whose activity is unresolved: "
      f"{int((legs_df.agency_fee.notna() & legs_df.activity.isna()).sum()):,} -- the split rule "
      f"never lets an unknown invent a split, so genuine Discharge->Load calls in here are still "
      f"being billed once. The total is a floor.\n")
    A("The per-departure figure is carried through on `port_call_event.agency_fee` unchanged and "
      "reconciled by a hard guardrail, so the two bases can be compared row by row. Do not sum "
      "that column for billing.\n")

    A("## Agency\n")
    A("Each leg is owned by the agency that brought the vessel in. That inbound agency is "
      "applied to every event of the leg -- it fills the source blanks and undoes the "
      "pilot-sheet artefact where an outbound agent appears on a sailing another agency worked.\n")
    A("| Where the leg's agency came from | Legs | % |\n|---|---|---|")
    for s, n in legs_df.agency_source.value_counts().items():
        A(f"| `{s}` | {n:,} | {n / n_leg:.1%} |")
    A("")
    norm = int(events_df.agency_normalized.sum())
    blank_src = int(events_df.src_agent.isna().sum())
    A(f"- Events whose operating agency differs from the agent on the row: **{norm:,}** "
      f"({norm / n_ev:.1%}) -- of which {blank_src:,} were blank in the source and are now filled.")
    A(f"- Legs where the source showed more than one agency inside the same leg: "
      f"{int(legs_df.agent_changed_in_leg.sum()):,}\n")

    A("## Time\n")
    w = legs_df[legs_df.waiting_hours > 0].waiting_hours
    A("Waiting time is only anchorage dwell **before** the leg's first berth arrival -- time spent "
      "waiting for that berth. Dwell after the last sailing is outbound idling and is reported "
      "separately so it can never be counted as berth waiting.\n")
    A(f"- Legs with berth waiting time: {len(w):,} of {n_leg:,} ({len(w) / n_leg:.1%})")
    if len(w):
        A(f"- Waiting hours: median {w.median():.1f}, mean {w.mean():.1f}, p90 {w.quantile(0.9):.1f}, max {w.max():.1f}")
    b = legs_df[legs_df.berth_hours > 0].berth_hours
    if len(b):
        A(f"- Hours alongside: median {b.median():.1f}, mean {b.mean():.1f}, p90 {b.quantile(0.9):.1f}")
    o = legs_df[legs_df.outbound_idle_hours > 0].outbound_idle_hours
    A(f"- Legs with outbound idling (excluded from waiting): {len(o):,}"
      + (f", median {o.median():.1f} h" if len(o) else ""))
    A("")

    A("## Cargo\n")
    with_cargo = legs_df[legs_df.cargo_group.notna()]
    A(f"- Legs carrying a cargo group: {len(with_cargo):,} of {n_leg:,} ({len(with_cargo) / n_leg:.1%})")
    A("| Source | Legs | Tons |\n|---|---|---|")
    for s, sub in legs_df.groupby("cargo_source"):
        A(f"| `{s}` | {len(sub):,} | {sub.actual_tons.fillna(0).sum():,.0f} |")
    A("")
    A("- Cargo, shipper, consignee, receiver, last/next port, origin and estimated tons stay "
      "**out of this table until a source exists for them**. FGIS supplies grain cargo, "
      "destination and certified tonnage; the zone dictionary supplies a cargo group where the "
      "berth can only ever handle one. Nothing else is inferred.\n")

    A("## Guardrails\n")
    A("Checked on every build, over the whole data set. A HARD failure aborts the build and "
      "leaves the database untouched; SOFT rows are health signals about the source data.\n")
    A(guards.to_markdown())
    A("")
    with open(path, "w") as fh:
        fh.write("\n".join(L))


# ---------------------------------------------------------------------------
# review sample
# ---------------------------------------------------------------------------

SAMPLE_SCENARIOS = [
    ("split call, discharge then load", lambda c, l: c.is_split & (c.berth_stop_count >= 2)),
    ("single grain load, FGIS cargo attached", lambda c, l: (~c.is_split) & (c.fgis_record_count > 0)),
    ("single discharge call", lambda c, l: (~c.is_split) & c.port_call_id.isin(
        l[l.activity == "Discharge"].port_call_id)),
    ("agency normalized inside the call", lambda c, l: c.port_call_id.isin(
        l[l.agent_changed_in_leg].port_call_id)),
    ("activity unresolved -- flat draft, no certificate", lambda c, l: c.port_call_id.isin(
        l[l.activity.isna()].port_call_id)),
    ("long wait for the berth", lambda c, l: c.port_call_id.isin(
        l[l.waiting_hours > 240].port_call_id)),
]


def export_sample(events_df, calls_df, legs_df, path, n):
    """Write a handful of whole port calls, every event, for eyeball review.

    Deliberately picked to cover the cases the assembly has to get right rather
    than sampled at random -- a random six would be six ordinary single grain
    loads and would prove nothing.
    """
    picked, notes = [], {}
    for label, pred in SAMPLE_SCENARIOS[:n]:
        cand = calls_df[pred(calls_df, legs_df) & ~calls_df.port_call_id.isin(picked)]
        cand = cand[cand.call_status == "complete"].sort_values("call_start", ascending=False)
        if len(cand):
            cid = cand.iloc[0].port_call_id
            picked.append(cid)
            notes[cid] = label
    sub = events_df[events_df.port_call_id.isin(picked)].copy()
    sub["scenario"] = sub.port_call_id.map(notes)
    order = {cid: k for k, cid in enumerate(picked)}
    sub = sub.sort_values(["port_call_id", "event_seq"], key=lambda s: s.map(order) if s.name == "port_call_id" else s)
    cols = ["scenario", "port_call_id", "leg_seq", "event_seq", "vessel_name", "imo", "vessel_type",
            "action", "event_time", "berth", "facility", "facility_type", "mile", "draft_ft",
            "activity", "activity_method", "cargo_group", "cargo", "destination", "actual_tons",
            "src_agent", "agency_leg", "agency_normalized", "is_waiting_time", "dwell_hours",
            "agency_fee", "src_zone", "src_action", "src_mile", "src_imo", "event_key"]
    sub[cols].to_csv(path, index=False)

    # A leg-level companion: the same calls one row per leg, which is the grain
    # the agency fee is billed on and the fastest way to check the split.
    legs = legs_df[legs_df.port_call_id.isin(picked)].copy()
    legs["scenario"] = legs.port_call_id.map(notes)
    legs = legs.sort_values(["port_call_id", "leg_seq"],
                            key=lambda s: s.map(order) if s.name == "port_call_id" else s)
    leg_cols = ["scenario", "port_call_id", "leg_seq", "leg_count", "activity", "activity_method",
                "activity_conflict_reason", "draft_delta_ft", "first_berth_facility",
                "facility_type", "berth_stop_count", "berth_arrive_time", "berth_depart_time",
                "waiting_hours", "berth_hours", "inter_berth_idle_hours", "outbound_idle_hours",
                "agency", "agency_source", "agent_changed_in_leg", "cargo_group", "cargo",
                "cargo_source", "destination", "actual_tons", "fgis_record_count",
                "agency_fee", "agency_fee_departures"]
    leg_path = path.replace(".csv", "_legs.csv")
    legs[leg_cols].to_csv(leg_path, index=False)
    return picked, notes


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Assemble MRTIS port calls from the zone-event warehouse.")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--sample", type=int, default=0, help="export N whole port calls for review")
    ap.add_argument("--sample-out", default=os.path.join(ROOT, "sample_port_calls.csv"))
    ap.add_argument("--min-draft-delta", type=int, default=1,
                    help="feet of draft change required before the draft is read as a cargo "
                         "operation. 1 (default) trusts the source as recorded; raising it "
                         "treats small changes as ballast/bunker noise and falls through to "
                         "FGIS and the dictionary instead.")
    ap.add_argument("--dry-run", action="store_true", help="assemble and validate, write nothing")
    args = ap.parse_args()

    guards = Guardrails("port call assembly")
    con = duckdb.connect(args.db_path)

    print("Loading warehouse ...")
    ev = load_events(con)
    ev["action_src"] = ev["action"] if "action" in ev.columns else ev["action_src"]
    ev = ev.reset_index(drop=True)
    aliases = load_event_names(con)
    fgis = load_fgis(con)
    zdict = load_zone_dict(ZONE_DICT)
    agent_map = load_agent_dict(AGENT_DICT, guards)
    print(f"  {len(ev):,} events, {len(fgis):,} FGIS-linked berth events, "
          f"{len(zdict):,} zone dictionary entries")

    print("Assembling port calls ...")
    calls, unassigned = assemble_calls(ev)
    calls_df, legs_df, events_df = build_frames(ev, calls, unassigned, zdict, agent_map,
                                                aliases, fgis, guards, args.min_draft_delta)
    print(f"  {len(calls_df):,} calls, {len(legs_df):,} legs, {len(events_df):,} spine rows")

    print("Checking guardrails ...")
    validate(con, calls_df, legs_df, events_df, guards)
    print(guards.to_console())
    guards.raise_if_failed()

    if args.dry_run:
        print("\n--dry-run: nothing written.")
    else:
        write_db(con, calls_df, legs_df, events_df)
        write_report(REPORT_PATH, calls_df, legs_df, events_df, guards)
        print(f"\nWrote port_call / port_call_leg / port_call_event to {args.db_path}")
        print(f"Wrote {os.path.relpath(REPORT_PATH, ROOT)}")

    if args.sample:
        picked, notes = export_sample(events_df, calls_df, legs_df, args.sample_out, args.sample)
        print(f"\nSample of {len(picked)} port calls -> {args.sample_out}")
        for cid in picked:
            print(f"  {cid:<28} {notes[cid]}")

    con.close()


if __name__ == "__main__":
    main()
