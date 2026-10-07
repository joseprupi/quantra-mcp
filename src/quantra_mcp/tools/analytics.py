"""Tier 5: analytics by composition (DV01, key-rate ladder, scenarios, fair rate).

Every number here is an engine output or a difference / sum of engine
outputs, and the raw per-call numbers are reported next to each difference so
a reader can redo the subtraction. A bumped market is the base market with
``bump_bp / 10_000`` added to the selected pillars' quotes
(:mod:`quantra_mcp.builders.bumps`); the bumped trade is repriced through the
SAME pricing tool it would normally use (``price_vanilla_swap`` /
``price_ois_swap`` internals), so each ``calls[*].result`` is a complete
uniform pricing result whose ``request`` can be replayed by curl.

Dates given as ``spot`` / a tenor are resolved by the engine once, in the
base call, and pinned for every bumped call, so the bumped requests differ
from the base request only in the bumped quotes.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders import bumps
from quantra_mcp.builders import market as mk
from quantra_mcp.builders.products import swaps as ps
from quantra_mcp.builders.products._common import FixedLegOverrides, FloatingLegOverrides
from quantra_mcp.builders.schedule import problem
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import PresetError
from quantra_mcp.schema.enums_generated import RateAveragingType, SwapType
from quantra_mcp.session import SessionStore
from quantra_mcp.tools._result import ToolResult, new_request_id
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire
from quantra_mcp.tools.pricing import Market, price_ois_swap_impl, price_vanilla_swap_impl

Product = Literal["vanilla_swap", "ois_swap"]
Scope = Literal["all", "discounting", "forwarding"]

_OIS_ONLY = (
    "payment_lag",
    "averaging_method",
    "lookback_days",
    "lockout_days",
    "apply_observation_shift",
    "telescopic_value_dates",
    "overnight_leg_overrides",
)


class SwapSpec(BaseModel):
    """The trade an analytics tool reprices: which pricing tool, plus its arguments."""

    model_config = ConfigDict(extra="forbid")

    product: Product = Field(description="vanilla_swap (price_vanilla_swap) or ois_swap.")
    preset: str = Field(description="Preset with the matching trade block.")
    discounting_curve: str
    forwarding_curve: str
    swap_type: SwapType
    notional: float = Field(gt=0)
    fixed_rate: float
    effective_date: str = Field(description="YYYY-MM-DD or 'spot'.")
    termination_date: str | None = None
    tenor: str | None = None
    spread: float = 0.0
    index_id: str | None = None
    fixed_leg_overrides: FixedLegOverrides | None = None
    floating_leg_overrides: FloatingLegOverrides | None = None
    # OIS only
    payment_lag: int | None = Field(default=None, ge=0)
    averaging_method: RateAveragingType | None = None
    lookback_days: int | None = Field(default=None, ge=0)
    lockout_days: int | None = Field(default=None, ge=0)
    apply_observation_shift: bool | None = None
    telescopic_value_dates: bool | None = None
    overnight_leg_overrides: FloatingLegOverrides | None = None

    def check(self) -> None:
        if self.product == "vanilla_swap":
            bad = [f for f in _OIS_ONLY if getattr(self, f) is not None]
            if bad:
                raise problem("/trade", f"{bad} apply to ois_swap only (product is vanilla_swap)")
        elif self.floating_leg_overrides is not None:
            raise problem(
                "/trade/floating_leg_overrides",
                "use overnight_leg_overrides for ois_swap",
            )

    def pinned(self, effective: str, termination: str) -> SwapSpec:
        return self.model_copy(
            update={"effective_date": effective, "termination_date": termination, "tenor": None}
        )

    def curves(self) -> list[str]:
        out = [self.discounting_curve]
        if self.forwarding_curve != self.discounting_curve:
            out.append(self.forwarding_curve)
        return out


class Bump(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curve: str
    bp: float
    pillar: str | int | None = Field(
        default=None, description="Pillar label ('5Y') or 0-based index; omit = every pillar."
    )


class QuoteReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curve: str
    pillar: str | int
    value: float = Field(description="New quote (rate / spread / futures price), replaces it.")


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bumps: list[Bump] | None = None
    replace_quotes: list[QuoteReplacement] | None = None


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


Call = dict[str, Any]


def _fail(
    tool: str,
    error: str,
    *,
    problems: list[dict[str, str]] | None = None,
    calls: list[Call] | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> ToolResult:
    out: ToolResult = {"ok": False, "tool": tool, "error": error, **extra}
    if problems:
        out["problems"] = problems
    out["calls"] = calls or []
    out["engine"] = {"api_version": None, "request_id": request_id}
    return out


async def _price_spec(
    backend: Backend,
    store: SessionStore,
    pricing: dict[str, Any],
    spec: SwapSpec,
    request_id: str,
) -> ToolResult:
    """Reprice ``spec`` on a (possibly bumped) pricing block through the product's tool."""
    common = spec.model_dump(
        include={
            "swap_type",
            "notional",
            "fixed_rate",
            "effective_date",
            "termination_date",
            "tenor",
            "spread",
            "index_id",
            "fixed_leg_overrides",
        }
    )
    if spec.product == "vanilla_swap":
        trade = ps.VanillaSwapTrade(**common, floating_leg_overrides=spec.floating_leg_overrides)
        return await price_vanilla_swap_impl(
            backend,
            store,
            pricing,
            spec.preset,
            [trade],
            spec.discounting_curve,
            spec.forwarding_curve,
            None,
            False,
            None,
            request_id,
        )
    ois = ps.OisSwapTrade(
        **common,
        payment_lag=spec.payment_lag,
        averaging_method=spec.averaging_method,
        lookback_days=spec.lookback_days,
        lockout_days=spec.lockout_days,
        apply_observation_shift=spec.apply_observation_shift,
        telescopic_value_dates=spec.telescopic_value_dates,
        overnight_leg_overrides=spec.overnight_leg_overrides,
    )
    return await price_ois_swap_impl(
        backend,
        store,
        pricing,
        spec.preset,
        [ois],
        spec.discounting_curve,
        spec.forwarding_curve,
        None,
        False,
        None,
        request_id,
    )


