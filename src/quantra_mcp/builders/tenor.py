"""Tenor strings (``1W``, ``3M``, ``2Y``, ``18M``, ``10D``) <-> engine ``Period`` dicts.

Only parsing and an *ordering key* live here. The ordering key is an
approximate day count used to sort a strip by maturity and to detect
duplicates before the request is sent; it is never put on the wire and never
used for any number the agent sees.
"""

from __future__ import annotations

import re
from typing import Any

from quantra_mcp.errors import LocalValidationError

_TENOR_RE = re.compile(r"^\s*(\d+)\s*([DWMYdwmy])\s*$")

_UNIT_BY_LETTER = {"D": "Days", "W": "Weeks", "M": "Months", "Y": "Years"}
_LETTER_BY_UNIT = {v: k for k, v in _UNIT_BY_LETTER.items()}

#: approximate length of one unit in days, for ordering only
_APPROX_DAYS = {"Days": 1.0, "Weeks": 7.0, "Months": 365.25 / 12.0, "Years": 365.25}

Period = dict[str, Any]


def parse_tenor(value: Any, field: str = "tenor") -> Period:
    """``"6M"`` or ``{"n": 6, "unit": "Months"}`` -> ``{"n": 6, "unit": "Months"}``.

    Accepts the four calendar units (D/W/M/Y). ``n`` must be a non-negative
    integer (``0D`` is the reference date itself, used by value curves).
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
        m = _TENOR_RE.match(value)
        if m:
            return {"n": int(m.group(1)), "unit": _UNIT_BY_LETTER[m.group(2).upper()]}
    raise LocalValidationError(
        f"{field}: expected a tenor like '1W', '3M', '18M', '2Y' or {{n, unit}}, got {value!r}",
        [{"path": "/" + field, "message": "unparseable tenor"}],
    )


def tenor_label(period: Period) -> str:
    """``{"n": 6, "unit": "Months"}`` -> ``"6M"`` (for notes and error messages)."""
    return f"{period['n']}{_LETTER_BY_UNIT[str(period['unit'])]}"


def approx_days(period: Period) -> float:
    """Ordering key only (never on the wire)."""
    return float(period["n"]) * _APPROX_DAYS[str(period["unit"])]
