-- MRTIS port call assembly layer.
--
-- Built by scripts/build_port_calls.py, which runs AFTER scripts/build_db.py
-- (core warehouse) and scripts/build_fgis_match.py (grain cross-reference).
-- Kept in its own file for the same reason the FGIS layer is: build_db.py
-- owns and drops only the core tables.
--
-- THE DESIGN RULE FOR THIS WHOLE LAYER (William, 2026-08-19): the raw MRTIS
-- event is the spine. Source values are never overwritten -- every canonical
-- or derived value lands in its own column alongside the original, so any row
-- can be traced back to exactly what the Zone Report said. Anything that
-- cannot be derived from evidence is left NULL with a reason code; nothing is
-- guessed.
--
-- Three tables, three grains:
--   port_call_event  -- ONE ROW PER fact_zone_event. The deliverable table.
--   port_call        -- one row per assembled call (Enter SWP .. Exit SWP).
--   port_call_leg    -- one row per leg of a call (a split call has 2+).

-- ---------------------------------------------------------------------------
-- port_call -- the header. One row per call: the vessel entered Southwest
-- Pass, worked however many anchorages and berths, and exited.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS port_call (
    port_call_id      VARCHAR PRIMARY KEY,  -- '<imo|NONAME:name>-<YYYYMMDDHHMM of first event>'
    vessel_key        BIGINT REFERENCES dim_vessel(vessel_key),
    imo               VARCHAR,
    vessel_name       VARCHAR,   -- dim_vessel's current name
    call_name         VARCHAR,   -- the name actually carried on this call's events
    vessel_type       VARCHAR,   -- canonical vessel type
    ship_type_group   VARCHAR,   -- ships register
    dwt               DOUBLE,
    tpc               DOUBLE,

    call_start        TIMESTAMP, -- first event of the call (the SWP entry when closed)
    call_end          TIMESTAMP, -- last event (the SWP exit when closed)
    call_hours        DOUBLE,

    -- 'complete'      : opens on Enter SWP and closes on Exit SWP -- a whole voyage.
    -- 'open_start'    : no Enter seen (data starts mid-call, or the entry was
    --                   never recorded). Times and waiting figures are partial.
    -- 'open_end'      : no Exit seen -- either still in the river at the end of
    --                   the export window, or the exit was never recorded.
    -- 'fragment'      : neither end seen.
    -- A call that is not 'complete' is NOT a defect to be hidden: it is a real
    -- gap in the source feed, kept and flagged so it can be excluded from
    -- duration analytics explicitly rather than silently distorting them.
    call_status       VARCHAR,
    is_complete       BOOLEAN,

    leg_count         INTEGER,   -- 1 = single call, 2+ = split call
    is_split          BOOLEAN,
    berth_stop_count  INTEGER,
    anchorage_stop_count INTEGER,
    event_count       INTEGER,

    entry_draft_ft    INTEGER,   -- draft crossing in  (NULL if no Enter event)
    exit_draft_ft     INTEGER,   -- draft crossing out (NULL if no Exit event)

    agency            VARCHAR,   -- canonical agency for the call (the inbound agency)
    -- Agency fee under William's 2026-08-19 ruling: "agency fee is per port
    -- call, not per berth except when split discharge then load" -- i.e. one fee
    -- per LEG that reached a berth. This is the billable figure.
    agency_fee_total  DOUBLE,
    -- The pre-ruling basis: the sum of fact_zone_event.agency_fee, which charges
    -- every berth departure. Kept alongside so the two can be compared directly
    -- rather than one silently replacing the other.
    agency_fee_departures_total DOUBLE,
    fgis_record_count INTEGER,   -- FGIS grain certificates tied to this call
    fgis_metric_tons  DOUBLE     -- their tonnage
);

