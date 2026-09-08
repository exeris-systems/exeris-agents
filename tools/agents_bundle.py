#!/usr/bin/env python3
"""Bundle materialiser and verifier — agents-md-schema.md rule 8.

Rule 8 says a manifest may import only an approved, version-pinned bundle, with a checksum when it
is fetched over the network, and that a bundle's scripts are reviewed code rather than something an
agent runs because a skill mentioned it. Until this script existed the rule had no target: both
repositories carried `imports: []` and the `[L1: pinned-import and checksum check]` marker named a
check nobody could write.

    agents_bundle.py vendor  --root <repo> --from <bundle-checkout>   # materialise + record digest
    agents_bundle.py verify  --root <repo>                            # recompute, compare, exit 1
    agents_bundle.py digest  --from <bundle-checkout>                 # print the digest only

The vendored tree is COMMITTED. That is the point: a reader on github.com, a CI runner with no
network, and an agent in a fresh clone all resolve the same `$ref` and read the same policy. A
bundle fetched at session time would be remote authority at runtime, which is what rule 8 forbids —
so the network is used once, by a human, at the moment the version is chosen.

The digest is over content, not over a tarball: every file's path and bytes, sorted, hashed. It
therefore survives repacking, line-ending normalisation on checkout is the one thing it does not
survive, and text files are read as bytes so nothing silently normalises them here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

VENDOR_DIR = os.path.join(".agents", "vendor")
BUNDLE_NAME = "exeris-agents"
# Only these subtrees are vendored. `tools/` deliberately is not: the renderer and the checker run
# in CI from a checkout of this repository at a pinned ref, and copying executable tooling into
# twenty repositories is the duplication the bundle exists to remove.
VENDORED = ("policies", "schemas", "hooks", "evals")
# Files copied to the root of the vendored tree, given as (source-relative, vendor-relative).
EXTRA = (("bundle/BUNDLE.md", "BUNDLE.md"), ("LICENSE", "LICENSE"))
SKIP_NAMES = {"__pycache__", ".DS_Store"}
# Written into the vendored tree after hashing, so it is never part of its own digest.
STAMP = ".bundle-digest"


def iter_files(root: str):
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_NAMES)
        for f in sorted(files):
            if f in SKIP_NAMES or f.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, f)
            yield full, os.path.relpath(full, root).replace(os.sep, "/")


def digest_of(entries) -> str:
    """sha256 over (vendor-relative path, size, bytes), in sorted path order.

    Content, not a tarball: it survives repacking and says nothing about mtimes or permissions.
    """
    h = hashlib.sha256()
    for rel, data in sorted(entries, key=lambda e: e[0]):
        h.update(rel.encode("utf-8")); h.update(b"\0")
        h.update(str(len(data)).encode("ascii")); h.update(b"\0")
        h.update(data); h.update(b"\n")
    return "sha256:" + h.hexdigest()


def staged(source: str):
    """Exactly what `vendor` would write, as (vendor-relative path, bytes).

    `digest` and `vendor` MUST agree, or the number a release publishes is not the number a
    consumer can verify — which is how the first pin written here was already wrong.
    """
    out = []
    for sub in VENDORED:
        s = os.path.join(source, "bundle", sub)
        if not os.path.isdir(s):
            continue
        for full, rel in iter_files(s):
            out.append((f"{sub}/{rel}", open(full, "rb").read()))
    for src_rel, dest_rel in EXTRA:
        full = os.path.join(source, src_rel)
        if os.path.exists(full):
            out.append((dest_rel, open(full, "rb").read()))
    return out


def digest_tree(tree: str) -> str:
    """Digest of a vendored tree on disk, excluding the stamp file it carries."""
    return digest_of([(rel, open(full, "rb").read())
                      for full, rel in iter_files(tree) if rel != STAMP])


def read_manifest(root: str) -> tuple[dict, str]:
    import yaml
    path = os.path.join(root, ".agents", "manifest.yaml")
    if not os.path.exists(path):
        sys.exit(f"agents_bundle: no {os.path.relpath(path, root)}")
    return yaml.safe_load(open(path, encoding="utf-8")) or {}, path


def pinned_import(manifest: dict) -> dict | None:
    for imp in manifest.get("imports") or []:
        if isinstance(imp, dict) and imp.get("bundle") == BUNDLE_NAME:
            return imp
    return None


def vendor_path(root: str, version: str) -> str:
    return os.path.join(root, VENDOR_DIR, f"{BUNDLE_NAME}-{version}")


def cmd_digest(a) -> int:
    if not os.path.isdir(os.path.join(a.source, "bundle")):
        sys.exit(f"agents_bundle: {a.source} has no bundle/ directory")
    print(digest_of(staged(a.source)))
    return 0


def cmd_vendor(a) -> int:
    src = os.path.join(a.source, "bundle")
    if not os.path.isdir(src):
        sys.exit(f"agents_bundle: {a.source} has no bundle/ directory")
    manifest, _ = read_manifest(a.root)
    imp = pinned_import(manifest)
    version = a.version or (imp or {}).get("version")
    if not version:
        sys.exit("agents_bundle: no version given and none pinned in the manifest — "
                 "rule 8 forbids a floating import, so the version is stated, never inferred")

    entries = staged(a.source)
    dest = vendor_path(a.root, version)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for rel, data in entries:
        full = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        if rel.endswith(".py"):
            os.chmod(full, 0o755)

    d = digest_of(entries)
    with open(os.path.join(dest, STAMP), "w", encoding="utf-8") as fh:
        fh.write(d + "\n")
    print(f"vendored {BUNDLE_NAME} {version} -> {os.path.relpath(dest, a.root)}")
    print(f"digest   {d}")
    print("\nRecord it in .agents/manifest.yaml:\n")
    print(f"imports:\n  - bundle: {BUNDLE_NAME}\n    version: {version}\n"
          f"    ref: {a.ref or '<full commit sha of the bundle checkout>'}\n    sha256: {d}")
    return 0


def cmd_verify(a) -> int:
    manifest, mpath = read_manifest(a.root)
    imp = pinned_import(manifest)
    if not imp:
        print("agents_bundle: no exeris-agents import pinned — nothing to verify")
        return 0
    version, recorded = imp.get("version"), imp.get("sha256")
    problems: list[str] = []
    if not version:
        problems.append("import has no version (rule 8)")
    if not recorded:
        problems.append("import has no sha256 (rule 8)")
    dest = vendor_path(a.root, str(version))
    if not os.path.isdir(dest):
        problems.append(f"pinned {version} is not vendored at "
                        f"{os.path.relpath(dest, a.root)} — run `agents_bundle.py vendor`")
    else:
        stamp = os.path.join(dest, STAMP)
        tmp = open(stamp, encoding="utf-8").read() if os.path.exists(stamp) else None
        actual = digest_tree(dest)
        if recorded and actual != recorded:
            problems.append(f"vendored tree does not match the pinned digest\n"
                            f"    pinned:   {recorded}\n    computed: {actual}\n"
                            f"    Someone edited .agents/vendor/ by hand, or the pin is stale. "
                            f"The vendored bundle is reviewed code; re-vendor from the pinned ref "
                            f"rather than editing it in place.")
        if tmp is not None and tmp.strip() != actual:
            problems.append(".bundle-digest inside the vendored tree disagrees with its contents")

    for p in problems:
        print(f"::error file={os.path.relpath(mpath, a.root)},title=agents_bundle pinned-import::{p}")
    if problems:
        print(f"\n{len(problems)} problem(s) with the pinned bundle import.")
        return 1
    print(f"agents_bundle: {BUNDLE_NAME} {version} vendored and matching its pinned digest.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("vendor", help="copy the bundle into the repo and print the pin")
    v.add_argument("--root", default=".")
    v.add_argument("--from", dest="source", required=True, help="a checkout of exeris-agents")
    v.add_argument("--version")
    v.add_argument("--ref", help="the bundle commit sha, recorded in the pin")
    v.set_defaults(fn=cmd_vendor)

    c = sub.add_parser("verify", help="recompute the digest and compare with the manifest pin")
    c.add_argument("--root", default=".")
    c.set_defaults(fn=cmd_verify)

    d = sub.add_parser("digest", help="print the digest of a bundle checkout")
    d.add_argument("--from", dest="source", required=True)
    d.set_defaults(fn=cmd_digest)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
