# MRTIS canonical build — open questions

Working list from the 2026-08-18 spec discussion (`logic.md`). Answer inline
or however's easiest — nothing here blocks the dictionary crosswalks, which
are already sent separately for you to fill in.

## 1. IMO cleanup — edge cases beyond the 9-digit rule — ANSWERED 2026-08-18

**Rule confirmed by William**: keep only the first 7 digits of the raw
value, drop anything after position 7 — applies to 8-digit and 9-digit
values alike, no special-casing by length. If the raw value has fewer than
7 digits, ignore it entirely (treat as no valid IMO, same as blank, fall
back to identifying the vessel by name).

For reference, the length distribution across all 314,335 rows this rule
applies to:

| IMO length | rows | outcome |
|---|---|---|
| 7 (valid) | 289,275 | used as-is |
| 9 | 13,976 | truncate to first 7 |
| 8 | 664 | truncate to first 7 |
| 0 (blank) | 9,757 | ignored — identify by name |
| 3 | 637 | ignored — identify by name |
| 6 | 10 | ignored — identify by name |
| 4 | 16 | ignored — identify by name |

Needs a code change in `scripts/lib/parse.py` when the canonical layer is
built (v0.1's `is_valid_imo()` currently rejects 8/9-digit values outright
instead of truncating them) — not yet applied to the live v0.1 schema.

## 2. Dredge/noise vessel exclusion — ANSWERED 2026-08-19

**Decided by William**: remove them at the front end rather than flagging
them — "if we remove the dredges on the list also on front end, removes
those records and focuses the table". `scripts/build_db.py` now filters the
9 vessels marked `exclude_as_dredge=Y` out at ingest (23,228 rows, 7.4% of
the raw feed). `--keep-dredges` restores them without editing the
dictionary, and `docs/DATA_QUALITY.md` always reports the per-vessel counts
dropped, so the removal is visible rather than silent.

**Matching is by IMO wherever the dictionary supplies one**, not by name.
This matters: "Texas Star" is BOTH a dredge (`imo_raw` 311000000) and,
separately, a real tanker (IMO 9256860, 20 events). A name-based filter
deleted the tanker too. Names are used only for the four entries that have
no IMO at all (Allisonk, Allins K, Keeneland, Ginny Lab), and even then only
against rows carrying no valid IMO themselves.

The Carnival cruise ships in the list are marked blank (not `Y`) and are
therefore kept, which matches the reading below that scheduled cruise
turnarounds are real traffic rather than noise.

### Original question (for context)

- Filter these completely out of the warehouse, or flag them with a boolean
  (e.g. `is_high_frequency_noise`) so they stay in the data but default-
  excluded from reports/analytics? I'd lean flag-not-delete so nothing is
  silently lost.
- Top-25 list is attached separately for you to review — is 25 a fixed
  count, or should the build recompute "top N by record count" fresh on
  every rebuild (so it adapts as more years are added)?
- Looking at the actual top 25: some are obviously repetitive
  workboats/tows (Mack B, Allisonk, Allins K, Ginny Lab), but three are
  Carnival cruise ships (Valor, Glory, Liberty) doing frequent scheduled
  turnarounds — those are probably NOT noise in the same sense. Worth a
  quick pass to confirm which of the 25 you actually want excluded vs. kept
  as legitimately high-frequency real traffic.

## 3. Mile marker format variants and the SWP -20 default

- You mentioned seeing '134M', 'M134', and '134 M' formats depending on
  agent. The current MRTIS CSVs (2019-2026) only show trailing-'M' format
  ('134M', '-19M') in my profiling — are the other formats from a different
  source/export you'll be adding later, or did I miss them somewhere in
  this data?
- The -20 default mile for SWP Cross events — is that a **fallback only
  when Mile is blank**, or should it **override** an existing raw value
  too? I ask because a meaningful share of SWP Cross rows already carry a
  real mile value (e.g. "-19M") in the source data.

## 4. Split-call detection and the agent-normalization rule

You said this will likely get clearer once the zone/facility dictionary is
in place, so no need to answer this yet — flagging it here so it's not
forgotten. Working hypothesis to react to whenever you're ready: a port
call is "split" when it contains two or more Terminal (berth) stops
between one Enter-SWP and the matching Exit-SWP; the depart-agent-
normalization rule (revert depart agent to match the inbound agent) only
applies when there's exactly one berth stop, since a genuine second berth
means a genuine second agent is plausible. Also still open: how buoys
zones (133 Buoys, 134 Buoys, etc.) should be treated in the assembly —
pure dwell like an anchorage, or can they host actual cargo transfer
(lightering) that should be treated more like a berth stop?

**Real-world confirmation, 2026-08-18** (from 4 Statements of Fact William
shared — Desert Seeker, Ultra Leopard x2, Asian Eternity):

- **Split call, confirmed with a real example.** MV Ultra Leopard (IMO
  9758428) discharged iron ore at Nucor Convent, LA (May 3-8, 2026, Master
  Alfredo Gavilo Jr), then — without leaving the river — sat idle at AMA
  Anchorage for a week, shifted to LaPlace Anchorage, and then loaded
  soybeans at ADM-Reserve, LA (May 15-26, 2026, Master Archie Pinangay,
  different cargo, different berth, different charter). Same IMO, same
  continuous river presence, two genuinely distinct legs (discharge then
  load) with different cargo, different berth, and even a **different
  master** in between. This is exactly the discharge-leg + load-leg split
  call the working hypothesis describes — and it shows master/crew change
  mid-call is not a signal that it's a different vessel or a data error.
