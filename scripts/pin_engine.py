"""Pin the vendored engine contract to a git tag of the engine repository.

Copies, from an engine checkout at the given tag (read with ``git show`` /
``git ls-tree``, never touching the working tree of that repository):

* ``jsonserver/openapi/openapi3.json``  -> ``src/quantra_mcp/schema/openapi3.json``
* ``docs/http-api.md``                  -> ``src/quantra_mcp/docs/http-api.md``
* ``docs/versioning.md``                -> ``src/quantra_mcp/docs/versioning.md``
* ``tests/functional/CATALOG.md``       -> ``src/quantra_mcp/docs/engine-catalog.md``
* ``LICENSE.txt``                       -> ``src/quantra_mcp/examples/ENGINE-LICENSE.txt``
* ``examples/data/**/*.json``           -> ``src/quantra_mcp/examples/<category>/<name>.json``

and writes ``src/quantra_mcp/schema/PIN`` (``<tag> <commit sha>``),
``src/quantra_mcp/schema/enums_generated.py`` (one ``StrEnum`` per engine
enum, so tool arguments are typed with the engine's own values) and
``src/quantra_mcp/examples/INDEX.json`` (one row per vendored example: name,
category, endpoint, title / description / reference value parsed from the
engine's functional manifest and CATALOG.md when the example is cataloged),
and the methodology pages ``src/quantra_mcp/docs/methodology/*.md`` +
``INDEX.json`` (``methodology_gen.py`` driven by the curated anchors in
``methodology_rules.py``: every statement is an excerpt of the engine tree at
the tag with a ``path@tag:Lstart-Lend`` citation).

The endpoint of a cataloged example comes from the manifest's ``product`` key
(the engine's product catalog maps it to the HTTP route); an uncataloged
example is matched against every endpoint's request schema and must validate
against exactly one. The two SOFR blog examples at the top of ``examples/``
are not from the engine repository and are kept as they are.

Usage::

    uv run python scripts/pin_engine.py --engine-repo /path/to/quantraserver --tag v0.7.0
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import keyword
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import methodology_gen

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "quantra_mcp"
SCHEMA_DIR = PKG / "schema"
DOCS_DIR = PKG / "docs"
EXAMPLES_DIR = PKG / "examples"

FILES = {
    "jsonserver/openapi/openapi3.json": SCHEMA_DIR / "openapi3.json",
    "docs/http-api.md": DOCS_DIR / "http-api.md",
    "docs/versioning.md": DOCS_DIR / "versioning.md",
    "tests/functional/CATALOG.md": DOCS_DIR / "engine-catalog.md",
    "LICENSE.txt": EXAMPLES_DIR / "ENGINE-LICENSE.txt",
}

ENUM_PREFIX = "quantra_enums_"
FIXTURE_PREFIX = "examples/data/"
MANIFEST_PATH = "tests/functional/manifest.py"
CATALOG_PATH = "tests/functional/CATALOG.md"
ROOT_CATEGORY = "misc"  # fixtures that sit directly under examples/data/

#: Operator rule: nothing vendor-specific is shipped. An engine fixture whose file
#: name, title or catalog description carries a vendor reference is NOT vendored.
VENDOR_RE = re.compile(r"bbg|bloomberg|swpm|refinitiv|markit|icvs", re.IGNORECASE)

#: The two blog examples (not from the engine repo) and their published oracles.
BLOG_EXAMPLES: list[dict[str, Any]] = [
    {
        "name": "sofr-bootstrap-request",
        "category": "blog",
        "file": "sofr-bootstrap-request.json",
        "endpoint": "/bootstrap-curves",
        "title": "USD SOFR OIS curve, 14-pillar strip, DF/ZERO grid out to 50Y",
        "description": (
            "The bootstrap request of the Quantra post 'Bootstrapping a SOFR curve' "
            "(quantra.io/blog/bootstrapping-a-sofr-curve); run verbatim against the public "
            "engine and checked against QuantLib-python 1.41."
        ),
        "compare": "series",
        "list_key": "results",
        "reference_text": "DF/ZERO series; 50Y discount factor 0.262755579831",
        "reference_value": None,
        "oracle": {"kind": "last_df", "value_prefix": "0.262755579831"},
        "source": "quantra.io blog",
    },
    {
        "name": "sofr-ois-swap-request",
        "category": "blog",
        "file": "sofr-ois-swap-request.json",
        "endpoint": "/price-ois-swap",
        "title": "USD 5Y payer SOFR OIS at 3%, payment lag 2",
        "description": (
            "The pricing request of the same post; NPV 337986.7913... against the public "
            "engine and QuantLib-python 1.41."
        ),
        "compare": "npv",
        "list_key": "swaps",
        "reference_text": "337986.79130030936",
        "reference_value": 337986.79130030936,
        "oracle": {"kind": "npv", "value": 337986.79130030936, "tolerance": 1e-6},
        "source": "quantra.io blog",
    },
]

#: Engine examples that are valid requests the engine is EXPECTED to reject (the
#: engine's own suite asserts the error). ``expected_status`` is 200 for all others.
EXPECTED_ERRORS: dict[str, dict[str, Any]] = {
    "fixed_rate_bond_beyond_pillar_request": {
        "expected_status": 422,
        "expected_error_contains": "past max curve time",
        "why": (
            "a bond maturing beyond the curve's last pillar without extrapolation; "
            "tests/caching/cache_correctness_test.py::test_cache_transparency_beyond_pillar_error "
            "asserts the 422 is stable"
        ),
    },
}


def git_show(repo: Path, tag: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), "show", f"{tag}:{path}"],
        check=True,
        capture_output=True,
    ).stdout


def git_sha(repo: Path, tag: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{tag}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.strip()


def git_ls(repo: Path, tag: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", tag],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.split()


def render_enums(spec: dict[str, object]) -> str:
    components = spec["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    info = spec["info"]
    assert isinstance(info, dict)
    lines = [
        '"""Engine enums generated from the vendored OpenAPI spec. DO NOT EDIT.',
        "",
        f"Generated by scripts/pin_engine.py from engine API version {info['version']}.",
        "A contract test asserts these match ``schema/openapi3.json``.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from enum import StrEnum",
        "",
        "__all__ = [",
    ]
    names = [n.removeprefix(ENUM_PREFIX) for n in schemas if n.startswith(ENUM_PREFIX)]
    lines.extend(f'    "{n}",' for n in sorted(names))  # sorted __all__, classes in spec order
    lines.append("]")
    for n in names:
        schema = schemas[ENUM_PREFIX + n]
        assert isinstance(schema, dict)
        values = schema["enum"]
        assert isinstance(values, list)
        lines.append("")
        lines.append("")
        lines.append(f"class {n}(StrEnum):")
        lines.append(f'    """Engine enum ``{n}`` ({len(values)} values)."""')
        lines.append("")
        for v in values:
            assert isinstance(v, str)
            member = v + "_" if keyword.iskeyword(v) else v
            lines.append(f'    {member} = "{v}"')
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# examples catalog
# --------------------------------------------------------------------------


