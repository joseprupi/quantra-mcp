"""Prompts: step-by-step guides the assistant follows for a user who talks in
trade and market terms. Each guide tells the model which tools to call, in
which order, and how to talk about the outcome: in the user's language, with
every assumption stated, every number the engine's own. No prompt computes or
assumes a number."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

VOICE = """\
How to talk to the user: in trade and market terms (notional, strike, expiry, the
curve, the vol, the settlement style, day counts), never in plumbing terms. Do not
mention tool names, presets, sessions, request bodies, schemas or MCP unless the user
asks how it works. Every number you quote must come out of a tool result; say which
assumptions it rests on. Offer the complete engine request only when asked
("want the full request we sent?").
"""


def _price_a_swap(currency: str = "EUR", kind: str = "vanilla") -> str:
    block = "ois_swap" if kind == "ois" else "vanilla_swap"
    tool = "price_ois_swap" if kind == "ois" else "price_vanilla_swap"
    return f"""\
Goal: price a {currency} {kind} interest-rate swap with correct market conventions and
show the user exactly what was priced, in their terms.

{VOICE}
Do this, in order:
1. quantra_meta -> confirm the engine is up (tell the user only if it is not).
2. list_presets -> pick the convention set for the currency/index ({currency}): one with
   curve helpers (deposit/swap or ois) that also carries a `trades.{block}` block
   (EUR_EURIBOR_6M, EUR_ESTR_OIS, USD_SOFR_OIS, ...). get_preset(id) shows every
   convention and where it comes from; tell the user the conventions you will assume
   (fixed leg frequency and day count, floating index, calendar, settlement lag).
3. Market data: ask for (or take from the conversation) the valuation date and the par
   quotes of the curve (tenor + rate per pillar; "3.75%" is fine). Licensed vendor
   curves are never available here, the user has to paste them. If they paste a
   table use curve_from_pasted_table(kind="par" | "discount" | "zero"), otherwise
   build_curve(id, preset, quotes, reference_date=as_of). Read `notes`.
4. session_put(name, kind="curve", value=<built curve>) so the market can be reused.
5. {tool}(market={{"session": name}}, preset, swap_type, notional, fixed_rate,
   effective_date="spot", tenor="5Y" (or termination_date), discounting_curve=<curve id>,
   forwarding_curve=<curve id>, as_of=as_of, include_flows=true). Spot and the end date
   are resolved by the engine's calendar and reported in `date_resolution`.
6. Report: NPV and fair rate from `summary` (sign explained: positive = in the user's
   favour for the side they named), the resolved dates, and the assumptions list from
   `notes`. The full `request` is available on demand.
7. If `ok` is false: status 400 means the request shape is wrong (fix and retry
   silently); 422 means the trade cannot be priced as given (a fixing is missing, a
   date is impossible): explain the engine's `error` in plain words and what the user
   can supply to fix it.
Never compute a price, a discount factor or a rate yourself.
"""


def _bootstrap_from_strip(preset: str = "USD_SOFR_OIS") -> str:
    return f"""\
Goal: turn the user's par quotes into a discount curve (discount factors and zero
rates) and show them the grid.

{VOICE}
1. get_preset("{preset}") -> the conventions the curve will assume (calendar, day
   counts, fixed leg frequency, payment lag); tell the user which ones apply.
2. build_curve(id="{preset}", preset="{preset}", quotes=[{{type: "ois"|"deposit"|"swap"|
   "fra"|"future", tenor: "1Y", rate: 0.03}}, ...], reference_date=<valuation date>), or
   curve_from_pasted_table(kind="par") if the quotes arrive as a pasted table. Quotes are
   sorted by maturity; a duplicate pillar is rejected. Read `notes`.
