# MRTIS session log

One entry per working session, newest first. This is the file the **begin-session
ritual** reads to recover context (`/Users/billy/Documents/File Maker/00_PROJECT/RITUALS.md`).
Decisions live in `docs/OPEN_QUESTIONS.md`, code history in `CHANGELOG.md`; this
is just the thread — what was done, what was decided, what to pick up next.

---

## 2026-08-19 — Ships register refreshed to the full world-fleet pull

**Objective**: §9 (extending the register to tankers/gas/containers) — check
whether the separate `Ships_Register` world-fleet expansion already
supersedes the chunk-pull plan before pulling from Sea-web again.

### Found

It does. William confirmed the expansion is done and the vessels are there.
`Ships_Register/data/out/ships_register.duckdb`'s `fleet_joined`: 49,763
rows, 133 `ship_type_group` values, zero blanks, zero null/duplicate IMOs —
exactly the shape recorded when that expansion happened. The chunk-pull plan
prepared for this session's §9 was never needed and was never run.

### Done

- Refreshed `dictionaries/ships_register_fleet.csv` from `fleet_joined` (the
  documented `docs/BUILD.md` procedure). Rebuilt the full chain
  (`build_db.py` → `build_fgis_match.py` → `build_port_calls.py`); all
  guardrails still pass.
- Match coverage against `dim_vessel`: **60.9% → 99.4%** of all vessels
  (**61.1% → 99.8%** of those with a valid IMO) — matches what was predicted
  when the world-fleet expansion happened. `dwt`/`tpc` now populate 99.7% of
  port calls, up from roughly 60%.
- 24 vessels with a valid IMO remain unmatched — the same known set as
  before (13 checksum-invalid, 10 behind the pre-1980 gate, 1 genuinely
  absent). No further pull needed.
- `docs/BUILD.md` "Ships register enrichment" rewritten (the dry-cargo-scope
  paragraph was now doubly stale); `OPEN_QUESTIONS.md` §9 marked resolved,
  original chunk-pull plan kept for the record.
- **Corrected an earlier same-day claim**: the `ship_type_group` backfill did
  *not* fix *Radcliffe R. Latimer*'s billing tier as I'd said — its Zone
  Report `Type` was already `Bulk`, which decides the fee before
  `ship_type_group` is ever consulted. Caught while verifying this refresh.
- **Raised, not fixed**: `vessel_key`/`event_key` are row-position, so this
  refresh — which touched only register enrichment — still forced a full
  FGIS + port-call rebuild from zero. Logged as `OPEN_QUESTIONS.md` §10.

### Decided (by William, 2026-08-19, closing the session)

- **§9 residual, closed, no change**: the two 1978 merchant vessels (Sunnanvik,
  Radcliffe R. Latimer) stay behind Sea-web's pre-1980 build-year gate. Both
  bill correctly regardless; they simply carry no `dwt`/`tpc`.
- **My earlier correction (the `Radcliffe R. Latimer` billing-tier claim)**:
  acknowledged, no further action.

### Open

- **§10**: is a stable, natural-key-derived `vessel_key`/`event_key` worth
  the schema change, given the register/dictionaries/feed all get revised on
  an ongoing basis and each one currently pays for a full downstream rebuild?
  Raised, not scoped, not started.

### Next session starts by

§7, §8 and §9 are fully resolved, nothing pending from either side. §10
(stable keys) is the only open thread, raised but deliberately not scoped —
pick it up only if/when William wants to size it. A fresh independent audit
is also due: the last one (`docs/audit/AUDIT_2026-08-19_0242.md`) predates
this entire session's work (tonnage rename, ship_type/backfill, §8 fee logic,
the world-fleet register refresh) — none of it has been adversarially
checked yet.

---

## 2026-08-19 — §8 resolved: layberth stops don't split and don't bill

### Decided (by William, 2026-08-19)

