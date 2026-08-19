# Build pipeline

## What the source data looks like

Every "Zone Report *.csv" file has the same 9 columns:

| Column | Example         | Notes |
|--------|-----------------|-------|
| IMO    | `9812494`       | Standard IMO is 7 digits. ~3% of rows have a non-standard or blank IMO (tugs, barges, entry variance). |
| Name   | `Ultra Angel`   | Vessel name as reported. |
| Action | `Arrive`        | One of `Arrive`, `Depart`, `Enter`, `Exit`. |
| Time   | `01/10/2020 12:58` | `MM/DD/YYYY HH:MM`, consistent across every file checked (2019-2026). |
| Zone   | `133 Buoys`     | 219 distinct zone names seen across the full history (anchorages, buoy ranges, terminals, crossings). |
| Agent  | `Southport`     | ~9.4% of rows are blank -- no agent recorded for that event. |
| Type   | `Bulk`          | Vessel type at time of event; can change between events for the same vessel. ~21% blank. |
| Draft  | `42ft`          | Always `<digits>ft` when present. |
| Mile   | `134M`          | River mile marker; can be negative (`-19M`); ~18% blank. |

Each period export (e.g. "01-01-24 - 12-31-24") is split across up to 4
files. This is **not** duplication -- each file covers a disjoint slice of
zones (confirmed by checking key overlap across a full year: 0 overlapping
`(IMO, Action, Time, Zone)` tuples between the 4 files of the same period).
The build script simply reads every CSV in the source folder and unions them.

## Pipeline stages (`scripts/build_db.py`)

1. **Find & load** -- glob `Zone Report*.csv` in the source directory (default:
   the project root), read each with pandas (`dtype=str` -- nothing is typed
   until the transform stage), tag every row with `source_file`.
2. **Transform** (`transform()`):
   - Parse `Time` -> `event_time` (timestamp).
   - Parse `Draft` -> `draft_ft` (int), `Mile` -> `mile_marker` (float),
     via `scripts/lib/parse.py`.
   - Compute `imo_valid` (exactly 7 digits) and a `natural_vessel_key`: the
     IMO when valid, otherwise `NONAME:<UPPERCASED NAME>`. This is how
     tugs/barges without a real IMO still get a stable vessel identity across
     events, at the cost of merging any two different vessels that happen to
     share an exact name with no valid IMO on either side.
   - Blank `Agent` becomes `NULL` rather than an empty string.
   - **Filter dredge/workboat noise** listed in
     `dictionaries/dredge_exclusions.csv` (`exclude_as_dredge=Y`) — 9 vessels,
     ~23,200 rows (7.4% of the feed), removed at ingest so high-frequency
     non-cargo movers don't crowd out real traffic in every rollup. Matched
     **by canonical IMO** where the dictionary has one, falling back to name
     only for the four entries with no IMO (and then only against rows that
     themselves carry no valid IMO). That precision matters: "Texas Star" is
     both a dredge and, separately, a real tanker — a name-only filter deletes
     both. `--keep-dredges` retains them; counts dropped are always reported.
   - **Repair corrupted IMOs** before they become vessel identity. A 7-digit
     IMO carries a check digit (first six digits x 7,6,5,4,3,2; last digit of
     the sum must equal the seventh). A value failing it is a typo, not a
     vessel — left alone it forks one ship into two. `Spring Aura` 9991064
     (invalid) took two events out of the middle of 9991082's single
     continuous Zen-Noh loading, making one ship appear to be two at the same
     elevator on the same day. Where an invalid IMO has **exactly one**
     same-name check-digit-valid twin, the two are merged: 31 repairs, 256
     rows. Guarded so genuinely different vessels are never merged — both
     `Aquitania`s (9300491, 9611278) are real bulk carriers with valid check
     digits and stay separate, as does `Sea Voyager`, which has two valid
     twins rather than one.
   - **Deduplicate** on `(natural_vessel_key, action, Time, zone_name)`,
     keeping the first occurrence. This matters because the 2020 exports have
     a one-day boundary overlap between "01-01-20 - 02-02-20" and
     "02-01-20 - 12-31-20" -- across the full 2019-2026 dataset this drops
     246 rows out of 314,335.
