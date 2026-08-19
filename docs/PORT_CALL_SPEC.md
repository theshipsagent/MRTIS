# Port call assembly — the rules

How `scripts/build_port_calls.py` turns the raw zone-event feed into assembled
port calls. Written from `logic.md` and William's rulings; every rule here is
either something he decided or something measured in the data, and the
provenance is stated in each case.

Built by `python3 scripts/build_port_calls.py`, after `build_db.py` and
`build_fgis_match.py`. Health of the most recent run:
[docs/PORT_CALL_QUALITY.md](PORT_CALL_QUALITY.md).

---

## 0. The spine rule

**The raw MRTIS event is the spine.** (William, 2026-08-19.)

`port_call_event` has exactly one row per `fact_zone_event` row — always, on
every build, including events the assembly could not place in a call. Source
values are kept in their own `src_*` columns and are never overwritten; every
canonical or derived value lands in a column beside them. So any row can be
read two ways at once: what the Zone Report said, and what we made of it.

Anything that cannot be derived from evidence is left NULL with a reason code.
Nothing is guessed, and nothing is dropped.

---

## 1. What a port call is

> "a vessel must enter swp and exit swp to become a total voyage even if we
> paper split the two operations in the middle" — `logic.md`

A port call runs from an `Enter` at the pilot station (SWP Cross) to the
matching `Exit`. Everything in between — anchorages, berths, shifts — belongs
to that call.

Reality is not always that tidy, and the three ways it breaks are handled
explicitly rather than swept up:

| Situation | What the build does |
|---|---|
| Events before the vessel's first `Enter` | Left unassigned, `unassigned_reason = 'before_first_entry'` |
| A second `Enter` while a call is still open | Closes the open call as `open_end`, starts a new one |
| An `Exit` with no open call | Those events unassigned, `no_open_call` |
| A call that never `Exit`s | Kept, `call_status = 'open_end'` |

`call_status` is `complete` only when both ends are present. **98.8% of calls
are complete**; the remaining 1.2% are real gaps in the source feed, kept and
flagged so they can be excluded from duration analytics deliberately instead of
quietly distorting them.

---

## 2. Berth stops — the visit, not the geofence hit

A **berth stop** is one visit to one facility. Two rulings from William
(2026-08-19) define it, and both exist because the source is geofence/AIS
derived rather than a berth log.

### The canonical facility is the unit, not the zone

> "focus on the facility canonical as will avoid this confusion"

A vessel that sails one berth of an elevator and ties up at the next berth of
the same elevator has **shifted, not called twice**. The `facility` column in
`dictionaries/zone_facility.csv` is what says two berths are one facility —
Zen-Noh Upper and Zen-Noh Lower are both Zen-Noh. `docs/PORT_CALL_EVIDENCE.md`
says the same from four real SOFs: *"Shifting within a berth is not a second
berth call."*

### Only the first docking and the last sailing count

> "there are instances where once a vessel docks, it gives some false hits
> because of moving in berth or overlaying geofences… only the first docking and
> last sailing applies, and rest ignored, as a physics, a large ocean vessel did
> not dock and sail and redock in only a few minutes"

Measured in this data, exactly as described:

| Pattern (consecutive berth events, same facility) | Count |
|---|---|
| Within 10 minutes of the previous | 2,485 |
| Dock → sail → **redock** inside 2 hours | 1,070 |
| Two arrivals in a row | 722 |
| Two sailings in a row inside 2 hours | 53 |

Carnival Valor is the clean example: it "sails" Julia St at 20:13 and Erato St
at 20:17 — four minutes and two terminals apart, on overlapping geofences.

So a visit is a run of consecutive berth events at the same facility, or within
`--berth-bounce-hours` (default **2**) of the previous one. The **first arrival**
is the docking and the **last departure** is the sailing; everything between is a
collection artefact. Artefacts are **kept on the spine** and flagged
`is_geofence_artifact` — the spine never drops a source row — they are simply not
read as operations, and the visit's draft change is measured across them.
**5,102 berth events (5.3%) collapse this way.**

An anchorage or pilot-station event between two berth events always ends the
visit: a vessel that goes to anchor and comes back has genuinely called twice.

A visit with only one half — a sailing with no recorded arrival, or the reverse —
is still a visit. That is a real gap in the feed, and discarding it would
silently delete a cargo operation.

---

