# MRTIS: Marine/River Traffic Information System
### Project white paper -- v0.1, 2026-08-18

## 1. Problem

The Zone Report exports capture, at fine granularity, every vessel
Arrive/Depart/Enter/Exit event across a set of river zones (anchorages, buoy
ranges, terminals, crossings) since 2019 -- over 300,000 events covering more
than 10,000 vessels and 40 shipping agents. On disk as flat CSVs, split into
multiple files per period, this data is hard to query, easy to double-count,
and isolated from the other systems (laytime, tariffs, invoicing) it would
naturally inform.

MRTIS exists to turn that raw export stream into a single, trustworthy,
queryable warehouse -- and to do it in a way that survives contact with new
years of data without a rebuild-from-scratch each time.

## 2. What MRTIS is

A local-first data pipeline and warehouse:

- **Input**: "Zone Report*.csv" files, exported periodically and dropped into
  the project folder as-is.
- **Transformation**: `scripts/build_db.py`, a deterministic, idempotent
  Python/DuckDB pipeline (see `docs/BUILD.md` for the full mechanics).
- **Output**: `data/db/mrtis.duckdb`, a star-schema warehouse -- one fact
  table (`fact_zone_event`) and three dimensions (`dim_vessel`, `dim_agent`,
  `dim_zone`), plus `dim_vessel_name_alias` (every name spelling a vessel has
  carried, since ~12% are renamed mid-history) and the FGIS grain-export
  layer (`fgis_raw`, `fgis_output`, `fgis_record`, `fgis_record_line`) --
  plus a plain-English `docs/DATA_QUALITY.md` report on every build.
- **Operability**: a Cowork skill (`skills/mrtis-rebuild-db`) that runs the
  rebuild on demand, so refreshing the warehouse doesn't require remembering
  a command.

## 3. Why these design choices

**DuckDB over SQLite/Postgres.** The workload is analytical (aggregations
across hundreds of thousands of rows: transit times, agent volumes, zone
dwell patterns) rather than transactional. DuckDB is embedded (no server to
run or maintain), fast at exactly this kind of columnar aggregation, and
still just a single file that's trivial to back up or hand to someone else.
Postgres would add real operational overhead (a server, a hosting decision,
auth) for no benefit at this scale and single-user-for-now usage pattern.
SQLite would work but is measurably weaker at the analytical query patterns
this data invites.

**Star schema with surrogate keys, not a flat table.** The raw data is
already close to a single flat table. We deliberately normalized it into a
fact table plus three dimensions so that:

1. Vessel, agent, and zone each have one canonical row and a stable
   surrogate key (`vessel_key`, `agent_key`, `zone_key`), even though the raw
   IMO/agent-name/zone-name strings have real-world messiness (see §4).
2. Future data sources -- laytime calculations, tariff schedules, invoices --
   can join against those same keys without touching this schema, because
   the join surface was designed for that from day one rather than bolted on
   later.

**Full rebuild, not incremental append.** At current volume (~314K rows,
seconds to rebuild) the simplicity and correctness guarantee of "the database
always exactly reflects what's on disk in the source folder" outweighs the
speed gained from incremental loading. Revisit if/when volume grows by an
order of magnitude or more.

**Raw CSVs and the built database are not version-controlled.** The CSVs are
the source of truth and can be large/numerous; the `.duckdb` file is fully
regenerable from them. Versioning the pipeline code, schema, and docs gives
full reproducibility without bloating git history with data.

## 4. Known limitations (see `docs/DATA_QUALITY.md` for current numbers)

- **Dredge/workboat traffic is removed, not flagged.** The 9 vessels marked
  `exclude_as_dredge=Y` in `dictionaries/dredge_exclusions.csv` are filtered
  out at ingest (~23,200 rows, 7.4% of the raw feed) so high-frequency
  non-cargo movers don't crowd out real traffic. Build with `--keep-dredges`
  to retain them; the counts dropped are always reported.
- **Corrupted IMOs are repaired, conservatively.** A 7-digit IMO carries a
  check digit; values failing it are typos that fork one ship into two.
  Where an invalid IMO has exactly one same-name valid twin they are merged
  (31 repairs, 256 rows). Where a name has two valid IMOs -- genuinely two
  different ships, as with both `Aquitania`s -- nothing is merged.