- **8a.** A layberth stop can't open a leg boundary — confirmed the earlier
  recommendation. Treated exactly like an unresolved stop for splitting.
- **8b.** "Also no fee on departing a layberth" — resolves 8b directly: a leg
  bills only if it did real, non-layberth work somewhere. A pure lay-up leg
  (nothing but layberth stops) accrues nothing.

### Done

- `split_into_legs()` and the leg-fee computation in
  `scripts/build_port_calls.py` updated; also had to fix the leg's own
  `activity`/`method` selection, since a leg can now legitimately mix a
  layberth stop with a real one (real activity always wins the leg's
  reported label over `No Cargo`).
- Rebuilt and reverified: split calls 4.4% → 4.1%, ruling-basis fee
  $304,808,000 → $298,868,500 (-$5,939,500). Checked the remaining 54 billed
  `No Cargo`-labeled legs individually — each genuinely touches a second,
  non-layberth berth (unresolved activity, e.g. bunkers/refinery), so they
  bill correctly and unchanged; the 142 genuinely pure lay-up legs are $0.
- `docs/OPEN_QUESTIONS.md` §8, `docs/PORT_CALL_SPEC.md` §4/§9 updated.

### Next session starts by

**OPEN_QUESTIONS §9** — extending the register to tankers/gas/containers;
check first whether the separate `Ships_Register` world-fleet expansion
already supersedes the chunk-pull plan recorded there.

---

## 2026-08-19 — Tonnage naming fix and ship-type gap-fill

**Objective**: review the six-month port-call sample; fix two data-integrity
gaps William spotted while reviewing it.

### Decided (by William, 2026-08-19)

1. **FGIS tonnage is `estimated_tons`, not `actual_tons`.** His original
   mapping (`docs/FGIS_MATCH_SPEC.md`) already said "Metric Ton → estimated
   tons" — the port-call build had implemented it as `actual_tons` instead.
   Corrected across `port_call`/`port_call_leg`/`port_call_event`. A genuine
   `actual_tons` column is added alongside, NULL until a real source exists;
   promoting a leg from estimated to actual is future work, not inferred here.
2. **`ship_type` (the register's raw type/family) is added**, and where a
   register family has no size vocabulary — so `ship_type_group` would
   otherwise be NULL — `ship_type_group` is backfilled from `ship_type`.
   "Gaps are worse than variances in convention." 16 vessels in the
   then-current register snapshot gain a group this way. **Correction, same
   day**: I originally claimed this fixed *Radcliffe R. Latimer*'s (IMO
   7711725) billing tier -- wrong, its `vessel_type_canonical` is already
   `Bulk` from the Zone Report's own Type field, so `agency_fee_for()` never
   even reached `ship_type_group` for it. Caught while verifying the
   world-fleet register refresh (see the entry above this one).

### Done

- Schema, `build_db.py` and `build_port_calls.py` updated; full chain
  rebuilt (`build_db.py` → `build_fgis_match.py` → `build_port_calls.py`),
  all guardrails still pass, fee/tonnage totals unchanged except the one
  vessel above.
- Fixed an unrelated pre-existing bug found while rebuilding: `build_db.py`
  crashed on every run after the FGIS/port-call drop message (`had_port_calls`
  computed but never returned).
- `docs/PORT_CALL_SPEC.md` §7 and `docs/BUILD.md` "Ships register enrichment"
  updated. Six-month and scenario samples regenerated.

### Next session starts by

Same as before this fix: extending the register to tankers/gas/containers
(**OPEN_QUESTIONS §9** — check first whether the separate `Ships_Register`
world-fleet expansion already supersedes the chunk-pull plan there), and
deciding **§8** (does a `No Cargo` leg open a split / bill at all).

---

## 2026-08-19 — Port call assembly layer

**Objective**: build the port call assembly layer, and produce a specific output
table for the matched and transformed data with the raw MRTIS event as the spine.

### Done

