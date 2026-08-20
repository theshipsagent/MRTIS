# Changelog

All notable changes to this project are recorded here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed — non-commercial time classification, and eight rulings closed (2026-08-19, session 3)

Built the five rulings William gave closing the `mrtis-claris` audit session,
then worked through the remaining open questions in the same conversation.
Scratch-copy rebuild and full reverification before the real repo was touched,
per standing practice; every headline figure re-derived independently in SQL
rather than read out of the build's own reporting.

- **Non-commercial time, built as one general classification** (per-stop
  `is_non_commercial`), not a layberth special case — William was explicit
  that other oddities will join it. Layberth is its first and only current
  member. Four behaviours follow from it:
  - **R5 prices off the leg's first *working* berth** (§12.3.3.1 amended).
    `first_berth_zone`/`first_berth_facility`/`facility_type` now skip
    layberth stops, the way §8a already skips them for splits — so a bulker
    that lays up at a repair yard before loading at an elevator no longer
    prices (or reports) from the repair yard.
  - **`berth_stop_count`/`berth_hours` exclude layberth time**, on both
    `port_call_leg` and `port_call`; new **`layberth_hours`** column on both
    carries it instead of dropping it. 45,742 hrs / 389 stops / 379 legs.
  - **Pure lay-up calls flagged, never deleted** —
    `port_call.is_commercial_call` / `call_class`. 142 calls out of counts and
    fees; their 23,390 hrs (975 vessel-days) stay on the SWP-to-SWP spine.
  - **An unresolved stop outranks `No Cargo`** for a leg's label (§11.1a).
    54 legs relabel to unresolved; the label/fee contradiction audit #2 found
    as W1 is gone.
- **Two new hard guardrails**: call-level `berth_stop_count`/`layberth_hours`
  reconcile to the sum of their legs'; no fee accrues on a non-commercial call.
- **R2 extended to `General Cargo Ship (with Ro-Ro facility)`** ($1,000,
  §12.3.2). Only 2 of its 5 chargeable legs move — the other 3 are Bulk at
  General Cargo berths, already R5-priced, since R5 outranks R1-R4.
- **IMO-repair merge guard** (§7.5) — `build_imo_repair_map` refuses a merge
  when the corrupted-IMO rows show no SWP crossing **and** their dates don't
  overlap the good vessel's. Both must fail. Repairs 31 → 30, rows 256 → 125;
  the one blocked merge is exactly audit #1's false Egret merge.
- **Egret excluded at ingest**, by IMO (`17825854`) and deliberately *not* by
  name — two vessels here are named exactly `Egret` and the other (`9747120`)
  is a real tanker that must survive. It does.
- **Name-only dredge exclusions now write
  `dictionaries/dredge_name_only_review.csv`** instead of dropping silently
  (§7.3) — 23 names, 8 forming a complete `Enter → Exit` transit (the signal
  that flagged T Jungfrau, Heino and Corinthian). A review artifact; no data
  change.

**Figures.** Billable leg basis **$272,167,500 → $272,660,000**. Port calls
40,170 total / **40,028 commercial**; legs 41,804 / **41,662 commercial**;
spine 290,436 → **290,305** rows.

**The per-departure benchmark moved: $349,625,500 → $349,527,500** — exactly
the $98,000 fabricated Egret fee. This does not contradict §12.3.4, which
froze the *pricing schedule* on that basis, not the number regardless of what
data exists. But it is cited throughout these docs and is a headline
reconciliation target in the `mrtis-claris` package, which will report a false
mismatch until re-exported.

**Corrected a figure carried in from the `mrtis-claris` audit**: its R5
estimate of "80 legs, +$440,000" enumerated only 5 of the 14 layberth zones
and never counted the five Violet Docks. Re-derived: **107** legs affected,
**93** revert to the $10,500 base tier (**+$511,500**), 14 correctly stay at
$5,000 against a genuine General Cargo working berth.

Rulings recorded without a build: §7.2 (Kennington stays excluded), §10
(stable keys approved, recommendation written, deliberately not built),
§11.2 (unplaced-events gap left as is, its shape measured), §11.3 (`tpc = 0`
traced upstream to `Ships_Register`'s own data, not an MRTIS pull gap —
deferred), §11.4 (no fee), §13.1 (reconfirmed discharge-only, no exception).

### Added — independent audit #2, port-call assembly layer (2026-08-19)

