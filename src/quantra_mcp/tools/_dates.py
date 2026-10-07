"""Dates the pricing tools cannot compute locally are resolved by the engine.

``effective_date: "spot"`` = as_of + settlement days (business days,
Following), ``tenor: "5Y"`` = effective + tenor (Unadjusted; the schedule
applies its own conventions), FRA ``3x6`` = spot + 3 / 6 months with the FRA
convention. Each resolution is ONE ``POST /calendar-advance`` whose request
and response are kept in the tool result (``date_resolution``) and named in
``notes``; nothing adds days to a date in this process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders.schedule import EndSpec, StartSpec, check_date
from quantra_mcp.schema.enums_generated import BusinessDayConvention, Calendar, TimeUnit
from quantra_mcp.tools._result import ToolResult, run_post
from quantra_mcp.tools.calendar import ADVANCE, advance_request, advance_summary


class DateResolutionFailed(Exception):
    """The engine rejected (or could not serve) a ``/calendar-advance`` call."""

    def __init__(self, result: ToolResult, label: str) -> None:
        super().__init__(f"could not resolve {label}: {result.get('error')}")
        self.result = result
        self.label = label


@dataclass
class DateResolver:
    backend: Backend
    calendar_overrides: list[dict[str, Any]] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    async def advance(
        self,
        label: str,
        calendar: str,
        date: str,
        n: int,
        unit: str,
        convention: str,
        end_of_month: bool = False,
    ) -> str:
        body = advance_request(
            Calendar(calendar),
            check_date(date, label),
            int(n),
            TimeUnit(unit),
            BusinessDayConvention(convention),
            end_of_month,
            None,
        )
        if self.calendar_overrides is not None:
            body["calendar_overrides"] = self.calendar_overrides
        result = await run_post(self.backend, ADVANCE, body, summarize=advance_summary)
        if not result.get("ok"):
            raise DateResolutionFailed(result, label)
        advanced = str(result["response"]["advanced_date"])
        self.calls.append(
            {"label": label, "endpoint": ADVANCE, "request": body, "response": result["response"]}
        )
        self.notes.append(
            f"{label}={advanced!r} <- engine /calendar-advance({date} + {n} {unit}, {convention}"
            f"{', end_of_month' if end_of_month else ''}, {calendar})"
        )
        return advanced

    async def spot(
        self, label: str, as_of: str, settlement_days: int, calendar: str, source: str
    ) -> str:
        """as_of + settlement days (business days, Following) on the given calendar."""
        out = await self.advance(label, calendar, as_of, settlement_days, "Days", "Following")
        self.notes.append(
            f"{label}: 'spot' = as_of + settlement_days {settlement_days} from {source}"
        )
        return out

    async def start(
        self,
        label: str,
        spec: StartSpec,
        as_of: str,
        settlement_days: int,
        calendar: str,
        source: str,
    ) -> str:
        if spec.spot:
            return await self.spot(label, as_of, settlement_days, calendar, source)
        assert spec.date is not None
        self.notes.append(f"{label}={spec.date!r} (explicit)")
        return spec.date

    async def end(self, label: str, spec: EndSpec, start: str, calendar: str) -> str:
        """Explicit date, or start + tenor Unadjusted (the schedule rolls it itself)."""
        if spec.date is not None:
            self.notes.append(f"{label}={spec.date!r} (explicit)")
            return spec.date
        assert spec.tenor is not None
        return await self.advance(
            label, calendar, start, spec.tenor["n"], spec.tenor["unit"], "Unadjusted"
        )