- **Buoys/Mid-Stream zones do host real cargo activity, not just dwell.**
  MV Desert Seeker loaded salt in bulk at "ARTCO BUOYS MILE 121 LOWER" and
  MV Asian Eternity discharged salt at "ARTCO BUOYS MILE 110" — both
  full berth-style calls (tugs alongside, all-fast, cargo ops, standby-
  weather delays, departure conditions) at a Mid-Stream buoy location, not
  an anchorage. So Mid-Stream zones need to be eligible as genuine
  activity/berth stops in the assembly, same as Terminal/Elevator zones —
  confirms the `facility_type = 'Mid-Stream'` rows in
  `dictionaries/zone_facility.csv` (39 of them, only 4 pre-tagged
  `Cargo group = Grain`) are real candidate activity locations, not just
  staging. The zone dictionary's `Cargo group` tag looks like it reflects
  each zone's *typical* cargo rather than an exhaustive/exclusive list —
  Desert Seeker/Asian Eternity both moved salt through Mid-Stream buoys
  that aren't tagged `Grain`, so cargo type has to be confirmed per-call
  (from draft delta / SOF / FGIS-type external data), not assumed from the
  zone alone.
- **Anchorage-hopping before a berth call is normal, not a separate call.**
  Ultra Leopard idled at AMA Anchorage, then explicitly shifted (pilot
  aboard, anchors aweigh, transit, dropped anchor again) to LaPlace
  Anchorage, before ever reaching its loading berth. Multiple
  Anchorage-zone events ahead of the first Terminal/Elevator/Mid-Stream
  stop should collapse into pre-berth staging for the same call, not be
  read as separate port calls or separate legs.
- SOFs also show the anchorage stop itself can be governed by NOR
  (Notice of Readiness) tendering / re-tendering "without prejudice" while
  waiting on berth instructions — multi-day anchorage dwell before a berth
  is the norm for this traffic, not an anomaly to filter out.

## 5. Scope of the "new columns for later stages"

Activity (Load/Discharge/Load-Discharge) looks computable now from draft
deltas plus facility type once the zone/facility dictionary exists. Cargo
Group, Cargo, Shipper, Consignee, Receiver, Last/Next Port,
Destination/Origin, Vessel Type Group, DWT, TPC, Est/Actual Tons all need
data this dataset doesn't have. You mentioned matching against two other
tables to complete some of these — no need to detail that now, just
flagging that we'll pick this up when you're ready to bring those tables
in.

## 6. Dictionary provenance (resolved)

Zone, agent, and vessel-type crosswalk drafts are being generated fresh
from the actual data (raw value + occurrence count, with canonical columns
left blank for you to fill in) rather than assumed to exist already —
sent alongside this doc.

## 7. Questions raised by the 2026-08-19 independent audit

From `docs/audit/AUDIT_2026-08-19_0242.md`. These are decisions only William
can make — the audit deliberately changed nothing.

### 7.1 Is the agency fee per port call, or per berth departure? — ANSWERED 2026-08-19

The rule as implemented charges on **every** `Depart` from a facility berth.
Measured against `Enter`/`Exit` (pilot-station) call boundaries:

- 7,271 of 38,296 fee-bearing port calls (19.0%) are charged 2–10 times
- Charging once per call yields $280,343,000 against the $347,602,500 booked
  inside call boundaries — a **$67,259,500 difference, 19.2% of the published
  $349,625,500**
- 179 of those charges ($1,746,500) are a second charge at the **same zone**
  within 60 minutes of the first (e.g. Bold Guardian, Meraux Buoys Lower,
  2020-04-27 08:27 then 08:28)

`docs/PORT_CALL_EVIDENCE.md` already states the opposing principle from four
real SOFs: *"Shifting within a berth is not a second berth call… Assembly must
not read these as separate stops."* The two documents currently disagree.

**Needs**: a ruling on the billing unit. The same-berth-within-an-hour repeats
look wrong under either reading and can be collapsed regardless.

**RULED BY WILLIAM, 2026-08-19**: *"agency fee is per port call, not per berth
except when split discharge then load."*

So the billing unit is the **operational leg**, not the berth departure and not
the bare call: one fee per port call, and a call that comes in laden, discharges
and then loads a fresh cargo is two.

`scripts/build_port_calls.py::split_into_legs()` already encodes exactly this —
consecutive Load berths (topping off at a second elevator) stay ONE leg, and the
Discharge→Load transition is what opens a second. **`port_call_leg` is therefore
the fee grain**, and the rule becomes: *one fee per leg that has a berth stop,
priced by the call's vessel type.*

Measured against the assembled layer (40,170 calls / 43,238 legs):

| Basis | Chargeable units | Total |
|---|---|---|
| As built — per berth departure | 48,167 sailings | $349,625,500 |
| **Per leg with a berth stop (the ruling)** | **41,823 legs** | **$308,885,500** |
| Difference | −6,344 | **−$40,740,000 (−11.7%)** |