3. **Build dimensions & fact** (`build_dims_and_fact()`):
   - `dim_vessel`: one row per `natural_vessel_key`, with the most recently
     observed name/IMO/type and first/last-seen timestamps.
   - `dim_vessel_name_alias`: one row per (vessel, distinct name spelling)
     ever observed. `dim_vessel` keeps only the latest name, but 1,217 of
     10,270 vessels (11.9%) are renamed during the covered period (IMO
     9397456 is `Hellas Explorer` in 2019 and `Alithini II` by 2022), so
     any by-name match against `dim_vessel` alone would silently miss every
     record predating the rename. FGIS matching resolves against this table.
   - `dim_vessel.vessel_type_canonical`: the raw `Type` mapped through
     `dictionaries/vessel_type.csv` to Bulk / Container / Gas / Other /
     Passenger / Reefer / Tanker. Blank source Type stays NULL — unknown is
     never guessed. This is the column FGIS matching uses to stay dry-bulk
     only. Note the ships register cannot supply it: that snapshot contains
     only Bulk Carrier and General Cargo rows — **zero tankers, gas carriers
     or cruise ships** — so the Zone Report's own Type field plus this
     dictionary is the only classification available for those 3,395 vessels.
   - `dim_vessel.imo_check_valid`: FALSE where the IMO fails its check digit
     and no unambiguous same-name twin existed to merge it into.
   - `dim_agent`: one row per distinct non-blank agent name.
   - `dim_zone`: one row per distinct zone name, plus a heuristic
     `zone_group` (Anchorage / Buoy Range / Crossing / Slip / Terminal-Berth)
     from `classify_zone_group()` -- string-pattern matching, not an
     authoritative taxonomy. Override individual rows post-load if you have
     better ground truth.
   - `dim_zone.facility_type`: the authoritative classification from
     `dictionaries/zone_facility.csv` (Elevator / Mid-Stream / Bulk Cargo /
     General Cargo / Tank Storage / Chemical Plant / Refinery / Anchorage /
     Pilot Station / Cruise / LNG). Prefer this over the heuristic
     `zone_group` beside it.
   - `fact_zone_event`: one row per source event, joined to the three
     dimensions via surrogate keys.
   - `fact_zone_event.agency_fee`: the agency fee in USD, accrued on
     **sailing from a facility berth** — `action = 'Depart'` at any zone whose
     `facility_type` is not `Anchorage` or `Pilot Station`. NULL everywhere
     else, so `SUM(agency_fee)` over any slice is the fee earned on it with no
     extra filtering. The rate is driven by the **vessel, not the berth**:
     `vessel_type_canonical = 'Bulk'` → $10,500, everything else → $3,500.

     Vessel-based was chosen over berth-based after measuring both: it covers
     90.5% of berth departures against 82.1% for facility-based (which misses
     every General Cargo berth), and it follows the ship being agented rather
     than the dock it happens to occupy. The two bases disagree on 487
     departures — mostly bulk carriers at chemical-plant and tank-storage
     berths — and the vessel wins those. The `$3,500` tier is a catch-all
     covering tanker, gas, container, cruise, reefer and blank-type vessels;
     see the caveat in `docs/DATA_QUALITY.md` about charging *unknown* as
     non-bulk.
4. **Write** -- `DROP`/recreate all four tables in `data/db/mrtis.duckdb`
   from `sql/schema.sql`, then load the built DataFrames.
5. **Report** -- write `docs/DATA_QUALITY.md` with row counts, date range,
   duplicate/blank statistics, and the known caveats above.

The whole run is a full rebuild, not an incremental append. At ~314K rows it
takes seconds; there's no need for incremental logic until volume grows by
orders of magnitude.

## Ships register enrichment