def _swap0(result: ToolResult) -> dict[str, Any] | None:
    resp = result.get("response")
    if isinstance(resp, dict) and isinstance(resp.get("swaps"), list) and resp["swaps"]:
        first = resp["swaps"][0]
        if isinstance(first, dict):
            return first
    return None


def _npv(result: ToolResult) -> float | None:
    s = _swap0(result)
    if s is not None and isinstance(s.get("npv"), int | float):
        return float(s["npv"])
    return None


def _pin_dates(spec: SwapSpec, base: ToolResult, notes: list[str]) -> SwapSpec:
    """Replace spot/tenor by the dates the engine resolved in the base call."""
    resolved = {
        c.get("label"): c.get("response", {}).get("advanced_date")
        for c in base.get("date_resolution") or []
        if isinstance(c, dict)
    }
    eff = resolved.get("effective_date") or spec.effective_date
    end = resolved.get("termination_date") or spec.termination_date
    if (eff, end) != (spec.effective_date, spec.termination_date) or spec.tenor is not None:
        notes.append(
            f"dates pinned from the base call for every reprice: effective_date={eff!r}, "
            f"termination_date={end!r} (resolved by the engine's /calendar-advance once)"
        )
        return spec.pinned(str(eff), str(end))
    return spec


def _diff(a: float, b: float) -> float:
    return a - b


