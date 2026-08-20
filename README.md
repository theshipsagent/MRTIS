# MRTIS

MRTIS turns the raw "Zone Report" vessel-movement exports in this folder into
a queryable DuckDB warehouse: every Arrive / Depart / Enter / Exit event, by
vessel, agent, and zone, from 2019 through the present.

For the full story on *why* this exists and how it's meant to grow, see
[docs/WHITEPAPER.md](docs/WHITEPAPER.md). For how the pipeline actually works,
see [docs/BUILD.md](docs/BUILD.md). For the health of the most recent build,
see [docs/DATA_QUALITY.md](docs/DATA_QUALITY.md). For how raw events become
port calls, see [docs/PORT_CALL_SPEC.md](docs/PORT_CALL_SPEC.md).

## Quickstart

```bash
pip install -r requirements.txt

# Build (or rebuild) the database from every "Zone Report*.csv" file in this folder
python3 scripts/build_db.py

# Assemble those events into port calls (run after build_db.py, and after
# scripts/build_fgis_match.py if you want cargo attached)
python3 scripts/build_port_calls.py
```

The first produces `data/db/mrtis.duckdb` and refreshes `docs/DATA_QUALITY.md`;
the second produces the `port_call` / `port_call_leg` / `port_call_event`
tables and `docs/PORT_CALL_QUALITY.md`.

Drop new Zone Report CSV exports into this folder at any time and re-run the
same commands -- the build is idempotent and always reflects exactly what's on
disk. `build_db.py` reassigns surrogate keys, so it drops the FGIS and port call
layers and tells you to re-run them.

`port_call_event` is the table to query for analysis: one row per raw event,
source values preserved alongside the canonical ones, assembled into port calls
and legs with activity, cargo, agency and waiting time attached. See
[docs/PORT_CALL_SPEC.md](docs/PORT_CALL_SPEC.md).

### Querying the database

```bash
python3 -c "
import duckdb
con = duckdb.connect('data/db/mrtis.duckdb', read_only=True)
print(con.execute('''
    select a.agent_name, count(*) events
    from fact_zone_event f
    left join dim_agent a on f.agent_key = a.agent_key
    group by 1 order by events desc limit 10
''').df())
"
```

Or open it directly with the DuckDB CLI: `duckdb data/db/mrtis.duckdb`.

## Project structure

```
MRTIS/
├── README.md              -- you are here
├── CHANGELOG.md            -- project history
├── requirements.txt
├── .gitignore               -- raw CSVs and the built .duckdb file are not versioned
├── Zone Report*.csv         -- raw source exports (not versioned, live here as dropped)
├── docs/
│   ├── WHITEPAPER.md        -- purpose, methodology, design rationale, roadmap
│   ├── BUILD.md             -- pipeline internals, schema, how to extend
│   ├── PORT_CALL_SPEC.md    -- port call assembly rules, and what a guardrail is
│   ├── OPEN_QUESTIONS.md    -- every business ruling, with its evidence and figures
│   ├── SESSION_LOG.md       -- one entry per working session; the context thread
│   ├── FGIS_MATCH_SPEC.md   -- how FGIS certificates resolve to MRTIS vessels
│   ├── PORT_CALL_QUALITY.md -- auto-generated report from the assembly
│   ├── DATA_QUALITY.md      -- auto-generated report from the most recent build
│   ├── FGIS_*_QUALITY.md    -- auto-generated reports from the FGIS stages
│   └── audit/               -- independent adversarial audits, read-only by design
├── sql/
│   ├── schema.sql           -- DuckDB DDL for dim_vessel, dim_agent, dim_zone, fact_zone_event
│   ├── schema_fgis_match.sql-- DDL for fgis_record, fgis_record_line
│   └── schema_port_call.sql -- DDL for port_call, port_call_leg, port_call_event
├── scripts/
│   ├── build_db.py          -- stage 1: ingest -> transform -> load the core warehouse
│   ├── build_fgis.py        -- stage 2: raw USDA FGIS export ingest
│   ├── build_fgis_match.py  -- stage 3: FGIS -> vessel matching and cross-reference
│   ├── build_port_calls.py  -- stage 4: assemble events into port calls and legs
│   ├── lib/parse.py         -- field-parsing helpers (draft, mile, IMO, zone classification)
│   └── lib/guardrails.py    -- the hard/soft check framework the builds run on themselves
├── dictionaries/            -- William's hand-built reference data, versioned
│   ├── zone_facility.csv    -- zone -> facility, facility_type, ops. The authority.
│   ├── vessel_type.csv      -- raw Type -> canonical vessel type
│   ├── dredge_exclusions.csv-- vessels filtered at ingest as non-cargo noise
│   ├── ships_register_fleet.csv -- snapshot from the Ships_Register project
│   └── *_review.csv         -- auto-generated: what a build declined to decide alone
├── data/
│   └── db/mrtis.duckdb      -- built database (not versioned, regenerable)
└── skills/
    └── mrtis-rebuild-db/    -- the Cowork skill that runs the rebuild on demand
```

## Working session cadence

This project spans a lot of back-and-forth, so a few conventions to keep
things clean -- see [docs/BUILD.md#session-cadence](docs/BUILD.md) for the
full detail:

- **Commit** after any change to `scripts/`, `sql/`, or `docs/` that leaves
  the build in a working state.
- **Push** to GitHub is a manual, deliberate step you run yourself (see
  `docs/BUILD.md` for the one-time remote setup) -- nothing here pushes
  automatically.
- **New session**: start fresh once a unit of work (e.g. "add laytime
  join support") is committed, rather than carrying a very long thread.
- **Model**: default is fine for most of this work; consider a
  stronger-reasoning model for schema/design decisions, and it's safe to use
  a lighter one for mechanical, well-specified script edits.