`dim_vessel` is enriched with `ship_type` (the register's raw type/family),
`ship_type_group` (size-bucketed within family), `dwt` (deadweight), and
`tpc` (tonnes per centimetre immersion) from William's separate
Ships_Register project (a Sea-web/S&P Global Maritime pull). The join is by
canonical IMO only -- no match, no guess: unmatched vessels simply have these
four columns NULL (see `docs/DATA_QUALITY.md` for the current match rate).

Where a matched family carries no size vocabulary at all (Cement Carrier,
Aggregates Carrier, self-discharging Lakers, ...), `ship_type_group` would
otherwise be NULL even though the vessel's type is known -- there it is
backfilled from `ship_type` instead (William, 2026-08-19: a gap is worse than
a variance in convention). `ship_type` always holds the register's original
value regardless.

**As of 2026-08-19, the register is a full world-fleet pull, not the earlier
dry-cargo-only extract.** `fleet_joined` went 20,101 -> 49,763 rows and 19 ->
133 `ship_type_group` values (tanker, containership, LPG/LNG, reefer, cruise,
PCC/PCTC, ro-ro all added), with zero blank `ship_type_group` at the source.
Match coverage against `dim_vessel` went **60.9% -> 99.4%** of all vessels
(**61.1% -> 99.8%** of those with a valid IMO). An earlier version of this
section described the gap as a deliberate dry-cargo scope boundary requiring a
further Sea-web pull for tankers/containers/gas (see `OPEN_QUESTIONS.md` §9)
-- that pull turned out to already be superseded by this world-fleet
expansion, done in the Ships_Register project directly; no further chunk pull
was needed once the refresh below ran.

**24 vessels with a valid-format IMO remain unmatched**, none of which need
another pull: 13 have MRTIS-side checksum-invalid IMOs (a key-repair problem,
not a register gap), 10 are present in Sea-web's source data but held behind
its pre-1980 build-year filter, and 1 (`9493523`, Stena Premium) is genuinely
absent from the register. Two of the pre-1980 vessels are active merchant
tonnage rather than genuine edge cases -- `7633375` Sunnanvik (cement carrier,
1978, 8,407 dwt) and `7711725` Radcliffe R. Latimer (self-discharging Laker,
1978, 37,257 dwt). **Decided, William, 2026-08-19: leave as is, no gate
change.** Both bill correctly regardless -- Zone Report `Type` is already
`Bulk` for both, which decides the fee before `ship_type_group` is ever
consulted -- they simply carry no `dwt`/`tpc`. A further 39 vessels carry no
IMO at all and were never candidates for this join.

The reference data lives at `dictionaries/ships_register_fleet.csv`, a
snapshot exported from `Ships_Register/data/out/ships_register.duckdb`'s
`fleet_joined` table. It's a snapshot, not a live read of the other
project, so MRTIS stays buildable on its own. To refresh it after
Ships_Register pulls a new batch:

```python
import duckdb, csv
con = duckdb.connect("<path to Ships_Register>/data/out/ships_register.duckdb", read_only=True)
rows = con.execute("select imo, name_of_ship, ship_type, ship_type_group, deadweight, tpc from fleet_joined order by imo").fetchall()
con.close()
with open("dictionaries/ships_register_fleet.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["imo", "name_of_ship_ref", "ship_type_ref", "ship_type_group", "dwt", "tpc"])
    w.writerows(rows)
```

then re-run `scripts/build_db.py` as usual.

## FGIS raw ingest

`scripts/build_fgis.py` is a separate, standalone pipeline (not part of
`build_db.py`) that pulls USDA Federal Grain Inspection Service export
certification data and loads it into two new tables in `mrtis.duckdb`:

- **`fgis_raw`** -- every column FGIS publishes (~110 columns of grading/
  quality detail: moisture, protein, test weight, aflatoxin, DON, etc.),
  every carrier type (ship/rail/truck/barge/container/other), every port,
  nationally, 2018 through the current year. Kept for other future uses per
  William's instruction, not just the Mississippi River slice.
- **`fgis_output`** -- the 14 columns William specified (Thursday, Type
  Shipm, Cert Date, Type Carrier, Carrier Name, Grain, Class, Pounds,
  Destination, Field Office, Port, AMS Reg, FGIS Reg, Metric Ton), filtered
  to `Type Carrier = 1 AND Port = "MISSISSIPPI R."` (ocean vessels calling
  the river). Adds typed `thursday_date`/`cert_date_parsed` (dates) and
  numeric `metric_ton`/`pounds` alongside the raw string columns.

**Source**: `https://fgisonline.ams.usda.gov/ExportGrainReport/CY{year}.csv`
-- one plain public CSV per calendar year, no auth, no scraping. Confirmed
2026-08-18 by inspecting the page directly; `default.aspx` there is just a
static list of these direct download links.

**Full rebuild, same philosophy as `build_db.py`**: every run re-downloads
each requested year's CSV (current year updates weekly, so it must always
be re-pulled; earlier years are stable but re-pulled anyway for simplicity)
and drops/recreates `fgis_raw`/`fgis_output` from scratch. `fgis_raw` is
built with `CREATE TABLE AS SELECT` straight from the loaded dataframe
rather than hand-maintained DDL in `sql/schema.sql` -- with ~110 columns
that can drift slightly year to year, generating the schema from whatever
FGIS actually published is more robust than a fixed column list. Use
`--use-cache` to skip the network round-trip and re-load from whatever's
already in `fgis_source/` during iteration.

