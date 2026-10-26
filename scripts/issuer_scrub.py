"""Operator rule: nothing issuer-, vendor- or real-security-specific is reachable through
the connector, so a model can never reuse a real trade's market data or result.

Shared (standard library only) by ``pin_engine.py`` (vendored examples, docs),
``methodology_gen.py`` (excerpts) and ``fetch_sources.py`` (the ``search_source`` /
``read_source`` engine tree, also run in the Docker build stage).

* ``excluded_name(name)``: an engine example whose FILE NAME carries an issuer token or
  an ISIN / CUSIP-like token is not vendored.
* ``scrub(path, data)``: the engine docs that describe such examples are rewritten LINE
  FOR LINE (the line count never changes, so every ``path@tag:La-Lb`` citation and
  GitHub permalink stays exact). After the rewrite no line of any engine text file may
  match ``ISSUER_TEXT_RE``; a re-pin that brings new issuer text fails loudly here
  instead of shipping it.
"""

from __future__ import annotations

import re

#: issuer / dealer tokens of an example file name (split on ``_``, ``-``, ``.``).
ISSUER_TOKENS = frozenset(
    {
        "bnp",
        "bnpp",
        "santander",
        "jpm",
        "jpmorgan",
        "scotia",
        "scotiabank",
        "bns",
        "goldman",
        "gs",
        "morgan",
        "morganstanley",
        "ms",
        "citi",
        "citigroup",
        "barclays",
        "ubs",
        "hsbc",
        "societe",
        "socgen",
        "sg",
        "credit_suisse",
        "creditsuisse",
        "cs",
        "deutsche",
        "db",
        "nomura",
        "natixis",
        "rbc",
        "bofa",
        "wells",
        "idad",
    }
)
#: ISIN: 2 letters + 9 alphanumerics + check digit, with at least 6 digits (so that a
#: descriptive token like ``cms10y`` never matches). CUSIP: 9 characters, 3 leading
#: digits, digit check.
_ISIN_RE = re.compile(r"^[a-z]{2}[0-9a-z]{9}[0-9]$")
_CUSIP_RE = re.compile(r"^[0-9]{3}[0-9a-z]{5}[0-9]$")


def _security_like(token: str) -> bool:
    if _ISIN_RE.match(token) and sum(c.isdigit() for c in token) >= 6:
        return True
    return bool(_CUSIP_RE.match(token))


def excluded_name(name: str) -> str | None:
    """The offending token of an example file name (stem or path), or ``None``."""
    stem = name.rsplit("/", 1)[-1].lower()
    stem = stem.removesuffix(".json")
    if "credit_suisse" in stem:
        return "credit_suisse"
    for token in re.split(r"[_\-.]+", stem):
        if token in ISSUER_TOKENS:
            return token
        if _security_like(token):
            return token
    return None


#: issuer / real-security text that must not appear in any served engine text file.
ISSUER_TEXT_RE = re.compile(
    r"\b(bnp|santander|scotia|scotiabank|jpm|jpmorgan|j\.\s?p\.\s?morgan|idad|"
    r"goldman|barclays|citigroup|hsbc|socgen|natixis|nomura)\b|nova scotia|"
    r"morgan stanley|societe generale|soci[ée]t[ée] g[ée]n[ée]rale|credit suisse|"
    r"\bXS\d{10}\b|\bUS06419|06419JME|46661PT|\bCUSIP\b|\bISIN\b|SEC-filed",
    re.IGNORECASE,
)

_CAT_ROW = "| _Issuer-named example: not served by this connector_ | | | |"

#: path (engine repo) -> {exact original line: replacement line}. Line for line.
LINE_REWRITES: dict[str, dict[str, str]] = {
    "docs/http-api.md": {
        "`pricing`. The two factsheet transcriptions and the synthetic cases are in": (
            "`pricing`. The synthetic cases are in"
        ),
        "`examples/data/structured_note/` (its README maps every factsheet line to a": (
            "`examples/data/structured_note/` (issuer-named and real-security examples"
        ),
        "field; every market number there is a placeholder).": (
            "there are not served by this connector)."
        ),
        '      "trade_id": "XS2741509801",': '      "trade_id": "NOTE-1",',
        "### The two factsheets in short": "### Two product shapes in short",
        "Both IDAD notes are worst-of autocalls on SMI / SPX / SX5E / NKY in USD, so": (
            "Take two worst-of autocalls on SMI / SPX / SX5E / NKY in USD, so"
        ),
        "three underlyings carry a `quanto` block. BNP XS2741509801: 7 % per": (
            "three underlyings carry a `quanto` block. A semi-annual snowball: 7 % per"
        ),
        "Santander XS3109492564: 2.5 % per quarter, three accrual-only events then": (
            "A quarterly low hurdle: 2.5 % per quarter, three accrual-only events then"
        ),
        "correlation sensitivity): the BNP note (4 underlyings) is 16 runs, about": (
            "correlation sensitivity): a four-underlying note is 16 runs, about"
        ),
        "expired), so size the paths of a Greeks request to the deadline: the BNP": (
            "expired), so size the paths of a Greeks request to the deadline: a"
        ),
        "Greeks example uses 16 384 paths (about 6 s through the gateway). Every": (
            "four-underlying note at 16 384 paths takes about 6 s through the gateway. Every"
        ),
    },
    "docs/versioning.md": {
        "**Examples.** `examples/data/structured_note/`: the two IDAD factsheet": (
            "**Examples.** `examples/data/structured_note/`: the engine repository's"
        ),
        "transcriptions (BNP Paribas Quad Index Classic Autocall June 2025,": (
            "issuer-named transcriptions are not served by this connector"
        ),
        "XS2741509801; Santander Quad Index Low Hurdle Autocall August 2025,": (
            "(nothing issuer- or security-specific is vendored); the vendored"
        ),
        "XS3109492564) with PLACEHOLDER market data (the README there maps every": (
            "structured-note examples are synthetic,"
        ),
        "factsheet line to a field and lists every placeholder), the BNP request": ("among them"),
        "with every diagnostic flag on, the BNP request with `greeks` on, a": "a",
    },
    "tests/functional/CATALOG.md": {},
}

