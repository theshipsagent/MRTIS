"""Parsing helpers for raw Zone Report fields.

Kept separate from build_db.py so they can be unit-tested and reused by
future ingestion scripts (e.g. a laytime or tariff loader).
"""

import re

IMO_DIGITS_RE = re.compile(r"^\d+$")
DRAFT_RE = re.compile(r"^(\d+)ft$")
MILE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)M$")


def canonical_imo(raw: str):
    """Confirmed rule (William, 2026-08-18): keep only the first 7 digits of
    the raw value, drop anything after position 7 -- applies uniformly
    whether the raw value is 8 or 9 (or more) digits, no special-casing by
    length. If the raw value has fewer than 7 digits, there is no usable
    IMO: return None (caller falls back to name-based vessel identity).
    Validated against real data: raw '950597400' -> '9505974' (Nordic Aki),
    confirmed correct.

    Returns the 7-digit canonical IMO as a string, or None."""
    if raw is None:
        return None
    raw = raw.strip()
    if not IMO_DIGITS_RE.match(raw):
        return None
    if len(raw) < 7:
        return None
    return raw[:7]


def is_valid_imo(raw: str) -> bool:
    """True if `raw` yields a usable canonical IMO under canonical_imo()."""
    return canonical_imo(raw) is not None


def parse_draft(raw: str):
    """'42ft' -> 42. Returns None if blank/unparseable."""
    if not raw:
        return None
    m = DRAFT_RE.match(raw.strip())
    return int(m.group(1)) if m else None


def parse_mile(raw: str):
    """'134M' -> 134.0, '-19M' -> -19.0. Returns None if blank/unparseable."""
    if not raw:
        return None
    m = MILE_RE.match(raw.strip())
    return float(m.group(1)) if m else None


def natural_vessel_key(imo_raw: str, name: str) -> str:
    """Stable identity for a vessel across events. Prefer the canonical
    (truncated) IMO; fall back to a normalized vessel name when no usable
    IMO exists, since many tugs and barges are tracked by name only in
    this data source."""
    imo = canonical_imo(imo_raw)
    if imo:
        return imo
    return "NONAME:" + (name or "").strip().upper()


ZONE_GROUP_RULES = [
    (re.compile(r"anch", re.I), "Anchorage"),
    (re.compile(r"buoy", re.I), "Buoy Range"),
    (re.compile(r"cross", re.I), "Crossing"),
    (re.compile(r"slip", re.I), "Slip"),
    (re.compile(r"bend", re.I), "Bend"),
]


def classify_zone_group(zone_name: str) -> str:
    """Heuristic classification of a zone name into a coarse group.
    Not authoritative -- intended as a starting point for reporting/rollups.
    Refine ZONE_GROUP_RULES (or override individual dim_zone.zone_group rows
    after load) as you learn more about specific zones."""
    for pattern, group in ZONE_GROUP_RULES:
        if pattern.search(zone_name or ""):
            return group
    return "Terminal / Berth"


# Vessel-name normalization for cross-source matching (FGIS <-> MRTIS).
#
# Deliberately NOT a fuzzy/edit-distance match. Confirmed with William
# 2026-08-18: "DSL Phoenix" / "D.S.L. Phoenix" are the same vessel, but
# "DSI Phoenix" is a genuinely different real vessel that also calls the
# river. DSI and DSL are one character apart, so any tolerance for
# single-character edits would merge two real vessels. Punctuation and
# spacing get normalized away; nothing else does.
#
# Note this strips punctuation entirely rather than replacing it with a
# space -- replacing with a space (the original spec's wording) turns
# "D.S.L. Phoenix" into "D S L PHOENIX", which does NOT equal "DSL PHOENIX"
# and so fails the exact case the rule exists to handle.
VESSEL_NAME_PREFIX_RE = re.compile(r"^(?:M\s*[/\\.]?\s*V|M\s*[/\\.]?\s*T|SS)\b\s*", re.I)
NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def normalize_vessel_name(name: str) -> str:
    """Canonical form of a vessel name for cross-source equality matching.

    Uppercases, drops a leading vessel-prefix ('M/V', 'M.V.', 'MV', 'M/T',
    'SS'), then removes every non-alphanumeric character:

        'D.S.L. Phoenix'    -> 'DSLPHOENIX'
        'DSL Phoenix'       -> 'DSLPHOENIX'   (same vessel, matches)
        'DSI PHOENIX'       -> 'DSIPHOENIX'   (different vessel, stays apart)
        'M/V Sider Madeira' -> 'SIDERMADEIRA'
        'GNG CONCORD 2'     -> 'GNGCONCORD2'

    Returns '' for blank/None input.
    """
    if not name:
        return ""
    s = VESSEL_NAME_PREFIX_RE.sub("", name.strip())
    return NON_ALNUM_RE.sub("", s.upper())