## 3. What the vessel did — `activity`

Evidence order, strongest first.

1. **The dictionary, where the facility can only do one thing.**

   > "as per dictionary, a vessel at a grain elevator can only load and only load
   > cargo group grain" — William, 2026-08-19

   `ops = Load` → Load. `ops = Discharge` → Discharge. `ops = Layberth` (or
   `Rule = "No cargo ever takes place"`) → `No Cargo`. **This outranks the
   draft**, because the draft on these records is AIS-derived and noisy while the
   physical capability of a berth is not: 531 legs previously read as discharges
   at Load-only facilities were AIS variance, not twenty thousand tonnes of grain
   coming back off a ship at an export elevator.
2. **FGIS.** A grain certificate issued against the visit → `Load`, cargo grain.
3. **The draft delta — a pass, not a decider.** Only for facilities the
   dictionary marks `Load/Discharge` (genuinely either) or leaves blank. Where
   the facility rule locks the answer the draft is not consulted at all: Elevator,
   Bulk Cargo and LNG legs are now 100% dictionary-resolved with zero draft
   involvement. Where it does apply: sailed deeper
   than it arrived → `Load`, lighter → `Discharge`. Measured **first docking to
   last sailing** across the whole visit, so a shift between berths of one
   facility cannot manufacture an operation out of a one-foot deballast.
4. **Nothing.** `activity` stays NULL, `activity_method = 'unresolved'`.

`activity_method` records which rung answered, on every leg.

Current split: dictionary 35.7%, draft 46.8%, FGIS 0.8%, unresolved 13.4%, and
3.3% of legs never reached a berth at all. **83.3% resolved.**

How well the draft performs depends entirely on the ship, exactly as William
predicted: the ambiguous facilities are the buoys, which serve larger vessels
where the delta is pronounced, while the small general-cargo berths barely move.

| Facility type | Legs decided by draft | Mean abs. delta |
|---|---|---|
| Mid-Stream (buoys) | 4,214 | **12.3 ft** |
| Refinery | 3,965 | 10.1 ft |
| Chemical Plant | 1,953 | 6.6 ft |
| Tank Storage | 5,533 | 6.5 ft |
| General Cargo | 4,329 | **3.8 ft** |
| Elevator / Bulk Cargo / LNG | 0 | — (dictionary-locked) |

`--min-draft-delta` (default 1 ft) sets how much draft change counts as cargo
work on the berths where the draft still decides. It matters far less than it
did before the facility and geofence rules landed — the case it used to break on
(Federal Icon, a one-foot deballast across a Zen-Noh shift reading as a
discharge) is now handled structurally.

### When evidence disagrees

Flagged, never silently settled — `activity_conflict` and
`activity_conflict_reason`:

- `draft` — the dictionary decided and the draft says otherwise. Usually AIS
  variance; occasionally a dictionary row that needs widening.
- `fgis` — a grain certificate was issued but the vessel sailed lighter.

853 legs (2.0%). The quality report lists the facilities where it happens most,
largest first, so the dictionary can be corrected from evidence.

---

## 4. Legs, and the split call

> "the ship entered swp for one agent and was deep draft, it anchored however
> many times, docked, sailed — this is first part of the port call, then the
> agent changes, and for second half of port call…" — `logic.md`

A **leg** is a run of consecutive berth stops sharing the same activity, plus
the anchorage and pilot-station events leading up to them. A new leg starts
**only when the activity changes** — Discharge → Load, or Load → Discharge.

- Two Load berths back to back (topping off at a second elevator) are **one
  leg**. Same cargo job.
- A stop whose activity could not be resolved **joins the leg in progress**. An
  unknown is never allowed to invent a split.

Confirmed against the ground truth: MV *Ultra Leopard* (IMO 9758428, May 2026)
assembles as leg 1 discharge of iron ore at Nucor Steel (48 ft → 23 ft), leg 2
load of soybeans for China at ADM Reserve (25 ft → 45 ft), with eleven days at
AMA and LaPlace anchorages in between — exactly what the Statements of Fact
show (`docs/PORT_CALL_EVIDENCE.md`).

