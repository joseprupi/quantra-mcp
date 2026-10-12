"""The two rules on market data, stated once; the server instructions and every prompt
quote them verbatim (a test pins the wording)."""

from __future__ import annotations

ABSOLUTE_RULE = """\
ABSOLUTE RULE on market data: Never type, estimate, recall or invent market data (quotes,
discount factors, zero rates, vols, fixings). Not as a placeholder, not as a test run, not
labelled as approximate. Market data for a user's trade exists only when the user has
pasted or dictated it in this conversation. Until then: say what is needed, in paste-able
form, and stop. Do not call any curve-building or pricing tool. Every curve-building and
pricing tool requires a market_data_source declaration (user_pasted, user_file,
engine_example, session); none means estimated or recalled data, so if you would have to
invent numbers, ask instead.
"""

EXAMPLE_SHAPE_RULE = """\
The 223 vendored engine examples are request-SHAPE references only. For a trade the user
brought, the market data MUST come from the user. Never reuse an example's market data
for the user's trade, and never say "the engine already ships this trade". An example's
own data runs only when the user explicitly asks for that example, and is reported as the
example's data. Without the user's data, ask for it in paste-able form and stop there.
"""