The split patterns confirm the ruling is describing real traffic — 2,874 calls
have more than one leg:

| Pattern | Calls |
|---|---|
| `Discharge -> Load` | 2,416 |
| `Load -> Discharge` | 151 |
| `Load -> Discharge -> Load` | 83 |
| `No Cargo -> Load` | 81 |
| `Discharge -> Load -> Discharge` | 29 |
| others | 114 |

1,415 legs never reached a berth and are excluded — no cargo work, no fee.

**IMPLEMENTED 2026-08-19** in `scripts/build_port_calls.py`:
`port_call_leg.agency_fee` is one fee per leg that reached a berth, and
`port_call.agency_fee_total` is the sum of its legs. Both are guarded (no fee on
a leg that never berthed; call total equals its legs). The pre-ruling
per-departure figure is preserved unchanged on `port_call_event.agency_fee` and
`*_departures*` columns so the two bases stay directly comparable.

The built figure is **$309,018,500 over 41,821 legs**, $133,000 above the
$308,885,500 estimated above because the build prices through
`build_db.py::agency_fee_for()`, which also falls back to the ships register for
a blank-type vessel -- follow-up 4 below, resolved in favour of the register
fallback so the two layers price identically.

#### 7.1a Follow-ups this ruling opens

1. **$308,885,500 is a floor, not the answer — but do not chase it here.**
   5,377 chargeable legs (12.9%) have an unresolved activity, and
   `split_into_legs()` deliberately never lets an unknown invent a split — so
   genuine Discharge→Load calls are being billed once. **682 single-leg calls
   have ≥2 berth stops and contain at least one unresolved stop**; if each were
   really a split, that is **+$3,661,000**.

   **Ruled by William, 2026-08-19**: *"nan fields need to be ignored as will
   populate from other sources."* The gap therefore closes when the additional
   source tables land (§5), not by inferring harder from what MRTIS already
   holds. Leave the NULLs NULL, keep reporting the floor, and re-price once the
   activity columns are populated.
2. **Does a `No Cargo` leg accrue a fee? — REFRAMED 2026-08-19, now §8.**
   239 legs, currently counted at $2,061,500. `No Cargo` turns out not to be an
   observation about the ship at all — it is the zone dictionary speaking in the
   absence of draft evidence, which makes it a NaN substitute. Split out into
   its own question below.
3. **`open_end` calls** (274 chargeable legs, $2,065,000) never record an SWP
   exit, so the call boundary is inferred from the next entry. Included as
   normal; flagging only because the call is incomplete in the source.
4. **Rate for a blank-type call.** The leg pricing above uses
   `port_call.vessel_type` only; `agency_fee_for()` in `build_db.py` also falls
   back to the ships register (`ship_type_group LIKE 'Bulk Carrier%'` → higher
   tier). 91 chargeable legs have no canonical type — some are register-known
   bulkers and would move to $10,500. The leg pricing must reuse
   `agency_fee_for()` rather than re-implement the tiering.
5. **The 179 same-berth-within-an-hour repeats disappear on their own** under
   this ruling: they were second charges at one berth inside one leg, and a leg
   can only be charged once. No separate fix needed.
6. **Where does the fee now live?** `fact_zone_event.agency_fee` is per-event by
   construction and cannot express a per-leg charge. Options: keep it as the
   raw signal and add `port_call_leg.agency_fee` as the billable column, or
   retire the event-level column. Needs a call before either script changes.

### 7.2 Is `Kennington` (IMO 9664926) really a dredge/workboat?

It sits on `dredge_exclusions.csv` with `exclude_as_dredge=Y` (one of the
original 9), but its data profile is commercial: valid IMO, `Type=Tank` on
every row, agent **Celtic** throughout, **174 `SWP Cross` events** (~87 river
entries/exits, 2019-03 → 2026-07), 171 events at `Crosstex Energy` (Tank
Storage), drafts to 25 ft. Excluding it removes 583 events, 87 berth sailings
and **$304,500** in accrued fees.

Same question, smaller stakes, for `Dodge Island` (7917800 — looks like a tug;
anchorage/pilot only, zero berth events, so no fee impact either way).

### 7.3 Should the name-only exclusion spare a complete river call?

The name fallback fires only against rows with no valid IMO — which is exactly
the documented "IMO, Agent and Type go missing together" defective input path,
so real vessels land in it. Three genuine ocean port calls are currently
deleted:

- **T Jungfrau** — Sep–Oct 2023: `Enter` 25 ft → `CFI Donaldsonville 106` →
  `Depart` **42 ft** → `Exit`. A loaded tanker call. (The IMO-bearing
  T Jungfrau 9389289 survives — this is a different call by the same ship.)
- **Heino** — Jun 2020: `Enter` 25 ft → `Chalmette Slip` → `Depart` 21 ft →
  `Exit`. A general-cargo discharge.
- **Corinthian** — Feb–Mar 2019: `Enter` → `Buck Kreihs` (ship repair) →
  `Exit`. A real vessel; shipyard visit.

**Possible rule**: never name-exclude rows that form a complete
`Enter` → berth → `Exit` river transit.

