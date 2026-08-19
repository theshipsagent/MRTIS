# Changelog

All notable changes to this project are recorded here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
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