Only vessel types that genuinely work this way are eligible (William, 2026-08-19:
*"reduced as the tankers, gas, other, cruise, container and reefer can be
ignored"*). A split is a dry-cargo pattern — arrive laden, discharge, move up,
load a fresh cargo, sail. `SPLIT_ELIGIBLE_TYPES` is Bulk plus vessels with no
recorded type (an unrecorded type must not be read as an excluded one);
`--split-all-types` reverses it. This removed 667 splits, almost all tankers.

**4.1% of calls are split calls** (down from 4.4% once layberth stops stopped
manufacturing splits — OPEN_QUESTIONS §8a).

### `No Cargo` (layberth) never splits, and never bills on its own

William's ruling, 2026-08-19 (OPEN_QUESTIONS §8): `No Cargo` (a stop at one of
the 14 zones `dictionaries/zone_facility.csv` marks `ops = Layberth`, "no cargo
ever takes place") is not a cargo job, so it cannot be the discharge-then-load
boundary the split rule is built on. It is treated exactly like an unresolved
stop for splitting purposes — it joins the leg in progress and never becomes
the leg's `cur_activity`, so a real Discharge → No Cargo → Load sequence still
splits on the Discharge/Load boundary as if the layberth stop were not there.

It follows the same rule on the fee: **no fee accrues on departing a layberth.**
A leg bills only if it did real (non-layberth) work somewhere. A leg of nothing
but layberth stops — a pure lay-up or repair call — accrues nothing, exactly
like a call that never berthed at all. A leg that mixes a layberth stop with a
genuine other berth (e.g. bunkers at a refinery, activity unresolved) still
bills as it always did; only the layberth stop itself is fee-exempt.

### Which leg an event belongs to

An event belongs to the leg of the **next berth stop it is heading for**:
anchorage dwell before a berth is time spent waiting for *that* berth. Events
after the final sailing belong to the final leg (outbound).

This is what makes `logic.md`'s carry-through work: the leg's activity, cargo,
cargo group and destination are stamped onto every event of the leg, including
its anchorage and SWP crossing rows. On a split call the inbound leg's values
stop at the discharge and the outbound leg's values start after it.

---

## 5. Agency

Two transformations, both non-destructive:

1. `agency` — the raw agent run through `dictionaries/agent_agency.csv`
   (spelling roll-ups, agencies since sold or renamed).
2. `agency_leg` — **the agency that brought the vessel in owns the leg**, applied
   to every event of that leg.

`agency_leg` is the column the analytics should use. It does two jobs at once:

- **It fills the blanks.** 2.4% of source events carry no agent at all; once the
  call is assembled the answer is obvious from the rest of the leg.
- **It undoes the pilot-sheet artefact.** These records come from pilot sheet
  logs, so when a different agent takes the ship out for its next voyage that
  agent appears on the sailing of a call the first agency actually worked.
  Reverting the sailing to the inbound agency is exactly `logic.md`'s rule — and
  because it is applied *per leg*, a genuine split call still keeps two
  genuinely different agencies, one per leg.

`agency_source` records where the leg's agency came from (`inbound` / `leg` /
`call` / `none`), and `agency_normalized` marks every event whose operating
agency differs from the agent written on the row.

---

## 6. Time

> "waiting can only be waiting for the berth, as the anchorage stop happens
> after it departs" — `logic.md`

- `waiting_hours` — anchorage dwell **before** the leg's first berth arrival.
  This is the only figure that is berth waiting time.
- `inter_berth_idle_hours` — dwell between the leg's berth arrival and its last
  sailing.
- `outbound_idle_hours` — dwell after the last sailing. The vessel is on its way
  out, not waiting on a dock. Reported separately so it can never be counted as
  waiting.
- `berth_hours` — hours alongside.

Dwell is attributed by **overlap** with those windows, not by which side of the
berth arrival an anchorage happened to start on. The pilot sheets routinely
leave an anchorage open for hours or days after the vessel is already alongside
(90 legs here); counting that whole dwell as waiting would double-count cargo
time. MV *Amanda C* (July 2026) is the worked example: 332 hours of raw
anchorage dwell, of which **117.6 are genuinely waiting for the berth**.

---

## 7. Cargo

Only where a source can actually say:

| Column | Source |
|---|---|
| `cargo`, `destination`, `estimated_tons` | FGIS grain certificates, aggregated per leg |
| `cargo_group` | FGIS (`Grain`), else the zone dictionary's `Cargo group` |
| `dwt`, `tpc`, `ship_type`, `ship_type_group` | the ships register, via canonical IMO |