### 7.4 Should `Gen` (general cargo) bill at the bulk tier?

`dictionaries/vessel_type.csv` maps `Gen` (16,752 rows) → `Bulk`, so general
cargo ships accrue **$10,500** and are reported inside the "Bulk" row of the
fee table in `DATA_QUALITY.md`. Consistent with BUILD.md's General Cargo
reasoning, but it is stated nowhere and the report gives no way to see it.

### 7.5 What counts as evidence that two same-name vessels are one ship?

The audit found one false merge among the 31 (`1782585 -> 9747120`, "Egret" —
131 rows, 51% of all repaired rows). `build_imo_repair_map`'s "exactly one
check-digit-valid IMO" test cannot see a *second real vessel whose IMO is also
corrupt*.

The proposed discriminator, which cleanly separates Egret from the other 30:
refuse the merge when the corrupted-IMO rows contain **no `Enter`/`Exit`
event** and their date range **does not overlap** the period the good vessel
actually carried that name. Confirm before implementing.

## 8. What is `No Cargo`, and should it bill? — RESOLVED, William, 2026-08-19

**What it is.** Not an inference about the vessel. `No Cargo` is
`dictionaries/zone_facility.csv` speaking: the 14 zones marked
`ops = Layberth` with `Rule = "No cargo ever takes place"` —

> Violet Dock 1-5, Buck Kreihs, Andry St, Alabo St, Poland St, Mandeville St,
> Gov Nicholls St, Esplanade Ave, Perry Street, Marlex

**When it fires.** `classify()` tries draft delta -> FGIS certificate -> zone
dictionary -> nothing. The dictionary is only reached when the first two are
empty. **All 239 `No Cargo` legs have `draft_delta_ft` NULL** — there is no
draft evidence on a single one. So `No Cargo` is asserted *exactly where the
evidence is missing*: it is a NaN substitute.

The stays themselves look real — median 3-6 days, longest 55 — and the busiest
berths are Buck Kreihs (59 legs, a repair yard), Perry Street (53), Poland St
(36) and the Violet Docks (50 combined). Nothing suggests the label misdescribes
what happened. The problem is what it *does*.

**Why it matters.** Unlike an unresolved stop — which joins the leg in progress
and can never invent a split — `No Cargo` is a distinct activity value, so it
**opens a leg boundary**, and every extra leg is an extra fee:

| Effect | Calls | Fee |
|---|---|---|
| Splits created **only** because a `No Cargo` label sits beside real cargo work | 147 | **$1,347,500** |
| Splits that would survive without it (real Discharge/Load either side) | 24 | — |
| Calls that are a lay-up and nothing else (sole leg) | 66 | **$532,000** |

**The tension with the NaN ruling.** *"nan fields need to be ignored as will
populate from other sources"* — but `No Cargo` is precisely what gets written
into a field that would otherwise be NaN. Applied consistently, a zone-rule
label derived in the absence of evidence should not be able to manufacture a
billable split.

**Two decisions, deliberately separate — both decided 2026-08-19:**

- **8a. Can a layberth stop open a leg? No.** A lay-by is not a cargo job, so
  it cannot be the "discharge then load" split that was ruled on; it joins the
  leg in progress exactly as an unresolved stop does. Split calls fell 4.4% →
  4.1% of calls (1,787 → 1,632).
- **8b. Does a pure lay-up call charge at all? No — "no fee on departing a
  layberth."** A leg bills only if it did real, non-layberth work somewhere; a
  leg of nothing but layberth stops accrues nothing, exactly like a call that
  never berthed at all. 142 pure lay-up legs moved from billed to $0. (54
  legs that mix a layberth stop with a genuine other berth, e.g. bunkers at a
  refinery with unresolved activity, still bill as before — only the layberth
  stop itself is exempt.)

Combined effect on the ruling-basis total: **$304,808,000 → $298,868,500**
(-$5,939,500), over 41,334 → 40,245 chargeable legs. Implemented in
`split_into_legs()` and the leg-fee computation in
`scripts/build_port_calls.py`; see `docs/PORT_CALL_SPEC.md` §4.

## 9. Extending the ships register to tankers and the other types — RESOLVED, 2026-08-19 (superseded)

**Superseded, not executed as planned.** The chunk-pull plan below (Sea-web,
2 batches of ~2,500 IMOs) was prepared but never run -- William instead
expanded the separate `Ships_Register` project directly into a full
world-fleet pull, done the same day. `fleet_joined` went 20,101 -> 49,763
rows, 19 -> 133 `ship_type_group` values, zero blank groups at the source.

`dictionaries/ships_register_fleet.csv` refreshed from it (`docs/BUILD.md`
"Ships register enrichment" procedure) and the full chain rebuilt
(`build_db.py` -> `build_fgis_match.py` -> `build_port_calls.py`). Match
coverage against `dim_vessel`: **60.9% -> 99.4%** of all vessels (**61.1% ->
99.8%** of those with a valid IMO), exactly the improvement the world-fleet
pull was expected to deliver. `port_call.dwt`/`.tpc` now populated on 99.7%
of calls (40,055 of 40,170), up from roughly 60%.