```bash
python3 scripts/build_fgis.py                          # full 2018-YTD pull
python3 scripts/build_fgis.py --start-year 2024 --use-cache  # dev iteration
```

`build_fgis.py` stops at raw ingest. Resolving `Carrier Name` to an MRTIS
vessel, rolling lines up, and writing the cross-reference is the next
script -- see below.

## FGIS matching, consolidation, and cross-reference

`scripts/build_fgis_match.py` is the third and final stage, implementing
`docs/FGIS_MATCH_SPEC.md`. It reads what the first two stages built and
writes two new tables plus a cross-reference. No network access needed.

1. **Resolve** each FGIS `carrier_name` (free text, no IMO) to a
   `vessel_key`:
   - Narrow to vessels that **departed** an Elevator or Mid-Stream berth
     (52 zones from `dictionaries/zone_facility.csv`) within
     `cert_date - 1 day .. cert_date + 4 days`. The window is asymmetric
     on purpose: the certificate is issued when loading completes and the
     vessel sails after (66.6% of matches depart the same day, 28.1% the
     next, 3.9% at +2, 1.0% at +3, 0.4% at +4), so the day backwards is only
     clock-rollover slack. +4 is the measured optimum: because the arrival
     fallback already absorbs most late sailings, widening buys anchor
     quality rather than volume, and past +4 a previously-matched record
     tips into ambiguity -- see `docs/FGIS_MATCH_SPEC.md`.
   - Match on an **exact punctuation-normalized name**
     (`lib/parse.normalize_vessel_name`). There is **no fuzzy/edit-distance
     matching anywhere**: `DSL Phoenix` and `D.S.L. Phoenix` are the same
     vessel and normalize together, while `DSI Phoenix` is a genuinely
     different real vessel one character away.
   - Matching runs against `dim_vessel_name_alias`, not
     `dim_vessel.vessel_name`, because ~12% of vessels are renamed during
     the covered period and `dim_vessel` keeps only the latest name.
   - **Fallback**: where MRTIS recorded no sailing at all (a genuine
     event-capture gap -- the vessel is seen berthing at the elevator and
     later exiting the river, with no berth departure in between), fall
     back to the **arrival** at the same class of berth, within
     `cert_date - 6 .. cert_date + 1`. Per William, for a grain call the
     arrival is a valid anchor and the berth stay runs about 4 days or less;
     confirmed against the matches that have both events -- 90.7% of stays
     are <=4 days (median 2). The window runs to -6 rather than -4 because
     the real Statements of Fact show longer stays are routine (Ultra
     Leopard 5.2 days loading grain, Desert Seeker 8.0 -- see
     `docs/PORT_CALL_EVIDENCE.md`); coverage is 93.1% at -4, 96.4% at -5,
     97.9% at -6, with no increase in ambiguous cases. The sailing is
     always preferred; the
     build verifies that no fallback record had a sailing available.
     `fgis_record.mrtis_event_action` records which event each link points
     at -- **check it before treating `mrtis_event_time` as a sailing
     timestamp.**
   - Zero or multiple matches are never guessed -- they go to
     `dictionaries/fgis_match_review.csv`.
2. **Consolidate** into one `fgis_record` per `(vessel_key, cert_date)`:
   grain/class/destination concatenated (comma-separated, deduplicated,
   sorted), `metric_ton`/`pounds` summed, under a human-readable
   `fgis_record_id` of `{IMO}-{certdate}`. Unmatched lines still get a
   record (prefixed `UNMATCHED-`), so `fgis_record` is a complete
   consolidation of `fgis_output` and nothing is silently dropped.
3. **Cross-reference** both ways: `fgis_record.mrtis_event_key` -> the
   berth departure, and `fact_zone_event.fgis_record_id` -> the record.
   `fgis_record_line` bridges each consolidated record back to the
   individual `fgis_output` lines it came from.

