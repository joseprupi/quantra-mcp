"""Tier 3: market construction — presets, curve builders, bootstrap.

``list_presets`` / ``get_preset`` / ``build_curve`` / ``build_value_curve`` /
``build_query`` never call the engine; they return explicit request fragments
plus ``notes`` listing every convention applied and where it came from.
``bootstrap_curve`` and ``bootstrap_inflation_curve`` POST to the engine and
return the uniform result shape; ``summary`` is a selection of response
fields, never a computation.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders import curves as cb
from quantra_mcp.builders import pasted_table as pt
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
from quantra_mcp.session import SessionStore, resolve_refs
from quantra_mcp.tools._market_source import MarketDataSource, stamp
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire

BOOTSTRAP_CURVES = "/bootstrap-curves"
BOOTSTRAP_INFLATION = "/bootstrap-inflation-curves"


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
    return built.as_result()


def build_curve_impl(
    id: str,
    preset: str,
    quotes: list[cb.CurveQuote],
    reference_date: str,
    trait: BootstrapTrait | None = None,
    interpolator: Interpolator | None = None,
    day_counter: DayCounter | None = None,
) -> ToolResult:
    try:
        p = get_preset(preset)
        built = cb.build_curve(id, p, quotes, reference_date, trait, interpolator, day_counter)
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
    store: SessionStore,
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


def _inflation_summary(response: Any) -> dict[str, Any] | None:
    return cb.bootstrap_summary(response)


async def bootstrap_inflation_curve_impl(
    backend: Backend, body: dict[str, Any], request_id: str | None = None
) -> ToolResult:
    rid = request_id or new_request_id()
    if not isinstance(body, dict):
        return local_error_result(
            BOOTSTRAP_INFLATION, body, "body must be a JSON object", request_id=rid
        )
    problems = validate_request(BOOTSTRAP_INFLATION, body)
    if problems:
        return local_error_result(
            BOOTSTRAP_INFLATION,
            body,
            f"request does not match the vendored schema for {BOOTSTRAP_INFLATION} "
            f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); nothing was sent",
            [p.as_dict() for p in problems],
            request_id=rid,
        )
    return await run_post(
        backend, BOOTSTRAP_INFLATION, body, request_id=rid, summarize=_inflation_summary
    )


def register(app: MCPServer, backend: Backend, store: SessionStore) -> None:
    @app.tool()
    def list_presets() -> dict[str, Any]:
        """Market-convention presets available to build_curve / build_value_curve.

        Each row: ``id``, ``currency``, ``index`` (the engine index id the
        preset registers), ``helpers`` (quote types it supports: deposit, fra,
        future, swap, ois), ``curve`` (day counter / interpolator / trait) and
        the ``provenance`` of the conventions. ``get_preset`` returns the data.
        """
        return {"ok": True, "presets": _list_presets()}

    @app.tool()
    def get_preset(id: str) -> dict[str, Any]:
        """One preset as data: index definition, curve settings, every helper
        convention block and the provenance of each field.

        Args:
            id: e.g. ``USD_SOFR_OIS``, ``EUR_ESTR_OIS``, ``GBP_SONIA_OIS``,
                ``GBP_SONIA_SWAP``, ``EUR_EURIBOR_6M``, ``EUR_EURIBOR_3M``.
        """
        try:
            return {"ok": True, "preset": get_preset_data(id)}
        except PresetError as exc:
            return {"ok": False, "error": str(exc)}

    @app.tool()
    def build_curve(
        id: str,
        preset: str,
        quotes: list[cb.CurveQuote],
        reference_date: str,
        market_data_source: MarketDataSource,
        trait: BootstrapTrait | None = None,
        interpolator: Interpolator | None = None,
        day_counter: DayCounter | None = None,
    ) -> ToolResult:
        """Turn a quote strip into an engine curve spec (no engine call).

        Args:
            id: curve id to register, e.g. ``USD_SOFR_OIS``.
            preset: a preset id from ``list_presets``; supplies the index
                definition and every helper convention.
            quotes: ``[{type: deposit|fra|future|swap|ois, tenor: "6M", rate: 0.052}, ...]``
                (fra: ``months_to_start``/``months_to_end``; future:
                ``future_start_date`` + ``price`` or ``rate``). Sorted by
                maturity; a duplicate (type, tenor) is rejected locally.
            reference_date: ``YYYY-MM-DD`` curve date (normally the pricing as_of).
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            trait: override the preset's bootstrap trait (``Discount``,
                ``ZeroRate``, ``FwdRate``).
            interpolator: override the preset's interpolator.
            day_counter: override the preset's curve day counter.

        Returns ``{ok, curve, indices, preset, notes}``: ``curve`` is the
        ``TermStructure`` and ``indices`` the ``IndexDef`` list to pass to
        ``bootstrap_curve`` (or to ``session_put``); ``notes`` lists every
        default applied with its source. Nothing is priced here.
        """
        return stamp(
            build_curve_impl(id, preset, quotes, reference_date, trait, interpolator, day_counter),
            market_data_source,
        )

    @app.tool()
    def build_value_curve(
        id: str,
        kind: cb.ValueKind,
        points: list[cb.ValuePoint],
        reference_date: str,
        market_data_source: MarketDataSource,
        preset: str | None = None,
        conventions: cb.ValueCurveConventions | None = None,
        compounding: Compounding | None = None,
        frequency: Frequency | None = None,
        interpolator: Interpolator | None = None,
    ) -> ToolResult:
        """An interpolated curve from explicit values (no engine call).

        Args:
            id: curve id.
            kind: ``zero`` (InterpolatedZero), ``discount`` (InterpolatedDiscount:
                first point must be the reference date with value 1.0) or
                ``forward`` (InterpolatedFwd: instantaneous continuously-compounded
                forwards; Linear/BackwardFlat/ForwardFlat only).
            points: ``[{date: "2026-01-15", value: 0.96}, {tenor: "2Y", value: ...}]``
                in order; the engine anchors the curve at the first point.
            reference_date: ``YYYY-MM-DD``.
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: take the curve day counter and point calendar/convention
                from this preset; or give ``conventions`` explicitly.
            conventions: ``{day_counter, calendar, business_day_convention}``.
            compounding, frequency: zero points only (default Continuous /
                Annual; all points share them).
            interpolator: default Linear (zero, forward) or LogLinear (discount).

        Returns ``{ok, curve, indices: [], preset, notes}``.
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

    @app.tool()
    def curve_from_pasted_table(
        text: str,
        id: str,
        kind: Literal["discount", "zero", "par"],
        market_data_source: MarketDataSource,
        preset: str | None = None,
        reference_date: str | None = None,
        conventions: cb.ValueCurveConventions | None = None,
        quote_type: HelperType | None = None,
        percent: bool | None = None,
        date_format: Literal["iso", "mdy", "dmy"] | None = None,
        compounding: Compounding | None = None,
        frequency: Frequency | None = None,
        interpolator: Interpolator | None = None,
    ) -> ToolResult:
        """A curve from a table the user pasted (a vendor curve screen, a spreadsheet,
        a ticket): parses it and calls build_value_curve (discount / zero) or
        build_curve (par quotes). No engine call; no arithmetic on the values.

        Args:
            text: the pasted rows. CSV / TSV / ';' / '|' / whitespace separated, header
                optional. Each row: a date (``2034-09-18``, ``18-Sep-2034``,
                ``09/18/2034`` with ``date_format``) or a tenor (``10Y``), then the
                value. ``%`` values are divided by 100; ``1,000.5`` loses its commas.
                An optional word per row (``ois``, ``swap``, ``deposit``) tags a par
                quote's type.
            id: curve id to register.
            kind: ``discount`` (discount factors -> InterpolatedDiscount), ``zero``
                (zero rates -> InterpolatedZero) or ``par`` (market quotes -> bootstrap
                helpers of the preset).
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: supplies the curve day counter and point calendar/convention
                (``USD_SOFR_OIS``...); required for ``par``; or give ``conventions``.
            reference_date: the curve / as-of date. For ``discount`` it may be omitted
                when the first row is that date with value 1.0.
            quote_type: par tables only: the helper type when the rows do not name one
                and the preset offers several.
            percent: ``true`` = every value is a percentage; default: only values
                written with ``%``.
            date_format: ``mdy`` / ``dmy`` for slash dates; inferred when a field exceeds
                12, otherwise required.
            compounding, frequency: zero tables only (default Continuous / Annual).
            interpolator: override the builder default.

        Returns the builder result (``{ok, curve, indices, preset, notes}``) plus
        ``parsed_rows`` (line, label, value as read), ``unparsed`` (line, text,
        reason) and ``header``. For a discount table whose first row is not the
        reference date, the anchor point ``{reference_date: 1.0}`` the engine
        requires is added in front and said so in ``notes``.
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

    @app.tool()
    def build_query(
        curve_id: str,
        measures: list[cb.Measure],
        tenors: list[str | dict[str, Any]] | None = None,
        range_grid: cb.RangeGrid | None = None,
        calendar: Calendar | None = None,
        business_day_convention: BusinessDayConvention | None = None,
        zero: cb.ZeroQuery | None = None,
        fwd: cb.FwdQuery | None = None,
    ) -> ToolResult:
        """A ``CurveQuerySpec`` for ``bootstrap_curve`` (no engine call).

        Args:
            curve_id: the curve to sample.
            measures: any of ``DF``, ``ZERO``, ``FWD``.
            tenors: TenorGrid, e.g. ``["1M", "6M", "1Y", "5Y", "10Y"]``; needs
                ``calendar`` + ``business_day_convention`` to roll each tenor.
            range_grid: RangeGrid alternative ``{end_date, step_number,
                step_time_unit, start_date?, business_days_only?, calendar?, ...}``.
            zero: options for ZERO (default: continuous, annual, curve day counter).
            fwd: required when FWD is requested (``forward_type`` Period + tenor,
                or Instantaneous + eps; compounding; frequency).

        Returns ``{ok, query, notes}``.
        """
        return build_query_impl(
            curve_id, measures, tenors, range_grid, calendar, business_day_convention, zero, fwd
        )

    @app.tool()
    async def bootstrap_curve(
        curves: list[dict[str, Any]],
        as_of: str,
        queries: list[dict[str, Any]],
        indices: list[dict[str, Any]] | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Bootstrap curves on the engine and sample them (POST /bootstrap-curves).

        Args:
            curves: items are ``TermStructure`` objects, ``build_curve`` results
                (``{curve, indices}``) or ``{"session": "<name>"}`` references
                to a stored ``curve`` or ``market``.
            as_of: ``YYYY-MM-DD`` valuation date (``pricing.as_of_date``).
            queries: ``CurveQuerySpec`` objects or ``build_query`` results.
            indices: extra ``IndexDef`` objects or ``{"session": name}`` refs;
                indices carried by build_curve results are added automatically.
                Identical duplicates are sent once; conflicting ids are rejected.
            calendar_overrides: per-request holiday corrections (engine >= 0.7.0).
            request_id: optional ``X-Request-Id``.

        The echoed ``request`` is the fully RESOLVED body. ``summary`` lists per
        curve ``{id, pillars, first_grid_date, last_grid_date, measures}``; the
        sampled values are in ``response.results[].series``.
        """
        return await bootstrap_curve_impl(
            backend, store, curves, as_of, queries, indices, calendar_overrides, request_id
        )

    @app.tool()
    async def bootstrap_inflation_curve(
        body: dict[str, Any], request_id: str | None = None
    ) -> ToolResult:
        """POST /bootstrap-inflation-curves with a raw request body (validated first).

        Args:
            body: the engine's ``BootstrapInflationCurvesRequest`` (see
                ``engine_schema('/bootstrap-inflation-curves')``); no preset
                support yet, the body is sent as given once it validates.
            request_id: optional ``X-Request-Id``.
        """
        return await bootstrap_inflation_curve_impl(backend, body, request_id)


def get_preset_data(preset_id: str) -> dict[str, Any]:
    return get_preset(preset_id).as_data()
