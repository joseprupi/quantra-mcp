"""Prompts: concrete tool-order guides. Each one names the tools to call, in
order, and tells the agent to read ``request`` and ``notes`` rather than trust
a summary. No prompt computes or assumes a number."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def _price_a_swap(currency: str = "EUR", kind: str = "vanilla") -> str:
    block = "ois_swap" if kind == "ois" else "vanilla_swap"
    tool = "price_ois_swap" if kind == "ois" else "price_vanilla_swap"
    return f"""\
Goal: price a {currency} {kind} interest-rate swap on the Quantra engine with correct
market conventions, and show the user exactly what was priced.

Do this, in order:
1. quantra_meta -> confirm the engine is up and note its API version.
2. list_presets -> pick the preset for the currency/index ({currency}): a curve preset
   (helpers: deposit/swap or ois) that also has a `trades.{block}` block
   (e.g. EUR_EURIBOR_6M, EUR_ESTR_OIS, USD_SOFR_OIS). get_preset(id) shows every convention
   and its provenance; tell the user which preset you chose and why.
3. Ask the user for (or take from the conversation) the as-of date and a quote strip
   (tenor + rate per pillar). Call build_curve(id, preset, quotes, reference_date=as_of).
   Read its `notes`: every helper convention and the index definition it applied.
4. session_put(name, kind="curve", value=<build_curve result>) so the market can be
   reused by name.
5. {tool}(market={{"session": name}}, preset, swap_type, notional, fixed_rate,
   effective_date="spot", tenor="5Y" (or termination_date), discounting_curve=<curve id>,
   forwarding_curve=<curve id>, as_of=as_of, include_flows=true).
   The tool resolves "spot" and the tenor through the engine's /calendar-advance and
   reports both in `date_resolution` and `notes`.
6. Read the result: `ok` must be true. `summary.swaps[0]` has npv and fair_rate selected
   from the engine response; `response.swaps[0]` is the full engine answer (flows when
   include_flows). `request` is the exact body sent: quote the dates, conventions and
   curve ids from it, never from memory. List the `notes` lines that name a default.
7. If `ok` is false: `status` 400 means the request shape is wrong (fix arguments and
   retry), 422 means the trade is unpriceable as given (missing fixing, bad dates):
   report the engine's `error` verbatim. engine_request(endpoint, request) replays the
   echoed body after edits.
Never compute a price, DF or rate yourself; every number must come from a tool result.
"""


def _bootstrap_from_strip(preset: str = "USD_SOFR_OIS") -> str:
    return f"""\
Goal: turn a quote strip into bootstrapped discount factors and zero rates.

1. get_preset("{preset}") -> read the helper conventions (calendar, day counters,
   frequencies, payment lag) and the index definition; tell the user which ones apply.
2. build_curve(id="{preset}", preset="{preset}", quotes=[{{type: "ois"|"deposit"|"swap"|"fra"|
   "future", tenor: "1Y", rate: 0.03}}, ...], reference_date=<as_of>). Quotes are sorted
   by maturity; a duplicate pillar is rejected locally. Read `notes`.
3. build_query(curve_id="{preset}", measures=["DF", "ZERO"], tenors=[...],
   calendar=<preset index calendar>, business_day_convention=<preset index convention>).
   For forwards add "FWD" and an explicit `fwd` block (no default is applied).
4. bootstrap_curve(curves=[<build_curve result>], as_of=<as_of>, queries=[<build_query
   result>]). `summary.curves` lists pillars and grid span; the values are in
   `response.results[].series` (the engine omits `measure` for DF).
5. Report the grid from `response`, and the full `request` on demand. For a worked,
   verified example read quantra://examples/sofr-bootstrap-request (50Y DF 0.262755579831).
6. session_put(name, "curve", <build_curve result>) if you will price on this curve next.
"""


HOLIDAY_CHECK = """\
Goal: check a calendar against the user's holiday list and price with corrections.

