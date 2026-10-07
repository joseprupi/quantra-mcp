# Connector analytics: swap_dv01, key_rate_ladder, scenario, fair_rate, reprice_with

_Generated from THIS server's source (quantra-mcp) at commit `e4b0d99` by `scripts/methodology_gen.py --connector-only`. Unlike the other pages this one is not about the engine: it documents the request edits and the arithmetic the connector performs on engine outputs. Every statement is an excerpt of this repository with its location `path@sha:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/<path>#L..`._

These five tools never price anything themselves. Every NPV they report is an engine output of a complete pricing request (echoed in `calls[*].result.request` / `base.request` / `changed.request`). What this server adds is (1) the edit of the request (a quote bumped by `bump_bp / 10 000`, a quote replaced, a field set) and (2) a subtraction, a halving or a sum of those engine NPVs. Every such derived number sits next to the raw per-call NPVs so it can be redone by hand.

## Which numbers are the engine's and which are computed here

| Result field | Origin |
|---|---|
| `calls[*].npv`, `npvs.*`, `ladder[*].npv_up` / `npv_down`, `table[*].npv`, `base` / `changed` results | engine output (NPV of the request shown) |
| `fair_rate`, `fair_spread` | engine output, read from the response; never solved here |
| `swap_dv01.dv01` | connector arithmetic: (npv_up - npv_down) / 2 (centered), npv_up - base_npv (up) or base_npv - npv_down (down) |
| `key_rate_ladder.ladder[*].dv01`, `parallel.dv01` | connector arithmetic: the same difference per pillar / for all pillars |
| `key_rate_ladder.sum_of_buckets` | connector arithmetic: sum of the ladder dv01s |
| `scenario.table[*].change` | connector arithmetic: npv - base_npv |
| `reprice_with.differences.fields.*.difference` | connector arithmetic: changed - base |
| `compare_results.rows[*].abs_diff` / `rel_diff` | connector arithmetic: quantra - external; abs_diff / |external| |
| bumped quotes (`bumped_quotes`, `calls[*].edits`, `changes_applied`) | connector edit of the request: quote + bump_bp / 10 000 (futures price - bump_bp / 100), or the value set |

## What this server does

### 1. A bump is a pure edit of the engine `TermStructure`: `bump_bp / 10_000` is added to the pillar's quoted rate or spread; a futures pillar moves by `-bump_bp / 100`; nothing else is computed.