- **`port_call_event`** — the deliverable. One row per `fact_zone_event`
  (290,436, always), source values preserved in `src_*` columns beside every
  canonical one. Plus `port_call` (40,170) and `port_call_leg` (41,985).
- **`scripts/build_port_calls.py`** + `sql/schema_port_call.sql` +
  `docs/PORT_CALL_SPEC.md` (the rules) and `docs/PORT_CALL_QUALITY.md`
  (auto-generated health of each run).
- **Build guardrails** (`scripts/lib/guardrails.py`) — hard invariants abort the
  build before anything is written; soft checks report source health. Caught two
  real bugs during the session before either reached the database.
- **Six-month sample** exported for review at all three grains
  (`sample_port_calls_6mo*.csv`, 2,899 calls / 3,018 legs / 18,104 events).
- **`docs/BUILD.md` corrected**: the ships-register gap is not "largely
  passenger/cruise" (0.9%) — it is 80% tankers.
- Ten commits, pushed to `origin/main`.

### Decided (by William, all 2026-08-19 — see OPEN_QUESTIONS §7, §9)

1. **Agency fee is per port call, not per berth — except a split discharge-then-
   load, which is two.** The billing unit is therefore the operational leg.
   Implemented: **$304,808,000** over 41,334 chargeable legs, against
   $349,625,500 over 48,167 berth departures on the old basis (−12.8%). The old
   basis is preserved alongside for comparison.
2. **The canonical facility is the unit, not the zone.** Two berths of one
   elevator are one visit. Zen-Noh Upper → Zen-Noh Lower is a shift.
3. **Only the first docking and the last sailing of a visit count.** Overlapping
   geofences and movement in berth produce false hits; a large ocean vessel does
   not dock, sail and redock in minutes. 5,102 berth events (5.3%) collapse as
   artefacts — kept on the spine, flagged, simply not read as operations.
4. **The dictionary outranks the AIS draft** where a facility can only do one
   thing. A grain elevator loads. The draft is a pass, not a decider: Elevator,
   Bulk Cargo and LNG resolve 100% from the dictionary with zero draft
   involvement.
5. **Only dry-cargo vessel types split.** Tankers, gas, cruise, container,
   reefer and other are not eligible; Bulk and no-recorded-type are. Splits fell
   2,874 → 1,787 (4.4% of calls).

### Found

- **Do not scrape VesselFinder.** Its own terms forbid building a dataset from
  it (§5.3, §7.4, §9), and the premise fails anyway: the test vessel *Ireland*
  (9770543) is already in the register with a TPC that VesselFinder does not
  publish for any ship. Equasis and MarineTraffic are more restrictive still.
- **`ship_type_group` is a pure step function of DWT within family** — zero
  monotonicity violations across 18,752 register rows. The size class can be
  derived rather than stored or scraped. Careful: the *published* industry bands
  overlap and would corrupt the register's clean partition; use the register's
  own cut points.
- **The register gap is a scope boundary, not a coverage failure** — the
  vocabulary holds only Bulk Carrier and General Cargo families.

### Open

- Does a `No Cargo` leg accrue a fee? 421 legs, $3,783,500.
- Dictionary `ops` blanks now drive the 7,139 unresolved legs, and each fill is
  decisive rather than advisory. Cruise berths (Erato St, Julia St) are the
  clearest — a cruise ship works no cargo.
- 853 legs where the draft contradicts the dictionary. Dictionary wins; listed
  by facility in the quality report in case a row needs widening.
- TPC probably needs a ruling of its own: captured per call from hydrostatic
  tables, versus estimated by formula and flagged. The column is 63.6% populated
  and the two should not be silently mixed.

### Next session starts by

Reviewing `sample_port_calls_6mo.csv`, then extending the Sea-web pull to
tankers/containers/gas — see **OPEN_QUESTIONS §9**, which has the sizing and the
upload chunks already prepared.
