# MRTIS

MRTIS turns the raw "Zone Report" vessel-movement exports in this folder into
a queryable DuckDB warehouse: every Arrive / Depart / Enter / Exit event, by
vessel, agent, and zone, from 2019 through the present.

For the full story on *why* this exists and how it's meant to grow, see
[docs/WHITEPAPER.md](docs/WHITEPAPER.md). For how the pipeline actually works,
see [docs/BUILD.md](docs/BUILD.md). For the health of the most recent build,
see [docs/DATA_QUALITY.md](docs/DATA_QUALITY.md).

## Quickstart

```bash
pip install -r requirements.txt

# Build (or rebuild) the database from every "Zone Report*.csv" file in this folder
python3 scripts/build_db.py
```

This produces `data/db/mrtis.duckdb` and refreshes `docs/DATA_QUALITY.md`.
Drop new Zone Report CSV exports into this folder at any time and re-run the
same command -- the build is idempotent and always reflects exactly what's on
disk.

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
│   └── DATA_QUALITY.md      -- auto-generated report from the most recent build
├── sql/
│   └── schema.sql           -- DuckDB DDL for dim_vessel, dim_agent, dim_zone, fact_zone_event
├── scripts/
│   ├── build_db.py          -- main entrypoint: ingest -> transform -> load
│   └── lib/parse.py         -- field-parsing helpers (draft, mile, IMO, zone classification)
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