-- ---------------------------------------------------------------------------
-- port_call_leg -- the operational half of a call.
--
-- A "split call" is one vessel doing two genuinely different jobs on one river
-- visit: arrive laden, discharge, then move up and load a fresh cargo, then
-- sail. Confirmed on the ground by the Ultra Leopard SOFs (discharged iron ore
-- at Nucor Convent, idled at AMA/LaPlace anchorages for eleven days, then
-- loaded soybeans at ADM Reserve -- different cargo, different berth, even a
-- different master, one continuous river presence).
--
-- A leg is a run of consecutive berth stops sharing the same activity, plus
-- the anchorage and pilot-station events that lead up to them. A new leg
-- starts only when the activity changes (Discharge -> Load or Load ->
-- Discharge). Two Load berths in a row (topping off at a second elevator) are
-- ONE leg, not two -- it is the same cargo job.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS port_call_leg (
    leg_id            VARCHAR PRIMARY KEY,  -- '<port_call_id>-L<n>'
    port_call_id      VARCHAR REFERENCES port_call(port_call_id),
    leg_seq           INTEGER,   -- 1-based, contiguous within the call
    leg_count         INTEGER,   -- legs in the parent call (denormalized for convenience)
    vessel_key        BIGINT REFERENCES dim_vessel(vessel_key),

    leg_start         TIMESTAMP,
    leg_end           TIMESTAMP,
    leg_hours         DOUBLE,

    -- 'Load' | 'Discharge' | 'No Cargo' | NULL (unresolved -- never guessed)
    activity          VARCHAR,
    -- How `activity` was decided, in the order the build tries them:
    --   'draft_delta' -- the vessel sailed deeper than it arrived (Load) or
    --                    lighter (Discharge). Physics; the strongest evidence
    --                    and always preferred.
    --   'fgis'        -- draft was flat or missing, but a USDA grain
    --                    certificate was issued against this sailing, so the
    --                    vessel loaded grain.
    --   'dictionary'  -- draft and FGIS both silent, but dictionaries/
    --                    zone_facility.csv says this berth can only ever do
    --                    one thing (an Elevator only loads; a Layberth never
    --                    works cargo at all).
    --   'unresolved'  -- none of the above could speak. activity stays NULL.
    activity_method   VARCHAR,
    -- TRUE when draft_delta and FGIS disagree (e.g. draft says the vessel
    -- lightened but a grain certificate was issued on the sailing). Kept and
    -- surfaced rather than silently resolved.
    activity_conflict BOOLEAN,
    -- Why the evidence disagreed: 'fgis' (a grain certificate was issued but the
    -- vessel sailed lighter) or 'dictionary' (the draft shows a cargo movement
    -- in a direction dictionaries/zone_facility.csv says this berth never
    -- works). The draft wins in both cases; the flag is how the dictionary gets
    -- corrected rather than quietly overruled.
    activity_conflict_reason VARCHAR,
    draft_delta_ft    INTEGER,   -- sailing draft - arrival draft at the leg's berth work

    berth_stop_count  INTEGER,
    first_berth_zone  VARCHAR,   -- raw zone of the leg's first berth stop
    first_berth_facility VARCHAR,-- its canonical facility name
    facility_type     VARCHAR,   -- its facility type
    berth_arrive_time TIMESTAMP, -- arrival at the leg's first berth
    berth_depart_time TIMESTAMP, -- sailing from the leg's last berth

    -- Time analytics. Waiting time is ONLY the anchorage dwell that happens
    -- BEFORE the leg's first berth arrival -- that is time spent waiting for
    -- that berth. Anchorage dwell after the last sailing is outbound idling
    -- (the vessel is on its way out, not waiting on a dock) and is reported
    -- separately so it can never be mistaken for berth waiting time.
    -- Anchorage dwell is attributed by OVERLAP with these windows, not by which
    -- side of the berth arrival it started on: the pilot sheets routinely leave
    -- an anchorage open for hours or days after the vessel is already alongside,
    -- and counting that whole dwell as waiting would double-count cargo time.
    waiting_hours          DOUBLE,
    -- Dwell between the leg's berth arrival and its last sailing -- either a
    -- genuine shift to anchorage between two berths of the same leg, or an
    -- anchorage the source left open while the vessel was already working.
    inter_berth_idle_hours DOUBLE,
    outbound_idle_hours    DOUBLE,
    berth_hours            DOUBLE,

    agency            VARCHAR,   -- canonical agency for this leg
    -- Where that agency came from:
    --   'inbound'  -- observed on this leg's own inbound events (the rule:
    --                 the agent who brought the vessel in owns the leg)
    --   'leg'      -- inbound events were blank; taken from elsewhere in the leg
    --   'call'     -- the whole leg was blank; taken from the parent call
    --   'none'     -- no agent recorded anywhere on the call
    agency_source     VARCHAR,
    agent_changed_in_leg BOOLEAN, -- source data showed >1 distinct agency inside the leg

    -- Cargo, where an external source can actually say. FGIS (USDA grain
    -- inspection certificates) is the only one wired in so far; everything
    -- else waits on source data William has not supplied yet.
    cargo_group       VARCHAR,   -- from FGIS, else the zone dictionary's Cargo group
    cargo             VARCHAR,   -- FGIS grain/grain class
    cargo_source      VARCHAR,   -- 'fgis' | 'dictionary' | NULL
    destination       VARCHAR,   -- FGIS declared destination (export legs)
    actual_tons       DOUBLE,    -- FGIS certified metric tons
    fgis_record_count INTEGER,
    -- ONE fee per leg that reached a berth (William's ruling, 2026-08-19),
    -- priced by the call's vessel type on the same tiers build_db.py uses.
    -- NULL when the leg never berthed -- no cargo work, no fee -- or when the
    -- vessel has no usable identity or type from either source (a tug or
    -- government craft, not an ocean vessel being agented).
    agency_fee        DOUBLE,
    -- What the same leg would accrue charging every berth departure, the
    -- pre-ruling basis. Kept for comparison, not for billing.
    agency_fee_departures DOUBLE
);

