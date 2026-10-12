"""Tier 3: market construction — presets, curve builders, bootstrap.

``preset`` / ``build_curve`` / ``build_value_curve`` / ``build_query`` never call
the engine; they return explicit request fragments plus ``notes`` listing every
convention applied and where it came from. ``bootstrap_curve`` POSTs to the
engine and returns the uniform result shape; ``summary`` is a selection of
response fields, never a computation. (``/bootstrap-inflation-curves`` has no
builder: ``engine_request`` takes its raw body.)
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders import curves as cb
from quantra_mcp.builders import pasted_table as pt
from quantra_mcp.digest import stamp_built
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import HelperType, PresetError, get_preset
from quantra_mcp.presets.registry import list_presets as _list_presets
from quantra_mcp.schema.enums_generated import (
    BootstrapTrait,
    BusinessDayConvention,
    Calendar,
    Compounding,
    DayCounter,
    Frequency,
    Interpolator,
)
from quantra_mcp.schema.validate import validate_component, validate_request
from quantra_mcp.session import ScratchStore, resolve_refs
from quantra_mcp.tools._market_source import SOURCE_DESCRIPTION, MarketDataSource, stamp
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire

BOOTSTRAP_CURVES = "/bootstrap-curves"


def _check_built(built: cb.BuiltCurve) -> ToolResult:
    """Self-check a built fragment against the vendored spec before handing it out."""
    problems = [p.as_dict() for p in validate_component("TermStructure", built.curve)]
    for i, ix in enumerate(built.indices):
        problems += [
            {"path": f"/indices/{i}" + p.path, "message": p.message}
            for p in validate_component("IndexDef", ix)
        ]
    if problems:
        return local_error_result(
            BOOTSTRAP_CURVES,
            {"curve": built.curve, "indices": built.indices},
            "built fragment does not match the vendored spec (this is a server bug; please report)",
            problems,
        )
    return stamp_built(built.as_result())


def build_curve_impl(
    id: str,
    preset: str,
    quotes: list[cb.CurveQuote],
    reference_date: str,
    trait: BootstrapTrait | None = None,
    interpolator: Interpolator | None = None,
    day_counter: DayCounter | None = None,
    discount_curve: str | None = None,
) -> ToolResult:
    try:
        p = get_preset(preset)
        built = cb.build_curve(
            id,
            p,
            quotes,
            reference_date,
            trait,
            interpolator,
            day_counter,
            discount_curve=discount_curve,
        )
    except PresetError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, str(exc))
    except LocalValidationError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, exc.error, exc.problems)
    return _check_built(built)


def build_value_curve_impl(
    id: str,
    kind: cb.ValueKind,
    points: list[cb.ValuePoint],
    reference_date: str,
    preset: str | None = None,
    conventions: cb.ValueCurveConventions | None = None,
    compounding: Compounding | None = None,
    frequency: Frequency | None = None,
    interpolator: Interpolator | None = None,
) -> ToolResult:
    try:
        p = get_preset(preset) if preset is not None else None
        built = cb.build_value_curve(
            id, kind, points, reference_date, p, conventions, compounding, frequency, interpolator
        )
    except PresetError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, str(exc))
    except LocalValidationError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, exc.error, exc.problems)
    return _check_built(built)


ALL_HELPER_TYPES: tuple[str, ...] = ("deposit", "fra", "future", "swap", "ois")


def curve_from_pasted_table_impl(
    text: str,
    id: str,
    kind: pt.TableKind,
    preset: str | None = None,
    reference_date: str | None = None,
    conventions: cb.ValueCurveConventions | None = None,
    quote_type: HelperType | None = None,
    percent: bool | None = None,
    date_format: pt.DateFormat | None = None,
    compounding: Compounding | None = None,
    frequency: Frequency | None = None,
    interpolator: Interpolator | None = None,
) -> ToolResult:
    """Parse the table, then hand the rows to build_value_curve (discount / zero)
    or build_curve (par quotes). Parsing notes and unreadable rows ride along."""
    if kind not in ("discount", "zero", "par"):
        return local_error_result(
            BOOTSTRAP_CURVES, None, f"kind must be discount, zero or par (got {kind!r})"
        )
    helper_types: tuple[str, ...] = ALL_HELPER_TYPES
    p = None
    if preset is not None:
        try:
            p = get_preset(preset)
        except PresetError as exc:
            return local_error_result(BOOTSTRAP_CURVES, None, str(exc))
        if p.helpers is not None:
            helper_types = tuple(p.helpers.available)
    table = pt.parse_table(
        text, kind, percent=percent, date_format=date_format, helper_types=helper_types
    )
    extra: dict[str, Any] = {
        "parsed_rows": [r.as_dict() for r in table.rows],
        "unparsed": table.unparsed,
        "header": table.header,
    }

    def fail(error: str, problems: list[dict[str, str]] | None = None) -> ToolResult:
        out = local_error_result(BOOTSTRAP_CURVES, None, error, problems)
        out["notes"] = table.notes
        out.update(extra)
        return out

    if not table.rows:
        return fail(
            "no row of the pasted table could be read as <date or tenor> <value>; "
            "see unparsed for the reason per line"
        )
    notes = list(table.notes)

    ref = reference_date
    if ref is None:
        first = table.rows[0]
        if kind == "discount" and first.date is not None and first.value == 1.0:
            ref = first.date
            notes.append(f"reference_date={ref!r} taken from the first row (discount factor 1.0)")
        else:
            return fail(
                "reference_date is required: the curve date (the as-of / valuation date the "
                "table was taken at), YYYY-MM-DD"
            )

    if kind == "par":
        quotes: list[cb.CurveQuote] = []
        single = helper_types[0] if len(helper_types) == 1 else None
        for r in list(table.rows):
            if r.tenor is None:
                table.unparsed.append(
                    {
                        "line": r.line_no,
                        "text": r.text,
                        "reason": "par quotes need a tenor (e.g. 5Y), not a date",
                    }
                )
                continue
            t = r.helper_type or (str(quote_type) if quote_type is not None else None) or single
            if t is None:
                return fail(
                    "quote_type is required for a par table when the preset offers several "
                    f"quote types {list(helper_types)} and the rows do not name one"
                )
            quotes.append(cb.CurveQuote(type=t, tenor=r.tenor, rate=r.value))  # type: ignore[arg-type]
        extra["unparsed"] = table.unparsed
        if not quotes:
            return fail("no par quote row with a tenor could be used")
        if preset is None:
            return fail("par tables need a preset (its helper conventions build the curve)")
        src = (
            "rows tagged per line"
            if any(r.helper_type for r in table.rows)
            else (
                f"quote_type={quote_type!s} (argument)"
                if quote_type
                else f"the preset's only quote type {single!r}"
            )
        )
        notes.append(f"par quotes -> build_curve helpers of type from {src}")
        result = build_curve_impl(id, preset, quotes, ref, None, interpolator, None)
    else:
        points = [cb.ValuePoint(date=r.date, tenor=r.tenor, value=r.value) for r in table.rows]
        if kind == "discount":
            head = points[0]
            at_ref = head.date == ref or (isinstance(head.tenor, dict) and head.tenor.get("n") == 0)
            if not at_ref:
                points.insert(0, cb.ValuePoint(date=ref, value=1.0))
                notes.append(
                    f"added the anchor point {{date: {ref}, discount_factor: 1.0}} in front: "
                    "the engine requires the first discount point to be 1.0 at the reference "
                    "date (a discount factor of 1.0 on the curve date is a definition, not a "
                    "computed value); your pasted values are unchanged"
                )
        result = build_value_curve_impl(
            id, kind, points, ref, preset, conventions, compounding, frequency, interpolator
        )
    result["notes"] = notes + list(result.get("notes", []))
    result.update(extra)
    return result


def build_query_impl(
    curve_id: str,
    measures: list[cb.Measure],
    tenors: list[str | dict[str, Any]] | None = None,
    range_grid: cb.RangeGrid | None = None,
    calendar: Calendar | None = None,
    business_day_convention: BusinessDayConvention | None = None,
    zero: cb.ZeroQuery | None = None,
    fwd: cb.FwdQuery | None = None,
) -> ToolResult:
    try:
        built = cb.build_query(
            curve_id, measures, tenors, range_grid, calendar, business_day_convention, zero, fwd
        )
    except LocalValidationError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, exc.error, exc.problems)
    problems = [p.as_dict() for p in validate_component("CurveQuerySpec", built.query)]
    if problems:
        return local_error_result(
            BOOTSTRAP_CURVES,
            built.query,
            "built query does not match the vendored spec (this is a server bug; please report)",
            problems,
        )
    return built.as_result()


def _unwrap_query(item: Any, i: int) -> dict[str, Any]:
    if isinstance(item, dict) and isinstance(item.get("query"), dict) and "curve_id" not in item:
        return dict(item["query"])  # a build_query result
    if isinstance(item, dict):
        return item
    raise LocalValidationError(
        f"queries[{i}]: expected a CurveQuerySpec object or a build_query result",
        [{"path": f"/queries/{i}", "message": "not an object"}],
    )


async def bootstrap_curve_impl(
    backend: Backend,
    store: ScratchStore,
    curves: list[dict[str, Any]],
    as_of: str,
    queries: list[dict[str, Any]],
    indices: list[dict[str, Any]] | None = None,
    calendar_overrides: list[CalendarOverride] | None = None,
    request_id: str | None = None,
) -> ToolResult:
    rid = request_id or new_request_id()
    try:
        if not curves:
            raise LocalValidationError(
                "curves: at least one curve is required", [{"path": "/curves", "message": "empty"}]
            )
        if not queries:
            raise LocalValidationError(
                "queries: at least one query is required (build_query makes one)",
                [{"path": "/queries", "message": "empty"}],
            )
        r_curves, r_indices, notes = resolve_refs(store, curves, indices)
        merged, merge_notes = cb.merge_indices(r_indices)
        notes += merge_notes
        qs = [_unwrap_query(q, i) for i, q in enumerate(queries)]
        body = cb.bootstrap_request(
            r_curves, merged, as_of, qs, overrides_to_wire(calendar_overrides)
        )
    except LocalValidationError as exc:
        return local_error_result(BOOTSTRAP_CURVES, None, exc.error, exc.problems, request_id=rid)
    problems = validate_request(BOOTSTRAP_CURVES, body)
    if problems:
        return local_error_result(
            BOOTSTRAP_CURVES,
            body,
            f"request does not match the vendored schema for {BOOTSTRAP_CURVES} "
            f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); nothing was sent",
            [p.as_dict() for p in problems],
            request_id=rid,
        )
    result = await run_post(
        backend, BOOTSTRAP_CURVES, body, request_id=rid, summarize=cb.bootstrap_summary
    )
    if notes:
        result["notes"] = notes
    return result


def register(app: MCPServer, backend: Backend, store: ScratchStore) -> None:
    @tool(app, "Market-convention presets", engine=False)
    def preset(
        id: Annotated[
            str | None,
            Field(
                description="A preset id (USD_SOFR_OIS, EUR_ESTR_OIS, GBP_SONIA_OIS, "
                "GBP_SONIA_SWAP, EUR_EURIBOR_6M, EUR_EURIBOR_3M, EUR_FIXED_BOND, DE_BUND, "
                "US_TREASURY, UK_GILT, EUR_CDS, EUR_EQUITY, EUR_HICP). Omit it to list every "
                "preset.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """The market-convention presets (no engine call). Without ``id``: one row per preset
        (id, currency, the engine index it registers, the quote types its curve helpers
        support, curve day counter / interpolator / trait, trade blocks, provenance). With
        ``id``: the preset as data: index definition, curve settings, every helper and trade
        convention block, and the provenance of each field (``field_provenance``).
        """
        return preset_impl(id)

    @tool(app, "Curve spec from a quote strip (no engine call)", engine=False)
    def build_curve(
        id: Annotated[str, Field(description="Curve id to register, e.g. USD_SOFR_OIS.")],
        preset: Annotated[
            str,
            Field(
                description=(
                    "Preset id (preset() lists them); supplies the index definition and every "
                    "helper convention."
                )
            ),
        ],
        quotes: Annotated[
            list[cb.CurveQuote],
            Field(
                description=(
                    "[{type: deposit|fra|future|swap|ois, tenor: '6M', rate: 0.052}, ...]; fra: "
                    "months_to_start / months_to_end; future: future_start_date + price or rate. "
                    "Sorted by maturity; a duplicate (type, tenor) is rejected."
                )
            ),
        ],
        reference_date: Annotated[
            str,
            Field(
                description="Curve date YYYY-MM-DD (normally the pricing as_of).",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        trait: Annotated[
            BootstrapTrait | None,
            Field(
                description="Override the preset's bootstrap trait: Discount, ZeroRate or FwdRate."
            ),
        ] = None,
        interpolator: Annotated[
            Interpolator | None, Field(description="Override the preset's interpolator.")
        ] = None,
        day_counter: Annotated[
            DayCounter | None, Field(description="Override the preset's curve day counter.")
        ] = None,
        discount_curve: Annotated[
            str | None,
            Field(
                description=(
                    "Id of ANOTHER curve in the same request (bootstrap_curve curves or a "
                    "price_* market) that discounts the swap / OIS helpers' cashflows "
                    "(deps.discount_curve on every SwapHelper / OISHelper point; deposits, FRAs "
                    "and futures untouched). Required for a dual-curve bootstrap of an IBOR "
                    "curve under OIS discounting; omitted = self-discounted (the engine "
                    "fixtures' behaviour). Either curve may come first in the request."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Turn a quote strip into an engine curve spec (no engine call). Returns ``{ok, curve,
        indices, preset, notes, digest}``: ``curve`` is the TermStructure and ``indices`` the
        IndexDef list to pass to bootstrap_curve, a pricing tool's market or session_put;
        ``notes`` lists every default applied with its source; ``digest`` is the sha256 of the
        canonical curve + indices JSON (pricing tools recognise it). Nothing is priced here.
        ``discount_curve`` names the OIS curve for a dual-curve (collateral-discounted) strip.
        """
        return stamp(
            build_curve_impl(
                id,
                preset,
                quotes,
                reference_date,
                trait,
                interpolator,
                day_counter,
                discount_curve=discount_curve,
            ),
            market_data_source,
        )

    @tool(app, "Curve spec from explicit values (no engine call)", engine=False)
    def build_value_curve(
        id: Annotated[str, Field(description="Curve id.")],
        kind: Annotated[
            cb.ValueKind,
            Field(
                description=(
                    "zero (InterpolatedZero), discount (InterpolatedDiscount: first point = "
                    "reference date with value 1.0) or forward (InterpolatedFwd, instantaneous "
                    "continuous forwards; Linear / BackwardFlat / ForwardFlat only)."
                )
            ),
        ],
        points: Annotated[
            list[cb.ValuePoint],
            Field(
                description=(
                    "[{date: '2026-01-15', value: 0.96}, {tenor: '2Y', value: ...}] in order; the "
                    "engine anchors the curve at the first point."
                )
            ),
        ],
        reference_date: Annotated[
            str, Field(description="Curve date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$")
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str | None,
            Field(
                description=(
                    "Take the curve day counter and the points' calendar / convention from this "
                    "preset; or give conventions."
                )
            ),
        ] = None,
        conventions: Annotated[
            cb.ValueCurveConventions | None,
            Field(
                description=(
                    "{day_counter, calendar, business_day_convention} when no preset is given."
                )
            ),
        ] = None,
        compounding: Annotated[
            Compounding | None,
            Field(
                description=(
                    "Zero points only: their compounding (default Continuous; all points share it)."
                )
            ),
        ] = None,
        frequency: Annotated[
            Frequency | None,
            Field(description="Zero points only: their compounding frequency (default Annual)."),
        ] = None,
        interpolator: Annotated[
            Interpolator | None,
            Field(description="Default Linear (zero, forward) or LogLinear (discount)."),
        ] = None,
    ) -> ToolResult:
        """An interpolated curve from explicit zero rates, discount factors or forwards (no engine
        call). Returns ``{ok, curve, indices: [], preset, notes, digest}``.
        """
        return stamp(
            build_value_curve_impl(
                id,
                kind,
                points,
                reference_date,
                preset,
                conventions,
                compounding,
                frequency,
                interpolator,
            ),
            market_data_source,
        )

    @tool(app, "Curve spec from a pasted table (no engine call)", engine=False)
    def curve_from_pasted_table(
        text: Annotated[
            str,
            Field(
                description=(
                    "The pasted rows: CSV / TSV / ';' / '|' / whitespace separated, header "
                    "optional. Each row: a date (2034-09-18, 18-Sep-2034, 09/18/2034 with "
                    "date_format) or a tenor (10Y), then the value; '%' values are divided by "
                    "100, thousands separators dropped; an optional word per row (ois, swap, "
                    "deposit) tags a par quote's type."
                )
            ),
        ],
        id: Annotated[str, Field(description="Curve id to register.")],
        kind: Annotated[
            Literal["discount", "zero", "par"],
            Field(
                description=(
                    "discount (discount factors -> InterpolatedDiscount), zero (zero rates -> "
                    "InterpolatedZero) or par (market quotes -> the preset's bootstrap helpers)."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str | None,
            Field(
                description=(
                    "Supplies the curve day counter and the points' calendar / convention; "
                    "required for par; or give conventions."
                )
            ),
        ] = None,
        reference_date: Annotated[
            str | None,
            Field(
                description=(
                    "The curve / as-of date YYYY-MM-DD; for discount it may be omitted when the "
                    "first row is that date with value 1.0."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        conventions: Annotated[
            cb.ValueCurveConventions | None,
            Field(
                description=(
                    "{day_counter, calendar, business_day_convention} when no preset is given."
                )
            ),
        ] = None,
        quote_type: Annotated[
            HelperType | None,
            Field(
                description=(
                    "Par tables only: the helper type when the rows do not name one and the "
                    "preset offers several."
                )
            ),
        ] = None,
        percent: Annotated[
            bool | None,
            Field(
                description=(
                    "true: every value is a percentage; default: only values written with '%'."
                )
            ),
        ] = None,
        date_format: Annotated[
            Literal["iso", "mdy", "dmy"] | None,
            Field(
                description=(
                    "mdy or dmy for slash dates; inferred when a field exceeds 12, otherwise "
                    "required."
                )
            ),
        ] = None,
        compounding: Annotated[
            Compounding | None,
            Field(description="Zero tables only: the zeros' compounding (default Continuous)."),
        ] = None,
        frequency: Annotated[
            Frequency | None,
            Field(description="Zero tables only: the zeros' frequency (default Annual)."),
        ] = None,
        interpolator: Annotated[
            Interpolator | None, Field(description="Override the builder default.")
        ] = None,
    ) -> ToolResult:
        """A curve from a table the user pasted (a vendor curve screen, a spreadsheet, a ticket):
        parses it and calls build_value_curve (discount / zero) or build_curve (par quotes). No
        engine call; no arithmetic on the values. Returns the builder result (``{ok, curve,
        indices, preset, notes, digest}``) plus ``parsed_rows`` (line, label, value as read),
        ``unparsed`` (line, text, reason) and ``header``. For a discount table whose first row
        is not the reference date, the anchor point {reference_date: 1.0} the engine requires is
        added in front and said so in ``notes``.
        """
        return stamp(
            curve_from_pasted_table_impl(
                text,
                id,
                kind,
                preset,
                reference_date,
                conventions,
                quote_type,
                percent,
                date_format,
                compounding,
                frequency,
                interpolator,
            ),
            market_data_source,
        )

    @tool(app, "Curve query spec (no engine call)", engine=False)
    def build_query(
        curve_id: Annotated[str, Field(description="The curve to sample.")],
        measures: Annotated[list[cb.Measure], Field(description="Any of DF, ZERO, FWD.")],
        tenors: Annotated[
            list[str | dict[str, Any]] | None,
            Field(
                description=(
                    "TenorGrid, e.g. ['1M', '6M', '1Y', '5Y', '10Y']; needs calendar + "
                    "business_day_convention to roll each tenor."
                )
            ),
        ] = None,
        range_grid: Annotated[
            cb.RangeGrid | None,
            Field(
                description=(
                    "RangeGrid alternative: {end_date, step_number, step_time_unit, start_date?, "
                    "business_days_only?, calendar?, ...}."
                )
            ),
        ] = None,
        calendar: Annotated[
            Calendar | None, Field(description="Calendar used to roll the tenors.")
        ] = None,
        business_day_convention: Annotated[
            BusinessDayConvention | None, Field(description="Convention used to roll the tenors.")
        ] = None,
        zero: Annotated[
            cb.ZeroQuery | None,
            Field(
                description=(
                    "Options for ZERO (default: continuous, annual, the curve's day counter)."
                )
            ),
        ] = None,
        fwd: Annotated[
            cb.FwdQuery | None,
            Field(
                description=(
                    "Required when FWD is requested: forward_type Period + tenor, or "
                    "Instantaneous + eps; compounding; frequency."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """A CurveQuerySpec for bootstrap_curve (no engine call). Returns ``{ok, query, notes}``."""
        return build_query_impl(
            curve_id, measures, tenors, range_grid, calendar, business_day_convention, zero, fwd
        )

    @tool(app, "Bootstrap and sample curves (POST /bootstrap-curves)", engine=True)
    async def bootstrap_curve(
        curves: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    'TermStructure objects, build_curve results ({curve, indices}) or {"session": '
                    "name} references to a stored curve or market."
                )
            ),
        ],
        as_of: Annotated[
            str,
            Field(
                description="Valuation date YYYY-MM-DD (pricing.as_of_date).",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ],
        queries: Annotated[
            list[dict[str, Any]],
            Field(description="CurveQuerySpec objects or build_query results."),
        ],
        indices: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    'Extra IndexDef objects or {"session": name} refs; indices carried by '
                    "build_curve results are added automatically. Identical duplicates are sent "
                    "once; conflicting ids are rejected."
                )
            ),
        ] = None,
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
        request_id: Annotated[
            str | None,
            Field(
                description=(
                    "X-Request-Id to forward; generated when absent and reported in "
                    "engine.request_id."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Bootstrap curves on the engine and sample them (POST /bootstrap-curves). The echoed
        ``request`` is the fully resolved body; ``summary`` lists per curve {id, pillars,
        first_grid_date, last_grid_date, measures}; the sampled values are in
        ``response.results[].series``.
        """
        return await bootstrap_curve_impl(
            backend, store, curves, as_of, queries, indices, calendar_overrides, request_id
        )


def get_preset_data(preset_id: str) -> dict[str, Any]:
    return get_preset(preset_id).as_data()


def preset_impl(preset_id: str | None) -> dict[str, Any]:
    if preset_id is None:
        return {"ok": True, "presets": _list_presets()}
    try:
        return {"ok": True, "preset": get_preset_data(preset_id)}
    except PresetError as exc:
        return {"ok": False, "error": str(exc)}
