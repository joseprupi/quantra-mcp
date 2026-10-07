# Releasing quantra-mcp

A release is a git tag `v<version>` pushed to `main`. `.github/workflows/release.yml`
then guards the version, runs the gate, builds and pushes the container image,
publishes to PyPI, and creates the GitHub Release. Nothing is published from a
laptop.

## Per release

1. Bump `version` in `pyproject.toml` (the package reads it at runtime via
   `importlib.metadata`, so there is no second copy to edit) and run `uv lock`
   so `uv.lock` carries the new version.
2. Finalise the `## <version> (<date>)` section at the top of `CHANGELOG.md`.
   The release body is that section, extracted verbatim (the workflow fails if
   the heading is missing).
3. Gate locally:
   `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`.
4. Commit, push to `main`, wait for `ci` to be green.
5. `git tag v<version> && git push origin v<version>`.
6. Watch Actions → `release`: `version-guard` → `gate` → `image` + `build-dist` →
   `pypi` → `github-release`. Then:
   - `docker pull ghcr.io/joseprupi/quantra-mcp:<version>` works anonymously
     (after the one-time visibility step below),
   - `uvx quantra-mcp@<version> --version` prints the version,
   - the GitHub Release lists the wheel, the sdist and `docker-compose.example.yml`.

## One-time operator setup

### PyPI trusted publishing (no API token)

The `pypi` job authenticates with GitHub's OIDC token
(`permissions: id-token: write`) through `pypa/gh-action-pypi-publish`. PyPI
must know which workflow may publish `quantra-mcp` before the first release:

1. Log in to https://pypi.org with the account that will own the project.
2. Go to **Your account → Publishing** (https://pypi.org/manage/account/publishing/).
3. Under **Add a new pending publisher**, GitHub tab, enter exactly:
   - PyPI project name: `quantra-mcp`
   - Owner: `joseprupi`
   - Repository name: `quantra-mcp`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
4. Save. The project is created on the first successful publish; the pending
   publisher becomes the project's trusted publisher. Later releases need no
   further PyPI clicks.

If the first publish fails with "invalid-publisher", one of the five fields
differs from the workflow (most often the environment name). Fix on PyPI and
re-run the failed job; no new tag is needed.

### GitHub environment `pypi`

1. Repository **Settings → Environments → New environment**, name `pypi`.
2. Optional, recommended: add yourself as a required reviewer so a tag push
   cannot publish to PyPI without one click of approval.

### GHCR package visibility

The first push of `ghcr.io/joseprupi/quantra-mcp` creates a **private**
package. After the first release:

1. https://github.com/joseprupi?tab=packages → `quantra-mcp` → **Package settings**.
2. **Danger Zone → Change visibility → Public**.
3. Under **Manage Actions access**, confirm `joseprupi/quantra-mcp` has
   **Write** (it will, because the package was created by this repository's
   workflow; a package pre-created by another repository would need this set
   by hand, which is what blocked the platform images once).

Anonymous `docker pull` works from then on.

### Branch protection (optional)

Require the `gate` check from `ci` on `main` so a tag is always cut from a
green commit.

## Deploying mcp.quantra.io

Deployment of the hosted instance is operated from the Quantra hub repo
(`deploy/mcp/README.md` there): compose service `mcp` on the demo VM with
`QUANTRA_ENGINE_URL=http://quantra-engine:8080`, `QUANTRA_TRUST_PROXY=1`,
`QUANTRA_ALLOWED_HOSTS=mcp.quantra.io`, `QUANTRA_PUBLIC_URL=https://mcp.quantra.io`,
behind Caddy (`reverse_proxy mcp:8765`). Caddy preserves the original `Host`
header and sets `X-Forwarded-For`, which is exactly what those two variables
assume.

## Rollback

Images and PyPI releases are immutable. Roll back by tagging a new patch
version from the last good commit; for the hosted instance just pin
`QUANTRA_MCP_VERSION` to the previous tag and `docker compose up -d mcp`.