- **`docs/audit/AUDIT_2026-08-19_1746.md` / `.pdf`** — adversarial, read-only
  audit of `git log 13937b9..HEAD` (13 commits): the whole port-call assembly
  layer plus the rulings built on it. Full chain rebuilt in an isolated scratch
  copy; every claim re-derived in SQL rather than read out of the build's own
  reporting. Real repo verified byte-identical afterwards (74-file SHA-256
  manifest + `git status`). **Nothing was fixed.**
- **Confirmed (14):** all row counts, both fee bases to the dollar
  ($298,868,500 / 40,245 legs; $349,625,500 / 48,167 departures), all 18 hard
  guardrails independently re-derived, §8a's split behaviour, §9's coverage
  figures and its 24 unmatched IMOs verified individually, the `actual_tons`
  rename, and the Radcliffe R. Latimer retraction. The build is deterministic —
  a cold rebuild reproduced `PORT_CALL_QUALITY.md` byte-for-byte.
- **Proved by rebuild, not inspection:** the per-departure basis is untouched by
  both rulings — §8 changed it on 0 of 40,170 calls; §9 changed the fee tier of
  0 of 10,211 vessels.
- **Audit #1's open findings do not reach the billing layer:** all 131 Egret
  workboat rows are unplaced (no `Enter`/`Exit`, so no call can open), and the
  fabricated $98,000 reaches no leg.
- **Wrong (8), $6,604,500 mis-stated or unreconciled** — none of it a wrong
  *rate*; all labelling, reconciliation or reporting:
  - **W1** 54 legs report `activity = 'No Cargo'` and bill $413,000 anyway. The
    documented rule is false — an *unresolved* stop cannot win the label but does
    trigger the fee. 7 also assert a `cargo_group`; 20 report the layberth as
    their berth.
  - **W2** "unchanged from before this fix" is not like-for-like — §8 changed leg
    membership. The real §8 movement, stated nowhere, is **-$3,258,500 across
    313 calls**.
  - **W3** `agency_fee_departures_total` sums to $346,692,500, not $349,625,500
    — 388 fee-bearing unplaced events, **$2,933,000**, unguarded.
  - **W4-W8** backfill lacks its stated guard (inert today, latent $7,000/leg);
    schema says ~12% over-bill, actual 17.0%; "3 ambiguous FGIS records" is 1
    record / 3 lines; `tpc = 0` on 4,045 calls (10.1%) behind a "99.7% populated"
    headline; §9 silently lost 2 register matches.
- **Structural finding:** all 18 guardrails are *shape* checks — none asserts a
  value is right. Two of six value-level gaps are already failing silently.

### Added — `OPEN_QUESTIONS.md` §11 and §12 (2026-08-19)

- **§11** — four business rulings raised by audit #2 (leg label vs fee on mixed
  layberth legs; whether the comparison column should include unplaced events;
  `tpc = 0` as null; two berthed-but-unbilled legs), plus five
  corrections-without-a-ruling.
- **§12** — William's revised fee schedule (Passenger/Cruise $2,500; Ro-Ro /
  Vehicles Carrier $1,000; Container Fully Cellular $750; Refrigerated Cargo Ship
  $5,000; dry bulk at a General Cargo berth $5,000), **captured and scoped but
  deliberately not implemented**. Indicative impact **-$26,701,000 (-8.9%)**.
  Blocked on three decisions: the named types are register `ship_type` values,
  not `vessel_type_canonical`, so as written today the rules would fire on 9 legs
  out of 4,211; R5 is the first berth-dependent fee ever (a 217-leg / $2.28M
  question on which berth decides); and three of the six named types have zero
  traffic in MRTIS.


### Changed — ships register refreshed to the full world-fleet pull (2026-08-19)

- **`dictionaries/ships_register_fleet.csv` refreshed** from the
  `Ships_Register` project's `fleet_joined` table, now a full world-fleet pull
  (20,101 -> 49,763 rows, 19 -> 133 `ship_type_group` values -- tanker,
  containership, LPG/LNG, reefer, cruise, PCC/PCTC, ro-ro all added), not the
  earlier dry-cargo-only extract. Superseded the chunk-pull plan prepared for
  this in `OPEN_QUESTIONS.md` §9 -- that pull was never run; the world-fleet
  expansion in the separate project made it unnecessary.
- Full chain rebuilt (`build_db.py` -> `build_fgis_match.py` ->
  `build_port_calls.py`), all guardrails pass. Match coverage against
  `dim_vessel`: **60.9% -> 99.4%** of all vessels (**61.1% -> 99.8%** of those
  with a valid IMO). `port_call.dwt`/`.tpc` populated on 40,055 of 40,170
  calls (99.7%), up from roughly 60%.
