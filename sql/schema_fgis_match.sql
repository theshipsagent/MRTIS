-- FGIS matching / consolidation layer.
--
-- Built by scripts/build_fgis_match.py, which runs AFTER scripts/build_db.py
-- (core warehouse) and scripts/build_fgis.py (raw FGIS ingest). Kept in its
-- own file, and out of sql/schema.sql, because build_db.py owns/drops only
-- the four core tables -- the same separation build_fgis.py already follows.
--
-- Grain of fgis_record: ONE ROW PER (vessel, Cert Date) for matched records,
-- or per (normalized carrier name, Cert Date) for unmatched ones. Every
-- fgis_output line rolls up into exactly one fgis_record, matched or not, so
-- this table is a complete consolidation of fgis_output rather than only the
-- successful matches -- nothing is silently dropped.

CREATE TABLE IF NOT EXISTS fgis_record (
    fgis_record_id      VARCHAR PRIMARY KEY,
        -- Human-readable and stable across rebuilds (unlike surrogate ints,
        -- which shift whenever the source CSV set changes):
        --   matched, vessel has IMO : '9812494-20260114'
        --   matched, no valid IMO   : 'NONAME-SOMEVESSEL-20260114'
        --   unmatched               : 'UNMATCHED-SOMEVESSEL-20260114'
        -- Unique by construction: the rollup key (vessel_key, cert_date)
        -- maps 1:1 onto (imo, cert_date).
    match_status        VARCHAR,   -- matched | unmatched_outside_coverage
                                   -- | unmatched_no_candidate | unmatched_ambiguous
    cert_date           DATE,      -- FGIS Cert Date = date loading completed

    -- MRTIS vessel identity (NULL on every unmatched row -- no guessing)
    vessel_key          BIGINT REFERENCES dim_vessel(vessel_key),
    imo                 VARCHAR,   -- canonical IMO, RESOLVED by the name match
                                   -- rather than assigned to FGIS up front
    vessel_name         VARCHAR,   -- dim_vessel's current name for that vessel
    matched_name        VARCHAR,   -- the alias spelling that actually matched
    name_normalized     VARCHAR,   -- parse.normalize_vessel_name() form
    carrier_name_raw    VARCHAR,   -- distinct raw FGIS Carrier Name spelling(s)

    -- Rolled-up cargo detail: comma-separated, deduplicated, sorted
    grain               VARCHAR,
    grain_class         VARCHAR,
    destination         VARCHAR,
    metric_ton_total    DOUBLE,
    pounds_total        DOUBLE,
    line_count          INTEGER,   -- fgis_output lines consolidated here

    -- Cross-reference back into the warehouse (NULL when unmatched)
    mrtis_event_key     BIGINT REFERENCES fact_zone_event(event_key),
    mrtis_event_time    TIMESTAMP, -- the berth departure this cert resolved to
    mrtis_zone_name     VARCHAR,   -- Elevator/Mid-Stream zone the link points at
    mrtis_event_action  VARCHAR,   -- 'Depart' (the sailing -- correct anchor) or
                                   -- 'Arrive' (fallback, used only when MRTIS
                                   -- recorded no sailing). ALWAYS check this
                                   -- before treating mrtis_event_time as a
                                   -- sailing timestamp.
    day_offset          INTEGER,   -- event date - cert date; positive for a
                                   -- sailing, typically negative for a fallback
                                   -- arrival (the vessel berths before loading
                                   -- completes)
    candidate_pool_size INTEGER,   -- vessels departing a grain berth in-window
    match_method        VARCHAR,   -- 'exact_normalized' (matched on the sailing)
                                   -- or 'arrive_fallback' (matched on the berth
                                   -- arrival because no sailing was recorded).
                                   -- Neither ever uses fuzzy matching.
    match_note          VARCHAR    -- why an unmatched row didn't match
);

-- Line-level bridge: which fgis_output certificate lines rolled into which
-- consolidated record. This is the "trace front and back" link -- fgis_output
-- itself is left untouched so build_fgis.py stays the sole owner of it.
CREATE TABLE IF NOT EXISTS fgis_record_line (
    fgis_record_id      VARCHAR REFERENCES fgis_record(fgis_record_id),
    fgis_output_key     BIGINT,    -- -> fgis_output(fgis_output_key)
    fgis_raw_key        BIGINT     -- -> fgis_raw(fgis_raw_key)
);

CREATE INDEX IF NOT EXISTS idx_fgis_record_vessel ON fgis_record(vessel_key);
CREATE INDEX IF NOT EXISTS idx_fgis_record_cert   ON fgis_record(cert_date);
CREATE INDEX IF NOT EXISTS idx_fgis_record_status ON fgis_record(match_status);
CREATE INDEX IF NOT EXISTS idx_fgis_record_event  ON fgis_record(mrtis_event_key);
CREATE INDEX IF NOT EXISTS idx_fgis_line_record   ON fgis_record_line(fgis_record_id);
CREATE INDEX IF NOT EXISTS idx_fgis_line_output   ON fgis_record_line(fgis_output_key);
