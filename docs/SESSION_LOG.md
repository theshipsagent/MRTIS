# MRTIS session log

One entry per working session, newest first. This is the file the **begin-session
ritual** reads to recover context (`/Users/billy/Documents/File Maker/00_PROJECT/RITUALS.md`).
Decisions live in `docs/OPEN_QUESTIONS.md`, code history in `CHANGELOG.md`; this
is just the thread — what was done, what was decided, what to pick up next.

---

## 2026-08-19 (session 3) — non-commercial time built; R5, §11.1a, merge guard, Egret, R2 Ro-Ro

**Objective**: build the five rulings William gave at the close of the
`mrtis-claris` review package's independent audit #2 session (recorded there,
not yet carried back here): R5 prices off the leg's first *working* berth,
layberth time moves into its own bucket instead of `berth_stop_count`/
`berth_hours`, pure lay-up calls are flagged (not deleted), an unresolved stop
outranks `No Cargo` for a leg's label, and all of it built as one general
non-commercial-time classification rather than layberth-specific logic.

Read `docs/BUILD.md`, the last two `docs/SESSION_LOG.md` entries, and
`OPEN_QUESTIONS.md` §7, §8, §11, §12, §13, §14 in full per the standing
ritual, then the handoff brief at the end of `mrtis-claris/SESSION_LOG.md`,
which carried the evidence and dollar figures behind each ruling. MRTIS
commit unchanged since the last session (`09e1cb63`).

### Method

Standing practice: scratch-copy rebuild and full reverification before
touching the real repo. Zone Report CSVs and `fgis_source/` symlinked
read-only into an isolated copy outside the repo; `scripts/`, `sql/`,
`dictionaries/`, `docs/` copied so they could be edited freely. Rebuilt the
core warehouse and FGIS match first (unmodified) to confirm the scratch copy
reproduces the known-good baseline exactly ($272,167,500 / 40,245 legs /
$349,625,500 frozen basis) before changing anything. Only then edited
`scripts/build_port_calls.py` and `sql/schema_port_call.sql`, reran the
port-call layer in the scratch copy, and cross-checked every headline figure
independently in SQL — not read out of the build's own report — before
applying the same two files to the real repo and rebuilding for real.

### Built

All five rulings landed in one pass, generalised as a single "non-commercial
time" classification (per-stop `is_non_commercial = (activity == 'No
Cargo')`), with layberth as its only current member:

1. **R5 prices off the leg's first *working* berth** (§12.3.3.1 amended).
   `head`/`zinfo` for a leg — which feed both `port_call_leg.facility_type`
   and the `agency_fee_for()` call site — now come from the leg's first
   non-layberth stop, falling back to the literal first stop only when every
   stop is layberth.
2. **`berth_stop_count`/`berth_hours` exclude layberth**, on both
   `port_call_leg` and `port_call`; a new `layberth_hours` column (leg total
   and call total) carries the excluded time instead of dropping it.
3. **Pure lay-up calls are flagged, not deleted**: `port_call.
   is_commercial_call` / `call_class = 'layup'`. Rows, events and SWP-to-SWP
   timestamps stay on the spine exactly as before.
4. **An unresolved stop outranks `No Cargo` for the leg's label** (§11.1a).
   `build_frames()`'s leg-activity resolution now checks real activity, then
   any unresolved stop, then (only if nothing else exists) `No Cargo`.
5. Two new hard guardrails (call-level `berth_stop_count`/`layberth_hours`
   reconcile to the sum of their legs'; no fee accrues on a non-commercial
   call) plus a soft report line for non-commercial calls.

### Found, while verifying — the `mrtis-claris` audit's own R5 estimate was incomplete

Independent re-derivation in SQL (before and after the code change, using a
throwaway copy of the unmodified script against the same rebuilt database)
found the handoff brief's **"80 legs, +$440,000"** R5 estimate undercounts.
That figure enumerated only 5 of the 14 `ops = Layberth` zones by name
(Poland St, Perry Street, Buck Kreihs, Alabo St, Esplanade Ave) and never
counted the five Violet Dock zones, despite the same write-up's own text
confirming all 14 carry `facility_type = General Cargo`. The real population
is **107** chargeable Bulk legs with a layberth first stop (not 80): **93**
genuinely revert to the $10,500 base tier (**+$511,500**, not $440,000), and
**14** stay at $5,000 because their first *working* berth is also,
legitimately, a non-layberth General Cargo zone (e.g. Chalmette Slip, 7th
Street, Globalplex) — those 14 are a correctness fix to *what's reported*,
not to the amount. Full reconciliation, including the 6-leg overlap with
ruling 4 (§11.1a), is in `OPEN_QUESTIONS.md` §12.3.3.1. The "389 stops / 379
legs / 45,742 hours preserved" layberth-footprint figures, by contrast, were
re-derived exactly as given once measured the same way the code measures them
(dwell-bearing stops, not `layberth_hours > 0`) — that estimate held.

