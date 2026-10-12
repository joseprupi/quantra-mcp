"""Tier 1: calendar tools over ``/calendar-holidays``, ``/calendar-business-days``,
``/calendar-advance``.

Dates are ``YYYY-MM-DD`` strings and are checked locally before anything is
sent. ``calendar_overrides`` is the engine 0.7.0 per-request holiday override
list, passed through as-is. Summaries are selections of the engine's response
(count, first and last date), never computed values.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.enums_generated import BusinessDayConvention, Calendar, TimeUnit
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, local_error_result, run_post

HOLIDAYS = "/calendar-holidays"
BUSINESS_DAYS = "/calendar-business-days"
ADVANCE = "/calendar-advance"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarOverride(BaseModel):
    """Per-request holiday corrections for one calendar (engine >= 0.7.0).

    Applies to every use of that calendar while the request is processed and
    is discarded afterwards; nothing is stored on the engine.
    """

    model_config = ConfigDict(extra="forbid")

    calendar: Calendar = Field(description="The calendar to override.")
    added_holidays: list[str] | None = Field(
        default=None,
        description="Dates (YYYY-MM-DD) that must be treated as holidays.",
    )
    removed_holidays: list[str] | None = Field(
        default=None,
        description=(
            "Dates (YYYY-MM-DD) that must be treated as business days (weekends are rejected)."
        ),
    )


def check_date(value: str, field: str) -> str:
    """Return ``value`` if it is a real ``YYYY-MM-DD`` date, else raise."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise LocalValidationError(
            f"{field}: expected a YYYY-MM-DD date, got {value!r}",
            [{"path": "/" + field, "message": "expected YYYY-MM-DD"}],
        )
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise LocalValidationError(
            f"{field}: {value!r} is not a valid calendar date ({exc})",
            [{"path": "/" + field, "message": str(exc)}],
        ) from exc
    return value


def overrides_to_wire(overrides: list[CalendarOverride] | None) -> list[dict[str, Any]] | None:
    """Typed overrides -> the engine's ``calendar_overrides`` list (dates validated)."""
    if overrides is None:
        return None
    out: list[dict[str, Any]] = []
    for i, o in enumerate(overrides):
        item: dict[str, Any] = {"calendar": str(o.calendar)}
        for key in ("added_holidays", "removed_holidays"):
            dates = getattr(o, key)
            if dates is not None:
                item[key] = [
                    check_date(d, f"calendar_overrides/{i}/{key}/{j}") for j, d in enumerate(dates)
                ]
        out.append(item)
    return out


def _with_overrides(
    body: dict[str, Any], overrides: list[CalendarOverride] | None
) -> dict[str, Any]:
    wire = overrides_to_wire(overrides)
    if wire is not None:
        body["calendar_overrides"] = wire
    return body


def holidays_request(
    calendar: Calendar,
    start_date: str,
    end_date: str,
    include_weekends: bool = False,
    calendar_overrides: list[CalendarOverride] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "calendar": str(calendar),
        "start_date": check_date(start_date, "start_date"),
        "end_date": check_date(end_date, "end_date"),
        "include_weekends": bool(include_weekends),
    }
    return _with_overrides(body, calendar_overrides)


def business_days_request(
    calendar: Calendar,
    start_date: str,
    end_date: str,
    include_start: bool = True,
    include_end: bool = True,
    calendar_overrides: list[CalendarOverride] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "calendar": str(calendar),
        "start_date": check_date(start_date, "start_date"),
        "end_date": check_date(end_date, "end_date"),
        "include_start": bool(include_start),
        "include_end": bool(include_end),
    }
    return _with_overrides(body, calendar_overrides)


def advance_request(
    calendar: Calendar,
    date: str,
    tenor_number: int,
    tenor_unit: TimeUnit,
    convention: BusinessDayConvention,
    end_of_month: bool = False,
    calendar_overrides: list[CalendarOverride] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "calendar": str(calendar),
        "date": check_date(date, "date"),
        "tenor_number": int(tenor_number),
        "tenor_unit": str(tenor_unit),
        "convention": str(convention),
        "end_of_month": bool(end_of_month),
    }
    return _with_overrides(body, calendar_overrides)