- 24 vessels with a valid-format IMO remain unmatched (13 checksum-invalid,
  10 behind Sea-web's pre-1980 build-year gate, 1 genuinely absent) -- none
  need another pull. `docs/BUILD.md` and `OPEN_QUESTIONS.md` §9 rewritten.
- **Correction**: an earlier same-day entry claimed the `ship_type_group`
  backfill (below) fixed *Radcliffe R. Latimer*'s billing tier. Wrong --
  see that entry's correction and `OPEN_QUESTIONS.md` §9.
- **Raised**: `vessel_key`/`event_key` are row-position, not a stable
  identity, so every core rebuild -- including this one, which only touched
  register enrichment -- forces re-deriving the FGIS and port-call layers
  from zero even when no vessel or event actually changed. Logged as
  `OPEN_QUESTIONS.md` §10; not fixed here, needs its own scoping.

### Changed — §8 resolved: layberth stops don't split and don't bill (2026-08-19)

- **8a — a layberth (`No Cargo`) stop can no longer open a leg boundary.**
  `split_into_legs()` now treats `No Cargo` the same as unresolved for
  splitting purposes: it joins the leg in progress and never becomes the
  leg's `cur_activity`, so a real Discharge → No Cargo → Load sequence still
  splits on the Discharge/Load boundary as if the layberth stop weren't
  there. Split calls fell 4.4% → 4.1% (1,787 → 1,632).
- **8b — "no fee on departing a layberth."** A leg now bills only if it did
  real, non-layberth work somewhere; a leg of nothing but layberth stops (a
  pure lay-up or repair call) accrues nothing, exactly like a call that never
  berthed at all. 142 pure lay-up legs moved from billed to $0. A leg that
  mixes a layberth stop with a genuine other berth (e.g. bunkers at a
  refinery, activity unresolved) still bills as it always did — only the
  layberth stop itself is fee-exempt (54 such legs, $413,000, unchanged).
- Combined effect on the ruling-basis fee total: **$304,808,000 →
  $298,868,500** (-$5,939,500), over 41,334 → 40,245 chargeable legs.
  `docs/OPEN_QUESTIONS.md` §8 and `docs/PORT_CALL_SPEC.md` §4 updated.

### Fixed — tonnage naming and ship-type gap-fill (2026-08-19)

- **`actual_tons` renamed to `estimated_tons`** on `port_call`, `port_call_leg`
  and `port_call_event`. William's original mapping (`docs/FGIS_MATCH_SPEC.md`)
  is explicit that summed FGIS metric tons is an *estimate*, not a certified
  actual weight — the port-call build had implemented it as `actual_tons`
  instead. A genuine `actual_tons` column is added alongside, NULL everywhere
  for now (no source wired in); promoting a leg from estimated to actual is
  future work.
- **`ship_type` added** (`dim_vessel`, `port_call`, `port_call_event`) — the
  ships register's raw type/family (e.g. `Cement Carrier`), alongside the
  existing size-bucketed `ship_type_group`. Some register families carry no
  size vocabulary at all (Cement Carrier, Aggregates Carrier, self-discharging
  Lakers, ...), so `ship_type_group` is now backfilled from `ship_type` where
  it would otherwise be NULL — a gap is worse than a variance in convention.
  16 vessels in the (then-current, pre-world-fleet-refresh) register snapshot
  gain a `ship_type_group` this way. **Correction, same day**: an earlier
  version of this entry claimed *Radcliffe R. Latimer* (IMO 7711725) started
  billing at the bulk tier because of this fix -- checked while verifying the
  register refresh below and that's wrong. Its `vessel_type_canonical` is
  already `Bulk` straight from the Zone Report's own Type field, which is
  priority-1 in `agency_fee_for()` and fires regardless of `ship_type_group` --
  it was billing correctly before this change touched anything, and after the
  world-fleet refresh it isn't matched in the register at all (still behind
  the pre-1980 build-year gate). The `ship_type_group` backfill itself is
  still correct and still needed for the vessels it actually affects.
- Fixed an unrelated pre-existing bug in `build_db.py::write_db()`/`main()`:
  `had_port_calls` was computed but never returned, crashing every run after
  the FGIS/port-call-layer drop message. Found while rebuilding to verify the
  above.

### Audited — 2026-08-19 (session: independent audit, findings only)

