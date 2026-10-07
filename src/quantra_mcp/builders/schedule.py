"""Schedule blocks and date arguments (pure; no date arithmetic happens here).

A trade's ``effective_date`` is either an explicit ``YYYY-MM-DD`` or the
literal ``"spot"``; a ``termination_date`` is an explicit date; a ``tenor``
(``"5Y"``) stands in for the termination date. Anything that is not an
explicit date is resolved by the engine (``/calendar-advance``) in the tool
layer and reported in ``notes``; nothing here adds days to a date.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from quantra_mcp.builders.tenor import parse_tenor
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import ScheduleConventions

SPOT = "spot"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def problem(path: str, message: str) -> LocalValidationError:
    return LocalValidationError(message, [{"path": path, "message": message}])


def is_date(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATE_RE.match(value))


def check_date(value: Any, field: str) -> str:
    if not is_date(value):
        raise problem(f"/{field}", f"{field}: expected a YYYY-MM-DD date, got {value!r}")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise problem(f"/{field}", f"{field}: {value!r} is not a valid date ({exc})") from exc
    return str(value)


@dataclass(frozen=True, slots=True)
class StartSpec:
    """``effective_date`` argument: an explicit date or ``spot``."""

    date: str | None  # explicit
    spot: bool


def parse_start(value: Any, field: str = "effective_date") -> StartSpec:
    if isinstance(value, str) and value.strip().lower() == SPOT:
        return StartSpec(date=None, spot=True)
    return StartSpec(date=check_date(value, field), spot=False)


@dataclass(frozen=True, slots=True)
class EndSpec:
    """``termination_date`` (explicit) or ``tenor`` (resolved by the engine from the start)."""

    date: str | None
    tenor: dict[str, Any] | None


def parse_end(
    termination_date: Any, tenor: Any, field: str = "termination_date", tenor_field: str = "tenor"
) -> EndSpec:
    if (termination_date is None) == (tenor is None):
        raise problem(
            f"/{field}",
            f"give exactly one of {field} (YYYY-MM-DD) or {tenor_field} (e.g. '5Y', resolved by "
            "the engine's /calendar-advance from the effective date)",
        )
    if termination_date is not None:
        return EndSpec(date=check_date(termination_date, field), tenor=None)
    t = parse_tenor(tenor, tenor_field)
    if t["n"] <= 0:
        raise problem(f"/{tenor_field}", f"{tenor_field}: must be a positive tenor, got {tenor!r}")
    return EndSpec(date=None, tenor=t)


def schedule_block(
    conv: ScheduleConventions,
    effective_date: str,
    termination_date: str,
    frequency: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An engine ``Schedule`` (fixture key order). ``overrides`` replace any field."""
    block: dict[str, Any] = {
        "calendar": str(conv.calendar),
        "effective_date": effective_date,
        "termination_date": termination_date,
        "frequency": str(frequency),
        "convention": str(conv.convention),
        "termination_date_convention": str(conv.termination_date_convention),
        "date_generation_rule": str(conv.date_generation_rule),
        "end_of_month": conv.end_of_month,
    }
    for key, value in (overrides or {}).items():
        block[key] = value
    return block


def schedule_notes(
    prefix: str, conv: ScheduleConventions, source: str, overrides: dict[str, Any] | None
) -> list[str]:
    ov = overrides or {}
    out: list[str] = []
    for key, value in conv.model_dump(mode="json").items():
        if key in ov:
            out.append(f"{prefix}.{key}={ov[key]!r} (explicit override)")
        else:
            out.append(f"{prefix}.{key}={value!r} from {source}")
    for key, value in ov.items():
        if key not in conv.model_fields:
            out.append(f"{prefix}.{key}={value!r} (explicit override)")
    return out
