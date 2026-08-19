# MRTIS canonical build — open questions

Working list from the 2026-08-18 spec discussion (`logic.md`). Answer inline
or however's easiest — nothing here blocks the dictionary crosswalks, which
are already sent separately for you to fill in.

## 1. IMO cleanup — edge cases beyond the 9-digit rule — ANSWERED 2026-08-18

**Rule confirmed by William**: keep only the first 7 digits of the raw
value, drop anything after position 7 — applies to 8-digit and 9-digit
values alike, no special-casing by length. If the raw value has fewer than
7 digits, ignore it entirely (treat as no valid IMO, same as blank, fall
back to identifying the vessel by name).

For reference, the length distribution across all 314,335 rows this rule
applies to:

| IMO length | rows | outcome |
|---|---|---|
| 7 (valid) | 289,275 | used as-is |
| 9 | 13,976 | truncate to first 7 |
| 8 | 664 | truncate to first 7 |
| 0 (blank) | 9,757 | ignored — identify by name |
| 3 | 637 | ignored — identify by name |
| 6 | 10 | ignored — identify by name |
| 4 | 16 | ignored — identify by name |

Needs a code change in `scripts/lib/parse.py` when the canonical layer is
built (v0.1's `is_valid_imo()` currently rejects 8/9-digit values outright
instead of truncating them) — not yet applied to the live v0.1 schema.

## 2. Dredge/noise vessel exclusion — ANSWERED 2026-08-19

**Decided by William**: remove them at the front end rather than flagging
them — "if we remove the dredges on the list also on front end, removes
those records and focuses the table". `scripts/build_db.py` now filters the
9 vessels marked `exclude_as_dredge=Y` out at ingest (23,228 rows, 7.4% of
the raw feed). `--keep-dredges` restores them without editing the
dictionary, and `docs/DATA_QUALITY.md` always reports the per-vessel counts
dropped, so the removal is visible rather than silent.

**Matching is by IMO wherever the dictionary supplies one**, not by name.
This matters: "Texas Star" is BOTH a dredge (`imo_raw` 311000000) and,
separately, a real tanker (IMO 9256860, 20 events). A name-based filter
deleted the tanker too. Names are used only for the four entries that have
no IMO at all (Allisonk, Allins K, Keeneland, Ginny Lab), and even then only
against rows carrying no valid IMO themselves.

The Carnival cruise ships in the list are marked blank (not `Y`) and are
therefore kept, which matches the reading below that scheduled cruise
turnarounds are real traffic rather than noise.

### Original question (for context)

- Filter these completely out of the warehouse, or flag them with a boolean
  (e.g. `is_high_frequency_noise`) so they stay in the data but default-
  excluded from reports/analytics? I'd lean flag-not-delete so nothing is
  silently lost.
- Top-25 list is attached separately for you to review — is 25 a fixed
  count, or should the build recompute "top N by record count" fresh on
  every rebuild (so it adapts as more years are added)?
- Looking at the actual top 25: some are obviously repetitive
  workboats/tows (Mack B, Allisonk, Allins K, Ginny Lab), but three are
  Carnival cruise ships (Valor, Glory, Liberty) doing frequent scheduled
  turnarounds — those are probably NOT noise in the same sense. Worth a
  quick pass to confirm which of the 25 you actually want excluded vs. kept
  as legitimately high-frequency real traffic.

## 3. Mile marker format variants and the SWP -20 default

- You mentioned seeing '134M', 'M134', and '134 M' formats depending on
  agent. The current MRTIS CSVs (2019-2026) only show trailing-'M' format
  ('134M', '-19M') in my profiling — are the other formats from a different
  source/export you'll be adding later, or did I miss them somewhere in
  this data?
- The -20 default mile for SWP Cross events — is that a **fallback only
  when Mile is blank**, or should it **override** an existing raw value
  too? I ask because a meaningful share of SWP Cross rows already carry a
  real mile value (e.g. "-19M") in the source data.

## 4. Split-call detection and the agent-normalization rule