````python
"""Quote bumps for the analytics tools: pure edits of an engine ``TermStructure``.

A curve's *pillars* are its actual points, in wire order. Bumping a pillar
adds ``bump_bp / 10_000`` to the point's quoted rate (or spread); a futures
pillar moves its IMM price by ``-bump_bp / 100`` (price = 100 - rate in
percent). That is the only arithmetic here. Pillars whose quote cannot be
shifted by a rate bump without local maths (discount factors, bond clean
prices, FX points) and pillars that carry a ``quote_id`` instead of a value
are rejected with a message that names them.
````

Source: `src/quantra_mcp/builders/bumps.py@e4b0d99:L1-L9`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/builders/bumps.py#L1-L9>

### 2. Which field is bumped per point type, and which point types cannot be bumped in bp (discount factors, FX points; bond clean prices and `quote_id` pillars are refused in `_quote`).

````python
#: point_type -> (quote field, scale applied to bump_bp)
_RATE_FIELDS: dict[str, tuple[str, float]] = {
    "DepositHelper": ("rate", BP),
    "FRAHelper": ("rate", BP),
    "SwapHelper": ("rate", BP),
    "OISHelper": ("rate", BP),
    "DatedOISHelper": ("rate", BP),
    "BondHelper": ("rate", BP),
    "TenorBasisSwapHelper": ("spread", BP),
    "CrossCcyBasisHelper": ("spread", BP),
    "ZeroRatePoint": ("zero_rate", BP),
    "ForwardRatePoint": ("forward_rate", BP),
}
````

Source: `src/quantra_mcp/builders/bumps.py@e4b0d99:L25-L37`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/builders/bumps.py#L25-L37>

### 3. Unbumpable point types and the reason reported.

````python
_UNBUMPABLE: dict[str, str] = {
    "DiscountFactorPoint": "a discount factor cannot be shifted by a rate bump without local "
    "maths; bump a zero or forward value curve instead",
    "FxSwapHelper": "fx_points are not a rate; this pillar cannot be bumped in bp",
}
````

Source: `src/quantra_mcp/builders/bumps.py@e4b0d99:L39-L43`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/builders/bumps.py#L39-L43>

### 4. The bump arithmetic itself (`after = before + bump_bp * scale`), recorded as `{curve, pillar, point_type, field, from, to, bump_bp}`.

````python
        scale = -0.01 if p.field == "futures_price" else BP
        point = out["points"][idx]["point"]
        before = float(point[p.field])
        after = before + bump_bp * scale
        point[p.field] = after
        edits.append(
            {
                "curve": cid,
                "pillar": p.label,
                "point_type": p.point_type,
                "field": p.field,
                "from": before,
                "to": after,
                "bump_bp": bump_bp,
````

Source: `src/quantra_mcp/builders/bumps.py@e4b0d99:L184-L197`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/builders/bumps.py#L184-L197>

### 5. A `replace_quotes` entry sets one pillar's quote to the given value (no arithmetic).

````python
def replace_quote(
    curve: dict[str, Any], pillar_index: int, value: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deep-copy ``curve`` with one pillar's quote replaced by ``value``."""
    pillars = pillars_of(curve)
    p = pillars[pillar_index]
    cid = curve.get("id")
    if p.field is None:
        raise problem(
            f"/curves/{cid}/points/{pillar_index}",
            f"curve {cid!r} pillar {p.label!r} ({p.point_type}): {p.reason}",
        )
    out = copy.deepcopy(curve)
    point = out["points"][pillar_index]["point"]
    before = float(point[p.field])
    point[p.field] = float(value)
````

Source: `src/quantra_mcp/builders/bumps.py@e4b0d99:L203-L218`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/builders/bumps.py#L203-L218>

### 6. Every reprice goes through the SAME pricing tool the trade would normally use (`price_vanilla_swap` / `price_ois_swap` internals), so each `calls[*].result` is a complete, replayable pricing result.

````python
async def _price_spec(
    backend: Backend,
    store: SessionStore,
    pricing: dict[str, Any],
    spec: SwapSpec,
    request_id: str,
) -> ToolResult:
    """Reprice ``spec`` on a (possibly bumped) pricing block through the product's tool."""
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L166-L173`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L166-L173>

### 7. The NPV read from each engine response is `swaps[0].npv`, unchanged.

````python
def _npv(result: ToolResult) -> float | None:
    s = _swap0(result)
    if s is not None and isinstance(s.get("npv"), int | float):
        return float(s["npv"])
    return None
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L236-L240`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L236-L240>

### 8. `spot` / tenor dates are resolved by the engine once (base call) and pinned for every reprice, so bumped requests differ from the base only in the quotes moved.

````python
def _pin_dates(spec: SwapSpec, base: ToolResult, notes: list[str]) -> SwapSpec:
    """Replace spot/tenor by the dates the engine resolved in the base call."""
    resolved = {
        c.get("label"): c.get("response", {}).get("advanced_date")
        for c in base.get("date_resolution") or []
        if isinstance(c, dict)
    }
    eff = resolved.get("effective_date") or spec.effective_date
    end = resolved.get("termination_date") or spec.termination_date
    if (eff, end) != (spec.effective_date, spec.termination_date) or spec.tenor is not None:
        notes.append(
            f"dates pinned from the base call for every reprice: effective_date={eff!r}, "
            f"termination_date={end!r} (resolved by the engine's /calendar-advance once)"
        )
        return spec.pinned(str(eff), str(end))
    return spec
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L243-L258`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L243-L258>

### 9. DV01 methods: which bumped reprices each needs (`centered` = up and down; `up`; `down`).

````python
def _sides(method: DvMethod, bump_bp: float) -> list[tuple[str, float]]:
    """The bumped reprices a method needs: ``(label, signed bump)``."""
    if method == "centered":
        return [("up", bump_bp), ("down", -bump_bp)]
    if method == "up":
        return [("up", bump_bp)]
    return [("down", -bump_bp)]
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L265-L271`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L265-L271>

