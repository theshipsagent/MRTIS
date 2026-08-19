"""Build guardrails -- the checks a build runs against its own output before
that output is allowed anywhere near the database.

WHY THIS EXISTS
---------------
A pipeline that silently produces slightly-wrong numbers is worse than one
that crashes: the crash gets fixed, the wrong number gets used in a decision.
A guardrail is a rule the build asserts about its own result -- "every source
event must appear in the output exactly once", "no port call may end before it
starts" -- checked on every run, on the whole data set, not on a sample.

Two severities:

  HARD -- an invariant that cannot be false unless the code is wrong. A hard
          failure ABORTS the build before anything is written. The database is
          left exactly as it was; you never get a half-correct table.

  SOFT -- a health signal that depends on the source data rather than the code
          (how many events fell outside a call, how many berth stops had no
          usable draft). These never block the build; they are counted,
          reported, and are the numbers to watch move between runs.

The distinction is the whole point: hard checks protect correctness, soft
checks protect trust. Neither is a substitute for the other.
"""


class Guardrails:
    def __init__(self, label="build"):
        self.label = label
        self.results = []  # (severity, name, passed, detail)

    def hard(self, name, passed, detail=""):
        """An invariant. False here aborts the build."""
        self.results.append(("HARD", name, bool(passed), detail))
        return passed

    def soft(self, name, detail="", passed=True):
        """A health signal. Always recorded, never blocks."""
        self.results.append(("SOFT", name, bool(passed), detail))
        return passed

    @property
    def failures(self):
        return [r for r in self.results if r[0] == "HARD" and not r[2]]

    @property
    def warnings(self):
        return [r for r in self.results if r[0] == "SOFT" and not r[2]]

    def ok(self):
        return not self.failures

    def to_console(self):
        lines = []
        for sev, name, passed, detail in self.results:
            mark = "PASS" if passed else ("FAIL" if sev == "HARD" else "WARN")
            lines.append(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
        return "\n".join(lines)

    def to_markdown(self):
        lines = ["| | Check | Result |", "|---|---|---|"]
        for sev, name, passed, detail in self.results:
            mark = "PASS" if passed else ("**FAIL**" if sev == "HARD" else "WARN")
            lines.append(f"| {sev} | {name} | {mark}{(' -- ' + detail) if detail else ''} |")
        return "\n".join(lines)

    def raise_if_failed(self):
        if self.failures:
            detail = "\n".join(f"  - {n}: {d}" for _, n, _, d in self.failures)
            raise AssertionError(
                f"{len(self.failures)} hard guardrail(s) failed in {self.label}; "
                f"nothing was written to the database.\n{detail}"
            )
