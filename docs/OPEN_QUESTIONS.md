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

#### 7.1a Follow-ups this ruling opens

1. **$308,885,500 is a floor, not the answer.** 5,377 chargeable legs (12.9%)
   have an unresolved activity, and `split_into_legs()` deliberately never lets
   an unknown invent a split — so genuine Discharge→Load calls are being missed
   wherever the draft delta, FGIS and dictionary all came up empty. **682
   single-leg calls have ≥2 berth stops and contain at least one unresolved
   stop**; if each were really one split, that is **+$3,661,000**. Worth
   resolving activity harder before the number is treated as final.
2. **Does a `No Cargo` leg accrue a fee?** 239 legs berth but work no cargo
   (bunkers, stores, repair, lay-by). Currently counted — **$2,061,500**, of
   which $1,837,500 is bulk. A ship at a berth is still being agented, but
   confirm.
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
