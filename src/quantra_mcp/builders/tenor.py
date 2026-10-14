"""Tenor strings (``1W``, ``3M``, ``2Y``, ``18M``, ``10D``, ``1 WK``, ``18 MO``, ``10 YR``)
<-> engine ``Period`` dicts.

The text form is ``<integer> [whitespace] <unit>`` where the unit is one of a closed,
case-insensitive list of spellings (:data:`UNIT_SPELLINGS`). Nothing outside that list
is guessed: ``MO`` is months, never minutes. Overnight / tomorrow-next / spot-next
labels (``ON``, ``O/N``, ``TN``, ``T/N``, ``SN``, ``S/N``) are recognised only to be
refused with a specific reason (:func:`unsupported_tenor_reason`).

Only parsing and an *ordering key* live here. The ordering key is an
approximate day count used to sort a strip by maturity and to detect
duplicates before the request is sent; it is never put on the wire and never
used for any number the agent sees.
"""

from __future__ import annotations

import re
from typing import Any

from quantra_mcp.errors import LocalValidationError

#: every accepted unit spelling (upper case) -> engine TimeUnit; a closed list
UNIT_SPELLINGS: dict[str, str] = {
    **dict.fromkeys(("D", "DY", "DAY", "DAYS"), "Days"),
    **dict.fromkeys(("W", "WK", "WKS", "WEEK", "WEEKS"), "Weeks"),
    **dict.fromkeys(("M", "MO", "MOS", "MTH", "MTHS", "MONTH", "MONTHS"), "Months"),
    **dict.fromkeys(("Y", "YR", "YRS", "YEAR", "YEARS"), "Years"),
}
_UNIT_ALT = "|".join(sorted(UNIT_SPELLINGS, key=len, reverse=True))
_TENOR_RE = re.compile(rf"^\s*(\d+)\s*({_UNIT_ALT})\s*$", re.IGNORECASE)

#: overnight-style labels, recognised only to be refused with a specific reason
OVERNIGHT_LABELS = frozenset({"ON", "O/N", "TN", "T/N", "SN", "S/N"})
OVERNIGHT_REASON = "overnight/tomorrow-next tenors are not supported as curve pillars"

_UNIT_BY_LETTER = {"D": "Days", "W": "Weeks", "M": "Months", "Y": "Years"}
_LETTER_BY_UNIT = {v: k for k, v in _UNIT_BY_LETTER.items()}

#: approximate length of one unit in days, for ordering only
_APPROX_DAYS = {"Days": 1.0, "Weeks": 7.0, "Months": 365.25 / 12.0, "Years": 365.25}

Period = dict[str, Any]


def is_unit_word(text: str) -> bool:
    """``"WK"``, ``"mo"``, ``"Years"`` ... (one of :data:`UNIT_SPELLINGS`)."""
    return text.strip().upper() in UNIT_SPELLINGS


def parse_tenor_text(text: str) -> Period | None:
    """``"18 MO"`` -> ``{"n": 18, "unit": "Months"}``; ``None`` when not a tenor."""
    m = _TENOR_RE.match(text)
    if m is None:
        return None
    return {"n": int(m.group(1)), "unit": UNIT_SPELLINGS[m.group(2).upper()]}


def unsupported_tenor_reason(text: str) -> str | None:
    """The specific refusal reason for ``ON`` / ``O/N`` / ``TN`` / ``T/N`` / ``SN`` /
    ``S/N``; ``None`` for anything else."""
    return OVERNIGHT_REASON if text.strip().upper() in OVERNIGHT_LABELS else None


def parse_tenor(value: Any, field: str = "tenor") -> Period:
    """``"6M"`` or ``{"n": 6, "unit": "Months"}`` -> ``{"n": 6, "unit": "Months"}``.

    Accepts the four calendar units in the spellings of :data:`UNIT_SPELLINGS`.
    ``n`` must be a non-negative integer (``0D`` is the reference date itself, used by
    value curves).
    """
    if isinstance(value, dict):
        n = value.get("n")
        unit = value.get("unit")
        extra = set(value) - {"n", "unit"}
        if extra or not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise LocalValidationError(
                f"{field}: expected {{n: non-negative int, unit: Days|Weeks|Months|Years}}, "
                f"got {value!r}",
                [{"path": "/" + field, "message": "bad period object"}],
            )
        if unit not in _APPROX_DAYS:
            raise LocalValidationError(
                f"{field}: unit must be one of {sorted(_APPROX_DAYS)}, got {unit!r}",
                [{"path": "/" + field + "/unit", "message": "unsupported unit"}],
            )
        return {"n": n, "unit": unit}
    if isinstance(value, str):
        period = parse_tenor_text(value)
        if period is not None:
            return period
        reason = unsupported_tenor_reason(value)
        if reason is not None:
            raise LocalValidationError(
                f"{field}: {value!r}: {reason}",
                [{"path": "/" + field, "message": reason}],
            )
    raise LocalValidationError(
        f"{field}: expected a tenor like '1W', '3M', '18M', '2Y', '1 WK', '18 MO', '10 YR' "
        f"or {{n, unit}}, got {value!r}",
        [{"path": "/" + field, "message": "unparseable tenor"}],
    )


def tenor_label(period: Period) -> str:
    """``{"n": 6, "unit": "Months"}`` -> ``"6M"`` (for notes and error messages)."""
    return f"{period['n']}{_LETTER_BY_UNIT[str(period['unit'])]}"


def approx_days(period: Period) -> float:
    """Ordering key only (never on the wire)."""
    return float(period["n"]) * _APPROX_DAYS[str(period["unit"])]