`Shipper`, `Consignee`, `Receiver`, `Last Port`, `Next Port` and `Origin` from
`logic.md` are **deliberately absent**. No source for them exists yet, and an
empty column that looks like a real one is worse than no column. They get
added when their data arrives.

Tonnage is aggregated per leg from `fgis_record`, never read from the scalar
`fgis_record_id` — one sailing routinely carries several certificates.
`estimated_tons` on an event row is the **leg total**; do not sum it across
the leg's rows.

**`estimated_tons` vs `actual_tons`** (William, 2026-08-19): William's original
mapping (`docs/FGIS_MATCH_SPEC.md`) is explicit that summed FGIS metric tons is
an *estimate*, not a certified actual weight — `logic.md`'s `'Est Tons'` field.
`estimated_tons` carries that FGIS figure. `actual_tons` is reserved for a
genuinely certified/actual weight and is NULL everywhere for now; no source for
it is wired in. Promoting a leg from estimated to actual is future work, not
something this build infers.

**`ship_type` vs `ship_type_group`** (William, 2026-08-19): `ship_type` is the
ships register's raw type/family (e.g. `Cement Carrier`); `ship_type_group` is
the size-bucketed group within a family (e.g. `Bulk Carrier-Handymax`). Some
families in the register carry no size vocabulary at all, so `ship_type_group`
would otherwise be NULL even though the vessel's type is known. There,
`ship_type_group` is backfilled from `ship_type` rather than left empty — a
gap is worse than a variance in convention. `ship_type` always holds the
register's original value regardless of whether the backfill fired.

---

## 8. Guardrails

A guardrail is a rule the build asserts about its own output, on every run, over
the whole data set — not a spot check. A pipeline that silently produces
slightly-wrong numbers is worse than one that crashes: the crash gets fixed, the
wrong number gets used in a decision.

There are two kinds, and the difference matters:

**HARD — invariants.** These cannot be false unless the code is wrong. A hard
failure **aborts the build before anything is written**; the database is left
exactly as it was. You never get a half-correct table. Examples: every source
event appears on the spine exactly once; every leg belongs to a call that
exists; the agency fee total is unchanged from `fact_zone_event`; every matched
FGIS certificate lands on exactly one leg; no activity is asserted without a
named method.

**SOFT — health signals.** These depend on the source data, not the code: how
many events fell outside a call, how many berth stops had no usable draft, how
many legs had no agent recorded anywhere. They never block a build. They are the
numbers to watch move between runs — a sudden jump means the incoming exports
changed shape.

This is not theory: the FGIS reconciliation guardrail failed on the first run of
this build and caught a real bug (13 berth stops carry grain certificates on
*both* the arrival and the sailing; the code was counting one and dropping the
other, losing 128 certificates and 4.4 Mt). Nothing was written until it was
fixed. The full current list, with results, is in
[docs/PORT_CALL_QUALITY.md](PORT_CALL_QUALITY.md).

Two more structural ones worth naming:

- **Schema alignment.** Frames are matched to the table by column *name* before
  insert, and a mismatch is an error. Positional inserts are fast but silently
  shift every value one column left when the DDL and the builder drift apart.
- **Transactional write.** All three tables are dropped, recreated and filled
  inside one transaction, after every hard guardrail has already passed.

---

## 9. Known gaps, for William

1. **Dictionary `ops` blanks** now drive most of the 7,139 unresolved legs.
   Filling `ops` in `dictionaries/zone_facility.csv` converts them directly, and
   since the dictionary now outranks the draft, each fill is decisive rather than
   advisory. The cruise berths (Erato St, Julia St) are the clearest — a cruise
   ship works no cargo, and they currently resolve to nothing.
2. **853 legs where the draft contradicts the dictionary.** The dictionary wins,
   so these are AIS variance or a dictionary row that needs widening. Listed by
   facility in the quality report.
3. ~~Does a `No Cargo` leg accrue an agency fee?~~ **Resolved, William,
   2026-08-19 (OPEN_QUESTIONS §8):** no. See §4.
4. **`canonical_mile` is empty** for all 220 dictionary rows, so the build falls
   back to `most_common_mile_in_data`. Stable, but the canonical column is the
   one that should hold the answer.
5. **17,100 events (5.9%) sit outside any call**, nearly all because the export
   window opens mid-voyage. An earlier Zone Report export would absorb most.
