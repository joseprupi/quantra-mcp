"""Live check: every tool and both examples against a real engine.

For each call the MCP tool's ``response`` must equal a direct ``httpx`` POST of
the tool's echoed ``request`` (byte-equal canonical JSON). Prints a table and
exits non-zero on any mismatch or failed call.

    QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/live_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.resources import EXAMPLE_ENDPOINTS, example_names, load_example
from quantra_mcp.server import build_server

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