### 10. The DV01 arithmetic: centered = (npv_up - npv_down) / 2; up = npv_up - base_npv; down = base_npv - npv_down. Nothing else.

````python
def dv01_of(method: DvMethod, base_npv: float, npvs: dict[str, float]) -> float:
    """The finite difference of engine NPVs a DV01 method stands for.

    centered: (npv_up - npv_down) / 2; up: npv_up - base_npv; down: base_npv - npv_down.
    ``npvs`` carries the engine NPVs of the bumped calls by side label.
    """
    if method == "centered":
        return (npvs["up"] - npvs["down"]) / 2.0
    if method == "up":
        return npvs["up"] - base_npv
    return base_npv - npvs["down"]
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L274-L284`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L274-L284>

### 11. `swap_dv01`: every pillar of the selected curve(s) is bumped on each side the method needs; the per-call NPVs are reported as `npvs`, the derived number as `dv01` with its `dv01_definition`.

````python
        for label, signed in _sides(method, bump_bp):
            repl, edits = _bumped_curves(r, curve_ids, signed)
            quotes[label] = edits
            jobs.append((label, r.with_curves(repl), edits))
        n = len(next(iter(quotes.values())))
        r.notes.append(
            f"method={method!r}, scope={scope!r}: every pillar of {curve_ids} bumped by "
            f"{bump_bp:g} bp on each side ({n} quote(s) per bumped call; sides: "
            f"{[lbl for lbl, _ in _sides(method, bump_bp)]})"
        )
        base = await r.base()
        if not base.get("label"):
            return base
        failed = await r.fan_out(jobs)
        if failed is not None:
            return failed
        npvs = {c["label"]: float(c["npv"]) for c in r.calls[1:]}
        return r.done(
            method=method,
            bump_bp=bump_bp,
            scope=scope,
            curves_bumped=curve_ids,
            base_npv=r.base_npv,
            npvs={"base": r.base_npv, **npvs},
            dv01=dv01_of(method, r.base_npv, npvs),
            dv01_definition=dv01_definition(method, bump_bp, "every selected pillar")
            + "; calls[*] are the complete pricing results, labelled base / up / down",
            bumped_quotes=quotes,
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L476-L503`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L476-L503>

### 12. `key_rate_ladder`: the buckets are the curve's ACTUAL pillars in wire order; one reprice per pillar and side plus a parallel reprice per side.