- **~1-3% of vessels lack a standard 7-digit IMO.** These are identified by
  name instead, which is a reasonable but imperfect fallback -- if a
  no-IMO vessel name is ever reused for a genuinely different vessel, their
  histories will be merged in `dim_vessel`.
- **~9% of events have no recorded agent.** Loaded as `agent_key = NULL`
  rather than guessed.
- **FGIS records outside the Zone Report's coverage can't be matched.**
  FGIS publishes from 2018 while the Zone Report exports start 2019-01-01,
  so 1,929 consolidated FGIS records predate anything MRTIS can match them
  against; a further 70 postdate the newest export (FGIS updates weekly and
  runs ahead). Neither is a matching failure -- the latter resolve on their
  own as newer exports arrive.
- **Zone grouping is heuristic**, based on substring matching on zone names
  (e.g. "contains 'Anch'" -> Anchorage). It's a reasonable starting
  classification, not an authoritative one.
- **Agent name variants** (e.g. "Norton Lilly" vs. "Norton Lilly Dest") are
  currently kept as distinct agents rather than merged, since it's not yet
  established whether these represent genuinely different offices/desks or
  simple data-entry variance. Worth resolving with domain knowledge before
  building agent-level reporting that assumes one row per company.

## 5. Intended uses

- **Vessel traffic analytics**: transit times between zones, dwell time at
  anchorages, seasonal/annual volume trends.
- **Agent performance / activity**: event volume and vessel mix by agent
  over time.
- **Foundation for laytime support**: zone arrival/departure timestamps are
  a natural input to laytime calculations once that data source is joined
  in (see `docs/BUILD.md` "Extending the schema").
- **Congestion / capacity signal**: concurrent vessel counts by zone over
  time.
- **Agency fee accrual**: `fact_zone_event.agency_fee` prices every sailing
  from a facility berth ($10,500 for bulk carriers, $3,500 for everything
  else), so revenue by agent, berth, vessel type or period is a single
  `SUM()` away. ~48,300 chargeable sailings and ~$350M across 2019-2026,
  running a steady ~$45M/year.

## 6. Roadmap

- [x] Ships register enrichment (ship_type_group/dwt/tpc on dim_vessel,
      matched by canonical IMO against William's separate Ships_Register/
      Sea-web pipeline) -- done 2026-08-18, see docs/BUILD.md.
- [ ] Resolve agent name-variant question (§4) with domain input.
- [ ] Join in laytime data once available, per the extension pattern in
      `docs/BUILD.md`.
- [ ] Revisit zone taxonomy with authoritative zone groupings if/when
      available.
- [ ] Consider incremental build once source volume materially grows.
- [ ] Expand the skill set beyond rebuild (e.g. a query/reporting skill),
      once the rebuild skill has proven out in daily use.
- [ ] Build the canonical dictionary-driven transform layer (zone/agent/
      vessel-type dictionaries are complete, see docs/OPEN_QUESTIONS.md)
      and the voyage/port-call assembly logic.
- [x] FGIS raw ingest (grain export certification data, 2018-YTD, USDA
      public source) -- done 2026-08-18, see docs/BUILD.md.
- [x] FGIS vessel matching, rollup/consolidation, and MRTIS cross-reference
      -- done 2026-08-18, see docs/BUILD.md and docs/FGIS_MATCH_QUALITY.md.
      99.2% of in-coverage FGIS records resolved to an MRTIS vessel and
      berth event (98.7% on the sailing, 1.3% on the arrival fallback where
      MRTIS never recorded a sailing); 1 ambiguous and 94 no-candidate
      cases go to dictionaries/fgis_match_review.csv rather than guessed.
      Dry bulk only -- tanker and gas vessels are never candidates.
- [ ] The 99 remaining no-candidate FGIS records are the hard tail:
      mostly vessels seen only at `SWP Cross` (leaving the river) with no
      berth event at all, plus ~42 name variants MRTIS records differently
      (`SEAS I`/`SEAS1`, and a systematic `CEMTEX`->`CEEX` corruption
      affecting a whole vessel family). Investigated 2026-08-18 -- the
      Elevator/Mid-Stream zone list is NOT the gap.
- [ ] Feed matched FGIS cargo/tonnage into MRTIS proper (Cargo Group,
      Cargo, Destination, estimated tons) once the canonical/port-call
      layer that owns those columns exists.

## 7. Project history

See `CHANGELOG.md` for the dated record of what changed and why.