- **Independent data-integrity audit** — `docs/audit/AUDIT_2026-08-19_0242.md`
  (+ `.pdf`, 18pp). Read-only adversarial verification of the seven published
  claims, run in an isolated scratch copy; repo, dictionaries and
  `mrtis.duckdb` verified byte-identical before and after. **Nothing in the
  pipeline was changed.**

  **Confirmed:** FGIS match rate 12,445 / 12,537 in-coverage = **99.2662%**,
  1 ambiguous (`AQUITANIA` 2024-10-13), **0** identity guesses. **No fuzzy
  matching anywhere** — all five name paths are exact equality on the
  normalized string; `DSI`/`DSL Phoenix` cannot bridge under any of them.
  Tonnage conserved **exactly** (MT and lb deltas 0.0), 18,315 output lines
  map 1:1 onto `fgis_record_line`, and every cross-reference integrity test
  is clean (0 wrong-vessel links, 0 action mismatches, 0 `day_offset` errors,
  0 `fgis_record_count` errors, 0 fallbacks that had a sailing available).
  **Idempotency confirmed** — two full rebuilds byte-identical across all 9
  tables; both generated reports and the review CSV reproduce exactly.
  Carnival ships and the real tanker Texas Star (9256860) survive; both
  `Aquitania`s and all four `Sea Voyager`s stay separate.

  **Wrong — 3 high:**
  1. **IMO merge `1782585 -> 9747120` ("Egret") is false.** 131 rows — 51% of
     all 256 repaired rows — of a 2019 Baton Rouge workboat (river miles
     226–232, zero `Enter`/`Exit`, no draft/agent/type on any row, 7-of-7
     digits differ) merged into a tanker that carried the name `Egret` only
     from 2026-01-01. 78% of that vessel's events now belong to another ship;
     $98,000 of its $112,000 in fees is fabricated.
  2. **Agency fee charges per berth departure, not per port call.** 19.0% of
     fee-bearing calls are charged 2–10 times; **$67,259,500 — 19.2% of the
     published $349,625,500**. 179 charges ($1,746,500) repeat at the *same*
     berth within an hour, which `PORT_CALL_EVIDENCE.md` explicitly says is
     not a second berth call.
  3. **`--keep-dredges` merges the Texas Star dredge into the real tanker**
     ($3,937,500 vs $10,500 correct). `load_dredge_exclusions` adds a name to
     `excluded_names` only for entries *without* an IMO, so the guard can
     never fire for an IMO-bearing dictionary entry.

  **Wrong — 5 medium:** `Kennington` (9664926) is a real agented tanker on the
  exclusion list (583 events, $304,500); name-only exclusion deletes 3 real
  ocean port calls (T Jungfrau, Heino, Corinthian); a missing dictionary
  silently reports $0 (zone) or $313.3M (vessel-type) with exit code 0;
  `DATA_QUALITY.md`'s "Raw rows read: 290,666" is post-filter — the true
  figure is 314,335; and its NULL-fee narrative names six vessels that are on
  the exclusion list and therefore cannot occur (the actual three are
  `Carnival Valor Rb2` and `Ncl Escape Rb Sb` ×2).

  **Also:** the "the build verifies that no fallback record had a sailing
  available" claim in BUILD.md, CHANGELOG and the printed match report is
  hardcoded prose — the property holds structurally and was confirmed by
  query, but no build-time check exists. Hand-written figures across
  WHITEPAPER / BUILD / CHANGELOG / FGIS_MATCH_SPEC have drifted from what the
  code now produces (14 discrepancies tabulated in §8.2 of the audit).

  Decisions this raises for William are logged as
  `docs/OPEN_QUESTIONS.md` §7.


### Added — port call assembly (2026-08-19)

- **Port call assembly layer** (`scripts/build_port_calls.py`,
  `sql/schema_port_call.sql`, `docs/PORT_CALL_SPEC.md`) -- the zone-event feed
  assembled into voyages. Three tables: `port_call_event` (**the deliverable**:
  one row per `fact_zone_event`, source values preserved in `src_*` columns
  alongside every canonical/derived one), `port_call` (Enter SWP .. Exit SWP)
  and `port_call_leg`. 40,170 calls, 43,238 legs, 98.8% of calls complete at
  both ends.
  - **Activity** (Load / Discharge / No Cargo) resolved by evidence order --
    draft delta (76.1% of legs), then an FGIS certificate (3.5%), then the zone
    dictionary's `ops` (4.7%); unresolved otherwise, never guessed.
    `activity_method` records which rung answered. 84.3% resolved.
  - **Split calls** -- a new leg starts only where the activity changes, so two
    Load berths in a row stay one leg but discharge-then-load splits. 7.2% of
    calls. Verified against the Ultra Leopard SOFs: iron ore discharge at Nucor
    (48->23 ft), eleven days at anchor, soybeans for China at ADM Reserve
    (25->45 ft).
  - **Agency normalization per leg** -- the agency that brought the vessel in
    owns the leg (`agency_leg`), which fills the 2.4% blank agents and undoes
    the pilot-sheet artefact where an outbound agent lands on a sailing another
    agency worked, while a genuine split call still keeps two agencies.
  - **Waiting time** is anchorage dwell before the leg's berth arrival only,
    attributed by interval overlap -- the pilot sheets leave anchorages open
    after the vessel is alongside (90 legs), and counting that whole dwell would
    double-count cargo time (Amanda C: 332 raw hours, 117.6 genuinely waiting).
  - Cargo, destination and certified tonnage attach from FGIS per leg; DWT/TPC
    from the ships register. Shipper/Consignee/Receiver/ports/Est Tons are
    deliberately absent until a source exists.