class _Runner:
    """Resolves the market once, prices the base, then fans out bounded reprices."""

    def __init__(
        self,
        tool: str,
        backend: Backend,
        store: SessionStore,
        max_concurrency: int,
        request_id: str | None,
    ) -> None:
        self.tool = tool
        self.backend = backend
        self.store = store
        self.sem = asyncio.Semaphore(max(1, max_concurrency))
        self.max_concurrency = max_concurrency
        self.rid = request_id or new_request_id()
        self.notes: list[str] = []
        self.pricing: dict[str, Any] = {}
        self.spec: SwapSpec | None = None
        self.calls: list[Call] = []
        self.base_npv: float = 0.0

    def resolve(
        self,
        market: Market,
        trade: SwapSpec,
        as_of: str | None,
        overrides: list[CalendarOverride] | None,
    ) -> None:
        trade.check()
        self.pricing, notes = mk.resolve_market(
            self.store, market, as_of, overrides_to_wire(overrides)
        )
        self.notes += notes
        for cid in trade.curves():
            mk.require_curve(self.pricing, cid, "trade.discounting_curve/forwarding_curve")
        self.spec = trade

    def curve(self, curve_id: str) -> dict[str, Any]:
        found = mk.find_item(self.pricing, "curves", curve_id)
        if found is None:
            raise problem(
                "/curve",
                f"curve {curve_id!r} is not in the market; curves: "
                f"{mk.ids_in(self.pricing, 'curves')}",
            )
        return found

    def with_curves(self, replacements: dict[str, dict[str, Any]]) -> dict[str, Any]:
        out = copy.deepcopy(self.pricing)
        for i, c in enumerate(out["rates"]["curves"]):
            if c.get("id") in replacements:
                out["rates"]["curves"][i] = replacements[c["id"]]
        return out

    async def price(self, label: str, pricing: dict[str, Any], edits: Any) -> Call:
        assert self.spec is not None
        async with self.sem:
            result = await _price_spec(
                self.backend, self.store, pricing, self.spec, f"{self.rid}:{label}"
            )
        return {"label": label, "edits": edits, "npv": _npv(result), "result": result}

    async def base(self) -> Call | ToolResult:
        """Price the base; returns the call, or a failure result."""
        call = await self.price("base", self.pricing, [])
        self.calls.append(call)
        if not call["result"].get("ok") or call["npv"] is None:
            return self.failure("base pricing failed", call)
        assert self.spec is not None
        self.spec = _pin_dates(self.spec, call["result"], self.notes)
        self.base_npv = float(call["npv"])
        return call

    def failure(self, what: str, call: Call) -> ToolResult:
        err = call["result"].get("error") or "no npv in the engine response"
        return _fail(
            self.tool,
            f"{what} ({call['label']}): {err}",
            calls=self.calls,
            request_id=self.rid,
            notes=self.notes,
        )

    async def fan_out(self, jobs: list[tuple[str, dict[str, Any], Any]]) -> ToolResult | None:
        results = await asyncio.gather(*(self.price(label, p, e) for label, p, e in jobs))
        self.calls += results
        self.notes.append(
            f"{len(jobs)} reprice(s) fanned out with at most {self.max_concurrency} "
            "concurrent engine calls (QUANTRA_MAX_CONCURRENCY)"
        )
        for call in results:
            if not call["result"].get("ok") or call["npv"] is None:
                return self.failure("reprice failed", call)
        return None

    def done(self, **fields: Any) -> ToolResult:
        assert self.spec is not None
        out: ToolResult = {"ok": True, "tool": self.tool, "product": self.spec.product}
        out.update(fields)
        out["calls"] = self.calls
        out["notes"] = self.notes
        out["engine"] = {
            "api_version": self.calls[0]["result"].get("engine", {}).get("api_version"),
            "request_id": self.rid,
        }
        return out


def _guarded(
    tool: str, request_id: str | None
) -> Callable[[Callable[[], Awaitable[ToolResult]]], Awaitable[ToolResult]]:
    async def run(fn: Callable[[], Awaitable[ToolResult]]) -> ToolResult:
        try:
            return await fn()
        except PresetError as exc:
            return _fail(tool, str(exc), request_id=request_id)
        except LocalValidationError as exc:
            return _fail(tool, exc.error, problems=exc.problems, request_id=request_id)

    return run


# --------------------------------------------------------------------------
# implementations
# --------------------------------------------------------------------------


