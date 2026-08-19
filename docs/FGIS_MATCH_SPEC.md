# FGIS matching, rollup, and cross-reference — build spec

Status: **BUILT and validated, 2026-08-18** — `scripts/build_fgis_match.py`.
See `docs/FGIS_MATCH_QUALITY.md` for the current match report. The rest of
this doc is the original spec, preserved for its reasoning, with the
as-built corrections marked inline. Read "Corrections found during the
build" first — three things in the original spec were wrong, one of them
fatally.

**Result**: 18,315 certificate lines consolidated into 14,528 records;
12,442 of 12,537 in-coverage records matched (**99.2%**) — 98.7% anchored on
the sailing, 1.3% on the arrival fallback — 1 ambiguous, 0 guesses.

## Corrections found during the build

**1. `Exit` is the wrong action — it must be `Depart`.** Step 1.2 below
says to narrow to vessels with an `Exit` event from an Elevator/Mid-Stream
zone. In this data `Enter`/`Exit` occur *exclusively* at Pilot Station
zones (the SWP river entry/exit); every berth-type zone, Elevator and
Mid-Stream included, records `Arrive`/`Depart`. Verified across all
314,089 events:

| facility_type | Arrive | Depart | Enter | Exit |
|---|---|---|---|---|
| Elevator | 11,051 | 10,941 | 0 | 0 |
| Mid-Stream | 12,598 | 12,673 | 0 | 0 |
| Pilot Station | 0 | 0 | 40,646 | 40,665 |

Implemented literally, the spec would have matched **zero rows**. This also
agrees with William's own note that Cert Date is "date of sailing from a
grain elevator".

**2. The normalization rule as written fails its own flagship example.**
Step 1.1 says "strip periods... collapse whitespace", i.e. replace
punctuation with a space. That turns `"D.S.L. Phoenix"` into
`"D S L PHOENIX"`, which does *not* equal `"DSL PHOENIX"` — the exact case
the rule exists to handle. The fix is to strip punctuation **entirely**
rather than substitute a space, which still keeps DSI and DSL apart:

```
'D.S.L. Phoenix' -> 'DSLPHOENIX'    'DSL Phoenix' -> 'DSLPHOENIX'   same vessel
'DSI PHOENIX'    -> 'DSIPHOENIX'    'Dsi Phoenix' -> 'DSIPHOENIX'   stays separate
```

**3. The FGIS-record-to-event link is many-to-one, not one-to-one.** Step 3
assumes one consolidated record per departure. In reality one sailing
routinely carries grain certified across several consecutive days — e.g.
`Dsi Aquila` departing ADM AMA on 2022-03-16 carries soybeans certified
03-14 plus corn certified 03-15 and 03-16. **1,562 of 10,600 matched
departures (14.7%) carry 2-3 FGIS records.** A single scalar
`fact_zone_event.fgis_record_id` cannot represent that, so
`fgis_record_count` sits alongside it and the scalar holds only the primary
(latest Cert Date) record. **To total tonnage for a sailing, aggregate
`fgis_record` on `mrtis_event_key`** — reading the scalar column would
silently understate every multi-certificate loading.

**4. Matching must resolve against name aliases, not `dim_vessel`.** Not in
the original spec at all: 1,217 of 10,270 vessels (11.9%) are renamed during
the covered period (IMO 9397456 is `Hellas Explorer` in 2019 and
`Alithini II` by 2022). `dim_vessel.vessel_name` keeps only the latest name,
so matching against it would systematically fail for older records. The
build adds `dim_vessel_name_alias` (every name spelling ever observed per
vessel) and matches against that.

## What we're starting from

`fgis_output`: 18,315 rows, 2018-01-01 through 2026-08-13, one row per FGIS
certificate line for ocean vessels calling the Mississippi River
(`Type Carrier = 1 AND Port = 'MISSISSIPPI R.'`). No IMO. `carrier_name` is
free text, ALL CAPS as FGIS publishes it (e.g. `"DSI PHOENIX"`). It commonly
has several lines per (vessel, Cert Date) — different Grain/Class/
Destination combos from the same loading.

`dim_vessel.vessel_name` (MRTIS side) is Title Case, sourced from the Zone
Report `Name` field (e.g. `"Dsi Phoenix"`).

## Step 1 — Vessel identity resolution

