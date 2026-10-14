"""The two rules on market data and the rule on reading source, stated once; the server
instructions quote all three and every prompt the first two, verbatim (a test pins the
wording)."""

from __future__ import annotations

from quantra_mcp.schema.loader import quantlib_pin

ABSOLUTE_RULE = """\
ABSOLUTE RULE on market data: Never type, estimate, recall or invent market data (quotes,
discount factors, zero rates, vols, fixings). Not as a placeholder, not as a test run, not
labelled as approximate. Market data for a user's trade exists only when the user has
pasted or dictated it in this conversation. Until then: say what is needed, in paste-able
form, and stop. Do not call any curve-building or pricing tool. Every curve-building and
pricing tool requires a market_data_source declaration; none exists for estimated or
recalled data: ask instead.
"""

EXAMPLE_SHAPE_RULE = """\
The 223 vendored engine examples are request-SHAPE references only; a user's trade
market data MUST come from the user. Never reuse an example's market data for the
user's trade, never say "the engine already ships this trade"; an example's own data
runs only when the user explicitly asks for that example, reported as such. Without the
user's data, ask for it in paste-able form and stop there.
"""

SOURCE_RULE = f"""\
HOW A NUMBER IS COMPUTED: explain_method first. If its page lacks the point, or the
engine instantiates a QuantLib class (PiecewiseYieldCurve, IterativeBootstrap, a pricer,
a helper), search_source / read_source that class in the engine and in QuantLib at the
pin and cite it (quantlib:ql/<file>@{quantlib_pin().tag}:Lx-Ly). Never "from memory", "general
knowledge" or "not verified here" while the file is readable; the only negative is "not
found in the engine or QuantLib source at this pin", after a search found nothing.
"""
