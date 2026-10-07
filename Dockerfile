# quantra-mcp: streamable-HTTP MCP server in front of a Quantra pricing engine.
# Build:  docker build -t quantra-mcp .
# Run:    docker run --rm -p 8765:8765 -e QUANTRA_ENGINE_URL=http://host.docker.internal:8080 quantra-mcp
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# dependencies first (cached unless the lock changes), then the project itself
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin quantra
COPY --from=build --chown=quantra:quantra /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    QUANTRA_MCP_LOG=info
USER quantra
WORKDIR /home/quantra
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).status == 200 else 1)"
CMD ["quantra-mcp", "--http", "--host", "0.0.0.0"]