def dates_summary(response: Any) -> dict[str, Any] | None:
    """``{count, first, last}`` selected from a dates response."""
    if not isinstance(response, dict):
        return None
    dates = response.get("dates")
    if not isinstance(dates, list):
        return None
    summary: dict[str, Any] = {"count": response.get("count", len(dates))}
    if dates:
        summary["first"] = dates[0]
        summary["last"] = dates[-1]
    return summary


def advance_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    return {k: response[k] for k in ("input_date", "advanced_date") if k in response}


def register(app: MCPServer, backend: Backend) -> None:
    @tool(app, "Engine calendar: holidays", engine=True)
    async def calendar_holidays(
        calendar: Annotated[
            Calendar, Field(description="Engine Calendar enum value, e.g. TARGET, UnitedStates.")
        ],
        start_date: Annotated[
            str,
            Field(
                description="First date YYYY-MM-DD (inclusive).", pattern="^\\d{4}-\\d{2}-\\d{2}$"
            ),
        ],
        end_date: Annotated[
            str,
            Field(
                description="Last date YYYY-MM-DD (inclusive).", pattern="^\\d{4}-\\d{2}-\\d{2}$"
            ),
        ],
        include_weekends: Annotated[
            bool, Field(description="Also list Saturdays and Sundays.")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Holidays of a QuantLib calendar between two dates (POST /calendar-holidays). ``summary``
        = {count, first, last} taken from the engine's response.
        """
        try:
            body = holidays_request(
                calendar, start_date, end_date, include_weekends, calendar_overrides
            )
        except LocalValidationError as exc:
            return local_error_result(HOLIDAYS, None, exc.error, exc.problems)
        return await run_post(backend, HOLIDAYS, body, summarize=dates_summary)

    @tool(app, "Engine calendar: business days", engine=True)
    async def calendar_business_days(
        calendar: Annotated[Calendar, Field(description="Engine Calendar enum value.")],
        start_date: Annotated[
            str, Field(description="First date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$")
        ],
        end_date: Annotated[
            str, Field(description="Last date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$")
        ],
        include_start: Annotated[
            bool, Field(description="Whether start_date itself may be listed.")
        ] = True,
        include_end: Annotated[
            bool, Field(description="Whether end_date itself may be listed.")
        ] = True,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Business days of a QuantLib calendar between two dates (POST /calendar-business-days).
        ``summary`` = {count, first, last} taken from the engine's response.
        """
        try:
            body = business_days_request(
                calendar, start_date, end_date, include_start, include_end, calendar_overrides
            )
        except LocalValidationError as exc:
            return local_error_result(BUSINESS_DAYS, None, exc.error, exc.problems)
        return await run_post(backend, BUSINESS_DAYS, body, summarize=dates_summary)

    @tool(app, "Engine calendar: advance a date", engine=True)
    async def calendar_advance(
        calendar: Annotated[Calendar, Field(description="Engine Calendar enum value.")],
        date: Annotated[
            str, Field(description="Start date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$")
        ],
        tenor_number: Annotated[
            int, Field(description="Number of units; negative shifts backwards.")
        ],
        tenor_unit: Annotated[
            TimeUnit, Field(description="Engine TimeUnit: Days, Weeks, Months, Years.")
        ],
        convention: Annotated[
            BusinessDayConvention,
            Field(description="Engine BusinessDayConvention applied to the result."),
        ],
        end_of_month: Annotated[bool, Field(description="Apply the end-of-month rule.")] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Advance a date by a tenor on a QuantLib calendar (POST /calendar-advance). ``summary`` =
        {input_date, advanced_date} taken from the engine's response.
        """
        try:
            body = advance_request(
                calendar,
                date,
                tenor_number,
                tenor_unit,
                convention,
                end_of_month,
                calendar_overrides,
            )
        except LocalValidationError as exc:
            return local_error_result(ADVANCE, None, exc.error, exc.problems)
        return await run_post(backend, ADVANCE, body, summarize=advance_summary)