- **Build guardrails** (`scripts/lib/guardrails.py`) -- HARD invariants abort the
  build before anything is written (spine completeness, referential integrity,
  fee and FGIS-tonnage reconciliation, no activity without a named method,
  schema/frame column alignment, single transaction); SOFT checks report source
  health without blocking. This caught a real bug on its first run: 13 berth
  stops carry grain certificates on both the arrival and the sailing, and the
  code was keeping one and dropping the other (128 certificates, 4.4 Mt).
  Nothing was written until it was fixed.
- `build_db.py` now drops the port call layer alongside the FGIS layer on
  rebuild, for the same reason -- both key off row-index surrogate keys -- and
  names both scripts to re-run.
- **Open for William**: `--min-draft-delta` defaults to 1 ft (trust the source
  as recorded). At +-1 ft the evidence is a coin toss -- it agrees with the zone
  dictionary 35 times and contradicts it 29. Raising it to 2 ft costs 4.5 points
  of resolved activity and removes ~340 split calls. Also 707 legs where the
  draft contradicts the dictionary's `ops`: the draft wins (531 sail a median
  14 ft lighter from Load-only berths, mostly ADM Destrehan Buoys -- a
  20,000-tonne discharge, not noise), so the dictionary needs correcting.
- **William's agency-fee ruling implemented on this layer** (OPEN_QUESTIONS
  §7.1): the billing unit is the leg. `port_call_leg.agency_fee` is one fee per
  leg that reached a berth, priced through `build_db.py::agency_fee_for()` so
  both layers use one definition of the rate; `port_call.agency_fee_total` sums
  its legs. **$309,018,500 over 41,821 chargeable legs**, against $349,625,500
  over 48,167 berth departures on the pre-ruling basis -- **-$40,607,000
  (-11.6%)**. The $133,000 above the §7.1 estimate is the ships-register
  fallback for blank-type vessels (§7.1a follow-up 4), which keeps the two
  layers pricing identically. The per-departure figure is preserved unchanged on
  `port_call_event.agency_fee` and the `*_departures*` columns and reconciled by
  a hard guardrail, so the two bases stay comparable row by row.
- Still open on the fee: whether the 239 `No Cargo` legs ($2,061,500) should
  accrue, and that $309.0M is a **floor** -- 5,375 chargeable legs have an
  unresolved activity, and the split rule never lets an unknown invent a split.

### Added
- **Agency fee refinements**: where the Zone Report never recorded a Type,
  the rate falls back to the ships register (`ship_type_group LIKE
  'Bulk Carrier%'` -> higher tier), recovering real Capesize/Kamsarmax
  bulkers under-billed at $3,500 (+$147,000). And a vessel with no usable
  IMO and no type from either source accrues NO fee -- `agency_fee` NULL
  means *no fee*, not *unknown*. Net effect -$322,000.
- **`fact_zone_event.agency_fee`** -- agency fee in USD accrued on sailing
  from a facility berth (`action='Depart'` at any zone whose `facility_type`
  is not Anchorage or Pilot Station); NULL elsewhere, so `SUM(agency_fee)`
  over any slice is the fee earned on it. Rate is driven by the **vessel, not
  the berth** (William, 2026-08-19): `vessel_type_canonical='Bulk'` ->
  $10,500, everything else -> $3,500. Chosen after measuring both bases:
  vessel covers 90.5% of berth departures vs 82.1% for facility-based, which
  misses all 7,350 General Cargo departures. The two disagree on 487
  departures (mostly bulk carriers at chemical-plant/tank-storage berths);
  the vessel wins. 48,301 chargeable sailings, ~$350M across 2019-2026.