**Correction from the first pass of this discussion**: the original example
was "DSI Phoenix" as a punctuation variant. William corrected this —
the real example is **"DSL Phoenix" vs. "D.S.L. Phoenix"** (same vessel,
punctuation/spacing variance only). **"DSI Phoenix" is a genuinely
different, real vessel** — it shows up in `fgis_output` in Dec 2025/Jan
2026, and MRTIS's own `dim_vessel` has a real "Dsi Phoenix" too. This
matters a lot for the matching algorithm: DSI and DSL are one character
apart, so **any matching approach that tolerates single-character edits
(fuzzy/Levenshtein-style matching) risks merging two real, different
vessels.** Recommendation: normalize aggressively for whitespace and
punctuation (strip periods, collapse multiple spaces, uppercase) but do
**not** do loose fuzzy/edit-distance matching on top of that. Match on the
punctuation-normalized string, not on "close enough."

Proposed algorithm:

1. Normalize both sides: uppercase, strip periods/commas, collapse
   whitespace, trim. (`"D.S.L.  Phoenix"` and `"DSL Phoenix"` both become
   `"DSL PHOENIX"`.)
2. Narrow the MRTIS candidate pool to vessels with an `Exit` event from a
   zone where `dictionaries/zone_facility.csv.facility_type` is `Elevator`
   or `Mid-Stream` (52 zones: 13 Elevator, 39 Mid-Stream), within
   `cert_date - 1 day` to `cert_date + 1 day` (per William: "+/- 1 day
   should be fine" for calendar/clock rollover). **Do not** further filter
   by the zone dictionary's `Cargo group` tag — real SOFs (Desert Seeker,
   Asian Eternity, both at Mid-Stream "Artco Buoys" locations) show
   Mid-Stream zones carry cargo other than grain even though only 4 of 39
   are tagged `Cargo group = Grain`; the tag reflects typical, not
   exclusive, cargo. The FGIS record itself is the confirmation that grain
   moved through that stop.
