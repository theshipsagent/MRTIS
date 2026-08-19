# Port call structure — evidence from real Statements of Fact

Source: four SOFs supplied by William (Blue Water Shipping Company, agents).
These are ground truth for how a Mississippi River call actually runs, and
they underpin both the FGIS matching design (`docs/FGIS_MATCH_SPEC.md`) and
the still-unbuilt voyage/port-call assembly (`docs/OPEN_QUESTIONS.md` §4).

| Vessel | IMO | Berth | Cargo | Activity |
|---|---|---|---|---|
| Ultra Leopard | 9758428 | ADM-Reserve, LA | Grain (yellow soybeans) | **Load** |
| Ultra Leopard | 9758428 | Nucor Convent, LA | Iron ore | Discharge |
| Asian Eternity | 9991472 | ARTCO Buoys Mile 110 | Salt in bulk | Discharge |
| Desert Seeker | 9899208 | ARTCO Buoys Mile 121 Lower | Salt in bulk | Discharge |

## The canonical call sequence

Every SOF follows the same shape:

```
END OF SEA PASSAGE
  [optional] anchor outside SWP awaiting inbound transit slot
PILOT ON BOARD / MASTER TENDERS NOTICE OF READINESS
ENTERED SOUTHWEST PASS BREAKWATER          <-- port call STARTS (Enter)
  inbound transit, pilot changes (Pilottown / Dockside / New Orleans)
  [usually] one or more ANCHORAGE stays awaiting berthing instructions
  tugs alongside -> approaching berth -> ALL FAST      <-- berth arrival
  cargo operations (with weather / equipment / waiting standbys)
  ALL ROPES / LAST LINE RELEASED                       <-- berth departure
  [often] shift to an ANCHORAGE rather than sailing directly
DROPPED LAST OUTBOUND PILOT AT SOUTHWEST PASS          <-- port call ENDS (Exit)
```

**Enter and Exit at Southwest Pass bracket the port call.** They occur in
strict time sequence and cannot overlap — a vessel physically cannot exit and
re-enter out of order. A return two weeks later is unambiguously a new call.
In MRTIS these are the only `Enter`/`Exit` actions in the data, and they occur
at exactly one zone (`SWP Cross`); every berth-type zone uses `Arrive`/`Depart`.

## What this confirms for FGIS matching

**Cert Date = loading completed, and the sailing follows within hours.**
Ultra Leopard at ADM-Reserve:

```
May 25 1455   COMPLETED LOADING OPERATIONS - LAST GRAIN   <-- Cert Date basis
May 25 1545   NCB ISSUED CERTIFICATE OF LOADING
May 25 1645   CARGO PAPERS SIGNED
May 25 1718   ALL ROPES RELEASED FROM ADM-RESERVE          <-- the sailing
```

2h23m from last grain to ropes released — which is exactly why 66.8% of
matches land at day offset 0 and 28.2% at +1 (a completion late in the day
rolls the sailing past midnight). It also confirms the sailing, not the
arrival, is the correct anchor.

**USDA/FGIS inspection is tied to the loading, not the sailing.** The same
SOF shows `USDA INSPECTORS ON BOARD` and `USDA INSPECTORS ACCEPTED ALL HOLDS
FOR LOADING` five days before loading began, then `USDA ACCEPTED ALL HOLDS
ALONGSIDE - ELEVATOR PRE-LOAD INSPECTION` at the berth. The certificate the
FGIS feed publishes is the output of that process.

**Berth stays run longer than a 4-day assumption allows — this changed the
build.** Observed: Ultra Leopard 5.2 days (grain, ADM-Reserve), Asian
Eternity 6.2 days, Desert Seeker 8.0 days. The `Arrive` fallback window was
originally `cert-4 .. cert+1` (93.1% coverage), which would have missed Ultra
Leopard's own arrival at cert-5 — the arrival of the very grain loading this
SOF documents. **Widened to `cert-6 .. cert+1` on William's instruction,
2026-08-19** (97.9% coverage): 8 further records recovered, no increase in
ambiguous cases. The observed tail bears the SOFs out — 5 fallback matches
land at -5 and 3 at -6. The fallback applies only where MRTIS recorded no
sailing at all, so it governs 168 of 12,442 matches.

**Mid-Stream buoys are real berths served by a rig.** Both salt discharges
happened at ARTCO buoys with a floating rig brought alongside:
`STANDBY WAITING FOR RIG TO COME ALONGSIDE` -> `RIG SECURE` -> `COMMENCED
CARGO OPERATIONS`, with `STANDBY FOR RIG MAINTENANCE` interruptions. These
are genuine cargo-transfer locations, not staging — but they carry cargo
other than grain, which is why only 4.1% of Mid-Stream departures link to an
FGIS record versus 91.9% at elevators.

## What this establishes for voyage/port-call assembly

- **Draft delta is the load/discharge signal**, as hypothesized:

  | Vessel | Arrival FWD/AFT | Departure FWD/AFT | Activity |
  |---|---|---|---|
  | Asian Eternity | 12.23 / 12.46 | 4.39 / 7.08 | Discharge |
  | Desert Seeker | 11.65 / 12.45 | 4.68 / 7.45 | Discharge |
  | Ultra Leopard (grain) | 4.81 / 7.51 | 13.17 / 13.70 | Load |

- **Shifting within a berth is not a second berth call.** Ultra Leopard
  shifted forward and aft at ADM-Reserve several times over the loading
  (tugs, lines re-made, pilot on/off each time) without ever leaving the
  berth. Assembly must not read these as separate stops.
- **Anchorage stays bracket the berth on both sides.** Ultra Leopard idled at
  AMA Anchorage, shifted to LaPlace Anchorage, then berthed; Asian Eternity
  and Desert Seeker both anchored *after* discharge (Kenner Bend, Bonnet
  Carre) before eventually sailing. Anchorage events belong to the call, not
  to a separate one.
- **Loading can be capped by the destination, not the ship.** Ultra Leopard:
  `STOPPED LOADING ACCOUNT: REACHED MAXIMUM DISCHARGE PORT ARRIVAL DRAFT` --
  so a partial-looking load is not necessarily an interrupted one.
- **Master/crew change mid-call is normal** and is not evidence of a
  different vessel (see `docs/OPEN_QUESTIONS.md` §4).
