"""Live check: every tool and both examples against a real engine.

For each call the MCP tool's ``response`` must equal a direct ``httpx`` POST of
the tool's echoed ``request`` (byte-equal canonical JSON). Prints a table and
exits non-zero on any mismatch or failed call.

The M2 section chains the market-construction tools: build_curve(USD_SOFR_OIS,
the 14-pillar SOFR strip) -> bootstrap_curve must be JSON-equal to the shipped
gold example and return the 50Y DF oracle; a discount value curve round-trips;
every other preset bootstraps with monotone DFs; a session reference resolves
to the same request as the inline curve.

    QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/live_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.presets.registry import get_preset, preset_ids
from quantra_mcp.resources import EXAMPLE_ENDPOINTS, example_names, load_example
from quantra_mcp.server import build_server

STRIPS_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "strips.json"
BOOTSTRAP_50Y_DF_ORACLE = "0.262755579831"

OVERRIDE = {
    "calendar": "TARGET",
    "added_holidays": ["2024-06-14"],
    "removed_holidays": ["2024-05-01"],
}

TOOL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("quantra_meta", {}),
    ("quantra_health", {}),
    ("list_endpoints", {}),
    ("engine_schema", {"endpoint": "/price-ois-swap", "depth": 2}),
    ("list_enums", {"name": "Calendar"}),
    (
        "calendar_holidays",
        {
            "calendar": "TARGET",
            "start_date": "2024-04-29",
            "end_date": "2024-06-21",
            "calendar_overrides": [OVERRIDE],
        },
    ),
    (
        "calendar_business_days",
        {"calendar": "UnitedStates", "start_date": "2024-05-24", "end_date": "2024-05-28"},
    ),
    (
        "calendar_advance",
        {
            "calendar": "TARGET",
            "date": "2024-04-30",
            "tenor_number": 1,
            "tenor_unit": "Days",
            "convention": "Following",
        },
    ),
]


@dataclass
class Row:
    tool: str
    endpoint: str
    status: str
    replay: str
    note: str


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _number_note(response: Any) -> str:
    """A short, number-bearing note selected from the response (no arithmetic)."""
    if not isinstance(response, dict):
        return ""
    if response.get("swaps"):
        return f"npv={response['swaps'][0].get('npv')!r}"
    if "dates" in response:
        d = response["dates"]
        first, last = (d[0], d[-1]) if d else (None, None)
        return f"count={response.get('count', len(d))} first={first} last={last}"
    if "advanced_date" in response:
        return f"advanced_date={response['advanced_date']}"
    if "openapi_version" in response:
        return f"openapi_version={response['openapi_version']}"
    if "status" in response:
        return f"status={response['status']}"
    if response.get("results"):
        last = response["results"][-1]
        grid = last.get("grid_dates") or []
        series = last.get("series") or []
        tail = series[0].get("values", [])[-1:] if series else []
        return (
            f"curve={last.get('id')} last_grid_date={grid[-1] if grid else None} last_value={tail}"
        )
    return ""


def _strips() -> dict[str, Any]:
    return {k: v for k, v in json.loads(STRIPS_PATH.read_text()).items() if k[0] != "_"}


def _df_values(result: dict[str, Any], curve_id: str) -> list[float]:
    res = next(r for r in result["response"]["results"] if r["id"] == curve_id)
    # the engine omits ``measure`` when it is the schema default DF
    series = next(s for s in res["series"] if s.get("measure", "DF") == "DF")
    return list(series["values"])


async def _tool(client: Client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, args)
    if result.is_error or result.structured_content is None:
        return {"ok": False, "error": str(result.content)[:120]}
    return dict(result.structured_content)


async def _replay_equal(http: httpx.AsyncClient, data: dict[str, Any]) -> bool:
    r = await http.post(
        data["endpoint"],
        content=json.dumps(data["request"]).encode(),
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    return canonical(r.json()) == canonical(data["response"])


async def _build_and_query(
    client: Client, preset_id: str, strip: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    preset = get_preset(preset_id)
    built = await _tool(
        client,
        "build_curve",
        {
            "id": preset_id,
            "preset": preset_id,
            "quotes": strip["quotes"],
            "reference_date": strip["reference_date"],
        },
    )
    query = await _tool(
        client,
        "build_query",
        {
            "curve_id": preset_id,
            "measures": ["DF", "ZERO"],
            "tenors": strip["grid"],
            "calendar": str(preset.index.calendar),
            "business_day_convention": str(preset.index.business_day_convention),
        },
    )
    return built, query


async def m2_checks(client: Client, http: httpx.AsyncClient, rows: list[Row]) -> bool:
    """Market construction: presets -> build -> bootstrap; returns True on any failure."""
    failed = False
    strips = _strips()

    lst = await _tool(client, "list_presets", {})
    rows.append(
        Row(
            "list_presets",
            "(local)",
            "ok" if lst.get("ok") else "FAIL",
            "n/a",
            f"presets={[p['id'] for p in lst.get('presets', [])]}",
        )
    )
    failed |= not lst.get("ok")
    one = await _tool(client, "get_preset", {"id": "USD_SOFR_OIS"})
    rows.append(
        Row(
            "get_preset USD_SOFR_OIS",
            "(local)",
            "ok" if one.get("ok") else "FAIL",
            "n/a",
            "ois.payment_lag="
            + str(one.get("preset", {}).get("helpers", {}).get("ois", {}).get("payment_lag")),
        )
    )
    failed |= not one.get("ok")

    # --- SOFR oracle --------------------------------------------------------
    built, query = await _build_and_query(client, "USD_SOFR_OIS", strips["USD_SOFR_OIS"])
    rows.append(
        Row(
            "build_curve USD_SOFR_OIS",
            "(local)",
            "ok" if built.get("ok") else f"FAIL {built.get('error')}",
            "n/a",
            f"points={len(built.get('curve', {}).get('points', []))} "
            f"notes={len(built.get('notes', []))}",
        )
    )
    rows.append(
        Row(
            "build_query USD_SOFR_OIS",
            "(local)",
            "ok" if query.get("ok") else f"FAIL {query.get('error')}",
            "n/a",
            f"measures={query.get('query', {}).get('measures')}",
        )
    )
    failed |= not (built.get("ok") and query.get("ok"))
    boot = await _tool(
        client,
        "bootstrap_curve",
        {"curves": [built], "as_of": strips["USD_SOFR_OIS"]["reference_date"], "queries": [query]},
    )
    if boot.get("ok"):
        same = await _replay_equal(http, boot)
        json_equal = canonical(boot["request"]) == canonical(load_example("sofr-bootstrap-request"))
        df50 = _df_values(boot, "USD_SOFR_OIS")[-1]
        oracle_ok = repr(df50).startswith(BOOTSTRAP_50Y_DF_ORACLE)
        failed |= not (same and json_equal and oracle_ok)
        rows.append(
            Row(
                "bootstrap_curve USD_SOFR_OIS",
                boot["endpoint"],
                "ok",
                "byte-equal" if same else "MISMATCH",
                f"json-equal-to-example={json_equal} 50Y_DF={df50!r} oracle={oracle_ok}",
            )
        )
    else:
        failed = True
        rows.append(
            Row(
                "bootstrap_curve USD_SOFR_OIS",
                "/bootstrap-curves",
                f"FAIL {boot.get('status')}",
                "-",
                str(boot.get("error"))[:80],
            )
        )

    # --- discount value curve ----------------------------------------------
    vc = await _tool(
        client,
        "build_value_curve",
        {
            "id": "VC",
            "kind": "discount",
            "points": [{"date": "2025-01-15", "value": 1.0}, {"tenor": "1Y", "value": 0.96}],
            "reference_date": "2025-01-15",
            "preset": "USD_SOFR_OIS",
        },
    )
    vq = await _tool(
        client,
        "build_query",
        {
            "curve_id": "VC",
            "measures": ["DF", "ZERO"],
            "tenors": ["1Y"],
            "calendar": "UnitedStatesGovernmentBond",
            "business_day_convention": "ModifiedFollowing",
        },
    )
    vboot = await _tool(
        client, "bootstrap_curve", {"curves": [vc], "as_of": "2025-01-15", "queries": [vq]}
    )
    if vc.get("ok") and vboot.get("ok"):
        same = await _replay_equal(http, vboot)
        res = vboot["response"]["results"][0]
        zero = next(s for s in res["series"] if s.get("measure") == "ZERO")["values"][0]
        df = _df_values(vboot, "VC")[0]
        failed |= not same or df != 0.96
        rows.append(
            Row(
                "build_value_curve discount -> bootstrap",
                vboot["endpoint"],
                "ok",
                "byte-equal" if same else "MISMATCH",
                f"DF@1Y={df!r} ZERO@1Y={zero!r} (engine; -ln 0.96 expected)",
            )
        )
    else:
        failed = True
        rows.append(
            Row(
                "build_value_curve discount",
                "/bootstrap-curves",
                "FAIL",
                "-",
                str(vc.get("error") or vboot.get("error"))[:80],
            )
        )

    # --- every other preset: 200 + monotone DFs -----------------------------
    for preset_id in preset_ids():
        if preset_id == "USD_SOFR_OIS":
            continue
        strip = strips[preset_id]
        b, q = await _build_and_query(client, preset_id, strip)
        r = await _tool(
            client,
            "bootstrap_curve",
            {"curves": [b], "as_of": strip["reference_date"], "queries": [q]},
        )
        if not (b.get("ok") and q.get("ok") and r.get("ok")):
            failed = True
            rows.append(
                Row(
                    f"build+bootstrap {preset_id}",
                    "/bootstrap-curves",
                    f"FAIL {r.get('status')}",
                    "-",
                    str(b.get("error") or q.get("error") or r.get("error"))[:80],
                )
            )
            continue
        same = await _replay_equal(http, r)
        dfs = _df_values(r, preset_id)
        mono = all(0 < y < x <= 1.0 for x, y in pairwise(dfs))
        failed |= not (same and mono)
        rows.append(
            Row(
                f"build+bootstrap {preset_id}",
                r["endpoint"],
                "ok",
                "byte-equal" if same else "MISMATCH",
                f"DF_monotone={mono} pillars={r['summary']['curves'][0]['pillars']} "
                f"last_DF={dfs[-1]!r}",
            )
        )

    # --- session round trip -------------------------------------------------
    put = await _tool(client, "session_put", {"name": "sofr", "kind": "curve", "value": built})
    via = await _tool(
        client,
        "bootstrap_curve",
        {"curves": [{"session": "sofr"}], "as_of": "2025-01-15", "queries": [query]},
    )
    if put.get("ok") and via.get("ok") and boot.get("ok"):
        resolved = via["request"] == boot["request"]
        same = await _replay_equal(http, via)
        failed |= not (resolved and same)
        rows.append(
            Row(
                "session_put -> bootstrap_curve [{session}]",
                via["endpoint"],
                "ok",
                "byte-equal" if same else "MISMATCH",
                f"request==inline_request={resolved} note={via.get('notes', [''])[0][:40]!r}",
            )
        )
    else:
        failed = True
        rows.append(
            Row(
                "session round trip",
                "/bootstrap-curves",
                "FAIL",
                "-",
                str(put.get("error") or via.get("error"))[:80],
            )
        )
    return failed


async def main() -> int:
    engine_url = os.environ.get("QUANTRA_ENGINE_URL", "").rstrip("/")
    if not engine_url:
        print("QUANTRA_ENGINE_URL is not set", file=sys.stderr)
        return 2
    rows: list[Row] = []
    failed = False
    app = build_server(Settings(engine_url=engine_url))
    async with (
        Client(app) as client,
        httpx.AsyncClient(base_url=engine_url, timeout=120) as http,
    ):
        calls = list(TOOL_CALLS) + [
            ("engine_request", {"endpoint": EXAMPLE_ENDPOINTS[n], "body": load_example(n)})
            for n in example_names()
        ]
        for name, args in calls:
            label = name if name != "engine_request" else f"engine_request {args['endpoint']}"
            result = await client.call_tool(name, args)
            if result.is_error or result.structured_content is None:
                rows.append(Row(label, "-", "TOOL ERROR", "-", str(result.content)[:80]))
                failed = True
                continue
            data = dict(result.structured_content)
            endpoint = str(data.get("endpoint", "-"))
            if "ok" not in data:  # spec lookups: no engine call, nothing to replay
                rows.append(Row(label, "(spec)", "ok", "n/a", f"keys={list(data)[:4]}"))
                continue
            if not data["ok"]:
                rows.append(
                    Row(
                        label,
                        endpoint,
                        f"FAIL {data.get('status')}",
                        "-",
                        str(data.get("error"))[:80],
                    )
                )
                failed = True
                continue
            request = data.get("request")
            if request is None:  # GET tools: replay the GET
                direct = (await http.get(endpoint)).json()
            else:
                r = await http.post(
                    endpoint,
                    content=json.dumps(request).encode(),
                    headers={"Content-Type": "application/json"},
                )
                r.raise_for_status()
                direct = r.json()
            same = canonical(direct) == canonical(data["response"])
            failed |= not same
            rows.append(
                Row(
                    label,
                    endpoint,
                    "ok",
                    "byte-equal" if same else "MISMATCH",
                    _number_note(data["response"]),
                )
            )

        failed |= await m2_checks(client, http, rows)

    fields = ("tool", "endpoint", "status", "replay", "note")
    header = Row(*fields)
    widths = [max(len(getattr(r, f)) for r in [header, *rows]) for f in fields]
    for r in [header, *rows]:
        print(
            " | ".join(
                getattr(r, f).ljust(w)
                for f, w in zip(
                    ("tool", "endpoint", "status", "replay", "note"), widths, strict=True
                )
            )
        )
        if r is header:
            print("-+-".join("-" * w for w in widths))
    print(f"\nengine={engine_url} calls={len(rows)} result={'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