def imo_check_digit_valid(imo: str) -> bool:
    """True if a canonical 7-digit IMO passes the standard check digit.

    The first six digits are multiplied by 7,6,5,4,3,2; the last digit of that
    sum must equal the seventh digit. A wrong check digit means the value was
    mistyped or corrupted -- it is NOT a real vessel identity.

    This matters because MRTIS keys vessel identity off the IMO. Without this
    check a corrupted IMO silently creates a phantom vessel that steals events
    from the real one: 'Spring Aura' 9991064 (invalid -- computes to 8, carries
    4) took two events out of the middle of 9991082's single continuous Zen-Noh
    loading, making one ship look like two at the same elevator on the same day.
    """
    if not imo or not isinstance(imo, str):
        return False
    if len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(imo[i]) * (7 - i) for i in range(6)) % 10 == int(imo[6])


def _overlaps(a, b):
    """True if two (min, max) timestamp ranges overlap. False if either is
    unknown (no dated evidence at all) -- absence of evidence is not overlap."""
    if a[0] is None or b[0] is None:
        return False
    return a[0] <= b[1] and b[0] <= a[1]


def build_imo_repair_map(records, excluded_names=frozenset()):
    """Map corrupted IMO -> the real one, for vessels split by a bad check digit.

    `records` is an iterable of (canonical_imo, normalized_vessel_name,
    canonical_vessel_type, action, event_time). `excluded_names` are
    normalized names to leave alone entirely (the dredge/noise list).

    A corrupted IMO is repaired only when its normalized name has EXACTLY ONE
    check-digit-valid IMO alongside it -- an unambiguous "same ship, one
    glitched row" case (e.g. Chicago Harmony 9755695 -> 9755696). Four guards
    stop that rule from merging genuinely different vessels:

    1. Exactly one valid IMO for the name. Two real vessels can share a name --
       both Aquitanias (9300491, 9611278) are real bulk carriers with valid
       check digits -- so merging on name alone would be the DSI/DSL mistake in
       another form.
    2. Never merge a name on the dredge/noise exclusion list. 'Texas Star' is
       both a dredge with a junk IMO (311000000 -> 3110000, 2,377 events) and,
       separately, a real tanker (9256860, 20 events). Merging on name would
       have dumped 2,377 dredge events into the tanker.
    3. Never merge across a known canonical type conflict (Tanker vs Bulk and
       so on). A blank/unknown type never blocks -- unknown is not evidence.
    4. Never merge when the corrupted-IMO rows show no SWP Enter/Exit crossing
       AND their date range doesn't overlap the period the good vessel
       actually carried that name (William, 2026-08-19, OPEN_QUESTIONS §7.5).
       Audit #1's one false merge -- Egret, 1782585 -> 9747120, 131 rows --
       is exactly this shape: no river crossing at all, in a date range the
       real Egret (9747120) was never observed under that name. A genuine
       "one glitched row" case is a blip inside an otherwise continuous
       history and clears at least one of the two checks; this guard only
       blocks the case that clears neither.
    """
    by_name = {}
    types = {}
    events = {}  # imo -> [has_crossing, min_time, max_time]
    for imo, name, vtype, action, event_time in records:
        # pandas hands NaN through for missing values -- guard explicitly
        if not imo or not isinstance(imo, str) or not name:
            continue
        by_name.setdefault(name, set()).add(imo)
        # Only a real, non-empty string counts as a known type. Guard against
        # pandas NaN specifically: bool(nan) is True and nan != anything is
        # also True, so an unguarded NaN masquerades as a known-but-different
        # type and silently blocks legitimate merges.
        if isinstance(vtype, str) and vtype.strip():
            types[imo] = vtype.strip()
        e = events.setdefault(imo, [False, None, None])
        if action in ("Enter", "Exit"):
            e[0] = True
        # event_time == event_time is False for both float NaN and pandas NaT
        # -- excludes unparseable timestamps without importing pandas here.
        if event_time is not None and event_time == event_time:
            e[1] = event_time if e[1] is None else min(e[1], event_time)
            e[2] = event_time if e[2] is None else max(e[2], event_time)

    repair = {}
    for name, imos in by_name.items():
        if name in excluded_names:
            continue
        valid = sorted(i for i in imos if imo_check_digit_valid(i))
        invalid = sorted(i for i in imos if not imo_check_digit_valid(i))
        if not invalid or len(valid) != 1:
            continue
        good = valid[0]
        good_type = types.get(good)
        good_ev = events.get(good, [False, None, None])
        for bad in invalid:
            bad_type = types.get(bad)
            if good_type and bad_type and good_type != bad_type:
                continue
            bad_ev = events.get(bad, [False, None, None])
            if not bad_ev[0] and not _overlaps(bad_ev[1:], good_ev[1:]):
                continue
            repair[bad] = good
    return repair
