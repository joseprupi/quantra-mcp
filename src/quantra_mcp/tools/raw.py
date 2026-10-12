"""Tier 2: the raw passthrough. Any engine endpoint, agent-supplied body.

``engine_request`` is also the home of the four endpoints that have no
convenience builder (``/calibrate-swaption-vol``, ``/calibrate-swaption-model``,
``/sample-vol-surfaces``, ``/bootstrap-inflation-curves``): their bodies are the
engine's own request objects, validated against the vendored spec and forwarded,
and the result carries the same selected ``summary`` the former dedicated tools
produced.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders.curves import bootstrap_summary
from quantra_mcp.schema.loader import SpecError, load_spec, normalize_endpoint
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import (
    Summarizer,
    ToolResult,
    local_error_result,
    new_request_id,
    run_post,
)

CALIBRATE_SWAPTION_VOL = "/calibrate-swaption-vol"
CALIBRATE_SWAPTION_MODEL = "/calibrate-swaption-model"
SAMPLE_VOL_SURFACES = "/sample-vol-surfaces"
BOOTSTRAP_INFLATION = "/bootstrap-inflation-curves"
BOOTSTRAP_CURVES = "/bootstrap-curves"


def calibration_summary(response: Any) -> dict[str, Any] | None:
    """Selected calibration outputs (``calibrate-swaption-vol`` / ``-model``)."""
    if not isinstance(response, dict):
        return None
    keys = ("model_id", "vol_id", "hw_a", "hw_sigma", "rmse", "num_helpers", "grid_points")
    out = {k: response[k] for k in keys if k in response}
    diag = response.get("diagnostics")
    if isinstance(diag, dict):
        out["diagnostics_keys"] = sorted(diag)
        if isinstance(diag.get("calibration"), dict):
            out["calibration"] = diag["calibration"]
    return out or None


def vol_sample_summary(response: Any) -> dict[str, Any] | None:
    """Per sampled surface: id, vol type, grid sizes (``sample-vol-surfaces``)."""
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        return None
    rows = []
    for r in response["results"]:
        if isinstance(r, dict):
            row = {
                k: r[k]
                for k in ("vol_id", "ql_vol_type", "n_expiries", "n_tenors", "n_strikes", "error")
                if k in r
            }
            vols = r.get("vols")
            if isinstance(vols, list):
                row["n_vols"] = len(vols)
            rows.append(row)
    return {"results": rows}


#: endpoints whose result gets a selected ``summary`` (never a computation)
SUMMARIZERS: dict[str, Summarizer] = {
    CALIBRATE_SWAPTION_VOL: calibration_summary,
    CALIBRATE_SWAPTION_MODEL: calibration_summary,
    SAMPLE_VOL_SURFACES: vol_sample_summary,
    BOOTSTRAP_INFLATION: bootstrap_summary,
    BOOTSTRAP_CURVES: bootstrap_summary,
}


async def engine_request_impl(
    backend: Backend,
    endpoint: str,
    body: Any,
    validate: bool = True,
    request_id: str | None = None,
) -> ToolResult:
    spec = load_spec()
    try:
        info = spec.endpoint(endpoint)
    except SpecError as exc:
        return local_error_result(normalize_endpoint(endpoint), body, str(exc))
    rid = request_id or new_request_id()
    if not isinstance(body, dict):
        return local_error_result(
            info.path,
            body,
            f"body must be a JSON object, got {type(body).__name__}",
            [{"path": "/", "message": "expected object"}],
            request_id=rid,
        )
    if validate:
        problems = validate_request(info.path, body, spec)
        if problems:
            return local_error_result(
                info.path,
                body,
                f"request does not match the {spec.api_version} schema for {info.path} "
                f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); "
                "fix and retry, or pass validate=false to send anyway",
                [p.as_dict() for p in problems],
                request_id=rid,
            )
    return await run_post(
        backend, info.path, body, request_id=rid, summarize=SUMMARIZERS.get(info.path)
    )


def register(app: MCPServer, backend: Backend) -> None:
    @tool(app, "POST any engine endpoint (raw body)", engine=True)
    async def engine_request(
        endpoint: Annotated[
            str,
            Field(
                description="One of the engine's POST paths (engine_schema() lists them), "
                "e.g. /price-ois-swap.",
            ),
        ],
        body: Annotated[
            dict[str, Any],
            Field(
                description="The complete request object exactly as the engine expects it "
                "(engine_schema(endpoint) and the quantra://examples/* resources show the "
                "shape). The engine does not default omitted fields.",
            ),
        ],
        validate: Annotated[
            bool,
            Field(
                description="Check body against the vendored OpenAPI schema first; on failure "
                "nothing is sent and problems lists each JSON-pointer path with a message.",
            ),
        ] = True,
        request_id: Annotated[
            str | None,
            Field(
                description="X-Request-Id to forward; generated when absent and reported in "
                "engine.request_id.",
            ),
        ] = None,
    ) -> ToolResult:
        """POST a JSON body to any engine endpoint: the raw escape hatch, and the home of the
        endpoints without a convenience builder. Those take the engine's own request
        object as ``body`` (``get_example(name)`` gives a complete one):
        /calibrate-swaption-vol (SABR cube calibration; examples ``sabrcal_*``, e.g.
        sabrcal_eur_2x2_beta_fixed_exact_fit), /calibrate-swaption-model (Hull-White to
        swaption vols; ``hwcal_*``, e.g. hwcal_eur_constant_normal_sigma_only),
        /sample-vol-surfaces (surfaces on a grid; ``volsample_*``, e.g.
        volsample_swaption_constant_cube), /bootstrap-inflation-curves (no vendored
        example; engine_schema("/bootstrap-inflation-curves")). Their results carry a
        selected ``summary`` (calibration block / hw_a, hw_sigma, rmse / per-surface grid
        sizes / per-curve pillars and grid). Returns ``{ok, endpoint, request, response,
        engine}``; on an engine error ``ok`` is false with the HTTP ``status`` and the
        engine's ``error`` text verbatim (400 = request wrong, 422 = unpriceable).
        """
        return await engine_request_impl(backend, endpoint, body, validate, request_id)