````python
        sides = _sides(method, bump_bp)
        jobs: list[tuple[str, dict[str, Any], Any]] = []
        for side, signed in sides:
            par_curve, par_edits = bumps.bump_curve(base_curve, signed)
            jobs.append((f"parallel:{side}", r.with_curves({curve_id: par_curve}), par_edits))
        for p in pillars:
            for side, signed in sides:
                bumped, edits = bumps.bump_curve(base_curve, signed, [p.index])
                jobs.append((f"pillar:{p.label}:{side}", r.with_curves({curve_id: bumped}), edits))
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L537-L545`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L537-L545>

### 13. `key_rate_ladder` rows: `dv01` per pillar from that pillar's own up / down NPVs; `sum_of_buckets` is their sum; `parallel.dv01` is the same difference with every pillar bumped together.

````python
        by_label = {c["label"]: c for c in r.calls[1:]}
        ladder: list[dict[str, Any]] = []
        total = 0.0
        for p in pillars:
            row: dict[str, Any] = {"pillar": p.label, "point_type": p.point_type}
            npvs: dict[str, float] = {}
            for side, _ in sides:
                call = by_label[f"pillar:{p.label}:{side}"]
                npvs[side] = float(call["npv"])
                row["quote_from"] = call["edits"][0]["from"]
                row[f"quote_{side}"] = call["edits"][0]["to"]
                row[f"npv_{side}"] = npvs[side]
            row["dv01"] = dv01_of(method, r.base_npv, npvs)
            total += row["dv01"]
            ladder.append(row)
        par_npvs = {side: float(by_label[f"parallel:{side}"]["npv"]) for side, _ in sides}
        parallel: dict[str, Any] = {f"npv_{k}": v for k, v in par_npvs.items()}
        parallel["dv01"] = dv01_of(method, r.base_npv, par_npvs)
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L557-L574`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L557-L574>

### 14. `scenario`: `change = npv - base_npv` per named market variant; `edits` counts the quotes moved (listed in `calls[*].edits`).

