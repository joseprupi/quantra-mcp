"""Parse a pasted curve table (CSV / TSV / whitespace, header optional) into
builder inputs. Pure: no engine call, no arithmetic on the values.

The only transformations applied are *parsing*: a trailing ``%`` divides by
100, thousands separators (``1,000.5``) are stripped, a date in one of the
accepted layouts becomes ``YYYY-MM-DD`` and a tenor such as ``10Y`` becomes a
period. Every rule that fires is reported in ``notes``; every row that cannot
be read is returned in ``unparsed`` with the reason, never silently dropped.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from quantra_mcp.builders.tenor import parse_tenor

TableKind = Literal["discount", "zero", "par"]
DateFormat = Literal["iso", "mdy", "dmy"]

_SPLIT_DELIMS = ("\t", ";", "|")
_TENOR_RE = re.compile(r"^(\d+)\s*([DWMYdwmy])$")
_ISO_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_SLASH_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})$")
_MON_RE = re.compile(r"^(\d{1,2})[-\s]([A-Za-z]{3})[-\s](\d{4}|\d{2})$")
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_COMMA_NUMBER_RE = re.compile(r"^[+-]?\d+,\d+$")
_PLAIN_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}

#: header words that mark the value column, per table kind (lower-case substrings)
_VALUE_HEADERS: dict[str, tuple[str, ...]] = {
    "discount": ("df", "discount", "disc"),
    "zero": ("zero", "rate", "yield", "spot"),
    "par": ("rate", "par", "quote", "mid", "price", "yield"),
}
_LABEL_HEADERS = ("date", "maturity", "tenor", "term", "pillar", "expiry", "end")


@dataclass
class ParsedRow:
    line_no: int
    text: str
    date: str | None = None
    tenor: dict[str, Any] | None = None
    value: float = 0.0
    value_text: str = ""
    helper_type: str | None = None

    @property
    def label(self) -> str:
        if self.date is not None:
            return self.date
        assert self.tenor is not None
        return f"{self.tenor['n']}{str(self.tenor['unit'])[0]}"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"line": self.line_no, "label": self.label, "value": self.value}
        if self.helper_type is not None:
            out["type"] = self.helper_type
        return out


@dataclass
class ParsedTable:
    rows: list[ParsedRow]
    unparsed: list[dict[str, Any]]
    header: list[str] | None
    notes: list[str] = field(default_factory=list)


@dataclass
class _Token:
    text: str
    date: str | None = None
    slash_date: tuple[int, int, int] | None = None  # (a, b, year) awaiting mdy/dmy
    tenor: dict[str, Any] | None = None
    number: float | None = None
    number_note: str | None = None
    word: str | None = None  # anything else (lower-case)


def _split(line: str) -> list[str]:
    for d in _SPLIT_DELIMS:
        if d in line:
            return [t.strip() for t in line.split(d)]
    toks = [t.strip().strip(",") for t in re.split(r"\s+", line.strip())]
    toks = [t for t in toks if t]
    if len(toks) == 1 and "," in line:
        toks = [t.strip() for t in line.split(",")]
    return [t for t in toks if t]


def _year(y: str) -> int:
    return int(y) if len(y) == 4 else 2000 + int(y)


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def _parse_number(text: str, percent: bool | None) -> tuple[float | None, str | None]:
    """``text`` -> (value, note). ``percent=True`` divides every value by 100,
    ``None`` divides only values written with ``%``; ``False`` never."""
    t = text.strip()
    has_pct = t.endswith("%")
    if has_pct:
        t = t[:-1].strip()
    note = None
    if _THOUSANDS_RE.match(t):
        t = t.replace(",", "")
        note = "thousands separators stripped"
    if not _PLAIN_NUMBER_RE.match(t):
        return None, None
    try:
        exact = Decimal(t)
    except InvalidOperation:  # pragma: no cover - the regex admits only valid literals
        return None, None
    if has_pct or percent is True:
        # decimal arithmetic so "3.7%" becomes the float of "0.037", not 3.7 / 100 in binary
        exact = exact / Decimal(100)
        note = (note + "; " if note else "") + "percent -> decimal (/100)"
    return float(exact), note


def _classify(text: str, percent: bool | None) -> _Token:
    tok = _Token(text=text)
    t = text.strip()
    m = _ISO_RE.match(t)
    if m:
        tok.date = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if tok.date is None:
            tok.word = t.lower()
        return tok
    m = _MON_RE.match(t)
    if m and m.group(2).lower() in _MONTHS:
        tok.date = _iso(_year(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if tok.date is None:
            tok.word = t.lower()
        return tok
    m = _SLASH_RE.match(t)
    if m:
        tok.slash_date = (int(m.group(1)), int(m.group(2)), _year(m.group(3)))
        return tok
    m = _TENOR_RE.match(t)
    if m:
        tok.tenor = parse_tenor(f"{m.group(1)}{m.group(2).upper()}")
        return tok
    num, note = _parse_number(t, percent)
    if num is not None:
        tok.number, tok.number_note = num, note
        return tok
    tok.word = t.lower()
    return tok


def _infer_slash_format(rows: list[list[_Token]], notes: list[str]) -> DateFormat | None:
    first_gt12 = second_gt12 = False
    seen = False
    for toks in rows:
        for tk in toks:
            if tk.slash_date is not None:
                seen = True
                a, b, _ = tk.slash_date
                first_gt12 |= a > 12
                second_gt12 |= b > 12
    if not seen:
        return None
    if first_gt12 and second_gt12:
        notes.append("slash dates: both day-first and month-first rows present; unparseable")
        return None
    if first_gt12:
        notes.append("slash dates read as day/month/year (a first field above 12 occurs)")
        return "dmy"
    if second_gt12:
        notes.append("slash dates read as month/day/year (a second field above 12 occurs)")
        return "mdy"
    return None


def _resolve_slash(tok: _Token, fmt: DateFormat | None) -> str | None:
    assert tok.slash_date is not None
    a, b, y = tok.slash_date
    if fmt == "mdy":
        return _iso(y, a, b)
    if fmt == "dmy":
        return _iso(y, b, a)
    return None


def _is_header(toks: list[_Token]) -> bool:
    return bool(toks) and all(
        tk.word is not None and tk.number is None and tk.date is None and tk.tenor is None
        for tk in toks
    )


def _pick_column(header: list[str] | None, keys: tuple[str, ...]) -> int | None:
    if header is None:
        return None
    for i, h in enumerate(header):
        if any(k in h.lower() for k in keys):
            return i
    return None


def parse_table(
    text: str,
    kind: TableKind,
    *,
    percent: bool | None = None,
    date_format: DateFormat | None = None,
    helper_types: tuple[str, ...] = (),
) -> ParsedTable:
    """Rows of ``label value [type]`` from free text.

    ``label`` is a date (``2034-09-18``, ``18-Sep-2034``, ``09/18/2034`` with an
    explicit or inferable ``date_format``) or a tenor (``10Y``); ``value`` is the
    first numeric token after the label, or the column a header names. A third
    word that is one of ``helper_types`` (``ois``, ``swap``, ``deposit``...)
    tags the row's quote type for par tables.
    """
    notes: list[str] = []
    lines = [(i + 1, ln.rstrip()) for i, ln in enumerate(text.splitlines())]
    lines = [(n, ln) for n, ln in lines if ln.strip()]
    tokenised = [(n, ln, [_classify(t, percent) for t in _split(ln)]) for n, ln in lines]

    header: list[str] | None = None
    if tokenised and _is_header(tokenised[0][2]):
        header = [tk.text for tk in tokenised[0][2]]
        notes.append(f"header row detected: {header}")
        tokenised = tokenised[1:]

    fmt = date_format or _infer_slash_format([t for _, _, t in tokenised], notes)
    if date_format is not None:
        notes.append(f"slash dates read as {date_format} (explicit)")

    label_col = _pick_column(header, _LABEL_HEADERS)
    value_col = _pick_column(header, _VALUE_HEADERS[kind])
    if header is not None and value_col is not None:
        notes.append(f"value column = {header[value_col]!r} (from the header)")
    delims = {d for _, ln, _ in tokenised for d in _SPLIT_DELIMS if d in ln}
    if delims:
        notes.append("delimiter: " + ", ".join(repr(d) for d in sorted(delims)) + " (per line)")
    else:
        notes.append("delimiter: whitespace / comma")

    rows: list[ParsedRow] = []
    unparsed: list[dict[str, Any]] = []
    rule_notes: set[str] = set()
    for n, ln, toks in tokenised:
        for tk in toks:
            if tk.slash_date is not None and tk.date is None:
                tk.date = _resolve_slash(tk, fmt)

        def bad(reason: str, n: int = n, ln: str = ln) -> None:
            unparsed.append({"line": n, "text": ln, "reason": reason})

        label: _Token | None = None
        if label_col is not None and label_col < len(toks):
            cand = toks[label_col]
            if cand.date is not None or cand.tenor is not None:
                label = cand
        if label is None:
            label = next((tk for tk in toks if tk.date is not None or tk.tenor is not None), None)
        if label is None:
            if any(tk.slash_date is not None for tk in toks):
                bad(
                    "ambiguous slash date (month/day or day/month?): pass date_format "
                    "'mdy' or 'dmy', or paste ISO dates"
                )
            elif any(tk.word is not None and _COMMA_NUMBER_RE.match(tk.word) for tk in toks):
                bad("comma inside a number is ambiguous (decimal or thousands?); use a dot decimal")
            else:
                bad("no date (YYYY-MM-DD, DD-Mon-YYYY) or tenor (e.g. 10Y) found")
            continue
        value: _Token | None = None
        if value_col is not None and value_col < len(toks) and toks[value_col].number is not None:
            value = toks[value_col]
        if value is None:
            after = toks[toks.index(label) + 1 :]
            value = next((tk for tk in after if tk.number is not None), None)
            if value is None:
                value = next((tk for tk in toks if tk.number is not None and tk is not label), None)
        if value is None:
            if any(tk.word is not None and _COMMA_NUMBER_RE.match(tk.word) for tk in toks):
                bad("comma inside a number is ambiguous (decimal or thousands?); use a dot decimal")
            else:
                bad("no numeric value found on the row")
            continue
        if value.number_note:
            rule_notes.add(value.number_note)
        helper = next(
            (tk.word for tk in toks if tk.word is not None and tk.word in helper_types), None
        )
        rows.append(
            ParsedRow(
                line_no=n,
                text=ln,
                date=label.date,
                tenor=label.tenor,
                value=float(value.number or 0.0),
                value_text=value.text,
                helper_type=helper,
            )
        )
    for r in sorted(rule_notes):
        notes.append(f"parsing rule applied on some values: {r}")
    if percent is True:
        notes.append("percent=true: every value divided by 100")
    notes.append(f"{len(rows)} row(s) parsed, {len(unparsed)} could not be read")
    return ParsedTable(rows=rows, unparsed=unparsed, header=header, notes=notes)
