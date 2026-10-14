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
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from quantra_mcp.builders.tenor import (
    is_unit_word,
    parse_tenor_text,
    unsupported_tenor_reason,
)

TableKind = Literal["discount", "zero", "par"]
DateFormat = Literal["iso", "mdy", "dmy"]

_SPLIT_DELIMS = ("\t", ";", "|")
_INT_RE = re.compile(r"^\d+$")
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

#: header words that mark the value column, per table kind: ordered tiers of
#: (lower-case substrings that match, substrings that exclude). The first tier with
#: exactly one matching column wins; a tier with several matches is a refusal.
_VALUE_TIERS: dict[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = {
    "discount": ((("discount", "df", "disc"), ()),),
    "zero": (
        (("zero",), ()),
        (("spot",), ("shift",)),
        (("yield",), ("par", "market", "shift")),
        (("rate",), ("par", "market", "mkt", "shift", "fwd", "forward", "discount")),
    ),
    "par": (
        (("par",), ()),
        (("market rate", "mkt rate", "market"), ("shift",)),
        (("mid",), ("zero", "shift")),
        (("quote",), ("zero", "shift")),
        (("rate",), ("zero", "shift", "fwd", "forward", "discount", "df")),
        (("yield",), ("zero", "shift")),
        (("price",), ("zero", "shift")),
    ),
}
#: second words that close a two-word column name in a single-space header
#: ("Market Rate", "Discount Factor"); used only when it aligns the header with the rows
_HEADER_TAILS = ("rate", "rates", "factor", "factors", "price", "yield", "value")
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
    #: set when the whole table is refused (e.g. the value column is ambiguous)
    refusal: str | None = None


@dataclass
class _Token:
    text: str
    date: str | None = None
    slash_date: tuple[int, int, int] | None = None  # (a, b, year) awaiting mdy/dmy
    tenor: dict[str, Any] | None = None
    number: float | None = None
    number_note: str | None = None
    word: str | None = None  # anything else (lower-case)


def _merge_tenors(toks: list[str]) -> list[str]:
    """``["1", "WK", "4.5"]`` -> ``["1 WK", "4.5"]``: an integer followed by a unit
    word (closed list, see :data:`~quantra_mcp.builders.tenor.UNIT_SPELLINGS`)."""
    out: list[str] = []
    for t in toks:
        if out and is_unit_word(t) and _INT_RE.match(out[-1]):
            out[-1] = f"{out[-1]} {t}"
        else:
            out.append(t)
    return out


def _split(line: str) -> list[str]:
    for d in _SPLIT_DELIMS:
        if d in line:
            return [t.strip() for t in line.split(d)]
    raw = [t for t in re.split(r"\s+", line.strip()) if t]
    if "," in line and any(
        "," in t.strip(",")
        and not _THOUSANDS_RE.match(t.strip(",").rstrip("%"))
        and not _COMMA_NUMBER_RE.match(t.strip(",").rstrip("%"))
        for t in raw
    ):
        # comma-separated with spaces inside cells ("1 WK,4.5", "Term,Market Rate")
        return [t.strip() for t in line.split(",") if t.strip()]
    toks = [t.strip().strip(",") for t in raw]
    toks = [t for t in toks if t]
    if len(toks) == 1 and "," in line:
        toks = [t.strip() for t in line.split(",")]
    return _merge_tenors([t for t in toks if t])


def _header_candidates(line: str) -> list[tuple[list[str], str | None]]:
    """Ways to read a whitespace header line as column names, with a note each."""
    if any(d in line for d in _SPLIT_DELIMS) or "," in line:
        return [(_split(line), None)]
    words = [t for t in re.split(r"\s+", line.strip()) if t]
    out: list[tuple[list[str], str | None]] = [(words, None)]
    wide = [t.strip() for t in re.split(r"\s{2,}", line.strip()) if t.strip()]
    if wide != words:
        out.append((wide, "header columns split on runs of 2+ spaces"))
    joined: list[str] = []
    for w in words:
        if joined and w.lower() in _HEADER_TAILS:
            joined[-1] = f"{joined[-1]} {w}"
        else:
            joined.append(w)
    if joined != words:
        out.append((joined, f"header words joined into column names {joined}"))
    return out


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
    period = parse_tenor_text(t)
    if period is not None:
        tok.tenor = period
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


def _pick_value_column(
    header: list[str], kind: TableKind, label_col: int | None
) -> tuple[int | None, str | None]:
    """(column index, refusal). The first tier with exactly one match wins."""
    for keys, excludes in _VALUE_TIERS[kind]:
        hits = [
            i
            for i, h in enumerate(header)
            if i != label_col
            and any(k in h.lower() for k in keys)
            and not any(x in h.lower() for x in excludes)
        ]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, (
                f"ambiguous value column for a {kind} table: {[header[i] for i in hits]} all "
                f"match; the table's columns are {header}. Paste only the term column and the "
                f"{kind} column, or pass the rows as structured rows"
            )
    return None, None


def _numbers(toks: list[_Token], label: _Token | None) -> list[_Token]:
    return [tk for tk in toks if tk.number is not None and tk is not label]


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
    header_line: str | None = None
    if tokenised and _is_header(tokenised[0][2]):
        header_line = tokenised[0][1]
        tokenised = tokenised[1:]

    widths = Counter(len(t) for _, _, t in tokenised)
    modal = widths.most_common(1)[0][0] if widths else 0
    if header_line is not None:
        cands = _header_candidates(header_line)
        header, how = next(((h, w) for h, w in cands if len(h) == modal), cands[0])
        notes.append(f"header row detected: {header}")
        if how:
            notes.append(how)

    fmt = date_format or _infer_slash_format([t for _, _, t in tokenised], notes)
    if date_format is not None:
        notes.append(f"slash dates read as {date_format} (explicit)")

    label_col = _pick_column(header, _LABEL_HEADERS)
    value_col: int | None = None
    if header is not None:
        value_col, refusal = _pick_value_column(header, kind, label_col)
        if refusal is not None:
            notes.append(refusal)
            return ParsedTable(rows=[], unparsed=[], header=header, notes=notes, refusal=refusal)
    if header is not None and value_col is not None:
        notes.append(f"value column = {header[value_col]!r} (from the header {header})")
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
            overnight = next(
                (r for tk in toks if (r := unsupported_tenor_reason(tk.text)) is not None), None
            )
            if overnight is not None:
                bad(overnight)
            elif any(tk.slash_date is not None for tk in toks):
                bad(
                    "ambiguous slash date (month/day or day/month?): pass date_format "
                    "'mdy' or 'dmy', or paste ISO dates"
                )
            elif any(tk.word is not None and _COMMA_NUMBER_RE.match(tk.word) for tk in toks):
                bad("comma inside a number is ambiguous (decimal or thousands?); use a dot decimal")
            else:
                bad("no date (YYYY-MM-DD, DD-Mon-YYYY) or tenor (e.g. 10Y, 10 YR) found")
            continue
        nums = _numbers(toks, label)
        aligned = header is not None and len(toks) == len(header)
        value: _Token | None = None
        if len(nums) > 1:
            # several numeric columns: only a header-named column in an aligned row is read
            if value_col is None or not aligned:
                cols = f"the header {header}" if header is not None else "no header"
                why = (
                    "does not name the " + kind + " column"
                    if header is not None and value_col is None
                    else f"has {len(header)} columns, the row {len(toks)}"
                    if header is not None
                    else "names no column"
                )
                refusal = (
                    f"rows carry {len(nums)} numeric columns and {cols} {why}; never picked "
                    f"silently. Add a header naming the {kind} column (e.g. 'Term, Zero Rate' / "
                    "'Market Rate' / 'Discount'), paste only the term and that column, or pass "
                    "structured rows"
                )
                notes.append(refusal)
                return ParsedTable(
                    rows=[], unparsed=[], header=header, notes=notes, refusal=refusal
                )
            assert value_col is not None
            if toks[value_col].number is None:
                bad(f"no number in the {header[value_col]!r} column")  # type: ignore[index]
                continue
            value = toks[value_col]
        elif value_col is not None and value_col < len(toks) and toks[value_col].number is not None:
            value = toks[value_col]
        else:
            value = nums[0] if nums else None
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


def parse_rows(
    items: list[dict[str, Any]],
    *,
    percent: bool | None = None,
    helper_types: tuple[str, ...] = (),
) -> ParsedTable:
    """Structured rows ``{tenor | date, value, type?}`` -> the same :class:`ParsedTable`
    as :func:`parse_table`, with the same tenor / date / number rules; ``line`` is the
    1-based position of the row in the list."""
    notes: list[str] = ["structured rows (no text parsing of the layout)"]
    rows: list[ParsedRow] = []
    unparsed: list[dict[str, Any]] = []
    rule_notes: set[str] = set()
    for i, item in enumerate(items, 1):
        text = json.dumps(item, sort_keys=True, default=str)

        def bad(reason: str, i: int = i, text: str = text) -> None:
            unparsed.append({"line": i, "text": text, "reason": reason})

        tenor_s, date_s, raw = item.get("tenor"), item.get("date"), item.get("value")
        if (tenor_s is None) == (date_s is None):
            bad("give exactly one of tenor or date")
            continue
        date: str | None = None
        tenor: dict[str, Any] | None = None
        if tenor_s is not None:
            tenor = parse_tenor_text(str(tenor_s))
            if tenor is None:
                bad(
                    unsupported_tenor_reason(str(tenor_s))
                    or f"tenor {tenor_s!r} is not like 10Y, 18M, 1W, 10 YR, 18 MO, 1 WK"
                )
                continue
        else:
            tk = _classify(str(date_s), None)
            date = tk.date
            if date is None:
                bad(f"date {date_s!r} is not YYYY-MM-DD or DD-Mon-YYYY")
                continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            bad("value must be a number or a numeric string")
            continue
        number, note = _parse_number(raw if isinstance(raw, str) else repr(raw), percent)
        if number is None:
            bad(f"value {raw!r} is not a number")
            continue
        if note:
            rule_notes.add(note)
        t = item.get("type")
        helper = str(t).lower() if t is not None else None
        if helper is not None and helper not in helper_types:
            bad(f"type {t!r} is not one of {list(helper_types)}")
            continue
        rows.append(
            ParsedRow(
                line_no=i,
                text=text,
                date=date,
                tenor=tenor,
                value=number,
                value_text=str(raw),
                helper_type=helper,
            )
        )
    for r in sorted(rule_notes):
        notes.append(f"parsing rule applied on some values: {r}")
    if percent is True:
        notes.append("percent=true: every value divided by 100")
    notes.append(f"{len(rows)} row(s) read, {len(unparsed)} could not be read")
    return ParsedTable(rows=rows, unparsed=unparsed, header=None, notes=notes)