- **`dim_zone.facility_type`** -- the authoritative zone classification from
  `dictionaries/zone_facility.csv`, alongside the existing heuristic
  `zone_group`. That dictionary had been read only by the FGIS matcher; it is
  now materialised in the warehouse.
- **FGIS matching, consolidation, and cross-reference**
  (`scripts/build_fgis_match.py`, `sql/schema_fgis_match.sql`) -- the third
  and final FGIS stage, implementing `docs/FGIS_MATCH_SPEC.md`. Resolves
  each free-text `carrier_name` to an MRTIS `vessel_key` by narrowing to
  vessels that departed an Elevator/Mid-Stream berth within
  `cert_date -1/+3` days and matching on an exact punctuation-normalized
  name; consolidates 18,315 certificate lines into 14,528 `fgis_record`
  rows; and writes the link both ways (`fgis_record.mrtis_event_key` and
  `fact_zone_event.fgis_record_id`), with `fgis_record_line` bridging back
  to the individual source lines. **12,442 of 12,537 in-coverage records
  matched (99.2%)**, 1 ambiguous, 94 no-candidate, 0 guesses. Unmatched
  and ambiguous cases go to `dictionaries/fgis_match_review.csv`. See
  `docs/FGIS_MATCH_QUALITY.md`.
- `dim_vessel_name_alias` -- every distinct name spelling ever observed per
  vessel (11,555 rows). `dim_vessel` keeps only the latest name, but 1,217
  of 10,270 vessels (11.9%) are renamed during the covered period (IMO
  9397456: `Hellas Explorer` 2019 -> `Alithini II` 2022), so matching an
  external source by name against `dim_vessel` alone would silently fail
  for every record predating a rename.
- `lib.parse.normalize_vessel_name()` -- uppercase, strip an `M/V`-type
  prefix, remove all non-alphanumerics. Deliberately no fuzzy/edit-distance
  matching: `DSL Phoenix`/`D.S.L. Phoenix` normalize together while
  `DSI Phoenix` (a genuinely different real vessel, one character away)
  stays separate.
- `fact_zone_event.fgis_record_id` / `fgis_record_count` columns.
- **Arrival fallback for grain calls** (William, 2026-08-18): the sailing
  from the elevator / mid-stream rig is always the correct anchor, but where
  MRTIS recorded no sailing at all -- a genuine event-capture gap, the vessel
  seen berthing and later exiting the river with no berth departure between
  -- the matcher falls back to the berth **arrival** within
  `cert_date -6/+1`. Window derived from the confirmed matches, not assumed:
  90.7% of berth stays are <=4 days (median 2) and the arrival falls 0-4 days
  before the Cert Date in 93.1% of cases at -4, 97.9% at -6; set to -6 after
  four real Statements of Fact showed berth stays of 5.2-8.0 days are routine
  (docs/PORT_CALL_EVIDENCE.md). Recovered 168 records (98.0% ->
  99.2%; no-candidate 252 -> 99). The build verifies on every run that no
  fallback record had a sailing available. New column
  `fgis_record.mrtis_event_action` records which event kind each link points
  at -- it must be checked before treating `mrtis_event_time` as a sailing
  timestamp.

### Fixed
- **Corrupted IMOs no longer fork one vessel into two.** `canonical_imo()`
  accepted any 7-digit string without validating the IMO check digit, so a
  mistyped IMO created a phantom vessel that stole events from the real one.
  `Spring Aura` 9991064 (invalid) held two events from the middle of
  9991082's single continuous Zen-Noh loading, making one ship look like two
  at the same elevator on the same day -- which is what surfaced it. Added
  `parse.imo_check_digit_valid()` and `parse.build_imo_repair_map()`: where an
  invalid IMO has exactly one same-name check-digit-valid twin, they merge.
  31 repairs across 256 rows; resolved 2 of the 3 ambiguous FGIS cases.
  Guarded three ways -- exactly one valid twin required, dredge-list names
  skipped, and known type conflicts blocked -- so genuinely different vessels
  sharing a name are never merged (both `Aquitania`s, and `Sea Voyager` with
  two valid twins, correctly stay separate).