1. calendar_holidays(calendar, start_date, end_date) -> the engine's QuantLib holidays.
   Compare with the user's list: dates the engine has but the user says are business
   days go to `removed_holidays`; dates the user has but the engine lacks go to
   `added_holidays`. Weekends cannot be removed.
2. Show the differences and build calendar_overrides=[{"calendar": <name>,
   "added_holidays": [...], "removed_holidays": [...]}].
3. Verify: calendar_holidays(..., calendar_overrides=overrides) must now match the user's
   list; calendar_advance(calendar, date, n, unit, convention, calendar_overrides=overrides)
   shows how a settlement date moves.
4. Pass the same `calendar_overrides` to bootstrap_curve / price_* (every tool with a
   `pricing` block accepts it; it applies to curve helpers, indices and schedules alike
   and is echoed in `request.pricing.calendar_overrides`). The engine 0.7.0 doc is at
   quantra://docs/http-api; a worked case is the example
   irs_eur_5y_payer_overrides_added_payment_holidays (get_example).
Nothing is stored on the engine; overrides live only in the request.
"""


EXPLORE_EXAMPLES = """\
Goal: find a complete, verified engine request for a product and run or adapt it.

1. list_examples(product=<e.g. "swaption">) or list_examples(category=<folder>). Every
   row has the endpoint, a title and `reference_text`: the QuantLib value the engine is
   asserted to match for that request (NPVs to the cent; series/calibration cases are
   described instead).
2. get_example(name) -> `body` (complete request), `endpoint`, `description`,
   `exercises`, `oracle`. Read `description`: it says what the trade is and which
   conventions it exercises.
3. Run it as is: engine_request(endpoint, body). Compare the primary number in
   `response` with `reference_value`; report both.
4. Adapt it two ways:
   a. Edit `body` (notional, rate, dates) and engine_request again (validated locally
      against the vendored spec first; `problems` lists JSON-pointer paths).
   b. Keep `body["pricing"]` as the `market` of the matching price_* tool and rebuild the
      trade from a preset with new economics; the tool's `notes` show every convention
      it applied and `request` is the exact body sent, so you can diff it against the
      example.
5. engine_schema(endpoint) and list_enums(name) explain any field; the catalog with all
   descriptions is quantra://docs/engine-catalog.
"""


def register(app: MCPServer) -> None:
    @app.prompt(
        name="price-a-swap",
        title="Price a swap from a quote strip",
        description=(
            "meta -> preset -> build_curve -> session -> price_vanilla_swap / price_ois_swap, "
            "reading request and notes."
        ),
    )
    def price_a_swap(currency: str = "EUR", kind: str = "vanilla") -> str:
        """Args: currency (EUR/USD/GBP), kind ('vanilla' fixed-vs-IBOR or 'ois')."""
        return _price_a_swap(currency, "ois" if kind.lower() == "ois" else "vanilla")

    @app.prompt(
        name="bootstrap-from-strip",
        title="Bootstrap a curve from a strip",
        description=(
            "get_preset -> build_curve -> build_query -> bootstrap_curve; read the grid "
            "from response."
        ),
    )
    def bootstrap_from_strip(preset: str = "USD_SOFR_OIS") -> str:
        """Args: preset id (list_presets)."""
        return _bootstrap_from_strip(preset)

    @app.prompt(
        name="holiday-check",
        title="Reconcile holidays and price with calendar overrides",
        description=(
            "calendar_holidays -> diff -> calendar_overrides -> verify -> pass to pricing tools."
        ),
    )
    def holiday_check() -> str:
        return HOLIDAY_CHECK

    @app.prompt(
        name="explore-examples",
        title="Find, run and adapt a verified engine example",
        description=(
            "list_examples -> get_example -> engine_request; or reuse the example's pricing "
            "block as a market."
        ),
    )
    def explore_examples() -> str:
        return EXPLORE_EXAMPLES