### Verified (all guardrails pass; every figure independently re-derived in SQL)

| Figure | Before | After |
|---|---:|---:|
| Billable total | $272,167,500 | **$272,660,000** (see the two later rulings below) |
| Per-departure basis (§12.3.4) | $349,625,500 | **$349,527,500** — see the Egret note below |
| Port calls | 40,170 | 40,170 total, **40,028** commercial (142 flagged) |
| Legs | 41,804 | 41,804 total, **41,662** commercial |
| `No Cargo` legs billing | 54 / $281,750 | **0** labelled so (all relabel to unresolved); fee for that population is $314,750, not $281,750, because 6 of the 54 also fall inside the R5 fix above |
| Lay-up time preserved | — | **23,390 hrs / 974.6 (≈975) vessel-days**, all 142 calls `call_status = 'complete'` |
| Layberth reallocated | — | **45,742 hrs, 389 stops, 379 legs** off `berth_hours` into `layberth_hours` |
| Spine rows | 290,436 | **290,305** (Egret exclusion, below) |

The R5 fix alone took the billable total to $272,679,000; the R2 Ro-Ro
ruling below then took it to **$272,660,000**, which is the figure this
session ends on.

### Then, same session — four more rulings, and a moved benchmark

William worked through the remaining open questions in the same
conversation. Four needed no build (§7.2 Kennington stays excluded; §11.2
leave the unplaced-events gap as is; §11.4 no fee, existing rule is right;
§13.1 reconfirmed discharge-only with no buoy-style positional exception).
Four did:

- **§12.3.2** — `General Cargo Ship (with Ro-Ro facility)` → R2's $1,000
  (*"a roro is a port call"*). Only **2** of its 5 chargeable legs actually
  move; the other 3 are Bulk-canonical vessels at General Cargo berths that
  R5 already priced at $5,000, since R5 outranks R1-R4. **−$19,000.**
