-- MRTIS core warehouse schema
-- Star-shaped: one fact table (zone events) + three dimensions (vessel, agent, zone).
-- Surrogate keys throughout so future data sources (laytime, tariffs, invoices) can
-- join on vessel_key / agent_key / zone_key without touching this schema again.

CREATE TABLE IF NOT EXISTS dim_vessel (
    vessel_key      BIGINT PRIMARY KEY,
    imo_raw         VARCHAR,        -- IMO exactly as it appeared in the source export
    imo             VARCHAR,        -- canonical 7-digit IMO (first 7 digits of imo_raw), NULL if imo_raw had <7 digits
    imo_valid       BOOLEAN,        -- TRUE if `imo` is populated
    vessel_name     VARCHAR,        -- most recently observed name for this vessel
    natural_key     VARCHAR UNIQUE, -- imo if present, else 'NONAME:' || upper(name)
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP,
    most_common_type VARCHAR,       -- most frequently observed raw Type value (Bulk, Tank, ...)
    -- Canonical vessel type from dictionaries/vessel_type.csv, mapped from
    -- most_common_type: Bulk / Container / Gas / Other / Passenger / Reefer /
    -- Tanker. NULL when the source Type was blank -- never guessed. Used to
    -- scope cargo-specific work: FGIS grain matching considers dry-bulk
    -- vessels only and skips Tanker/Gas outright (they still get port calls
    -- built like any other vessel -- this scopes matching, not the warehouse).
    vessel_type_canonical VARCHAR,
    -- NOTE: vessels marked exclude_as_dredge=Y in
    -- dictionaries/dredge_exclusions.csv (dredges, workboats and other
    -- high-frequency non-cargo noise) are FILTERED OUT at ingest and never
    -- reach this table at all -- they are not flagged-and-kept. This focuses
    -- the warehouse on real cargo traffic. Build with --keep-dredges to
    -- retain them; the counts dropped are always reported in
    -- docs/DATA_QUALITY.md so the removal is visible, not silent.
    -- FALSE when `imo` fails the standard IMO check digit, i.e. the value is
    -- corrupted rather than a real vessel identity. See parse.imo_check_digit_valid.
    imo_check_valid BOOLEAN,
    -- Enriched by canonical IMO match against dictionaries/ships_register_fleet.csv
    -- (William's separate Ships_Register/Sea-web pipeline). NULL when no IMO match --
    -- never guessed/inferred.
    ship_type_group VARCHAR,        -- e.g. 'Bulk Carrier-Handymax'
    dwt             DOUBLE,         -- deadweight tonnage
    tpc             DOUBLE          -- tonnes per centimetre immersion
);

-- One row per (vessel, distinct name spelling) ever observed for that vessel.
-- dim_vessel keeps only the MOST RECENT name, but 11.9% of vessels in this
-- data are renamed at some point during the covered period (e.g. IMO 9397456
-- was 'Hellas Explorer' in 2019 and 'Alithini II' by 2022). Matching an
-- external source by name against dim_vessel alone would therefore silently
-- fail for any record predating a rename -- hence this table.
CREATE TABLE IF NOT EXISTS dim_vessel_name_alias (
    alias_key       BIGINT PRIMARY KEY,
    vessel_key      BIGINT REFERENCES dim_vessel(vessel_key),
    vessel_name     VARCHAR,   -- name exactly as it appeared in the source export
    name_normalized VARCHAR,   -- parse.normalize_vessel_name(vessel_name)
    first_seen      TIMESTAMP, -- first event carrying this spelling
    last_seen       TIMESTAMP, -- last event carrying this spelling
    event_count     BIGINT     -- how many events carried this spelling
);

CREATE TABLE IF NOT EXISTS dim_agent (
    agent_key       BIGINT PRIMARY KEY,
    agent_name      VARCHAR UNIQUE,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_zone (
    zone_key        BIGINT PRIMARY KEY,
    zone_name       VARCHAR UNIQUE,
    zone_group      VARCHAR,  -- heuristic classification: Anchorage / Buoy Range / Crossing / Slip / Terminal-Berth / Other
    -- Authoritative facility classification from dictionaries/zone_facility.csv:
    -- Elevator / Mid-Stream / Bulk Cargo / General Cargo / Tank Storage /
    -- Chemical Plant / Refinery / Anchorage / Pilot Station / Cruise / LNG.
    -- Prefer this over the heuristic zone_group above -- it is William's
    -- hand-built dictionary, not a string match.
    facility_type   VARCHAR
);

CREATE TABLE IF NOT EXISTS fact_zone_event (
    event_key       BIGINT PRIMARY KEY,
    vessel_key      BIGINT REFERENCES dim_vessel(vessel_key),
    agent_key       BIGINT REFERENCES dim_agent(agent_key),  -- NULL when source Agent was blank
    zone_key        BIGINT REFERENCES dim_zone(zone_key),
    action          VARCHAR,     -- Arrive / Depart / Enter / Exit
    event_time      TIMESTAMP,
    vessel_type     VARCHAR,     -- Type value as it appeared on this specific event
    draft_ft        INTEGER,     -- parsed from e.g. "42ft"
    mile_marker     DOUBLE,      -- parsed from e.g. "134M" / "-19M"; NULL if blank
    source_file     VARCHAR,     -- originating Zone Report CSV, for lineage/debugging
    -- Set by scripts/build_fgis_match.py. NULL for every event that isn't a
    -- matched Elevator/Mid-Stream 'Depart'.
    --
    -- CAUTION -- this link is many-to-one, not one-to-one. One sailing often
    -- carries grain certified across several consecutive days (e.g. Dsi
    -- Aquila, ADM AMA, 2022-03-16: soybeans certified 03-14 plus corn
    -- certified 03-15 and 03-16, one departure). ~14% of matched departures
    -- carry 2-3 FGIS records.
    --
    -- fgis_record_id holds only the PRIMARY record (latest Cert Date -- the
    -- certificate that completed the loading), as a convenience for
    -- single-row lookups. fgis_record_count tells you how many there really
    -- are. To total tonnage or cargo for a sailing you MUST aggregate
    -- fgis_record via its mrtis_event_key, not read this column -- doing the
    -- latter would silently understate any multi-certificate loading.
    fgis_record_id    VARCHAR,   -- -> fgis_record(fgis_record_id), primary only
    fgis_record_count INTEGER,   -- FGIS records resolving to this event (NULL if none)
    -- Agency fee in USD, accrued on SAILING from a facility berth. Set only
    -- where action = 'Depart' AND the zone's facility_type is an actual berth
    -- (i.e. not Anchorage and not Pilot Station); NULL everywhere else, so
    -- SUM(agency_fee) over any slice is the fee earned on it with no further
    -- filtering needed.
    --
    -- Rate is driven by the VESSEL, not the berth (confirmed by William
    -- 2026-08-19): dim_vessel.vessel_type_canonical = 'Bulk' -> 10500,
    -- everything else -> 3500. Vessel-based beats berth-based on coverage
    -- (90.5% vs 82.1% of berth departures) and follows the ship being
    -- agented rather than the dock it happens to be at. Note this settles the
    -- 487 departures where the two bases disagree in favour of the vessel --
    -- a bulk carrier sailing a chemical plant berth accrues 10500, not 3500.
    --
    -- CAVEAT: the 'everything else' tier absorbs 106 departures whose source
    -- Type was blank. Unknown is being charged as non-bulk, which is a
    -- decision rather than a fact -- see docs/DATA_QUALITY.md.
    agency_fee        DOUBLE
);

CREATE INDEX IF NOT EXISTS idx_fact_vessel ON fact_zone_event(vessel_key);
CREATE INDEX IF NOT EXISTS idx_fact_agent  ON fact_zone_event(agent_key);
CREATE INDEX IF NOT EXISTS idx_fact_zone   ON fact_zone_event(zone_key);
CREATE INDEX IF NOT EXISTS idx_fact_time   ON fact_zone_event(event_time);

CREATE INDEX IF NOT EXISTS idx_alias_vessel ON dim_vessel_name_alias(vessel_key);
CREATE INDEX IF NOT EXISTS idx_alias_norm   ON dim_vessel_name_alias(name_normalized);