24 vessels with a valid-format IMO remain unmatched -- 13 checksum-invalid
(MRTIS-side), 10 behind Sea-web's pre-1980 build-year gate, 1 genuinely
absent -- none of which call for another pull. **Decided, William,
2026-08-19: leave the two 1978 merchant vessels (`7633375` Sunnanvik,
`7711725` Radcliffe R. Latimer) as is, no gate change.** Both bill correctly
regardless (Zone Report `Type` is already `Bulk` for both); they simply
carry no `dwt`/`tpc`. See `docs/BUILD.md` for the full breakdown.

The original chunk-pull plan is kept below for the record, in case a future
gap needs the same approach.

## 9 (original plan, not executed). Extending the ships register to tankers and the other types — 2026-08-19

**Intent confirmed by William**: yes, extend. The question is the best shape of
pull, given Sea-web's two caps: **12 display fields per pull** and **2,500 rows
per pull**. For the dry-bulk batch, getting the 24 fields needed took **2
passes**. His read: tankers, cruise and the rest can stay on the same 12 fields
and will not need the second half of the second pass.

**That is right, and it is easier than the dry-bulk batch was**, for three
reasons:

### 1. MRTIS consumes six fields, not 24

`dictionaries/ships_register_fleet.csv` carries exactly:

    imo, name_of_ship_ref, ship_type_ref, ship_type_group, dwt, tpc

All six fit inside a single 12-field pull with six slots spare. Whatever else
pass 2 was fetching for the dry-bulk batch, MRTIS does not read it — so for this
extension **one pass is enough**, not one and a half. (Confirm against the
FileMaker/Ships_Register consumers before dropping pass 2 outright; MRTIS is not
necessarily the only downstream.)

### 2. It is 3,972 vessels — two chunks

Every MRTIS vessel with a valid IMO and no register match:

| Type | Vessels |
|---|---|
| Tanker | 3,174 |
| Container | 379 |
| Gas | 182 |
| Bulk | 93 |
| (no type recorded) | 68 |
| Passenger | 35 |
| Other / Reefer | 41 |
| **Total** | **3,972** |

At 2,500 per pull that is **2 chunks × 1 pass = 2 downloads**, against the 80
the full 50k universe needed. The chunk files are already written in the format
`PULL_PLAN.md` step 3 expects — a bare IMO list, sorted, ready to upload:

- `dictionaries/register_gap_chunk_01.txt` — 2,500 IMOs
- `dictionaries/register_gap_chunk_02.txt` — 1,472 IMOs
- `dictionaries/register_gap_imos.csv` — the same vessels with name, MRTIS type,
  event count and date range, for eyeballing before the pull

### 3. No top-up problem, because these vessels were never pulled

`PULL_PLAN.md` records the trap: *"a vessel quarantined in an earlier batch for
missing one pass is only completed by a later batch that supplies every pass for
it again, in that same batch."* That bites a top-up of vessels already in the
register. These 3,972 are not in it at all, so the batch is self-contained —
whatever passes are run, they are all run for these IMOs within one batch, and
the complete/incomplete decision resolves cleanly.

### Worth knowing before deciding

- **These 3,972 vessels account for 125,704 events — 43% of the warehouse.**
  Tankers call far more often than their headcount suggests. This is not a
  long-tail cleanup; it is nearly half the traffic currently unable to be priced
  or classified by anything except the Zone Report's own Type field.
- **`ship_type_group` will need new size vocabularies.** The register's 19
  values are all Bulk Carrier or General Cargo. Tankers need MR/LR1/LR2/Aframax/
  Suezmax/VLCC, containers TEU bands, gas LNG/LPG classes. Decide whether to
  pull that column for these types or derive it from DWT — within the existing
  families it is a pure step function of DWT (zero monotonicity violations
  across 18,752 rows), so deriving is defensible and avoids a second vocabulary
  drifting from the first.
- **TPC coverage is the real prize.** No public source carries TPC at all; it
  comes from the ship's hydrostatic tables and only the licensed registers have
  it. If tanker TPC comes back in this pull, laytime and tonnage work opens up
  for 43% more of the traffic.

**Needs**: confirmation that 12 fields covers what the other consumers of
Ships_Register need for these types, and a decision on pulling vs deriving
`ship_type_group`.

## 10. `vessel_key`/`event_key` are row position, not a stable identity — OPEN, raised 2026-08-19

Both are assigned as `dataframe.index + 1` in `build_db.py` -- arbitrary
position in that run's rebuild, not derived from anything about the vessel or
event itself. `dim_vessel.natural_key` (IMO, or `'NONAME:'+name`) already
exists as a stable, content-derived identity right next to it; the surrogate
int is only there for smaller/faster joins.

Because the key is positional, `build_db.py` cannot tell whether a given
vessel or event landed on the same key across two rebuilds, so `write_db()`
takes the conservative option and drops the FGIS and port-call layers on
**every** core rebuild, regardless of whether the vessels/events they
reference actually changed. Raised while refreshing the ships register
(§9): that refresh only changed register enrichment columns, not a single
raw event or vessel, and still forced re-deriving both downstream layers
from zero.

