"""Fetch the two source trees the ``search_source`` / ``read_source`` / ``list_source``
tools serve: the engine at the pinned tag (``schema/PIN``) and QuantLib at the pinned
version (``schema/QUANTLIB_PIN``; GitHub tag ``v<version>``).

Downloads each GitHub tarball (``.../archive/refs/tags/<tag>.tar.gz``), verifies its
sha256 against ``schema/SOURCES.lock`` (recorded on the first fetch of a tag; a later
fetch of the same tag must match or the script fails), and extracts only the allowed
trees and only text files into ``vendor/sources/{engine,quantlib}/`` (gitignored).
Idempotent: a tree already extracted from the locked tarball is left alone.

Standard library only, so the container build stage can run it with the base image's
python. Tar members are extracted with the ``data`` filter and additionally refused when
they are links, devices, absolute or contain ``..``.

    uv run python scripts/fetch_sources.py              # vendor/sources/
    python scripts/fetch_sources.py --dest /app/vendor/sources --cache /tmp/cache
    python scripts/fetch_sources.py --check             # exit 1 when a tree is missing/stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issuer_scrub  # stdlib only; copied beside this script in the image

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "src" / "quantra_mcp" / "schema"
LOCK_PATH = SCHEMA_DIR / "SOURCES.lock"
DEFAULT_DEST = ROOT / "vendor" / "sources"
DEFAULT_CACHE = ROOT / "vendor" / "cache"
STAMP = ".fetched.json"
#: bump when issuer_scrub's rewrites change, so an extracted tree is re-extracted.
SCRUB_VERSION = 2

TEXT_SUFFIXES = {
    ".h",
    ".hpp",
    ".cpp",
    ".c",
    ".fbs",
    ".md",
    ".json",
    ".txt",
    ".cmake",
    ".sh",
    ".py",
}

#: per repository: GitHub ``owner/name``, the allowed trees (prefix -> suffix filter or
#: None = the text suffixes above) and the trees skipped on purpose.
REPOS: dict[str, dict[str, Any]] = {
    "engine": {
        "github": "joseprupi/quantraserver",
        "trees": {
            "src/": None,
            "jsonserver/": None,
            "flatbuffers/": {".fbs"},
            "docs/": None,
            "tests/functional/CATALOG.md": None,
            "LICENSE.txt": None,
            "README.md": None,
        },
        "skip": ["examples/data/ (already vendored under src/quantra_mcp/examples/)"],
        # issuer / real-security passages rewritten line for line (scripts/issuer_scrub.py)
        "scrub": True,
    },
    "quantlib": {
        "github": "lballabio/QuantLib",
        "trees": {"ql/": None, "LICENSE.TXT": None, "README.md": None},
        "skip": [],
    },
}


def read_pins() -> dict[str, str]:
    engine_tag = (SCHEMA_DIR / "PIN").read_text().split()[0]
    ql_version = (SCHEMA_DIR / "QUANTLIB_PIN").read_text().split()[0]
    return {"engine": engine_tag, "quantlib": f"v{ql_version}"}


def tarball_url(github: str, tag: str) -> str:
    return f"https://github.com/{github}/archive/refs/tags/{tag}.tar.gz"


def load_lock() -> dict[str, Any]:
    if LOCK_PATH.is_file():
        data: dict[str, Any] = json.loads(LOCK_PATH.read_text())
        return data
    return {}


def save_lock(lock: dict[str, Any]) -> None:
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "quantra-mcp fetch_sources"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _allowed(rel: str, trees: dict[str, set[str] | None]) -> bool:
    for prefix, suffixes in trees.items():
        if prefix.endswith("/"):
            if not rel.startswith(prefix):
                continue
        elif rel != prefix:
            continue
        name = rel.rsplit("/", 1)[-1]
        suffix = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
        allowed = TEXT_SUFFIXES if suffixes is None else suffixes
        return suffix in allowed or name == "CMakeLists.txt"
    return False


def extract(
    tarball: Path, dest: Path, trees: dict[str, set[str] | None], *, scrub: bool = False
) -> tuple[int, int]:
    """Extract the allowed text files of ``tarball`` (GitHub layout: one top directory)
    into ``dest``; returns ``(files, bytes)``. ``scrub``: issuer / real-security text is
    rewritten line for line (``issuer_scrub.scrub``) before it is written."""
    files = 0
    total = 0
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue  # links, directories, devices: never extracted
            parts = member.name.split("/")
            if len(parts) < 2 or parts[0] in ("", "..", ".") or member.name.startswith("/"):
                continue
            if any(p in ("..", "") for p in parts[1:]):
                continue
            rel = "/".join(parts[1:])
            if not _allowed(rel, trees):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            data = fh.read()
            if b"\x00" in data[:8192]:
                continue  # not text after all
            if scrub:
                data = issuer_scrub.scrub(rel, data)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files += 1
            total += len(data)
    return files, total


def fetch_one(
    key: str, tag: str, dest_root: Path, cache: Path, lock: dict[str, Any], *, force: bool
) -> dict[str, Any]:
    spec = REPOS[key]
    url = tarball_url(spec["github"], tag)
    dest = dest_root / key
    stamp_path = dest / STAMP
    entry = lock.get(key)
    if entry is not None and entry.get("tag") != tag:
        print(
            f"{key}: lock records {entry['tag']}, pin is {tag}; re-recording (pin bump)",
            file=sys.stderr,
        )
        entry = None
    if not force and stamp_path.is_file() and entry is not None:
        stamp = json.loads(stamp_path.read_text())
        if (
            stamp.get("sha256") == entry["sha256"]
            and stamp.get("tag") == tag
            and stamp.get("scrub_version", 0) == (SCRUB_VERSION if spec.get("scrub") else 0)
        ):
            print(
                f"{key}: {tag} already extracted ({stamp['files']} files), skipping",
                file=sys.stderr,
            )
            return entry
    tarball = cache / f"{key}-{tag}.tar.gz"
    if not tarball.is_file():
        print(f"{key}: downloading {url}", file=sys.stderr)
        download(url, tarball)
    digest = sha256_of(tarball)
    if entry is not None and entry["sha256"] != digest:
        raise SystemExit(
            f"{key}: sha256 of {tarball} is {digest}, lock says {entry['sha256']} for {tag}; "
            "refusing (delete the cached tarball to re-download, or update the lock on purpose)"
        )
    scrub = bool(spec.get("scrub"))
    files, nbytes = extract(tarball, dest, spec["trees"], scrub=scrub)
    record = {
        "github": spec["github"],
        "tag": tag,
        "url": url,
        "sha256": digest,
        "tarball_bytes": tarball.stat().st_size,
        "files": files,
        "bytes": nbytes,
        "trees": sorted(spec["trees"]),
        "skipped": list(spec["skip"]),
    }
    if scrub:
        record["scrub_version"] = SCRUB_VERSION
    if entry is not None and entry.get("files") != files:
        print(
            f"{key}: file count {files} differs from the lock's {entry.get('files')} "
            "(filter changed?); lock updated",
            file=sys.stderr,
        )
    stamp_path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"{key}: {tag} -> {dest} ({files} files, {nbytes / 1048576:.1f} MB)", file=sys.stderr)
    return record


def check(dest_root: Path, lock: dict[str, Any]) -> int:
    pins = read_pins()
    rc = 0
    for key, tag in pins.items():
        entry = lock.get(key)
        stamp_path = dest_root / key / STAMP
        if entry is None or entry.get("tag") != tag:
            print(f"{key}: no lock entry for {tag}", file=sys.stderr)
            rc = 1
            continue
        if not stamp_path.is_file():
            print(f"{key}: not fetched under {dest_root / key}", file=sys.stderr)
            rc = 1
            continue
        stamp = json.loads(stamp_path.read_text())
        want_scrub = SCRUB_VERSION if REPOS[key].get("scrub") else 0
        if (
            stamp.get("sha256") != entry["sha256"]
            or stamp.get("tag") != tag
            or stamp.get("scrub_version", 0) != want_scrub
        ):
            print(f"{key}: extracted tree is stale against the lock", file=sys.stderr)
            rc = 1
            continue
        print(f"{key}: {tag} ok ({stamp['files']} files)", file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--force", action="store_true", help="re-extract even if current")
    parser.add_argument("--check", action="store_true", help="verify only, no network")
    parser.add_argument(
        "--only", choices=sorted(REPOS), action="append", help="fetch only this repo"
    )
    args = parser.parse_args(argv)
    lock = load_lock()
    if args.check:
        return check(args.dest, lock)
    pins = read_pins()
    for key, tag in pins.items():
        if args.only and key not in args.only:
            continue
        lock[key] = fetch_one(key, tag, args.dest, args.cache, lock, force=args.force)
    save_lock(lock)
    total = sum(lock[k]["bytes"] for k in pins if k in lock)
    print(
        f"lock {LOCK_PATH.relative_to(ROOT)}: "
        + ", ".join(f"{k} {lock[k]['tag']} {lock[k]['files']} files" for k in pins if k in lock)
        + f"; {total / 1048576:.1f} MB on disk under {args.dest}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
