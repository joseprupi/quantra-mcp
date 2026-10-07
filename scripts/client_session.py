"""A scripted MCP client session against the in-process app (the SDK's in-memory client).

Lists tools, resources and resource templates, then calls ``quantra_meta`` and
``calendar_holidays`` (TARGET with the 0.7.0 doc-example override). Needs an
engine at ``QUANTRA_ENGINE_URL``.

    QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/client_session.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.server import build_server

OVERRIDE = {
    "calendar": "TARGET",
    "added_holidays": ["2024-06-14"],
    "removed_holidays": ["2024-05-01"],
}


async def main() -> int:
    engine_url = os.environ.get("QUANTRA_ENGINE_URL", "").rstrip("/")
    if not engine_url:
        print("QUANTRA_ENGINE_URL is not set", file=sys.stderr)
        return 2
    app = build_server(Settings(engine_url=engine_url))
    async with Client(app) as client:
        info = client.server_info
        print(f"server: {info.name if info else '?'} {info.version if info else ''}")
        tools = (await client.list_tools()).tools
        print(f"tools ({len(tools)}):")
        for t in tools:
            print(f"  - {t.name}: {(t.description or '').strip().splitlines()[0]}")
        resources = (await client.list_resources()).resources
        print(f"resources ({len(resources)}):")
        for r in resources:
            print(f"  - {r.uri}")
        templates = (await client.list_resource_templates()).resource_templates
        print(f"resource templates ({len(templates)}):")
        for tpl in templates:
            print(f"  - {tpl.uri_template}")

        meta = await client.call_tool("quantra_meta", {})
        assert not meta.is_error and meta.structured_content is not None
        body = meta.structured_content["response"]
        print(
            "quantra_meta: ok="
            f"{meta.structured_content['ok']} openapi_version={body.get('openapi_version')} "
            f"quantlib={body.get('dependencies', {}).get('quantlib')} "
            f"products={len(body.get('products', []))}"
        )

        hol = await client.call_tool(
            "calendar_holidays",
            {
                "calendar": "TARGET",
                "start_date": "2024-04-29",
                "end_date": "2024-06-21",
                "calendar_overrides": [OVERRIDE],
            },
        )
        assert not hol.is_error and hol.structured_content is not None
        res = hol.structured_content
        print("calendar_holidays(TARGET, 2024-04-29..2024-06-21,")
        print("                  override added 2024-06-14 / removed 2024-05-01):")
        print(f"  request : {json.dumps(res['request'])}")
        print(f"  response: {json.dumps(res['response'])}")
        print(f"  summary : {json.dumps(res['summary'])}  engine={json.dumps(res['engine'])}")
        dates = res["response"]["dates"]
        ok = "2024-06-14" in dates and "2024-05-01" not in dates
        print(f"  override honored: {ok}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