**This will keep recurring.** MRTIS is not a one-off build -- the register,
the dictionaries and the raw Zone Report feed all get revised on an ongoing
basis, and each one currently pays for a full FGIS + port-call rebuild.

**Possible fix**: derive `vessel_key`/`event_key` deterministically from a
stable input (a hash of `natural_key` for vessels; something equivalent for
events, e.g. hash of `(natural_key, event_time, action, zone)`) instead of
row position. An unrelated vessel appearing, disappearing or reordering would
then never renumber anyone else's key -- only a vessel's own identity
changing (e.g. an IMO repair merging two records) would move it. This is a
schema-level change (`sql/schema.sql`, `build_db.py`, and every downstream FK
in the FGIS and port-call layers) -- needs scoping as its own piece of work,
not folded into an unrelated fix.

---

## 11. Questions raised by the 2026-08-19 independent audit #2 — OPEN

Raised by `docs/audit/AUDIT_2026-08-19_1746.md`, which audited the port-call
assembly layer (`git log 13937b9..HEAD`, 13 commits). Each of these needs a
business ruling; the audit deliberately did not decide any of them.

### 11.1 What should a leg report when it mixes a layberth stop with an unresolved working berth?

**54 legs currently report `activity = 'No Cargo'` and bill $413,000 anyway.**

§8a lets a `No Cargo` stop join the leg in progress. The leg's label then comes
from `real_acts[0]` — the first stop whose activity is truthy and not
`No Cargo`. An **unresolved** stop has `activity = None`, which is falsy, so it
cannot win the label, and `No Cargo` does. But the fee test uses a different
predicate (`any(st["activity"] != "No Cargo")`), and `None != "No Cargo"` is
True — so the leg bills.

The result is a leg labelled "no cargo" that charged a full agency fee. The
berths are real working berths (Chalmette Slip, IMC Faustina, 7th Street, Arabi
Terminal, IMTT St Rose 14, Blackwater Harvey, Globalplex).

Both statements cannot be right. Which is wrong?

- **(a)** The label. An unresolved stop should outrank `No Cargo`, making these
  legs report `NULL`/unresolved — honest about not knowing, and consistent with
  "nothing is guessed".
- **(b)** The fee. If the leg genuinely did no cargo work anywhere, $413,000
  should not be billed. (Unlikely — these are working berths — but it is the
  other way to make the two agree.)
- **(c)** Neither. Add a separate `billable_activity` so the operational label
  and the billing predicate stop pretending to be the same thing.

Two knock-on effects of the same code path, which the same ruling should cover:

- **7 `No Cargo` legs assert a `cargo_group`** (`Liquid Bulk` ×6, `Passengers`
  ×1). `cargo_group` is read from `lg[0]`, the leg's *first* stop; `activity` is
  read from a possibly different stop.
- **20 of the 54 report the layberth** (Buck Kreihs, Perry Street, Poland St,
  Violet Dock 2, Alabo St) as `first_berth_zone`/`facility_type` while billing
  for work done at another berth.

*Dollars: $413,000 across 54 legs. Rows: 54 legs, 7 with contradictory cargo,
20 with a misleading berth.*

### 11.2 Should `port_call.agency_fee_departures_total` include unplaced events?

The column exists so the two fee bases "can be compared directly". It does not
reconcile to the basis it is meant to represent:

| Source | Total |
|---|---|
| `fact_zone_event.agency_fee` — the pre-ruling basis | $349,625,500 |
| `SUM(port_call.agency_fee_departures_total)` | $346,692,500 |
| **Gap** | **$2,933,000** (388 fee-bearing events on 17,100 unplaced events) |

So the over-billing ratio is 17.0% against `fact_zone_event` and 16.0% against
the call-level column — two analysts comparing "the two bases" get different
answers.

Defensible as-is (a call-level column can only hold call-level fees), but then
the schema comment must say the comparison is *within placed calls only*, and a
guardrail should assert the gap equals exactly the unplaced fee rather than
leaving it unmeasured. Note **$98,000 of the gap is audit #1's fabricated
Egret fee** — the gap carries known bad data, not just coverage loss.

*Dollars: $2,933,000 unreconciled. Not a billing error — a comparability one.*

### 11.3 Should `tpc = 0` be stored as NULL?

`port_call.dwt`/`.tpc` are reported 99.7% populated. But `tpc > 0` on only
**89.6%** — `tpc = 0` on **4,045 calls (10.1%)**, across 1,130 distinct
vessels, including **2,582 Bulk calls**, 1,031 Passenger, 116 Container, 99 Gas.

Zero is a legitimate TPC for some small craft and a missing value for a
Capesize. Any draft-survey calculation dividing by `tpc` breaks on 10% of calls
with no warning from the coverage headline. Only the register owner can say
which of the 4,045 are genuinely zero.

Related to, but distinct from, the TPC provenance question already logged in
`docs/SESSION_LOG.md` (captured-vs-estimated). This one is purely
zero-encoded-as-null.

### 11.4 Should the 2 berthed-but-unbilled legs bill?

Two legs reached a berth and did non-layberth work but carry no fee, because
`agency_fee_for()` returns `None` for a vessel with no usable IMO **and** no
type from either the Zone Report or the register — the "this is a tug, not an
agented ocean vessel" rule.