````python
        rows: list[dict[str, Any]] = [
            {"name": "base", "npv": r.base_npv, "change": 0.0, "edits": 0}
        ]
        for call in r.calls[1:]:
            npv = float(call["npv"])
            rows.append(
                {
                    "name": call["label"],
                    "npv": npv,
                    "change": _diff(npv, r.base_npv),
                    "edits": len(call["edits"]),
                }
            )
        return r.done(
            base_npv=r.base_npv,
            table=rows,
            definitions={"change": "npv - base_npv; calls[*].edits lists every quote moved"},
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L647-L663`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L647-L663>

### 15. `fair_rate`: `fair_rate` / `fair_spread` are read from the engine response; when absent the tool says so and solves nothing.

````python
        swap = _swap0(base["result"]) or {}
        fields = {k: swap[k] for k in ("fair_rate", "fair_spread") if k in swap}
        if not fields:
            r.notes.append(
                f"the engine response for {trade.product} carries no fair_rate / fair_spread "
                f"(fields present: {sorted(swap)}); not solved locally"
            )
        return r.done(
            npv=r.base_npv,
            fair_rate=fields.get("fair_rate"),
            fair_spread=fields.get("fair_spread"),
            provided_by_engine=bool(fields),
            message=None if fields else f"fair rate not provided by the engine for {trade.product}",
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L686-L698`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L686-L698>

### 16. Reprices fan out concurrently, bounded by `QUANTRA_MAX_CONCURRENCY`; a failed reprice fails the whole result with its engine error.

````python
    async def fan_out(self, jobs: list[tuple[str, dict[str, Any], Any]]) -> ToolResult | None:
        results = await asyncio.gather(*(self.price(label, p, e) for label, p, e in jobs))
        self.calls += results
        self.notes.append(
            f"{len(jobs)} reprice(s) fanned out with at most {self.max_concurrency} "
            "concurrent engine calls (QUANTRA_MAX_CONCURRENCY)"
        )
        for call in results:
            if not call["result"].get("ok") or call["npv"] is None:
                return self.failure("reprice failed", call)
        return None
````

Source: `src/quantra_mcp/tools/analytics.py@e4b0d99:L392-L402`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/analytics.py#L392-L402>

### 17. `reprice_with`: a change is either `value` (set, any JSON) or `bump_bp` (add `bump_bp / 10_000` to an existing numeric field); what was done is recorded as `{path, kind, before, after}`.

````python
def apply_change(body: dict[str, Any], change: FieldChange) -> dict[str, Any]:
    """Apply one change in place; return what was done (path, before, after)."""
    segs = parse_path(change.path)
    parent, key = _descend(body, segs, change.path)
    if isinstance(parent, dict):
        present = key in parent
        before: Any = parent.get(key)
    else:
        present = True
        before = parent[key]
    if change.bump_bp is not None:
        if not present or isinstance(before, bool) or not isinstance(before, (int, float)):
            raise LocalValidationError(
                f"{change.path}: bump_bp needs an existing numeric field (found "
                f"{'absent' if not present else type(before).__name__})",
                [{"path": "/changes/bump_bp", "message": "field is not numeric"}],
            )
        after: Any = before + change.bump_bp / 10_000.0
        kind = f"bump {change.bump_bp:+g}bp"
    else:
        after = copy.deepcopy(change.value)
        kind = "set"
    parent[key] = after
    return {
        "path": change.path,
        "kind": kind,
        "before": before if present else None,
        "before_present": present,
        "after": after,
````

Source: `src/quantra_mcp/tools/reconcile.py@e4b0d99:L345-L373`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/reconcile.py#L345-L373>

### 18. `reprice_with.request_diff`: every leaf that differs between the two requests.

````python
def request_diff(a: Any, b: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Every leaf that differs between two JSON values, as ``{path, before, after}``."""
````

Source: `src/quantra_mcp/tools/reconcile.py@e4b0d99:L377-L378`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/reconcile.py#L377-L378>

### 19. `reprice_with.differences`: the numeric top-level fields of the first priced item, `difference = changed - base` (subtraction only).

````python
def numeric_differences(base: Any, changed: Any) -> dict[str, Any]:
    """Top-level numeric fields of the first priced item: base, changed, changed - base."""
    lb, lc = first_item(base), first_item(changed)
    if lb is None or lc is None:
        return {"item": None, "fields": {}, "note": "no priced item in one of the responses"}
    path_b, item_b = lb
    path_c, item_c = lc
    fields: dict[str, Any] = {}
    for k in item_b:
        if k in item_c and _is_num(item_b[k]) and _is_num(item_c[k]):
            fields[k] = {
                "base": item_b[k],
                "changed": item_c[k],
                "difference": item_c[k] - item_b[k],
            }
    only_base = sorted(k for k in item_b if k not in item_c and _is_num(item_b[k]))
    only_changed = sorted(k for k in item_c if k not in item_b and _is_num(item_c[k]))
    out: dict[str, Any] = {
        "item": path_b if path_b == path_c else f"{path_b} vs {path_c}",
        "fields": fields,
        "arithmetic": "difference = changed - base (subtraction only)",
````

Source: `src/quantra_mcp/tools/reconcile.py@e4b0d99:L401-L421`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/reconcile.py#L401-L421>

### 20. `compare_results`: `abs_diff = quantra - external`, `rel_diff = abs_diff / |external|` (null when external is 0).

````python
            row["abs_diff"] = value - ext
            row["rel_diff"] = (value - ext) / abs(ext) if ext != 0 else None
````

Source: `src/quantra_mcp/tools/reconcile.py@e4b0d99:L214-L215`

GitHub: <https://github.com/joseprupi/quantra-mcp/blob/e4b0d99/src/quantra_mcp/tools/reconcile.py#L214-L215>

## Caveats

- No sensitivity is analytic here: a DV01 is a finite difference of two engine NPVs 1bp apart (or base and one bump); convexity is what makes `up`, `down` and `centered` differ, and what makes `sum_of_buckets` differ from `parallel.dv01`.
- A bumped pillar is re-bootstrapped by the engine with its neighbours fixed, so a key-rate bucket reshapes the forwards around that pillar; a small bucket may carry either sign.
- Bumps are applied to the quotes of the curve as given in the market (par rates, spreads, futures prices, zero / forward values). Discount-factor value curves cannot be bumped in bp; paste a zero or forward table, or use `reprice_with` on a specific field instead.
- Nothing here is a vendor's definition of DV01 / PV01 / key-rate; when a vendor reports a one-sided or a 1bp-up number, choose `method` accordingly and compare like with like.