**The event link is many-to-one, not one-to-one.** One sailing routinely
carries grain certified across several consecutive days (e.g. `Dsi Aquila`
departing ADM AMA 2022-03-16 with soybeans certified 03-14 and corn
certified 03-15 and 03-16). 14.7% of matched departures carry 2-3 FGIS
records, so `fact_zone_event.fgis_record_id` holds only the primary record
(latest Cert Date) with `fgis_record_count` alongside it. **To total
tonnage or cargo for a sailing, aggregate `fgis_record` on
`mrtis_event_key`** -- reading the scalar column understates every
multi-certificate loading.

```bash
python3 scripts/build_fgis_match.py
```

Current result: 18,315 certificate lines -> 14,528 records, 99.2% of
in-coverage records matched (98.7% on the sailing, 1.3% on the arrival
fallback), 1 ambiguous. See `docs/FGIS_MATCH_QUALITY.md`.

## Run order

The three stages are separate scripts and must run in this order:

```bash
python3 scripts/build_db.py         # core warehouse (reads Zone Report CSVs)
python3 scripts/build_fgis.py       # FGIS raw ingest (downloads from USDA)
python3 scripts/build_fgis_match.py # matching + cross-reference
```

`event_key` and `vessel_key` are assigned by row index, so **any rebuild of
the core warehouse reassigns them** and invalidates an existing FGIS
cross-reference. Rather than leave links silently pointing at the wrong
rows, `build_db.py` drops `fgis_record`/`fgis_record_line` and prints a
reminder to re-run `build_fgis_match.py`. `fgis_raw`/`fgis_output` are
untouched by this -- they key off FGIS's own identifiers, not ours -- so
restoring the cross-reference after a rebuild costs seconds and needs no
network round-trip.

All three are full rebuilds and safe to re-run; `build_fgis_match.py` is
verified to produce byte-identical output across consecutive runs.

## Extending the schema for future data sources

This schema was deliberately built with `vessel_key` / `agent_key` /
`zone_key` as clean surrogate join keys so future sources can attach without
reshaping what's here:

- **Laytime data**: a new `fact_laytime_event` (or similar) table keyed on
  `vessel_key` (+ a voyage/call identifier) joins straight in.
- **Tariffs / invoices**: a `fact_invoice` table keyed on `agent_key` and/or
  `vessel_key` joins straight in.
- **Better zone taxonomy**: if you get an authoritative zone-to-group
  mapping, just update `dim_zone.zone_group` directly (or replace
  `classify_zone_group()` and rebuild) -- `zone_key` values are stable across
  rebuilds as long as zone names don't change.

To add a new dimension entirely, follow the pattern in `build_dims_and_fact()`:
compute a natural key, dedupe to one row per key, assign a surrogate key, add
the `CREATE TABLE` to `sql/schema.sql`, and join it into the relevant fact
table the same way `dim_agent`/`dim_zone` are joined into `fact_zone_event`.

## Re-running the build

Always safe to re-run. Drop new/updated CSVs into the source folder and run:

```bash
python3 scripts/build_db.py
```

Or use the `mrtis-rebuild-db` Cowork skill, which wraps this exact command.

## Session cadence

Because this project involves a lot of iterative design conversation, here's
the intended rhythm:

- **Commit locally** after each meaningful, working change -- a schema
  change, a new script, a docs update. Small, reviewable commits over one
  giant one.
- **Push to GitHub** is manual and deliberate. Set up the remote once:
  ```bash
  git remote add origin <your-empty-github-repo-url>
  git push -u origin main
  ```
  then `git push` whenever you want that commit history backed up remotely.
  Nothing in this project pushes on its own.
- **Start a new session** once a self-contained unit of work is committed
  (e.g. "added laytime join support", "reworked zone taxonomy") rather than
  running one very long thread across unrelated changes -- keeps context
  sharp and history easy to follow.
- **Model choice**: schema/design decisions and anything touching data
  correctness benefit from a stronger-reasoning model; mechanical,
  well-specified edits (add a column, tweak a regex, rerun a report) are
  fine on a lighter/faster one.
- **Good stopping points**: right after a successful `build_db.py` run with
  a clean `DATA_QUALITY.md`, and right after a commit -- the repo is in a
  known-good, reproducible state at both.