3. build_query(curve_id="{preset}", measures=["DF", "ZERO"], tenors=[...],
   calendar=<the curve's calendar>, business_day_convention=<its convention>). For
   forwards add "FWD" and an explicit `fwd` block.
4. bootstrap_curve(curves=[<built curve>], as_of=<valuation date>, queries=[<query>]).
   The values are in `response.results[].series` (DF series has no `measure` label).
5. Show the grid as a table (date, discount factor, zero rate) and state the day count
   and compounding the zero rates use. For a verified reference see
   quantra://examples/sofr-bootstrap-request (50Y DF 0.262755579831).
6. session_put(name, "curve", <built curve>) if a trade is to be priced on it next.
"""


HOLIDAY_CHECK = (
    """\
Goal: check the engine's business calendar against the user's holiday list and price
with the corrections.

"""
    + VOICE
    + """
1. calendar_holidays(calendar, start_date, end_date) -> the engine's holidays. Compare
   with the user's list: dates the engine has but the user says are business days go
   to `removed_holidays`; dates the user has but the engine lacks go to
   `added_holidays`. Weekends cannot be removed.
2. Show the differences in plain words and build calendar_overrides=[{"calendar":
   <name>, "added_holidays": [...], "removed_holidays": [...]}].
3. Verify: calendar_holidays(..., calendar_overrides=overrides) now matches the user's
   list; calendar_advance(calendar, date, n, unit, convention, calendar_overrides)
   shows how a settlement date moves.
4. Pass the same `calendar_overrides` to bootstrap_curve / price_* (every pricing tool
   accepts it; it applies to curve helpers, indices and schedules alike). A worked case
   is the example irs_eur_5y_payer_overrides_added_payment_holidays (get_example).
Nothing is stored on the engine; the corrections live only in the request.
"""
)


EXPLORE_EXAMPLES = """\
Goal: find a complete, verified pricing example for a product and run or adapt it.

1. list_examples(product=<e.g. "swaption">) or list_examples(category=<folder>). Every
   row has the endpoint, a title and `reference_text`: the QuantLib value the engine is
   asserted to match for that request.
2. get_example(name) -> `body` (complete request), `endpoint`, `description`,
   `exercises`, `oracle`. Read `description`: what the trade is and which conventions it
   exercises.
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


def _price_from_screen(product: str = "") -> str:
    hint = f" The user says it is a {product}." if product.strip() else ""
    return f"""\
Goal: the user pasted a screenshot or a ticket (a Bloomberg SWPM screen, a term sheet,
a confirmation) and asks whether this trade can be priced here and what market data is
needed.{hint} Work like a desk quant talking to a trader.

{VOICE}
Step 1, read the ticket. From the image or text, write down in trade terms: product and
direction (payer/receiver, buyer/seller, cap/floor), notional and currency, the dates
(valuation date, effective/start, expiry, maturity or tenor), strike or fixed rate,
the underlying index (SOFR, Euribor 6M, ESTR...), settlement style (physical, cash;
for cash: collateralized cash price or par yield), the volatility quoted (normal in bp
or lognormal in %), and the number on the screen you will reconcile against (the spot
premium, i.e. the price paid today, not the forward premium). Say plainly whether it
can be priced: supported products are vanilla and overnight-index swaps, fixed /
floating / zero-coupon / callable bonds, FRAs, caps and floors, European / Bermudan /
American swaptions on vanilla or OIS underlyings, CDS, equity options, inflation swaps
and inflation caps/floors.

Step 2, list the market data that is missing, in a form the user can paste:
- The curve. Licensed vendor curves (Bloomberg ICVS/SWDF, Refinitiv, Markit...) are
  never available on this side; the user must paste them. Ask for either the discount
  factors from the Curves tab as `date, DF` rows (or `tenor, DF`), the zero rates as
  `date, zero %`, or the par quotes of the strip as `tenor, rate %`. Say which one is
  closest to what the screen shows; if the discount and forward curves differ, both
  are needed.
- The volatility: the normal vol in bp (or lognormal %) at the trade's expiry x tenor,
  or the ATM matrix if a surface is to be built.
- Anything the screen implies but does not show (payment lag, fixing lag, day counts).
Do not guess a vendor curve; never fill one in from memory.

Step 3, confirm the conventions before pricing: list every convention you will assume
(fixed and floating leg frequency and day count, business day rule, calendar,
settlement lag, payment lag, compounding of the overnight index, vol type and shift,
cash settlement method) and where they come from (get_preset(id) gives the source of
each). Ask the user to confirm or correct the ones the screen shows.

Step 4, build the market from what was pasted:
- discount factors: curve_from_pasted_table(text, id, kind="discount", preset=<the
  currency's convention set>, reference_date=<valuation date>) -> uses build_value_curve;
  the engine anchors the curve at the valuation date with DF 1.0 (the tool adds that
  anchor and says so).
- zero rates: curve_from_pasted_table(kind="zero", ...) and state the compounding the
  user's zeros use (default continuous); par quotes: kind="par" (bootstrapped with the
  preset's helpers).
  Read `parsed_rows`, `unparsed` and `notes`; tell the user about any row that could
  not be read. session_put(name, "curve", <result>) to reuse it.
- the vol: for a single quoted vol use vol={{"constant": <decimal>, "type": "Normal" |
  "Lognormal"}} on the pricing tool; for a matrix use the ATM-matrix form.

Step 5, price with the product's tool (price_swaption, price_vanilla_swap,
price_ois_swap, price_cap_floor, ...) with market={{"session": name}} or
{{"curves": [...], "indices": [...]}}, the preset for the currency, the trade economics
and the dates from the ticket. For a swaption on an overnight-index underlying use the
currency's OIS preset with underlying_type="OisSwap"; give the settlement_type and, for
cash settlement, the settlement_method the screen shows (CollateralizedCashPrice or
ParYieldCurve); the underlying's effective date may be "spot" (= expiry + the preset's
settlement days).
If `ok` is false, explain the engine's `error` in plain words and what to paste to fix it.

Step 6, reconcile. Put the engine's number next to the number on the screen (premium
vs premium, same unit and sign; fair rate vs fair rate), state the difference and the
most likely reasons in order (curve pasted at pillars only vs the vendor's full curve,
a convention that differs, forward vs spot premium, rounding of the pasted values).
Always show the engine's own numbers with the assumptions they rest on; never adjust a
number to make it match. The complete request is in `request` for anyone who wants to
audit or replay it; offer it, do not paste it unasked.
"""


def register(app: MCPServer) -> None:
    @app.prompt(
        name="price-a-swap",
        title="Price a swap from a quote strip",
        description=(
            "Price an interest-rate swap (fixed vs IBOR, or overnight index) from the user's "
            "par quotes with the currency's standard conventions, stating every assumption."
        ),
    )
    def price_a_swap(currency: str = "EUR", kind: str = "vanilla") -> str:
        """Args: currency (EUR/USD/GBP), kind ('vanilla' fixed-vs-IBOR or 'ois')."""
        return _price_a_swap(currency, "ois" if kind.lower() == "ois" else "vanilla")

    @app.prompt(
        name="bootstrap-from-strip",
        title="Build a discount curve from par quotes",
        description=(
            "Turn par quotes into discount factors and zero rates on a grid, with the "
            "conventions used stated."
        ),
    )
    def bootstrap_from_strip(preset: str = "USD_SOFR_OIS") -> str:
        """Args: preset id (list_presets)."""
        return _bootstrap_from_strip(preset)

    @app.prompt(
        name="holiday-check",
        title="Reconcile holidays and price with calendar corrections",
        description=(
            "Compare the engine's business calendar with the user's holiday list and price "
            "with the corrections applied."
        ),
    )
    def holiday_check() -> str:
        return HOLIDAY_CHECK

    @app.prompt(
        name="explore-examples",
        title="Find, run and adapt a verified pricing example",
        description=(
            "Browse the engine's verified example requests for a product, run one and adapt "
            "it to the user's trade."
        ),
    )
    def explore_examples() -> str:
        return EXPLORE_EXAMPLES

    @app.prompt(
        name="price-from-screen",
        title="Price a trade from a screenshot or ticket",
        description=(
            "From a pasted pricing screen or ticket: say whether it can be priced, list the "
            "missing market data in paste-able form, confirm conventions, build the curve "
            "from pasted discount factors or quotes, price, and reconcile against the screen."
        ),
    )
    def price_from_screen(product: str = "") -> str:
        """Args: product hint (optional), e.g. 'swaption', 'OIS swap', 'cap'."""
        return _price_from_screen(product)