3. Within that narrowed, date-windowed candidate set, look for an exact
   match on the normalized name. Exactly one match -> confident link.
   Zero matches -> no link, flag for review (candidate pool may be
   missing a zone/date-window edge case). More than one match -> flag for
   review rather than guessing (should be rare given the narrow pool, but
   don't silently pick one).
4. Cache confirmed matches (raw `carrier_name` -> `vessel_key`) so repeat
   names across weeks don't require re-searching the full candidate pool
   each time — but always re-validate against the date-windowed candidate
   pool rather than trusting the cache blindly, since the same normalized
   name over enough years could plausibly refer to two different real
   vessels (renames, re-flagging) even after punctuation normalization.

**Open question for whoever builds this**: is exact-normalized-match
after narrowing good enough, or will there be real cases (typos beyond
punctuation, e.g. a genuine misspelling that isn't a different vessel)
that need human review anyway? If so, build the "no match" and
"ambiguous match" cases as a reviewable list (like the dictionaries),
not a hard failure.

## Step 2 — Rollup / consolidation

Grain: one consolidated record per `(matched vessel_key, cert_date)`.
Concatenate `grain`, `grain_class`, `destination` across the matching
lines; sum `metric_ton`. Assign a new `fgis_record_id`.

Open question: what's the concat format? (e.g. `"CORN, WHEAT"` /
`"CORN; WHEAT"` / structured list) — cosmetic, pick something and confirm
with William, not a design blocker.

## Step 3 — Bidirectional cross-reference

The Cert Date is matching a specific **Exit event from an Elevator/
Mid-Stream zone** (the vessel's departure from the loading berth) — not a
whole port call. Recommendation: attach the link directly on the matching
`fact_zone_event` row rather than waiting on the still-unbuilt
`port_call_id`/`call_leg_id` voyage-assembly design:

- Add `fgis_record_id` (nullable) to `fact_zone_event`.
- Add `mrtis_event_key` (nullable) to the new consolidated FGIS table.

This keeps the FGIS integration decoupled from the voyage-assembly
backlog. If/when `call_leg_id` exists, the link can be re-pointed there
instead (or kept at both levels) without redesigning the matching logic
above.

## Decided before building — confirmed by William 2026-08-18

1. **Match strictness**: exact-normalized-match only, **no fuzzy/edit-distance
   matching anywhere**. Normalization is uppercase → strip an `M/V`-type
   prefix → remove all non-alphanumerics (`parse.normalize_vessel_name`).
   Anything that doesn't match exactly goes to the review list rather than
   being guessed. The evidence behind this: of the 10 near-miss cases in a
   full-year sample, **9 were deterministic normalization problems, not fuzzy
   ones** — `GNG CONCORD 2`/`GNG CONCORD2`, `KNIDOS-M`/`KNIDOSM`,
   `S-BOND`/`SBOND`, `SEA WAVE`/`SEAWAVE`, `M/V SIDER MADEIRA`/`SIDER MADEIRA`.
   Only one (`OCEAN HELOS`/`OCEAN HELIOS`) was a true typo. Aggressive
   deterministic normalization gets almost all of it with none of the
   DSI/DSL risk.
2. **Date window**: `cert_date - 1 day` to `cert_date + 4 days` — asymmetric,
   widened from the originally proposed ±1. The measured offset distribution
   between berth departure and Cert Date is strongly one-directional, because
   the certificate is issued when loading completes and the vessel sails
   after:

   | offset | −1 | 0 | +1 | +2 | +3 |
   |---|---|---|---|---|---|
   | share of matches | 0.1% | 66.8% | 28.2% | 3.9% | 1.0% |

   A vessel effectively never sails before its certificate issues, so the one
   day backwards is only clock/calendar-rollover slack (per William).

   Originally built at −1/+2 and widened to −1/+3 on 2026-08-18 after
   reviewing the no-candidate records (see below). Measured effect across the
   full history:

Re-measured 2026-08-19 against the full history with the arrival fallback in
   place, corrupted IMOs repaired and dredges removed. Because the fallback
   already absorbs most late sailings, widening the sailing window buys
   **anchor quality, not volume** — it moves records off an arrival anchor and
   onto the sailing, which is the correct anchor:

   | window | matched | on sailing | on fallback | ambiguous |
   |---|---|---|---|---|
   | −1/+3 | 12,433 | 12,273 | 160 | 1 |
   | **−1/+4** | **12,434** | **12,322** | **112** | **1** |
   | −1/+5 | 12,433 | 12,334 | 99 | **2** |
   | −1/+6 | 12,433 | 12,342 | 91 | 2 |

   **+4 is the optimum.** Past it the total stops improving and one
   previously-matched record tips into ambiguity — precision given up for
   nothing. Widening is otherwise purely additive: `pick_event()` sorts by
   absolute offset first, so a more distant candidate can never displace an
   existing nearer match.
3. **Concat format**: comma-separated, deduplicated, sorted — deduped so a
   three-line all-corn record reads `CORN` rather than `CORN, CORN, CORN`,
   sorted so the value is stable across rebuilds. Verified that no
   Grain/Class/Destination value contains an embedded comma (the build warns
   if a future year ever introduces one).
4. **`fgis_record_id` format**: human-readable `{IMO}-{certdate}`, e.g.
   `9738337-20251214`. Unique by construction, since the rollup key
   `(vessel_key, cert_date)` maps 1:1 onto `(imo, cert_date)`. Vessels with
   no valid IMO use `NONAME-{NORMALIZED_NAME}-{certdate}`; unmatched records
   use `UNMATCHED-{NORMALIZED_NAME}-{certdate}`. Deliberately not a surrogate
   integer: integers would shift on every rebuild, whereas this is stable and
   traceable by eye.

## Still open

- **The anchor is the sailing, with arrival as a fallback** (William,
  2026-08-18): "the fgis matching should be on sailed from the elevator, or
  the mgmt rig which is midstream, no other places handle grain on the fgis
  list… if matching any other value other than sailing (dept) the elevator or
  mgmt buoys, will never match correctly" — and then: "for a grain call, you
  use the arrival to the elevator or mgmt, typically the arrival time to
  sailing is about +/- 4 days or less as fall back."

  Implemented exactly that way: the `Depart` from an Elevator/Mid-Stream zone
  is always preferred, and the `Arrive` at the same class of berth is used
  **only** where MRTIS recorded no sailing at all. The build asserts on every
  run that no fallback record had a qualifying sailing available.

  The ±4-day figure is confirmed by the data. Measured across the 11,678
  matches that have a prior arrival at the same berth:

  | berth stay | ≤1d | ≤2d | ≤3d | **≤4d** | ≤6d |
  |---|---|---|---|---|---|
  | share | 27.7% | 61.9% | 81.1% | **90.7%** | 97.3% |

  The fallback window is `cert_date - 6 .. cert_date + 1` — mirrored relative
  to the sailing window, because the vessel berths before loading completes
  and sails after. Coverage of the arrival-to-cert offset: 93.1% at -4, 96.4%
  at -5, **97.9% at -6**. Set to -6 rather than -4 on William's instruction
  after the Statements of Fact showed longer berth stays are routine (Ultra
  Leopard 5.2 days loading grain at ADM-Reserve, Asian Eternity 6.2, Desert
  Seeker 8.0 — see `docs/PORT_CALL_EVIDENCE.md`); -4 would have missed the
  arrival of the very grain loading those documents record. Recovered 168
  records in total (252 → 94 no-candidate), with no increase in ambiguity.

  `fgis_record.mrtis_event_action` records which event kind each link points
  at. **Check it before treating `mrtis_event_time` as a sailing timestamp.**

- **`unmatched_no_candidate` (99 records, 0.8% of in-coverage)** —
  investigated 2026-08-18. Breakdown of the 376 that existed at the −1/+2
  window, which is what drove widening to −1/+3:

  | | count | |
  |---|---|---|
  | C | 192 (51%) | vessel departed a non-grain-listed zone in-window |
  | D | 91 (24%) | real grain-berth departure exists, just outside the window |
  | E | 49 (13%) | only non-grain departures, all outside the window |
  | A | 42 (11%) | vessel name never appears anywhere in MRTIS |
  | B | 2 | no departure within 20 days |

  **The Elevator/Mid-Stream zone list is NOT the gap.** Of the 211 in-window
  departures behind category C, **206 were at an Anchorage** and only 5 at
  any other facility type (3 Tank Storage, 1 General Cargo, 1 Chemical
  Plant). So no dictionary additions are warranted. Those vessels are simply
  observed leaving an anchorage rather than the berth itself — resolving them
  properly needs **port-call assembly**: walk back from the anchorage
  departure to the berth stop preceding it. That's voyage-assembly work, not
  matching work.

  Category D drove the window widening (+124 records recovered), and the
  arrival fallback then resolved the 82-record "arrived but no departure
  recorded" bucket (+160 in total). What remains is the hard tail: vessels
  seen only at `SWP Cross` on their way out of the river with no berth event
  at all, plus category A.

  Category A is the one place a name issue is genuinely the cause — and it is
  worth being precise about scope, since 83% of the pre-fallback no-candidate
  records had names that matched *exactly*. Of the 42: 38 have a near-miss
  name active in MRTIS on that exact date. Two are pure homoglyphs
  (`SEAS I`/`SEAS1`, `NAVIOS PROSPERITY I`/`NAVIOSPROSPERITY1` — `I` vs `1`),
  recoverable deterministically; folding `1↔I`/`0↔O` was measured to
  introduce **zero** new collisions across 11,307 names and keeps DSI/DSL
  Phoenix distinct, but it is not currently applied. The rest are real typos
  (`NIKKEI PROGERSSO`, `AETORIA`/`AETOLIA`) that only edit-distance would
  catch — deliberately out of scope — plus a systematic **MRTIS-side**
  corruption, `CEMTEX` → `CEEX`, hitting a whole vessel family (Innovation,
  Honor, Leader, Sincerity). That one is better fixed as a vessel alias
  dictionary entry than by any matching rule, since it will recur on new
  data.

- **The reverse direction is expected and fine** (confirmed by William
  2026-08-18): an MRTIS grain-berth departure with no FGIS record is normal,
  because FGIS only certifies inspected grain. Byproducts — soybean meal
  (SBM), distillers dried grains (DDG) — load at the same elevators but are
  never inspected, so they never appear in FGIS. The data agrees: **91.9% of
  Elevator departures carry an FGIS record**, with the 8.1% gap the right
  size for byproduct traffic. Mid-Stream is only 4.1% linked, consistent with
  those buoy locations mostly handling non-grain cargo entirely (see the salt
  loadings in the Desert Seeker / Asian Eternity SOFs in
  `docs/OPEN_QUESTIONS.md`). Match completeness should therefore be judged on
  the FGIS→MRTIS direction only.
- **The 3 ambiguous records** (`AQUITANIA` 2024-10-13, `SPRING AURA`
  2025-08-14 and 2025-08-15) — two genuinely different vessels sharing a
  normalized name inside one window. Needs a human call; both are in
  `dictionaries/fgis_match_review.csv`.
- **Feeding the matched values into MRTIS proper.** William's note maps FGIS
  Grain → Cargo Group, Class → Cargo, Destination → Destination, and summed
  Metric Ton → estimated tons. Those columns don't exist on the MRTIS side
  yet (they belong to the unbuilt canonical/port-call layer), so the values
  currently land on `fgis_record` and wait there.
- **Re-pointing the link at `call_leg_id`** once voyage assembly exists, per
  Step 3 below.