#: path (engine repo) -> [(substring, replacement)], applied inside a line after
#: ``LINE_REWRITES`` (engine 0.13.0+: one-line JSON descriptions and long table rows carry
#: the issuer text mid-line). Never contains a newline, so the line count is unchanged.
SUB_REWRITES: dict[str, list[tuple[str, str]]] = {
    "docs/http-api.md": [
        ("(Santander's 80 % hurdle, BNP's 100 % call)", "(an 80 % hurdle, a 100 % call)"),
        (
            "a final date that only decides the redemption (the Scotia note).",
            "a final date that only decides the redemption (a trigger note with upside).",
        ),
        (
            "The Scotia note (`scotia_autocallable_trigger_ndx_rty_us06419jme72.json`)",
            "A worst-of trigger note with upside",
        ),
        ("(README there). The Scotia note", "(README there). A structured-note"),
        (
            "example `examples/data/structured_note/scotia_\u2026_with_synthetic_surface.json`",
            "example on synthetic surfaces (issuer-named: not served by this connector)",
        ),
    ],
    "docs/versioning.md": [
        (
            "`examples/data/structured_note/scotia_\u2026_with_synthetic_surface.json` (the",
            "a structured-note example on synthetic surfaces (an issuer-named",
        ),
        (
            "Scotia note on synthetic surfaces under Dupire local vol). Functional",
            "note, not served by this connector, under Dupire local vol). Functional",
        ),
        ("`structured_note_scotia_\u2026_synthetic_surface`.", "and one structured-note row."),
        ("Scotia example's last event", "trigger-note example's last event"),
        ("the BNP example round trip", "a four-underlying example round trip"),
    ],
    "flatbuffers/fbs/structured_note.fbs": [
        ("(Santander's 80 % hurdle:", "(an 80 % hurdle:"),
        ("BNP's 100 % call:", "a 100 % call:"),
        ("(the Scotia note: 75 %", "(for example: 75 %"),
    ],
    "jsonserver/openapi/openapi3.json": [
        ("(Santander's 80 % hurdle:", "(an 80 % hurdle:"),
        ("BNP's 100 % call:", "a 100 % call:"),
        ("(the Scotia note: 75 %", "(for example: 75 %"),
    ],
}

#: CATALOG.md: a table row naming an issuer / security is replaced by a neutral row.
_CATALOG = "tests/functional/CATALOG.md"
_CASES_RE = re.compile(r"^_(\d+) cases across (\d+) family\(ies\)\._$")


def scrub_text(path: str, text: str) -> str:
    """Rewrite ``text`` of engine file ``path`` line for line; raise when issuer text
    survives."""
    lines = text.split("\n")
    rewrites = LINE_REWRITES.get(path, {})
    out: list[str] = []
    dropped = 0
    for line in lines:
        if line in rewrites:
            out.append(rewrites[line])
            continue
        for old, new in SUB_REWRITES.get(path, ()):
            line = line.replace(old, new)
        if path == _CATALOG and line.startswith("| ") and ISSUER_TEXT_RE.search(line):
            out.append(_CAT_ROW)
            dropped += 1
            continue
        out.append(line)
    if dropped:
        for i, line in enumerate(out):
            m = _CASES_RE.match(line)
            if m:
                out[i] = f"_{int(m.group(1)) - dropped} cases across {m.group(2)} family(ies)._"
    assert len(out) == len(lines)
    bad = [(i + 1, ln) for i, ln in enumerate(out) if ISSUER_TEXT_RE.search(ln)]
    if bad:
        shown = "; ".join(f"L{n}: {ln[:120]}" for n, ln in bad[:5])
        raise SystemExit(
            f"issuer/security text left in {path} ({len(bad)} lines): {shown}. "
            "Extend scripts/issuer_scrub.py LINE_REWRITES (line for line)."
        )
    return "\n".join(out)


def scrub(path: str, data: bytes) -> bytes:
    """Bytes version of ``scrub_text``; non-UTF-8 data is returned unchanged."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if path not in LINE_REWRITES and path not in SUB_REWRITES and not ISSUER_TEXT_RE.search(text):
        return data
    return scrub_text(path, text).encode("utf-8")