def parse_manifest(source: str) -> list[dict[str, Any]]:
    """The engine's ``CASES`` list as plain dicts, without executing the module.

    Module-level constants referenced by name (``DEFAULT_TOLERANCE`` and
    friends) are resolved from their own assignments.
    """
    tree = ast.parse(source)
    consts: dict[str, Any] = {}
    cases: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                if target.id == "CASES":
                    cases = node.value
                else:
                    with contextlib.suppress(ValueError):
                        consts[target.id] = ast.literal_eval(node.value)
    if not isinstance(cases, ast.List):
        raise SystemExit("manifest.py: CASES list not found")

    def value(node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            return consts[node.id]
        return ast.literal_eval(node)

    out: list[dict[str, Any]] = []
    for item in cases.elts:
        if not isinstance(item, ast.Dict):
            raise SystemExit("manifest.py: CASES entries must be dict literals")
        row: dict[str, Any] = {}
        for k, v in zip(item.keys, item.values, strict=True):
            assert isinstance(k, ast.Constant) and isinstance(k.value, str)
            row[k.value] = value(v)
        out.append(row)
    return out


_ROW_RE = re.compile(
    r"^\| \*\*(?P<title>.*?)\*\*<br><sub>(?P<desc>.*?)</sub> \| .*? \| "
    r"\[(?P<file>[^\]]+)\]\(.*?\) \| (?P<value>.*?) \|$"
)


def parse_catalog(text: str) -> dict[str, dict[str, str]]:
    """``{request file name: {title, description, reference_text}}`` from CATALOG.md."""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        out[m.group("file")] = {
            "title": m.group("title").strip(),
            "description": m.group("desc").strip(),
            "reference_text": m.group("value").strip(),
        }
    return out


_NPV_RE = re.compile(r"^-?[\d,]+\.\d+$")
_COUNT_RE = re.compile(r"^(\d+) (holidays|business days|values)$")
_ADVANCED_RE = re.compile(r"^advanced date = (\d{4}-\d{2}-\d{2})$")
_SERIES_RE = re.compile(r"^([A-Z/a-z]+) series, (\d+) points$")
_FIELD_RE = re.compile(r"^([a-z_]+)=([-+0-9.eE]+)$")


def parse_oracle(compare: str, text: str, tolerance: float | None) -> dict[str, Any] | None:
    """Turn a CATALOG.md value cell into a machine-checkable oracle, or None."""
    if compare == "npv":
        if not _NPV_RE.match(text):
            return None
        # CATALOG.md prints NPVs with two decimals; the engine's own gate uses 0.01 abs.
        return {"kind": "npv", "value": float(text.replace(",", "")), "tolerance": 0.01}
    if compare == "exact":
        m = _COUNT_RE.match(text)
        if m:
            return {"kind": "count", "count": int(m.group(1)), "list_key": "dates"}
        m = _ADVANCED_RE.match(text)
        if m:
            return {"kind": "advanced_date", "value": m.group(1)}
        return None
    if compare == "series":
        m = _SERIES_RE.match(text)
        if m:
            return {"kind": "series", "names": m.group(1).split("/"), "points": int(m.group(2))}
        return None
    if compare == "fields":
        fields: dict[str, float] = {}
        for part in text.split(", "):
            fm = _FIELD_RE.match(part.strip())
            if fm:
                fields[fm.group(1)] = float(fm.group(2))
        if not fields:
            return None
        # CATALOG.md prints 6 significant digits; the engine gate uses 1e-7 abs.
        return {"kind": "fields", "values": fields, "tolerance": tolerance or 1e-7, "sig": 6}
    return None


ROUTE_BY_PRODUCT: dict[str, str] = {
    "fixed_rate_bond": "/price-fixed-rate-bond",
    "floating_rate_bond": "/price-floating-rate-bond",
    "vanilla_swap": "/price-vanilla-swap",
    "zero_coupon_inflation_swap": "/price-zero-coupon-inflation-swap",
    "year_on_year_inflation_swap": "/price-year-on-year-inflation-swap",
    "ois_swap": "/price-ois-swap",
    "basis_swap": "/price-basis-swap",
    "fra": "/price-fra",
    "cap_floor": "/price-cap-floor",
    "swaption": "/price-swaption",
    "cds": "/price-cds",
    "bootstrap_curves": "/bootstrap-curves",
    "bootstrap_inflation_curves": "/bootstrap-inflation-curves",
    "sample_vol_surfaces": "/sample-vol-surfaces",
    "calendar_business_days": "/calendar-business-days",
    "calendar_holidays": "/calendar-holidays",
    "calendar_advance": "/calendar-advance",
    "calibrate_swaption_model": "/calibrate-swaption-model",
    "calibrate_swaption_vol": "/calibrate-swaption-vol",
    "equity_option": "/price-equity-option",
    "zero_coupon_bond": "/price-zero-coupon-bond",
    "zero_coupon_swap": "/price-zero-coupon-swap",
    "year_on_year_inflation_cap_floor": "/price-year-on-year-inflation-cap-floor",
    "callable_fixed_rate_bond": "/price-callable-fixed-rate-bond",
}


def _matching_endpoints(body: Any) -> list[str]:
    # imported lazily: the spec must already be written at this point
    from quantra_mcp.schema.loader import load_spec
    from quantra_mcp.schema.validate import validate_request

    spec = load_spec()
    return [e for e in spec.endpoint_paths if validate_request(e, body, spec) == []]


def vendor_examples(repo: Path, tag: str, sha: str) -> list[dict[str, Any]]:
    """Copy every fixture and build the INDEX rows (blog examples first)."""
    for child in EXAMPLES_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    paths = [p for p in git_ls(repo, tag) if p.startswith(FIXTURE_PREFIX) and p.endswith(".json")]
    manifest = parse_manifest(git_show(repo, tag, MANIFEST_PATH).decode())
    by_request = {c["request"]: c for c in manifest}
    catalog = parse_catalog(git_show(repo, tag, CATALOG_PATH).decode())

    rows: list[dict[str, Any]] = [{**b, "expected_status": 200} for b in BLOG_EXAMPLES]
    seen: dict[str, str] = {b["name"]: b["file"] for b in BLOG_EXAMPLES}
    excluded: list[dict[str, str]] = []
    for path in sorted(paths):
        rel = path.removeprefix(FIXTURE_PREFIX)
        parts = rel.split("/")
        category = parts[0] if len(parts) > 1 else ROOT_CATEGORY
        name = Path(rel).stem
        if name in seen:
            raise SystemExit(f"duplicate example name {name!r}: {rel} vs {seen[name]}")
        seen[name] = rel
        pre = by_request.get(rel) or {}
        pre_cat = catalog.get(Path(rel).name, {})
        vendor_text = " ".join(
            str(x)
            for x in (
                rel,
                pre.get("title", ""),
                pre.get("description", ""),
                pre_cat.get("title", ""),
                pre_cat.get("description", ""),
            )
        )
        hit = VENDOR_RE.search(vendor_text)
        if hit:
            excluded.append({"path": path, "matched": hit.group(0)})
            print(f"excluded (vendor reference {hit.group(0)!r}): {path}", file=sys.stderr)
            continue
        data = git_show(repo, tag, path)
        dest = EXAMPLES_DIR / category / f"{name}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        body = json.loads(data)
        matches = _matching_endpoints(body)
        case = by_request.get(rel)
        row: dict[str, Any] = {
            "name": name,
            "category": category,
            "file": f"{category}/{name}.json",
            "source": f"quantraserver {tag} ({sha[:12]}) {path}",
            "expected_status": 200,
        }
        if name in EXPECTED_ERRORS:
            row.update(EXPECTED_ERRORS[name])
        if case is not None:
            endpoint = ROUTE_BY_PRODUCT[case["product"]]
            if endpoint not in matches:
                raise SystemExit(f"{rel}: manifest says {endpoint} but it validates as {matches}")
            cat = catalog.get(Path(rel).name, {})
            compare = str(case.get("compare", "npv"))
            text = cat.get("reference_text", "")
            row.update(
                {
                    "endpoint": endpoint,
                    "endpoint_source": "manifest",
                    "catalog_id": case["id"],
                    "family": case["family"],
                    "title": case.get("title") or cat.get("title", ""),
                    "description": case.get("description") or cat.get("description", ""),
                    "exercises": list(case.get("exercises", [])),
                    "compare": compare,
                    "list_key": case.get("list_key"),
                    "tolerance": case.get("tolerance"),
                    "reference_text": text,
                    "reference_value": (
                        float(text.replace(",", ""))
                        if compare == "npv" and _NPV_RE.match(text)
                        else None
                    ),
                    "oracle": parse_oracle(compare, text, case.get("tolerance")),
                }
            )
        else:
            if len(matches) != 1:
                raise SystemExit(f"{rel}: not cataloged and validates as {matches}")
            row.update(
                {
                    "endpoint": matches[0],
                    "endpoint_source": "schema-match",
                    "catalog_id": None,
                    "family": None,
                    "title": name.replace("_", " "),
                    "description": "Uncataloged engine example (no QuantLib reference value).",
                    "exercises": [],
                    "compare": None,
                    "list_key": None,
                    "tolerance": None,
                    "reference_text": "",
                    "reference_value": None,
                    "oracle": None,
                }
            )
        rows.append(row)
    EXCLUDED[:] = excluded
    return rows


#: Filled by vendor_examples(): fixtures left out under VENDOR_RE (recorded in INDEX.json).
EXCLUDED: list[dict[str, str]] = []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--engine-repo", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    repo: Path = args.engine_repo
    tag: str = args.tag

    sha = git_sha(repo, tag)
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for src, dest in FILES.items():
        data = git_show(repo, tag, src)
        dest.write_bytes(data)
        print(f"{src} -> {dest.relative_to(ROOT)} ({len(data)} bytes)", file=sys.stderr)

    spec = json.loads((SCHEMA_DIR / "openapi3.json").read_text())
    (SCHEMA_DIR / "enums_generated.py").write_text(render_enums(spec))
    (SCHEMA_DIR / "PIN").write_text(f"{tag} {sha}\n")
    print(f"PIN = {tag} {sha} (API {spec['info']['version']})", file=sys.stderr)

    rows = vendor_examples(repo, tag, sha)
    index = {
        "engine_tag": tag,
        "engine_sha": sha,
        "api_version": spec["info"]["version"],
        "count": len(rows),
        "excluded_vendor_specific": EXCLUDED,
        "examples": rows,
    }
    (EXAMPLES_DIR / "INDEX.json").write_text(json.dumps(index, indent=2) + "\n")
    methodology = methodology_gen.generate(repo, tag, sha)
    print(
        f"methodology: {len(methodology['topics'])} topics, "
        f"{sum(len(t['citations']) for t in methodology['topics'])} citations -> "
        f"{methodology_gen.OUT_DIR.relative_to(ROOT)}",
        file=sys.stderr,
    )
    cataloged = sum(1 for r in rows if r.get("catalog_id"))
    print(
        f"examples: {len(rows)} rows ({cataloged} cataloged, "
        f"{sum(1 for r in rows if r['oracle'])} with an oracle) -> "
        f"{(EXAMPLES_DIR / 'INDEX.json').relative_to(ROOT)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