-- ---------------------------------------------------------------------------
-- port_call_event -- THE DELIVERABLE. One row per fact_zone_event, always.
--
-- Every event in the warehouse appears here exactly once, including events the
-- assembly could not place in a call (port_call_id NULL, unassigned_reason
-- set). The build hard-fails if this table's event_key set is not identical to
-- fact_zone_event's -- the spine must never lose or duplicate a row.
--
-- Columns come in three bands:
--   src_*  -- the source value, exactly as the Zone Report gave it
--   (bare) -- the canonical/transformed value
--   call/leg attributes carried down from the assembly
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS port_call_event (
    event_key         BIGINT PRIMARY KEY REFERENCES fact_zone_event(event_key),
    port_call_id      VARCHAR,   -- NULL when the event could not be placed
    leg_id            VARCHAR,
    leg_seq           INTEGER,
    event_seq         INTEGER,   -- 1-based position within the call, time order
    -- Why an event has no call: 'before_first_entry' (the export window opens
    -- mid-voyage), 'no_open_call' (an Enter was never recorded for this run of
    -- events). Kept, never dropped.
    unassigned_reason VARCHAR,

    -- identity ------------------------------------------------------------
    vessel_key        BIGINT REFERENCES dim_vessel(vessel_key),
    src_imo           VARCHAR,   -- IMO exactly as exported
    imo               VARCHAR,   -- canonical 7 digits
    vessel_name       VARCHAR,   -- name carried on this event
    src_vessel_type   VARCHAR,   -- Type as exported (often blank)
    vessel_type       VARCHAR,   -- canonical, back-filled from the vessel
    ship_type_group   VARCHAR,
    dwt               DOUBLE,
    tpc               DOUBLE,

    -- the event -----------------------------------------------------------
    src_action        VARCHAR,   -- Arrive / Depart / Enter / Exit
    -- Canonical action, per William's labelling: at a berth Arrive/Depart
    -- become Arrived/Sailed; at an anchorage they become Anchor/Weigh Anchor;
    -- at the pilot station the crossing keeps Enter/Exit and it is the ZONE
    -- that gains the direction (SWP Cross In / SWP Cross Out).
    action            VARCHAR,
    event_time        TIMESTAMP, -- unchanged, military time
    src_zone          VARCHAR,   -- Zone as exported
    berth             VARCHAR,   -- the zone, relabelled (SWP Cross -> In/Out)
    facility          VARCHAR,   -- canonical facility name (Shell Norco 1|2 -> Shell Norco)
    facility_type     VARCHAR,   -- Elevator / Tank Storage / Anchorage / ...
    src_mile          DOUBLE,    -- Mile as parsed off this event
    mile              DOUBLE,    -- canonical mile for the zone (SWP In -20 / Out -19)
    draft_ft          INTEGER,   -- 'Draft FT' -- the number only

    -- agent ---------------------------------------------------------------
    src_agent         VARCHAR,   -- Agent as exported; blank stays blank here
    agency            VARCHAR,   -- canonical agency of the raw agent, per dictionary
    -- The agency that owns this event operationally: the leg's inbound agency,
    -- applied to every event of the leg. This is the column the analytics use.
    -- It fills the source blanks and it undoes the pilot-sheet artefact where
    -- an outbound agent appears on the sailing of a call another agent worked.
    agency_leg        VARCHAR,
    agency_normalized BOOLEAN,   -- TRUE where agency_leg differs from the row's own agency

    -- assembly ------------------------------------------------------------
    berth_stop_seq    INTEGER,   -- which berth stop of the call this event belongs to
    is_berth_stop     BOOLEAN,
    is_anchorage      BOOLEAN,
    -- TRUE on anchorage events that sit before the leg's first berth arrival,
    -- i.e. the dwell that counts as waiting for that berth.
    is_waiting_time   BOOLEAN,
    dwell_hours       DOUBLE,    -- on an Arrive/Anchor row: hours until the matching Depart
    hours_since_prev  DOUBLE,    -- gap from the previous event of the call

    -- carried down from the leg -------------------------------------------
    activity          VARCHAR,
    activity_method   VARCHAR,
    cargo_group       VARCHAR,
    cargo             VARCHAR,
    destination       VARCHAR,
    actual_tons       DOUBLE,    -- leg total; do NOT sum across the leg's rows
    call_status       VARCHAR,
    is_split_call     BOOLEAN,
    -- The per-departure fee copied straight from fact_zone_event, unchanged and
    -- reconciled by a hard guardrail. This is NOT the billable figure -- under
    -- William's ruling the fee is charged once per leg, and that number lives on
    -- port_call_leg.agency_fee. Summing this column over-bills by ~12%.
    agency_fee        DOUBLE
);

CREATE INDEX IF NOT EXISTS idx_pce_call    ON port_call_event(port_call_id);
CREATE INDEX IF NOT EXISTS idx_pce_leg     ON port_call_event(leg_id);
CREATE INDEX IF NOT EXISTS idx_pce_vessel  ON port_call_event(vessel_key);
CREATE INDEX IF NOT EXISTS idx_pce_time    ON port_call_event(event_time);
CREATE INDEX IF NOT EXISTS idx_leg_call    ON port_call_leg(port_call_id);
CREATE INDEX IF NOT EXISTS idx_call_vessel ON port_call(vessel_key);