You said this will likely get clearer once the zone/facility dictionary is
in place, so no need to answer this yet — flagging it here so it's not
forgotten. Working hypothesis to react to whenever you're ready: a port
call is "split" when it contains two or more Terminal (berth) stops
between one Enter-SWP and the matching Exit-SWP; the depart-agent-
normalization rule (revert depart agent to match the inbound agent) only
applies when there's exactly one berth stop, since a genuine second berth
means a genuine second agent is plausible. Also still open: how buoys
zones (133 Buoys, 134 Buoys, etc.) should be treated in the assembly —
pure dwell like an anchorage, or can they host actual cargo transfer
(lightering) that should be treated more like a berth stop?

**Real-world confirmation, 2026-08-18** (from 4 Statements of Fact William
shared — Desert Seeker, Ultra Leopard x2, Asian Eternity):

- **Split call, confirmed with a real example.** MV Ultra Leopard (IMO
  9758428) discharged iron ore at Nucor Convent, LA (May 3-8, 2026, Master
  Alfredo Gavilo Jr), then — without leaving the river — sat idle at AMA
  Anchorage for a week, shifted to LaPlace Anchorage, and then loaded
  soybeans at ADM-Reserve, LA (May 15-26, 2026, Master Archie Pinangay,
  different cargo, different berth, different charter). Same IMO, same
  continuous river presence, two genuinely distinct legs (discharge then
  load) with different cargo, different berth, and even a **different
  master** in between. This is exactly the discharge-leg + load-leg split
  call the working hypothesis describes — and it shows master/crew change
  mid-call is not a signal that it's a different vessel or a data error.
- **Buoys/Mid-Stream zones do host real cargo activity, not just dwell.**
  MV Desert Seeker loaded salt in bulk at "ARTCO BUOYS MILE 121 LOWER" and
  MV Asian Eternity discharged salt at "ARTCO BUOYS MILE 110" — both
  full berth-style calls (tugs alongside, all-fast, cargo ops, standby-
  weather delays, departure conditions) at a Mid-Stream buoy location, not
  an anchorage. So Mid-Stream zones need to be eligible as genuine
  activity/berth stops in the assembly, same as Terminal/Elevator zones —
  confirms the `facility_type = 'Mid-Stream'` rows in
  `dictionaries/zone_facility.csv` (39 of them, only 4 pre-tagged
  `Cargo group = Grain`) are real candidate activity locations, not just
  staging. The zone dictionary's `Cargo group` tag looks like it reflects
  each zone's *typical* cargo rather than an exhaustive/exclusive list —
  Desert Seeker/Asian Eternity both moved salt through Mid-Stream buoys
  that aren't tagged `Grain`, so cargo type has to be confirmed per-call
  (from draft delta / SOF / FGIS-type external data), not assumed from the
  zone alone.
- **Anchorage-hopping before a berth call is normal, not a separate call.**
  Ultra Leopard idled at AMA Anchorage, then explicitly shifted (pilot
  aboard, anchors aweigh, transit, dropped anchor again) to LaPlace
  Anchorage, before ever reaching its loading berth. Multiple
  Anchorage-zone events ahead of the first Terminal/Elevator/Mid-Stream
  stop should collapse into pre-berth staging for the same call, not be
  read as separate port calls or separate legs.
- SOFs also show the anchorage stop itself can be governed by NOR
  (Notice of Readiness) tendering / re-tendering "without prejudice" while
  waiting on berth instructions — multi-day anchorage dwell before a berth
  is the norm for this traffic, not an anomaly to filter out.

## 5. Scope of the "new columns for later stages"

Activity (Load/Discharge/Load-Discharge) looks computable now from draft
deltas plus facility type once the zone/facility dictionary exists. Cargo
Group, Cargo, Shipper, Consignee, Receiver, Last/Next Port,
Destination/Origin, Vessel Type Group, DWT, TPC, Est/Actual Tons all need
data this dataset doesn't have. You mentioned matching against two other
tables to complete some of these — no need to detail that now, just
flagging that we'll pick this up when you're ready to bring those tables
in.

## 6. Dictionary provenance (resolved)

Zone, agent, and vessel-type crosswalk drafts are being generated fresh
from the actual data (raw value + occurrence count, with canonical columns
left blank for you to fill in) rather than assumed to exist already —
sent alongside this doc.
