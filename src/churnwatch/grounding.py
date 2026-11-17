"""Check that every piece of evidence an assessment cites is really in the record.

An explanation is only useful to a CSM if its numbers are true. The model is asked to
cite (field, value) pairs; this module verifies each one against the source row:

- numeric fields must match within rounding (1% relative or 1.0 absolute);
- text fields must match exactly (case-insensitive);
- notes evidence must be a verbatim substring of the CSM notes.
"""

from __future__ import annotations

import re

from .schema import Assessment, Evidence


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().strip("'\".")


def _as_number(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*[-+]?\$?[\d,]*\.?\d+\s*%?\s*", v)
        if m:
            return float(re.sub(r"[^\d.\-+]", "", v))
    return None


def check_evidence(ev: Evidence, record: dict, notes_field: str = "csm_notes") -> bool:
    if ev.field not in record:
        return False
    actual = record[ev.field]
    if ev.field == notes_field:
        quote = _norm(str(ev.value))
        return bool(quote) and quote in _norm(actual or "")
    if actual is None:
        return str(ev.value).strip().lower() in ("", "none", "null", "n/a", "blank")
    a_num, e_num = _as_number(actual), _as_number(ev.value)
    if a_num is not None and e_num is not None:
        return abs(a_num - e_num) <= max(1.0, abs(a_num) * 0.01)
    return _norm(str(actual)) == _norm(str(ev.value))


def grounding(assessment: Assessment, record: dict, notes_field: str = "csm_notes") -> tuple[int, int, list[str]]:
    """Return (verified, total, failures) for all evidence in an assessment."""
    verified, total, failures = 0, 0, []
    for d in assessment.drivers:
        for ev in d.evidence:
            total += 1
            if check_evidence(ev, record, notes_field):
                verified += 1
            else:
                failures.append(f"{ev.field}={ev.value!r} (record has {record.get(ev.field, '<no such field>')!r})")
    return verified, total, failures
