"""The two rules on market data and the rule on reading source, stated once; the server
instructions quote all three and every prompt the first two, verbatim (a test pins the
wording)."""

from __future__ import annotations

from quantra_mcp.schema.loader import quantlib_pin

ABSOLUTE_RULE = """\
ABSOLUTE RULE on market data: Never type, estimate, recall or invent market data
(quotes, DFs, rates, vols, fixings, correlations), not even as a placeholder, test or
approximation. Use only data (a) the user pasted or attached, (b) fetched in this
conversation with a web or code tool from a trusted publisher (central bank, treasury,
FRED, exchange, index administrator, SEC filing, fund sponsor; any other site only after
asking the user), or (c) from a data connector the user connected. Table every obtained
value with URL or connector and as-of date in the reply; derive values (correlation,
yields) with a code tool, method shown; label proxies (Treasury yields for SOFR, a
VIX-family index for a 2Y vol) with the bias. No fetch tool or connector: say so, ask for a
paste. Without data: say what is needed and stop; no curve or pricing tool call.
market_data_source has no value for estimated or recalled data; sourced needs `sources`.
"""

EXAMPLE_SHAPE_RULE = """\
The 247 vendored engine examples are request-SHAPE references only: never reuse their
market data for a user's trade or say "the engine already ships this trade"; an
example's data runs only when the user asks for that example.
"""

SOURCE_RULE = f"""\
HOW A NUMBER IS COMPUTED: explain_method first. If its page lacks the point, or the
engine instantiates a QuantLib class (PiecewiseYieldCurve, IterativeBootstrap, a pricer,
a helper), search_source / read_source that class in the engine and in QuantLib at the
pin and cite it (quantlib:ql/<file>@{quantlib_pin().tag}:Lx-Ly). Never "from memory", "general
knowledge" or "not verified here" while the file is readable; the only negative is "not
found in the engine or QuantLib source at this pin", after a search found nothing.
"""