- **Blank Agent is not random -- it tracks malformed IMOs.** Rows with a
  clean 7-digit IMO are missing an Agent 1.8% of the time; rows with a
  malformed IMO (8/9-digit, 3/4-digit or blank) are missing one ~99% of the
  time. 8/9-digit rows are 4.7% of the feed but carry 49% of every blank
  Agent -- IMO, Agent and Type go missing together, pointing at one
  defective input path. `Nordic Aki`/`Bonita Aki` (IMO 9505974, spotted by
  William) is the clean case: all 52 rows with the 7-digit IMO carry
  Type=Tank and Agent=General Maritime; all 713 rows with the 9-digit
  variant carry neither. Documented, not yet repaired. Measured backfill
  potential once port calls exist (SWP Enter->Exit brackets): 83.7% of
  missing types and 32.4% of missing agents are recoverable from elsewhere
  in the same call.
- **136 fact rows were orphaned from `dim_vessel` with a NULL `vessel_key`.**
  Introduced when vessel identity was rewired onto the repaired IMO:
  `bool(nan)` is True, so an unguarded truth test let NaN become the natural
  vessel key, and `groupby()` silently drops NaN keys -- those rows never
  produced a dim_vessel entry and joined to nothing. Surfaced by the
  agency-fee work, where an inner join and a left join disagreed by exactly
  136 rows. Fixed with an `isinstance` guard; `dim_vessel` gained 58 real
  vessels and every fact row now joins cleanly to all three dimensions.
- **NaN truthiness bug in the IMO type-conflict guard.** `bool(nan)` is True
  and `nan != 'Bulk'` is also True, so a *missing* vessel type masqueraded as
  a known-conflicting one and silently blocked 12 legitimate merges. Only
  appeared once dredge filtering changed pandas' dtype inference for that
  column.
- **Three errors in `docs/FGIS_MATCH_SPEC.md` found during the build**, all
  corrected in both the spec and the implementation:
  1. The spec narrowed candidates on an `Exit` event from an Elevator/
     Mid-Stream zone. In this data `Enter`/`Exit` occur *only* at Pilot
     Station zones -- every berth-type zone records `Arrive`/`Depart`, so
     the spec as written would have matched **zero rows**.
  2. The spec's normalization ("strip periods, collapse whitespace")
     replaced punctuation with a space, turning `D.S.L. Phoenix` into
     `D S L PHOENIX` -- which does not equal `DSL PHOENIX`, failing the
     exact case the rule existed to handle. Punctuation is now stripped
     entirely, which still keeps `DSI` and `DSL` apart.
  3. The spec assumed one FGIS record per departure. One sailing routinely
     carries grain certified across several consecutive days (`Dsi Aquila`,
     ADM AMA, 2022-03-16: three certificates, one sailing), so 1,562 of
     10,600 matched departures (14.7%) carry 2-3 records. A scalar column
     cannot represent that; `fact_zone_event.fgis_record_id` now holds only
     the primary (latest Cert Date) record with `fgis_record_count`
     alongside it, and tonnage must be aggregated from `fgis_record` via
     `mrtis_event_key`.

### Changed
- **Exclusion list extended from 9 to 28 vessels.** The agency-fee work
  surfaced 19 vessels with no usable IMO and no type from any source that
  were nonetheless accruing fees on berth sailings -- tugs and workboats
  (Dixie Raider, Jesse A Mollineaux, Sarah Dann), government craft
  (`Usace Mat Sink Unit`, `Cg Eagle`, `French Warship`) and non-vessels
  (`Shop`, `Abc Test`). Cruise-line support records (`Carnival Valor Rb2`,
  `Ncl Escape Rb Sb`, `Carnival Liberty Rb1/Rb2`) were deliberately left in
  place -- note these are 1-2 event support craft, NOT the cruise ships,
  which are separate IMO-bearing records (Carnival Valor 9236389, 1,760
  events) and are untouched. Total filtered: 23,669 rows, 28 vessels.
  Side effect: 4 zones (Andry St, Castleton Braithwaite, Magellan Marrero 3,
  Mandeville St) no longer appear in `dim_zone` -- their only recorded
  traffic was excluded workboats.