- **§7.3** — name-only dredge exclusions (the path that wrongly deleted
  T Jungfrau, Heino and Corinthian) now write
  `dictionaries/dredge_name_only_review.csv` instead of dropping silently:
  23 names, 8 forming a complete `Enter → Exit` transit. Scaled to the
  lighter end of what William asked for (*"a quarantine table... or if
  that's too much also fine leave as is"*) — mirrors the existing
  `fgis_match_review.csv` pattern rather than adding a mechanism. **No data
  change**; a review artifact only.
- **§7.5** — the IMO-repair merge guard, approved and built. Repairs
  **31 → 30**, repaired rows **256 → 125**. The one blocked merge is exactly
  the Egret false merge audit #1 found; the other 30 are byte-identical.
- **§2/§7.5** — Egret then excluded at ingest outright. Added **by IMO
  (`17825854`), not by name**: two vessels in this data are named exactly
  `Egret` and the other (`9747120`) is a real tanker with 10 river crossings.
  It survives, as do `Egret Bulker`, `Egret Oasis`, `Hafnia Egret` and
  `NONAME:EGRET`.

**The per-departure benchmark moved, and it matters downstream.**
$349,625,500 → **$349,527,500**, exactly the $98,000 fabricated Egret fee.
This does not contradict §12.3.4: "frozen" fixes the *pricing schedule* on
that basis, not the number regardless of what data exists. But that figure
is cited throughout these docs and is a headline reconciliation target in
the `mrtis-claris` package — anything checking the old value will report a
false mismatch until re-exported. The **billable basis, port-call count and
leg count did not move** ($272,660,000 / 40,170 / 41,804): all 131 Egret rows
were unplaced events reaching no call and no leg, precisely as audit #2
predicted.

**§10** (stable `vessel_key`/`event_key`) was approved — *"suggest best
practice"* — and a concrete recommendation is written into
`OPEN_QUESTIONS.md` §10, deliberately **not built**: it is a schema-wide key
change touching every downstream FK and wants its own session. The one trap
recorded there: do not use Python's built-in `hash()`, which is salted
per-process and would silently reintroduce the very instability being fixed.

**§11.3** (`tpc = 0`) was investigated but deferred by William (*"leave TPC
for now, will triage later"*). Worth recording what the investigation found,
since it redirects the eventual fix: this is **not** an MRTIS pull gap. All
1,110 affected vessels are matched in the register, and
`ships_register_fleet.csv` itself stores a literal `0` for every one. The
fix, when it happens, starts in `Ships_Register`, not here.

### Decided without stopping to ask (technical, not a business-rule fact)

- **§13.1 stays deferred to phase 2**, not bundled with this session's build.
  It touches the same 14 (soon 29) General Cargo zones but a different
  decision (activity resolution, not which stop is "first working"); the two
  code paths are independent and this session's verification holds regardless
  of §13's build state. Reasoning recorded in `OPEN_QUESTIONS.md` §13's
  "Still open" note for confirmation.
- Trusted the re-derived database over the `mrtis-claris` audit's own
  write-up where the two disagreed (the R5 population above), consistent
  with this project's standing practice of re-deriving every claim rather
  than reading it out of a prior report.

### Docs updated

`OPEN_QUESTIONS.md` §2/§7.5 (merge guard + Egret exclusion, both built),
§7.2 (Kennington stays excluded), §7.3 (name-only review CSV, built), §7.4
(ruled, Gen=Bulk, no figure moves), §8 (extended/generalised into the
non-commercial-time classification), §10 (stable-key recommendation,
approved not built), §11.1 (ruled (a), built, figure corrected $413,000 →
$281,750-then-$314,750 with the overlap explained), §11.2 (leave as is, with
the gap's shape measured), §11.3 (`tpc = 0` traced upstream to
`Ships_Register`, deferred), §11.4 (no fee, confirmed), §11.5 (schema
comment's stale "~12%" replaced with a pointer to the auto-generated report
instead of another number that will drift), §12.3.2 (R2 Ro-Ro, built),
§12.3.3.1 (the first-working-berth amendment, built, with the corrected
$511,500/93-leg figures and the audit-undercount finding), §12.3.4 (the
benchmark's new value and why), §13.1 (reconfirmed, no exception), §14 (pure
lay-up exclusion answers one sub-question); `PORT_CALL_SPEC.md` §3 (refreshed
resolution percentages), §4 (rewritten "non-commercial time" section), §6
(`layberth_hours`); `sql/schema_port_call.sql` (new columns, corrected
comments); `scripts/lib/parse.py` (merge guard);
`dictionaries/dredge_exclusions.csv` (Egret). Sample CSVs in the working tree
(`sample_port_calls*.csv`) predate this session and were not regenerated — no
`--sample`/`--sample-months` flag was passed, so they are now stale against
this build.

### Next session starts by

Carrying this back to `mrtis-claris`. Its `SESSION_LOG.md` handoff brief is
superseded on **two** counts now: the R5 figures ($272,660,000 final, 93
legs — not $272,607,500 / 80 legs), and the per-departure benchmark
($349,527,500, not $349,625,500), which that package cites as a headline
reconciliation target. Re-export, re-run charts and reports, and clear audit
#2's A4-A14 documentation findings while there.

**§14 (per-agent port-call counts) is the next build**, at William's
request — one count per port call, two on a genuine discharge→load split,
each leg to its own agent, filtering `is_commercial_call` by default (the
pure-lay-up sub-question is now answered; the fee-bearing-only scope
question is not).

A **third audit** was flagged as due once §12 and the §11 rulings landed;
both have now happened — due whenever William wants to size it, and it now
has more surface to cover than when it was first raised. **§10** (stable
keys) is approved with a recommendation written up and wants its own
session. §13/§13.4 stay phase 2. Remaining genuinely open: §11.3's upstream
`Ships_Register` fix, §14's scope, and §3/§4/§5's older items.

---

## 2026-08-19 (session 2) — §12 fee schedule built and deployed; missing tier guardrail added

**Objective**: rule on and build the revised agency fee schedule captured
(not built) at the end of the previous session's audit — `docs/OPEN_QUESTIONS.md`
§12 — and, per the hard ordering constraint from audit #2 §5, add the missing
guardrail before touching the tiers at all.

### Decided (by William, all 2026-08-19 — see OPEN_QUESTIONS §12.3, §13, §14)

1. **§12.3.1**: register `ship_type` is authoritative for R1-R4; the canonical
   Zone-Report type is the fallback only for vessels with no register row.
2. **§12.3.3**: R5 (dry bulk at a General Cargo berth) is priced by the leg's
   **first** berth, not any berth or the last berth touched — reached after
   several rounds of clarification. The billing unit stays the port call
   regardless of how many berths are visited (explicitly confirmed for
   tankers, which can call 4-6 berths in one visit); the only thing that ever
   produces a second fee within one port call remains the existing
   discharge→load agent-turnover split, unchanged from §7/§8. "Dry bulk" =
   `vessel_type_canonical = 'Bulk'`, same as the base tier. R5 outranks R1-R4
   if a vessel ever satisfies both (currently impossible).
3. **§12.3.4**: the per-departure comparison basis (`fact_zone_event.agency_fee`)
   stays frozen at the pre-ruling two-tier schedule; only the leg basis
   (what actually bills) gets the six new rules.
4. **§13 (new, raised this session)**: General Cargo berths are discharge-only
   by dictionary; a buoy stop before a confirmed Load is Discharge by
   elimination; two ambiguous buoy stops in sequence are Discharge-then-Load
   by position (empirically checked against draft delta before accepting —
   122 confirming, 0 contradicting, 162 with no signal either way, 23 genuine
   exceptions attributed to rare weather/evacuation interruptions and
   deliberately not scripted for — flagged by a guardrail instead, human
   reviewed before reports print). **Ruled but explicitly parked to a "phase
   2"**, along with §13.4 (buoy cargo_group) and §14 (per-agent call counts,
   raised, timing/scope still open) — none of these are built.

### Done

- **The missing guardrail** (audit #2 §5: nothing asserted a fee matches its
  vessel's tier) — added to `validate()` in `build_port_calls.py`, verified in
  a scratch copy to actually catch an injected mismatch, then applied. This
  landed *before* the tier change per the session's hard ordering constraint.
- **§12's six-rule fee schedule** — `SHIP_TYPE_FEE_TIERS`,
  `CANONICAL_FEE_FALLBACK`, `AGENCY_FEE_BULK_GENERAL_CARGO` added to
  `build_db.py`; `agency_fee_for()` extended with optional `ship_type`,
  `facility_type`, and `apply_2026_tiers` parameters so every existing caller
  is unaffected unless it opts in.
- **A real bug caught mid-build, before the database ever saw it**: the first
  version inferred "apply the new tiers" from whether `ship_type` was passed,
  which silently leaked the R1/R3/R4 canonical fallback into the frozen
  per-departure basis (`build_db.py`'s own call never passes `ship_type`),
  moving it from $349,625,500 to $339,708,750. The tier guardrail didn't
  catch it — it made the same mistake independently. Only cross-checking
  against the known historical total caught it. Fixed with an explicit
  `apply_2026_tiers` flag defaulting to `False`.
- Verified in a scratch copy per standing practice, full chain rebuilt
  (`build_db.py` → `build_fgis_match.py` → `build_port_calls.py`), all
  guardrails pass, figures matched exactly against independently re-derived
  SQL before being trusted. Then rebuilt for real.
- `docs/OPEN_QUESTIONS.md` §12 (all sub-decisions, marked built and verified),
  §13, §14 written up in full.

### Found

- Confirmed `github.com/theshipsagent/MRTIS` is a **public** repository.
  William's call: the fee figures are modeled/estimated, industry-standard
  numbers, not confidential or sensitive in any capacity — no action needed.

### Figures — both re-derived independently from the database, not read out of the build's own reporting

- **Leg basis (what bills): $298,868,500 → $272,167,500** (−$26,701,000,
  −8.9%) over 40,245 chargeable legs.
- **Per-departure basis: $349,625,500**, unchanged.
- Every guardrail passes, including the new "fee matches its vessel's tier"
  at 0 mismatches.

### Next session starts by

**Phase 2**, if/when William wants to size it: §13's build (General Cargo /
buoy activity-resolution rules — reaches split detection, so needs its own
scratch rebuild and reverification of every downstream figure before
trusting it), §13.4 (buoy cargo_group), §14 (per-agent call counts — timing
and scope still undecided). A **third audit** was already flagged as due
once §12 and the §11 rulings land; §11 (54 `No Cargo` legs that bill anyway,
and §11.2-11.4) was not reached this session and is still open.

Separately, William raised wanting a Claris/FileMaker clone. Confirmed the
repo is public (`github.com/theshipsagent/MRTIS`) but William's call is that
the fee figures are modeled/estimated and not sensitive — no action taken.

Scoped and set up before this session closed: **`mrtis-claris`**, a new
private repo (`github.com/theshipsagent/mrtis-claris`), Phase 1 only — a
FileMaker-importable export of the validated port-call/fee data (following
the pattern already proven in `Ships_Register/src/build_filemaker_package.py`),
a plain-language business-rules spec, sample charts, and sample reports, for
review — not a native FileMaker rebuild. Scoped this way specifically to sit
underneath the two existing, more mature FileMaker projects
(`/Users/billy/Documents/File Maker/` — the internal platform
evaluation/redesign, Phase 1, no build yet; `/Users/billy/Documents/File Maker Analysis /`
— the independent audit of Blue Water Shipping's live Agency Platform) rather
than jump ahead of either's own "no build yet" governance. `README.md` and
`CLAUDE.md` written and pushed; William starts the actual build in a fresh
session there, recommended on Claude Opus 5 given the cross-project synthesis
and real architectural calls that session will need to make on its own.

---

## 2026-08-19 — Ships register refreshed to the full world-fleet pull

**Objective**: §9 (extending the register to tankers/gas/containers) — check
whether the separate `Ships_Register` world-fleet expansion already
supersedes the chunk-pull plan before pulling from Sea-web again.

### Found

It does. William confirmed the expansion is done and the vessels are there.
`Ships_Register/data/out/ships_register.duckdb`'s `fleet_joined`: 49,763
rows, 133 `ship_type_group` values, zero blanks, zero null/duplicate IMOs —
exactly the shape recorded when that expansion happened. The chunk-pull plan
prepared for this session's §9 was never needed and was never run.

### Done

- Refreshed `dictionaries/ships_register_fleet.csv` from `fleet_joined` (the
  documented `docs/BUILD.md` procedure). Rebuilt the full chain
  (`build_db.py` → `build_fgis_match.py` → `build_port_calls.py`); all
  guardrails still pass.
- Match coverage against `dim_vessel`: **60.9% → 99.4%** of all vessels
  (**61.1% → 99.8%** of those with a valid IMO) — matches what was predicted
  when the world-fleet expansion happened. `dwt`/`tpc` now populate 99.7% of
  port calls, up from roughly 60%.
- 24 vessels with a valid IMO remain unmatched — the same known set as
  before (13 checksum-invalid, 10 behind the pre-1980 gate, 1 genuinely
  absent). No further pull needed.
- `docs/BUILD.md` "Ships register enrichment" rewritten (the dry-cargo-scope
  paragraph was now doubly stale); `OPEN_QUESTIONS.md` §9 marked resolved,
  original chunk-pull plan kept for the record.
- **Corrected an earlier same-day claim**: the `ship_type_group` backfill did
  *not* fix *Radcliffe R. Latimer*'s billing tier as I'd said — its Zone
  Report `Type` was already `Bulk`, which decides the fee before
  `ship_type_group` is ever consulted. Caught while verifying this refresh.
- **Raised, not fixed**: `vessel_key`/`event_key` are row-position, so this
  refresh — which touched only register enrichment — still forced a full
  FGIS + port-call rebuild from zero. Logged as `OPEN_QUESTIONS.md` §10.

### Decided (by William, 2026-08-19, closing the session)

- **§9 residual, closed, no change**: the two 1978 merchant vessels (Sunnanvik,
  Radcliffe R. Latimer) stay behind Sea-web's pre-1980 build-year gate. Both
  bill correctly regardless; they simply carry no `dwt`/`tpc`.
- **My earlier correction (the `Radcliffe R. Latimer` billing-tier claim)**:
  acknowledged, no further action.

### Open

- **§10**: is a stable, natural-key-derived `vessel_key`/`event_key` worth
  the schema change, given the register/dictionaries/feed all get revised on
  an ongoing basis and each one currently pays for a full downstream rebuild?
  Raised, not scoped, not started.

### Next session starts by

§7, §8 and §9 are fully resolved, nothing pending from either side. §10
(stable keys) is the only open thread, raised but deliberately not scoped —
pick it up only if/when William wants to size it. A fresh independent audit
is also due: the last one (`docs/audit/AUDIT_2026-08-19_0242.md`) predates
this entire session's work (tonnage rename, ship_type/backfill, §8 fee logic,
the world-fleet register refresh) — none of it has been adversarially
checked yet.

---

## 2026-08-19 — §8 resolved: layberth stops don't split and don't bill

### Decided (by William, 2026-08-19)

- **8a.** A layberth stop can't open a leg boundary — confirmed the earlier
  recommendation. Treated exactly like an unresolved stop for splitting.
- **8b.** "Also no fee on departing a layberth" — resolves 8b directly: a leg
  bills only if it did real, non-layberth work somewhere. A pure lay-up leg
  (nothing but layberth stops) accrues nothing.

### Done

- `split_into_legs()` and the leg-fee computation in
  `scripts/build_port_calls.py` updated; also had to fix the leg's own
  `activity`/`method` selection, since a leg can now legitimately mix a
  layberth stop with a real one (real activity always wins the leg's
  reported label over `No Cargo`).
- Rebuilt and reverified: split calls 4.4% → 4.1%, ruling-basis fee
  $304,808,000 → $298,868,500 (-$5,939,500). Checked the remaining 54 billed
  `No Cargo`-labeled legs individually — each genuinely touches a second,
  non-layberth berth (unresolved activity, e.g. bunkers/refinery), so they
  bill correctly and unchanged; the 142 genuinely pure lay-up legs are $0.
- `docs/OPEN_QUESTIONS.md` §8, `docs/PORT_CALL_SPEC.md` §4/§9 updated.

### Next session starts by

**OPEN_QUESTIONS §9** — extending the register to tankers/gas/containers;
check first whether the separate `Ships_Register` world-fleet expansion
already supersedes the chunk-pull plan recorded there.

---

## 2026-08-19 — Tonnage naming fix and ship-type gap-fill

**Objective**: review the six-month port-call sample; fix two data-integrity
gaps William spotted while reviewing it.

### Decided (by William, 2026-08-19)

1. **FGIS tonnage is `estimated_tons`, not `actual_tons`.** His original
   mapping (`docs/FGIS_MATCH_SPEC.md`) already said "Metric Ton → estimated
   tons" — the port-call build had implemented it as `actual_tons` instead.
   Corrected across `port_call`/`port_call_leg`/`port_call_event`. A genuine
   `actual_tons` column is added alongside, NULL until a real source exists;
   promoting a leg from estimated to actual is future work, not inferred here.
2. **`ship_type` (the register's raw type/family) is added**, and where a
   register family has no size vocabulary — so `ship_type_group` would
   otherwise be NULL — `ship_type_group` is backfilled from `ship_type`.
   "Gaps are worse than variances in convention." 16 vessels in the
   then-current register snapshot gain a group this way. **Correction, same
   day**: I originally claimed this fixed *Radcliffe R. Latimer*'s (IMO
   7711725) billing tier -- wrong, its `vessel_type_canonical` is already
   `Bulk` from the Zone Report's own Type field, so `agency_fee_for()` never
   even reached `ship_type_group` for it. Caught while verifying the
   world-fleet register refresh (see the entry above this one).

### Done

- Schema, `build_db.py` and `build_port_calls.py` updated; full chain
  rebuilt (`build_db.py` → `build_fgis_match.py` → `build_port_calls.py`),
  all guardrails still pass, fee/tonnage totals unchanged except the one
  vessel above.
- Fixed an unrelated pre-existing bug found while rebuilding: `build_db.py`
  crashed on every run after the FGIS/port-call drop message (`had_port_calls`
  computed but never returned).
- `docs/PORT_CALL_SPEC.md` §7 and `docs/BUILD.md` "Ships register enrichment"
  updated. Six-month and scenario samples regenerated.

### Next session starts by

Same as before this fix: extending the register to tankers/gas/containers
(**OPEN_QUESTIONS §9** — check first whether the separate `Ships_Register`
world-fleet expansion already supersedes the chunk-pull plan there), and
deciding **§8** (does a `No Cargo` leg open a split / bill at all).

---

## 2026-08-19 — Port call assembly layer

**Objective**: build the port call assembly layer, and produce a specific output
table for the matched and transformed data with the raw MRTIS event as the spine.

### Done

- **`port_call_event`** — the deliverable. One row per `fact_zone_event`
  (290,436, always), source values preserved in `src_*` columns beside every
  canonical one. Plus `port_call` (40,170) and `port_call_leg` (41,985).
- **`scripts/build_port_calls.py`** + `sql/schema_port_call.sql` +
  `docs/PORT_CALL_SPEC.md` (the rules) and `docs/PORT_CALL_QUALITY.md`
  (auto-generated health of each run).
- **Build guardrails** (`scripts/lib/guardrails.py`) — hard invariants abort the
  build before anything is written; soft checks report source health. Caught two
  real bugs during the session before either reached the database.
- **Six-month sample** exported for review at all three grains
  (`sample_port_calls_6mo*.csv`, 2,899 calls / 3,018 legs / 18,104 events).
- **`docs/BUILD.md` corrected**: the ships-register gap is not "largely
  passenger/cruise" (0.9%) — it is 80% tankers.
- Ten commits, pushed to `origin/main`.

### Decided (by William, all 2026-08-19 — see OPEN_QUESTIONS §7, §9)

1. **Agency fee is per port call, not per berth — except a split discharge-then-
   load, which is two.** The billing unit is therefore the operational leg.
   Implemented: **$304,808,000** over 41,334 chargeable legs, against
   $349,625,500 over 48,167 berth departures on the old basis (−12.8%). The old
   basis is preserved alongside for comparison.
2. **The canonical facility is the unit, not the zone.** Two berths of one
   elevator are one visit. Zen-Noh Upper → Zen-Noh Lower is a shift.
3. **Only the first docking and the last sailing of a visit count.** Overlapping
   geofences and movement in berth produce false hits; a large ocean vessel does
   not dock, sail and redock in minutes. 5,102 berth events (5.3%) collapse as
   artefacts — kept on the spine, flagged, simply not read as operations.
4. **The dictionary outranks the AIS draft** where a facility can only do one
   thing. A grain elevator loads. The draft is a pass, not a decider: Elevator,
   Bulk Cargo and LNG resolve 100% from the dictionary with zero draft
   involvement.
5. **Only dry-cargo vessel types split.** Tankers, gas, cruise, container,
   reefer and other are not eligible; Bulk and no-recorded-type are. Splits fell
   2,874 → 1,787 (4.4% of calls).

### Found

- **Do not scrape VesselFinder.** Its own terms forbid building a dataset from
  it (§5.3, §7.4, §9), and the premise fails anyway: the test vessel *Ireland*
  (9770543) is already in the register with a TPC that VesselFinder does not
  publish for any ship. Equasis and MarineTraffic are more restrictive still.
- **`ship_type_group` is a pure step function of DWT within family** — zero
  monotonicity violations across 18,752 register rows. The size class can be
  derived rather than stored or scraped. Careful: the *published* industry bands
  overlap and would corrupt the register's clean partition; use the register's
  own cut points.
- **The register gap is a scope boundary, not a coverage failure** — the
  vocabulary holds only Bulk Carrier and General Cargo families.

### Open

- Does a `No Cargo` leg accrue a fee? 421 legs, $3,783,500.
- Dictionary `ops` blanks now drive the 7,139 unresolved legs, and each fill is
  decisive rather than advisory. Cruise berths (Erato St, Julia St) are the
  clearest — a cruise ship works no cargo.
- 853 legs where the draft contradicts the dictionary. Dictionary wins; listed
  by facility in the quality report in case a row needs widening.
- TPC probably needs a ruling of its own: captured per call from hydrostatic
  tables, versus estimated by formula and flagged. The column is 63.6% populated
  and the two should not be silently mixed.

### Next session starts by

Reviewing `sample_port_calls_6mo.csv`, then extending the Sea-web pull to
tankers/containers/gas — see **OPEN_QUESTIONS §9**, which has the sizing and the
upload chunks already prepared.

---

## Session — 2026-08-19 (evening): independent audit #2, port-call assembly layer

**Read-only. Nothing was fixed.** Commissioned as an adversarial audit of
everything built since audit #1: `git log 13937b9..HEAD`, 13 commits — the whole
port-call assembly layer plus a session of rulings on top of it.

Method per standing practice: full chain rebuilt (`build_db.py` ->
`build_fgis_match.py` -> `build_port_calls.py`) in an isolated scratch copy with
the Zone Reports and `fgis_source/` symlinked read-only. Every claim re-derived
in SQL from the rebuilt database rather than read out of the build's own
reporting. Real repo verified byte-identical afterwards (74-file SHA-256
manifest + `git status`).

Output: `docs/audit/AUDIT_2026-08-19_1746.md` / `.pdf`. Questions routed to
`OPEN_QUESTIONS.md` §11.

### What held

- **Every count and every dollar reproduces exactly.** 290,436 / 40,170 /
  41,804; $298,868,500 over 40,245 legs; $349,625,500 over 48,167 departures.
  Both bases close in closed form from the tier counts with no rounding slack.
- **All 18 hard guardrails independently re-derived in SQL** — not trusted,
  rewritten — and all 18 confirmed. Six further invariants the build does not
  check were tested and also hold.
- **The build is deterministic**: a cold rebuild reproduced the shipped
  `PORT_CALL_QUALITY.md` byte-for-byte bar the timestamp.
- **§8a is exactly right.** `No Cargo` never opens a leg boundary — 0 occurrences
  across all 1,632 split calls. Verified end to end on a real
  Discharge -> layberth -> Load call (`9757527-202606191251`, PCS Nitrogen ->
  Perry Street -> ADM Destrehan): splits on the Discharge/Load boundary, layberth
  joins leg 1.
- **The per-departure basis really is untouched by both rulings.** Proved by
  rebuild, not by inspection: §8 changed it on **0 of 40,170 calls**; §9 changed
  the fee tier of **0 of 10,211 vessels** (the old register was recovered from
  git and `agency_fee_for()` re-evaluated under both).
- **§9's figures are exact**, including the 24 unmatched IMOs verified
  individually — 13 checksum-invalid, 10 in the source's own
  `quarantine_pre1980`, 1 (9493523 *Stena Premium*) genuinely absent.
- **The tonnage rename is complete.** `actual_tons` NULL on all three tables, no
  code path can write it; `estimated_tons` conserves FGIS tonnage exactly
  (469,416,219 + 4,381,969 = 473,798,188 t).
- **Audit #1's open findings do not reach the billing layer.** All 131 Egret
  workboat rows are *unplaced* — they carry no `Enter`/`Exit`, so no call can
  open for them. All 5 assembled calls belong to the genuine tanker. The
  fabricated $98,000 reaches no leg and no call.
- **The Radcliffe R. Latimer retraction was correct**, for the reason given:
  `vessel_type_canonical = 'Bulk'` fires on branch 1 of `agency_fee_for()` and
  `ship_type_group` is never read. $0 impact. (New detail: 7711725 is no longer
  in the register at all after §9.)

### What did not hold — $6,604,500 mis-stated or unreconciled

None of it is money billed at the wrong *rate*. All of it is labelling,
reconciliation or reporting.

- **W1 (Medium, $413,000, 54 legs).** Legs reporting `activity = 'No Cargo'`
  that bill in full. The documented rule — "only a leg with nothing but layberth
  stops reports No Cargo" — is false: an *unresolved* stop is falsy so it cannot
  win the label, but `None != 'No Cargo'` is True so it does trigger the fee. The
  label and the money disagree. 7 such legs also assert a `cargo_group`; 20
  report the layberth as their berth while billing for work elsewhere.
- **W2 (Medium, $3,258,500, 313 calls).** "$413,000, unchanged from before this
  fix" is not like-for-like — §8 changed leg membership (41,985 -> 41,804 legs,
  1,787 -> 1,632 splits), so that cohort did not exist before. Proved by
  rebuilding at `c208a67^`. The real §8 movement — $302,127,000 -> $298,868,500
  — is stated nowhere.
- **W3 (Medium, $2,933,000).** `agency_fee_departures_total` sums to
  $346,692,500, not $349,625,500; the 388 fee-bearing unplaced events reach no
  call. Over-bill reads 17.0% or 16.0% depending on denominator. $98,000 of the
  gap is audit #1's Egret fee.
- **W4-W8 (Low).** Backfill lacks its stated guard (inert today, latent
  $7,000/leg); schema says ~12% over-bill, actual 17.0%; "3 ambiguous FGIS
  records" is 1 record / 3 lines; `tpc = 0` on 4,045 calls (10.1%) hidden behind
  "99.7% populated"; §9 silently lost 2 register matches.

### The structural lesson

The 18 guardrails are all **shape** checks — uniqueness, referential integrity,
set equality, ordering, sum-of-parts. Not one asserts that a *value* is right.
Two of the six value-level gaps are already failing silently (W1, W3).
`no cargo is asserted without a source` shows the pattern: it verifies
`cargo_source` is populated, which is provenance, and passes cleanly on a leg
asserting `Liquid Bulk` alongside `activity = 'No Cargo'`.

### Also raised this session — new fee tiers, not built

William gave a revised fee schedule at the end of the session
(Passenger/Cruise $2,500; Ro-Ro/Vehicles Carrier $1,000; Container Fully
Cellular $750; Refrigerated Cargo Ship $5,000; dry bulk at a General Cargo
berth $5,000). **Deliberately not implemented** — captured and scoped in
**OPEN_QUESTIONS §12** for a fresh session.

Indicative impact: **-$26,701,000 (-8.9%)**, $298,868,500 -> $272,167,500.
R5 (bulk at a General Cargo berth) is 64% of that on its own.

Three things make this more than a table edit, all written up in §12:

- **The names are register `ship_type` values, not `vessel_type_canonical`.**
  `agency_fee_for()` reads the canonical type first and returns, so as written
  today these rules would fire on **9 legs out of 4,211** — priority 1 catches
  the rest. Either the rules move onto the canonical vocabulary (but then
  Ro-Ro/Vehicles Carrier becomes unexpressible) or the priority order changes.
- **R5 is the first berth-dependent fee.** Every fee to date is priced by the
  vessel by explicit design. Which berth decides on a multi-stop leg is a
  217-leg / $2.28M question on its own.
- **Three of the six named types have zero traffic** (Ro-Ro Cargo Ship,
  Vehicles Carrier, Container FC/Ro-Ro Facility). The nearest real thing is
  `General Cargo Ship (with Ro-Ro facility)` — 5 chargeable legs — and whether
  it is covered is ambiguous.

Note the ordering risk: audit #2 §5 found there is **no guardrail asserting a
fee matches its vessel's tier**, so a mistake in this change would not be caught
by anything. That guardrail should land before the tiers move.

### Next session starts by

Ruling on **OPEN_QUESTIONS §12.3.1** — whether the new fee tiers key off the
register's raw `ship_type` or the canonical vessel type. Everything else in §12
depends on it, and the schedule cannot be built until it is settled.

Then **§11.1** from the audit (what a leg should report when it mixes a layberth
stop with an unresolved working berth — $413,000 across 54 legs labelled
`No Cargo` while billing in full), and §11.2-11.4, which are cheap once §11.1 is
settled.

A **third audit** is expected once the §12 fee schedule and the §11 rulings are
implemented.