Both did berth. If the rule is right, this is correct and no action follows. If
a berthing vessel is always agented regardless of identity, they are under-billed.

*Dollars at stake: ≤ $21,000.*

### 11.5 Not a question — items for correction without a ruling

Recorded here so they are not lost; none needs a decision:

- `sql/schema_port_call.sql` states summing `port_call_event.agency_fee`
  "over-bills by ~12%". Actual: **17.0%** ($50,757,000).
- `docs/PORT_CALL_QUALITY.md:86` says the 54 legs "bill exactly as [they] did
  before this ruling". They cannot — §8 changed leg membership. The real §8
  movement is **−$3,258,500 across 313 calls** ($302,127,000 → $298,868,500),
  which is stated nowhere and should be.
- `build_db.py:266`'s `ship_type_group` backfill is an unconditional
  `fillna(ship_type)`, not the "only where the family has no size vocabulary"
  rule it is documented as. Currently inert (0 blanks in the refreshed
  register), and it fired 1,349 times legitimately under the old one — but
  `'Bulk Carrier, Self-discharging, Laker'` starts with `"Bulk Carrier"`, so a
  future register with blanks in such a family would silently promote vessels to
  the $10,500 tier. Worth an explicit guard.
- §9 was recorded as a pure coverage gain (60.9% → 99.4%). Two vessels **lost**
  their match: 7711725 *Radcliffe R Latimer* and 7633375 *Sunnanvik*, both
  behind the pre-1980 gate. Billing impact $0 (both are `Bulk` from the Zone
  Report); they lose only `dwt`/`tpc`.
- Audit #1's ambiguous FGIS finding is **1 record** (`UNMATCHED-AQUITANIA-20241013`)
  spanning **3 certificate lines**, not 3 records.

---

## 12. Agency fee — vessel-type and berth-type tiers — PENDING IMPLEMENTATION, William, 2026-08-19

William's instruction, verbatim:

> *"minor change to the fee rules, if vessel type is; Passenger/Cruise use fee
> $2500; if Ro-Ro Cargo Ship or Vehicles Carrier use fee $1000; if vessel type is
> Container Ship (Fully Cellular) or Container Ship (Fully Cellular/Ro-Ro
> Facility) use fee $750; if vessel type is Refrigerated Cargo Ship use fee
> $5000; last, any dry bulk vessel calling a general cargo facility type, use fee
> $5000"*

**Not implemented.** Captured here with impact scoped so the next session can
build it. Nothing in `scripts/` or the database has been changed.

### 12.1 The rules as given

| # | Condition | Fee |
|---|---|---|
| R1 | vessel type = `Passenger/Cruise` | $2,500 |
| R2 | vessel type = `Ro-Ro Cargo Ship` or `Vehicles Carrier` | $1,000 |
| R3 | vessel type = `Container Ship (Fully Cellular)` or `Container Ship (Fully Cellular/Ro-Ro Facility)` | $750 |
| R4 | vessel type = `Refrigerated Cargo Ship` | $5,000 |
| R5 | any **dry bulk vessel** calling a **General Cargo** facility type | $5,000 |

Existing tiers (`Bulk` $10,500 / everything else $3,500) presumably remain the
fallback — confirm.

### 12.2 Indicative impact — −$26,701,000 (−8.9%)

Measured against the current build (ruling basis, $298,868,500 over 40,245
chargeable legs). Indicative only: R5's exact scope is undecided (see 12.3.3).

| Rule | Chargeable legs | Bills today | Would bill | Change |
|---|---|---|---|---|
| R1 Passenger/Cruise | 1,043 | $3,650,500 | $2,607,500 | **−$1,043,000** |
| R2 Ro-Ro / Vehicles Carrier | **0** | $0 | $0 | $0 |
| R3 Container (Fully Cellular) | 3,128 | $10,948,000 | $2,346,000 | **−$8,602,000** |
| R3 Container (FC/Ro-Ro Facility) | **0** | $0 | $0 | $0 |
| R4 Refrigerated Cargo Ship | 40 | $140,000 | $200,000 | **+$60,000** |
| R5 Bulk @ General Cargo berth | 3,112 | $32,676,000 | $15,560,000 | **−$17,116,000** |
| | | **$298,868,500** | **$272,167,500** | **−$26,701,000** |

R4 is the only increase. R5 alone is 64% of the movement.

### 12.3 Questions that must be answered before this can be built

#### 12.3.1 These are register `ship_type` values, not the canonical vocabulary — so the priority order has to change

`Passenger/Cruise`, `Container Ship (Fully Cellular)`, `Refrigerated Cargo Ship`,
`Vehicles Carrier` and `Ro-Ro Cargo Ship` are all values of the ships register's
raw `ship_type` (`ship_type_ref`). They are **not** values of
`vessel_type_canonical`, which is the 7-value dictionary vocabulary
(Bulk / Container / Gas / Other / Passenger / Reefer / Tanker).

`agency_fee_for()` reads `vessel_type_canonical` **first** and returns
immediately. Almost every vessel these rules target already has one:

| Register `ship_type` | `vessel_type_canonical` | Chargeable legs |
|---|---|---|
| Container Ship (Fully Cellular) | `Container` | 3,124 |
| Container Ship (Fully Cellular) | *(none)* | 4 |
| Passenger/Cruise | `Passenger` | 1,042 |
| Passenger/Cruise | *(none)* | 1 |
| Refrigerated Cargo Ship | `Reefer` | 36 |
| Refrigerated Cargo Ship | *(none)* | 4 |

So **as `agency_fee_for()` is written today, these rules would fire on 9 legs out
of 4,211** — priority 1 catches the rest first and returns $3,500.

Two ways out; this is the decision:

- **(a)** Express the rules on `vessel_type_canonical` instead —
  `Passenger` → $2,500, `Container` → $750, `Reefer` → $5,000. Simple, uses the
  column that is 79% populated from the Zone Report, and needs no register match.
  But the canonical vocabulary has no way to say `Ro-Ro Cargo Ship` or
  `Vehicles Carrier` — both collapse into `Other`, so R2 becomes unexpressible.
- **(b)** Reorder `agency_fee_for()` so the specific register `ship_type` is
  consulted **before** the canonical type. Expresses every rule exactly as
  written, but makes the fee depend on a register match — and 63 vessels have no
  register row at all, plus the 2 lost in §9 (§11.5).

Recommendation to consider: (b) with (a) as the fallback when the register has
no row — but William should rule.

#### 12.3.2 Three of the six named types have no traffic

`Ro-Ro Cargo Ship`, `Vehicles Carrier` and
`Container Ship (Fully Cellular/Ro-Ro Facility)` exist in the world register
(579, 941 and 5 vessels) but **zero of them appear in MRTIS traffic**. R2 and
half of R3 currently match nothing.

The nearest thing actually in the data is
**`General Cargo Ship (with Ro-Ro facility)`** — 6 vessels, 5 chargeable legs,
$52,500 today. **Is that intended to be covered by R2?** It is a Ro-Ro-capable
ship but a general-cargo hull, so it is genuinely ambiguous. Also present in the
register but not in traffic: `Container/Ro-Ro Cargo Ship` (11),
`Rail Vehicles Carrier` (12).

#### 12.3.3 R5 is the first berth-dependent rule, and it needs three sub-decisions

Every fee to date has been priced by the **vessel**, never the berth — the
choice was made deliberately and is documented in `docs/BUILD.md`
("it follows the ship being agented rather than the dock it happens to occupy").
R5 reverses that for one case, which is fine, but it raises questions the
vessel-only rules never had to answer:

1. **Which berth decides, on a leg with more than one stop?** A leg is a run of
   berth stops sharing one activity, so it can touch several facilities.
   - by the leg's **first** berth (`port_call_leg.facility_type`): **3,112 legs,
     $32,676,000 today**
   - by **any** stop in the leg being General Cargo: **3,329 legs, $34,954,500**

   A 217-leg / $2.28M difference. The leg's other reported attributes
   (`first_berth_facility`, `cargo_group`) already come from the first stop —
   but audit #2 finding W1 shows that convention is itself producing misleading
   rows, so it should not be adopted here by default.

2. **What is a "dry bulk vessel"?** `vessel_type_canonical = 'Bulk'` (the Zone
   Report's own Type, 79% populated, what the $10,500 tier uses today), or
   register `ship_type LIKE 'Bulk Carrier%'`? The figures above use the former.

3. **Does R5 override R1–R4, or only the $10,500 base tier?** As written R5 is
   "last", which reads as highest precedence. A Bulk vessel cannot also be
   Passenger/Container/Reefer, so today the rules cannot collide — but the
   precedence should be stated rather than left to the accident that they are
   disjoint.

#### 12.3.4 Does this apply to the per-departure comparison basis too?

`fact_zone_event.agency_fee` is built by the same `agency_fee_for()` in
`build_db.py`. If it changes, the pre-ruling comparison basis ($349,625,500)
moves too — which audit #2 §A3 confirmed no ruling has done so far. Either is
defensible:

- **Change both** — the two bases stay comparable, but the "pre-ruling basis"
  stops being a fixed historical benchmark.
- **Change the leg basis only** — the benchmark is preserved, but the two bases
  then differ by pricing *and* by counting rule, and the 17.0% over-bill figure
  becomes a mixture of two effects rather than one.

Note R5 cannot be expressed on `fact_zone_event` in the same way regardless: that
table charges per *departure*, and a departure has exactly one berth, so
"the leg's berth" has no meaning there.

#### 12.3.5 Confirm R4 is an increase

`Refrigerated Cargo Ship` moves **$3,500 → $5,000**, the only rule that raises a
fee. 40 legs, +$60,000. Flagged only because every other rule cuts.

### 12.4 Suggested build order for the next session

1. Rule 12.3.1 (which column the rules key off) — everything else depends on it.
2. Rule 12.3.3 (R5's three sub-decisions).
3. Rule 12.3.4 (one basis or both).
4. Implement in `agency_fee_for()`; the tier table should move out of the
   function body into a declared mapping, since it is now six rules rather than two.
5. **Add the value-level guardrails audit #2 §5 found missing before changing the
   tiers** — there is currently no check that a fee matches its vessel's tier, so
   a mistake in this change would not be caught by any guardrail.