def _bumped_curves(
    runner: _Runner, curve_ids: list[str], bump_bp: float
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    repl: dict[str, dict[str, Any]] = {}
    edits: list[dict[str, Any]] = []
    for cid in curve_ids:
        bumped, e = bumps.bump_curve(runner.curve(cid), bump_bp)
        repl[cid] = bumped
        edits += e
    return repl, edits


async def swap_dv01_impl(
    backend: Backend,
    store: SessionStore,
    max_concurrency: int,
    market: Market,
    trade: SwapSpec,
    bump_bp: float,
    scope: Scope,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    tool = "swap_dv01"
    r = _Runner(tool, backend, store, max_concurrency, request_id)

    async def go() -> ToolResult:
        if bump_bp == 0:
            raise problem("/bump_bp", "bump_bp must be non-zero")
        r.resolve(market, trade, as_of, calendar_overrides)
        if scope == "discounting":
            curve_ids = [trade.discounting_curve]
        elif scope == "forwarding":
            curve_ids = [trade.forwarding_curve]
        else:
            curve_ids = trade.curves()
        repl, edits = _bumped_curves(r, curve_ids, bump_bp)
        r.notes.append(
            f"scope={scope!r}: every pillar of {curve_ids} bumped by {bump_bp:+g} bp "
            f"({len(edits)} quote(s))"
        )
        base = await r.base()
        if not base.get("label"):
            return base
        failed = await r.fan_out([("bumped", r.with_curves(repl), edits)])
        if failed is not None:
            return failed
        bumped_npv = float(r.calls[1]["npv"])
        return r.done(
            bump_bp=bump_bp,
            scope=scope,
            curves_bumped=curve_ids,
            base_npv=r.base_npv,
            bumped_npv=bumped_npv,
            dv01=_diff(bumped_npv, r.base_npv),
            dv01_definition=f"dv01 = bumped_npv - base_npv (NPV change for {bump_bp:+g} bp "
            "on every selected pillar; calls[1].result is the bumped pricing)",
            bumped_quotes=edits,
        )

    return await _guarded(tool, r.rid)(go)


async def key_rate_ladder_impl(
    backend: Backend,
    store: SessionStore,
    max_concurrency: int,
    market: Market,
    trade: SwapSpec,
    bump_bp: float,
    curve: str | None,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    tool = "key_rate_ladder"
    r = _Runner(tool, backend, store, max_concurrency, request_id)

    async def go() -> ToolResult:
        if bump_bp == 0:
            raise problem("/bump_bp", "bump_bp must be non-zero")
        r.resolve(market, trade, as_of, calendar_overrides)
        curve_id = curve
        if curve_id is None:
            curve_id = trade.discounting_curve
            r.notes.append(f"curve defaulted to trade.discounting_curve {curve_id!r}")
        base_curve = r.curve(curve_id)
        pillars = bumps.pillars_of(base_curve)
        if not pillars:
            raise problem("/curve", f"curve {curve_id!r} has no points")
        jobs: list[tuple[str, dict[str, Any], Any]] = []
        par_curve, par_edits = bumps.bump_curve(base_curve, bump_bp)
        jobs.append(("parallel", r.with_curves({curve_id: par_curve}), par_edits))
        for p in pillars:
            bumped, edits = bumps.bump_curve(base_curve, bump_bp, [p.index])
            jobs.append((f"pillar:{p.label}", r.with_curves({curve_id: bumped}), edits))
        r.notes.append(
            f"buckets = the {len(pillars)} actual pillars of curve {curve_id!r} in wire order: "
            f"{[p.label for p in pillars]}; one reprice per pillar plus one parallel reprice"
        )
        base = await r.base()
        if not base.get("label"):
            return base
        failed = await r.fan_out(jobs)
        if failed is not None:
            return failed
        parallel = r.calls[1]
        ladder: list[dict[str, Any]] = []
        total = 0.0
        for p, call in zip(pillars, r.calls[2:], strict=True):
            npv = float(call["npv"])
            dv01 = _diff(npv, r.base_npv)
            total += dv01
            ladder.append(
                {
                    "pillar": p.label,
                    "point_type": p.point_type,
                    "quote_from": call["edits"][0]["from"],
                    "quote_to": call["edits"][0]["to"],
                    "bumped_npv": npv,
                    "dv01": dv01,
                }
            )
        par_npv = float(parallel["npv"])
        return r.done(
            bump_bp=bump_bp,
            curve=curve_id,
            base_npv=r.base_npv,
            ladder=ladder,
            parallel={"bumped_npv": par_npv, "dv01": _diff(par_npv, r.base_npv)},
            sum_of_buckets=total,
            definitions={
                "dv01": f"bumped_npv - base_npv for {bump_bp:+g} bp on that pillar alone",
                "parallel.dv01": "bumped_npv - base_npv with every pillar bumped together",
                "sum_of_buckets": "sum of ladder[*].dv01 (differs from parallel.dv01 by "
                "convexity / bootstrap cross-effects)",
            },
        )

    return await _guarded(tool, r.rid)(go)


async def scenario_impl(
    backend: Backend,
    store: SessionStore,
    max_concurrency: int,
    market: Market,
    trade: SwapSpec,
    scenarios: list[Scenario],
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    tool = "scenario"
    r = _Runner(tool, backend, store, max_concurrency, request_id)

    async def go() -> ToolResult:
        if not scenarios:
            raise problem("/scenarios", "give at least one scenario")
        names = [s.name for s in scenarios]
        if len(set(names)) != len(names) or "base" in names:
            raise problem("/scenarios", "scenario names must be unique and not 'base'")
        r.resolve(market, trade, as_of, calendar_overrides)
        jobs: list[tuple[str, dict[str, Any], Any]] = []
        for si, sc in enumerate(scenarios):
            if not sc.bumps and not sc.replace_quotes:
                raise problem(f"/scenarios/{si}", f"scenario {sc.name!r}: bumps or replace_quotes")
            curves: dict[str, dict[str, Any]] = {}
            edits: list[dict[str, Any]] = []
            for bi, b in enumerate(sc.bumps or []):
                cur = curves.get(b.curve) or r.curve(b.curve)
                sel = None
                if b.pillar is not None:
                    pil = bumps.resolve_pillar(
                        bumps.pillars_of(cur), b.pillar, f"/scenarios/{si}/bumps/{bi}/pillar"
                    )
                    sel = [pil.index]
                curves[b.curve], e = bumps.bump_curve(cur, b.bp, sel)
                edits += e
            for qi, q in enumerate(sc.replace_quotes or []):
                cur = curves.get(q.curve) or r.curve(q.curve)
                pil = bumps.resolve_pillar(
                    bumps.pillars_of(cur), q.pillar, f"/scenarios/{si}/replace_quotes/{qi}/pillar"
                )
                curves[q.curve], e1 = bumps.replace_quote(cur, pil.index, q.value)
                edits.append(e1)
            jobs.append((sc.name, r.with_curves(curves), edits))
        base = await r.base()
        if not base.get("label"):
            return base
        failed = await r.fan_out(jobs)
        if failed is not None:
            return failed
        rows: list[dict[str, Any]] = [
            {"name": "base", "npv": r.base_npv, "change": 0.0, "edits": 0}
        ]
        for call in r.calls[1:]:
            npv = float(call["npv"])
            rows.append(
                {
                    "name": call["label"],
                    "npv": npv,
                    "change": _diff(npv, r.base_npv),
                    "edits": len(call["edits"]),
                }
            )
        return r.done(
            base_npv=r.base_npv,
            table=rows,
            definitions={"change": "npv - base_npv; calls[*].edits lists every quote moved"},
        )

    return await _guarded(tool, r.rid)(go)


async def fair_rate_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    trade: SwapSpec,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    tool = "fair_rate"
    r = _Runner(tool, backend, store, 1, request_id)

    async def go() -> ToolResult:
        r.resolve(market, trade, as_of, calendar_overrides)
        base = await r.base()
        if not base.get("label"):
            return base
        swap = _swap0(base["result"]) or {}
        fields = {k: swap[k] for k in ("fair_rate", "fair_spread") if k in swap}
        if not fields:
            r.notes.append(
                f"the engine response for {trade.product} carries no fair_rate / fair_spread "
                f"(fields present: {sorted(swap)}); not solved locally"
            )
        return r.done(
            npv=r.base_npv,
            fair_rate=fields.get("fair_rate"),
            fair_spread=fields.get("fair_spread"),
            provided_by_engine=bool(fields),
            message=None if fields else f"fair rate not provided by the engine for {trade.product}",
        )

    return await _guarded(tool, r.rid)(go)


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

_TRADE_DOC = """trade: the swap to reprice: ``product`` (``vanilla_swap`` | ``ois_swap``),
            ``preset``, ``discounting_curve``, ``forwarding_curve`` and the same
            economics as price_vanilla_swap / price_ois_swap (swap_type, notional,
            fixed_rate, effective_date 'spot' | date, tenor | termination_date, ...)."""


def register(app: MCPServer, backend: Backend, store: SessionStore, max_concurrency: int) -> None:
    @app.tool()
    async def swap_dv01(
        market: Market,
        trade: SwapSpec,
        bump_bp: float = 1.0,
        scope: Scope = "all",
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Parallel DV01 of a swap: reprice with every quote of the selected curve(s) bumped.

        Args:
            market: as in the pricing tools (session, engine pricing block or build_curve
                results).
            trade: the swap: ``product`` (``vanilla_swap`` | ``ois_swap``), ``preset``,
                ``discounting_curve``, ``forwarding_curve`` and the price_vanilla_swap /
                price_ois_swap economics (swap_type, notional, fixed_rate, effective_date
                'spot' | date, tenor | termination_date, spread, index_id, overrides).
            bump_bp: size of the bump in basis points (default +1; added to every helper
                rate / spread; futures prices move by -bp/100).
            scope: ``all`` (discounting and forwarding curves together), ``discounting``
                or ``forwarding``.
            as_of: required unless ``market`` is a pricing block.

        Result: ``base_npv``, ``bumped_npv``, ``dv01 = bumped_npv - base_npv``,
        ``bumped_quotes`` (every quote moved, from/to) and ``calls`` = the two complete
        pricing results (each with its echoed request). Nothing else is computed.
        """
        return await swap_dv01_impl(
            backend,
            store,
            max_concurrency,
            market,
            trade,
            bump_bp,
            scope,
            as_of,
            calendar_overrides,
            request_id,
        )

    @app.tool()
    async def key_rate_ladder(
        market: Market,
        trade: SwapSpec,
        bump_bp: float = 1.0,
        curve: str | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Key-rate DV01 ladder: one reprice per pillar of a curve, plus a parallel bump.

        Args:
            market, trade, bump_bp, as_of: as in swap_dv01.
            curve: id of the curve whose pillars are the buckets (default: the trade's
                discounting curve). Buckets are that curve's ACTUAL points in wire order.

        Result: ``ladder`` = ordered ``[{pillar, quote_from, quote_to, bumped_npv, dv01}]``,
        ``parallel`` (all pillars bumped together), ``sum_of_buckets`` (sum of the ladder
        dv01s) and ``calls`` = base + parallel + one complete pricing result per pillar.
        Reprices run concurrently, bounded by ``QUANTRA_MAX_CONCURRENCY``.
        """
        return await key_rate_ladder_impl(
            backend,
            store,
            max_concurrency,
            market,
            trade,
            bump_bp,
            curve,
            as_of,
            calendar_overrides,
            request_id,
        )

    @app.tool()
    async def scenario(
        market: Market,
        trade: SwapSpec,
        scenarios: list[Scenario],
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Reprice a swap under named market variants and tabulate NPV vs base.

        Args:
            market, trade, as_of: as in swap_dv01.
            scenarios: ``[{name, bumps: [{curve, bp, pillar?}], replace_quotes:
                [{curve, pillar, value}]}]``. A bump without ``pillar`` moves every pillar
                of that curve; ``pillar`` is a label (``"5Y"``, ``"3x6"``) or a 0-based
                index; ``replace_quotes`` sets a pillar's quote to an explicit value.

        Result: ``table`` = ``[{name, npv, change = npv - base_npv, edits}]`` starting with
        ``base``; ``calls`` = one complete pricing result per row with the quotes moved.
        """
        return await scenario_impl(
            backend,
            store,
            max_concurrency,
            market,
            trade,
            scenarios,
            as_of,
            calendar_overrides,
            request_id,
        )

    @app.tool()
    async def fair_rate(
        market: Market,
        trade: SwapSpec,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """The engine's fair (par) rate of a swap, read from the pricing response.

        Args:
            market, trade, as_of: as in swap_dv01.

        Result: ``fair_rate`` / ``fair_spread`` exactly as the engine returned them (plus
        ``npv``); when the response carries neither, ``provided_by_engine`` is false and
        ``message`` says so (nothing is solved locally). ``calls[0]`` is the pricing.
        """
        return await fair_rate_impl(
            backend, store, market, trade, as_of, calendar_overrides, request_id
        )


__all__ = [
    "Bump",
    "QuoteReplacement",
    "Scenario",
    "SwapSpec",
    "fair_rate_impl",
    "key_rate_ladder_impl",
    "register",
    "scenario_impl",
    "swap_dv01_impl",
]
