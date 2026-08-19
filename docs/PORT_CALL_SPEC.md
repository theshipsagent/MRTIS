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

## 2. Berth stops

A **berth stop** is an `Arrive` … `Depart` pair at a zone whose `facility_type`
is anything other than Anchorage or Pilot Station. That is the same definition
`build_db.py` already uses to decide whether an agency fee accrues, deliberately
— one definition of "worked a berth" across the warehouse.

A stop with only one half (a sailing with no recorded arrival, or the reverse)
is still a stop. It is a real gap in the feed, and discarding it would silently
delete a cargo operation.

---

## 3. What the vessel did — `activity`

Evidence order, strongest first. The first one that can speak, wins:

1. **Draft delta.** Sailed deeper than it arrived → `Load`. Lighter →
   `Discharge`. Physics, and it needs no dictionary. This is `logic.md`'s
   "law of physics" rule.
2. **FGIS.** Draft flat or missing, but a USDA grain certificate was issued
   against this sailing → `Load`, and the cargo is grain.
3. **The zone dictionary.** Draft and FGIS both silent, but
   `dictionaries/zone_facility.csv` says this berth can only ever do one thing:
   `ops = Load` → Load, `ops = Discharge` → Discharge, `ops = Layberth` (or
   `Rule = "No cargo ever takes place"`) → `No Cargo`.
4. **Nothing.** `activity` stays NULL and `activity_method = 'unresolved'`.

`activity_method` records which rung was used, on every leg, so a number can
always be traced to its evidence.

### The draft threshold

`--min-draft-delta` (default **1**) is how many feet of change count as a cargo
operation. At the default the source is trusted exactly as recorded. It is a
real judgement call and it is left exposed rather than baked in:

| Threshold | Legs | Activity resolved | Split calls |
|---|---|---|---|
| 1 ft (default) | 43,238 | 84.3% | 2,874 (7.2%) |
| 2 ft | 42,813 | 79.8% | 2,535 (6.3%) |
| 3 ft | 42,570 | 76.1% | 2,336 (5.8%) |

At 1 ft the evidence is genuinely thin: among berths the dictionary marks
one-directional, a ±1 ft delta agrees with the dictionary 35 times and
contradicts it 29 — a coin toss. Raising the threshold costs resolved activity
and removes ~340 split calls. **Open for William to rule on.**

### When evidence disagrees

Flagged, never silently settled — `activity_conflict` and
`activity_conflict_reason`:

- `fgis` — a grain certificate was issued but the vessel sailed lighter.
- `dictionary` — the draft shows a cargo movement in a direction the dictionary
  says this berth never works.

**The draft wins in both cases.** 531 legs sail a median 14 ft lighter from
berths the dictionary marks Load-only (mostly ADM Destrehan Buoys) — that is a
20,000-tonne discharge, not a rounding artefact. The dictionary is incomplete
there, and the flag is how it gets found and corrected rather than overruled in
silence.

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

**7.2% of calls are split calls.**

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
| `cargo`, `destination`, `actual_tons` | FGIS grain certificates, aggregated per leg |
| `cargo_group` | FGIS (`Grain`), else the zone dictionary's `Cargo group` |
| `dwt`, `tpc`, `ship_type_group` | the ships register, via canonical IMO |

`Shipper`, `Consignee`, `Receiver`, `Last Port`, `Next Port`, `Origin`,
`Est Tons` and `Vessel Type Group` from `logic.md` are **deliberately absent**.
No source for them exists yet, and an empty column that looks like a real one is
worse than no column. They get added when their data arrives.

Tonnage is aggregated per leg from `fgis_record`, never read from the scalar
`fgis_record_id` — one sailing routinely carries several certificates.
`actual_tons` on an event row is the **leg total**; do not sum it across the
leg's rows.

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

1. **The draft threshold** (§3) — a ruling is needed on whether ±1 ft counts as
   cargo work.
2. **Dictionary `ops` blanks** drive most of the 6,792 unresolved legs: General
   Cargo (1,532), Cruise (1,038), Mid-Stream (552). Filling `ops` for those rows
   in `dictionaries/zone_facility.csv` converts them directly.
3. **`ops` contradictions** (§3) — ADM Destrehan Buoys and a handful of others
   are marked Load-only but demonstrably discharge.
4. **`canonical_mile` is empty** in the zone dictionary for all 220 rows, so the
   build falls back to `most_common_mile_in_data`. That is fine and stable, but
   the canonical column is the one that should hold the answer.
5. **17,100 events (5.9%) sit outside any call**, nearly all because the export
   window opens mid-voyage. An earlier Zone Report export would absorb most of
   them.