- **Dredge/workboat traffic filtered out at ingest** rather than flagged
  (William, 2026-08-19: "if we remove the dredges on the list also on front
  end, removes those records and focuses the table"). 9 vessels, 23,228 rows
  (7.4% of the feed). `dictionaries/dredge_exclusions.csv` had never been
  wired into any code before this. Matching is by canonical IMO wherever the
  dictionary supplies one -- "Texas Star" is both a dredge (311000000) and a
  separate real tanker (9256860), and a name-only filter deleted the tanker
  too. `--keep-dredges` retains them; per-vessel counts are always reported.
- **FGIS matching is dry-bulk only**: vessels whose canonical type is Tanker
  or Gas are excluded from the candidate pool (William: "what we are solving
  for is only dry bulk cargo"). Scopes matching only -- those vessels keep
  every zone event and still get port calls built.
- `scripts/build_db.py` now drops the FGIS match layer on rebuild and says
  so. `event_key`/`vessel_key` are row-index assigned, so a core rebuild
  reassigns them and would leave an existing FGIS cross-reference pointing
  at the wrong rows. Run order is now `build_db.py` -> `build_fgis.py` ->
  `build_fgis_match.py`; the `mrtis-rebuild-db` skill was updated to match.

### Added (earlier this session)
- **FGIS raw ingest** (`scripts/build_fgis.py`, new standalone pipeline):
  pulls USDA grain export certification data directly from
  `fgisonline.ams.usda.gov` (one public CSV per calendar year, no auth/
  scraping needed) for 2018-YTD and loads two new tables: `fgis_raw`
  (all ~110 columns, all carrier types, all ports nationally -- 214,213
  rows) and `fgis_output` (the 14-column Mississippi-River/ocean-vessel
  slice -- `Type Carrier=1 AND Port='MISSISSIPPI R.'` -- 18,315 rows,
  2018-01-01 to 2026-08-13). See `docs/BUILD.md` and
  `docs/FGIS_DATA_QUALITY.md`. Matching FGIS records to MRTIS
  vessels/events, rolling them up, and cross-referencing is spec'd but not
  yet built -- see `docs/FGIS_MATCH_SPEC.md`.
- `docs/OPEN_QUESTIONS.md` item 4 (split-call/agent-normalization) updated
  with real confirming evidence from 4 Statements of Fact William shared
  (Ultra Leopard's discharge-then-load split call at two different
  berths with a master change in between; Mid-Stream buoy locations
  confirmed to host real cargo ops, not just dwell).
- `dim_vessel` enrichment: `ship_type_group`, `dwt`, `tpc` matched by
  canonical IMO from `dictionaries/ships_register_fleet.csv` (a snapshot
  of William's separate Ships_Register/Sea-web pipeline, 20,101 vessels).
  No match, no guess -- unmatched vessels get NULL. 6,216 of 10,270
  vessels matched (60.5%) as of this build.
- Zone, agent, and vessel-type canonicalization dictionaries completed by
  William and locked in: `dictionaries/zone_facility.csv` (220 rows),
  `dictionaries/agent_agency.csv` (41 rows), `dictionaries/vessel_type.csv`
  (22 rows), `dictionaries/dredge_exclusions.csv` (9 noise vessels flagged
  for exclusion from analytics).

### Fixed
- **IMO cleanup rule corrected**: canonical IMO is now the first 7 digits
  of the raw value for any raw value of 7+ digits (previously only exact
  7-digit raw values were accepted; 8/9-digit values were incorrectly
  treated as invalid and fell back to name-based identity). This fixed
  vessel identity fragmentation -- distinct vessel count dropped from
  10,316 to 10,270 as previously-split records for the same physical
  vessel (one IMO variant correct, one glitched) correctly merged.
  Non-standard/missing-IMO vessel count dropped from 145 (1.4%) to 62
  (0.6%) accordingly.

## [0.1.0] - 2026-08-18

### Added
- Initial project scaffold: `sql/schema.sql` (dim_vessel, dim_agent,
  dim_zone, fact_zone_event), `scripts/build_db.py` build pipeline,
  `scripts/lib/parse.py` field-parsing helpers.
- Full build validated against all 36 source files spanning 2019-01-01
  through 2026-07-31 (314,335 raw rows -> 314,089 fact rows after
  dedup; 10,316 vessels, 41 agents, 219 zones).
- `docs/BUILD.md` (pipeline internals) and `docs/WHITEPAPER.md` (purpose,
  design rationale, known limitations, roadmap).
- `docs/DATA_QUALITY.md` auto-generated report, refreshed on every build.
- `mrtis-rebuild-db` Cowork skill wrapping `scripts/build_db.py`.
- Decisions locked for v0.1: DuckDB as the engine, star schema with
  surrogate keys designed for future laytime/tariff/invoice joins, raw
  CSVs and the built `.duckdb` file kept out of version control, repo
  lives directly in this folder with GitHub push handled manually by the
  project owner.

### Known issues (see `docs/DATA_QUALITY.md` for current figures)
- Non-standard/missing IMO vessels identified by name only.
- ~9% of events have no recorded agent.
- Zone grouping is a heuristic, not an authoritative taxonomy.
- "Norton Lilly" vs "Norton Lilly Dest" (and similar) kept as distinct
  agents pending domain confirmation.
