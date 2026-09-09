#!/usr/bin/env python3
"""Version-free indirection between a rendered hook config and the vendored dispatcher.

A rendered `.claude/settings.json` used to name the hook by its vendored path, and that path
carries the pin: `.agents/vendor/exeris-agents-1.2.0/hooks/bin/hook.py`. One string, two owners —
the adapter, written when the renderer last ran, and the vendored tree, replaced at every bump. Any
checkout holding one of them at a version the other does not have a command pointing at a file that
is not there.

That failure is not a warning. `python3` exits 2 on a file it cannot open, and exit 2 from a
`PreToolUse` hook means *blocked*, so a recorder that declares `--on-error allow` blocks the tool
call anyway: the interpreter answers before the layer can. Every shell call in the session is
denied, and the reason is a path, not a rule.

It is also not hypothetical. The review environment for a pull request pairs the base branch's
protected `.claude/` with the branch's own tree, so the first review of every bundle bump ran with
no shell at all and reported its checks as `not-run` — twice on exeris-docs #106 before anyone
looked at why.

The renderer copies this file to `.agents/hooks/bin/dispatch.py` and the rendered config names that
instead. It carries no version: it reads the pin from `.agents/manifest.yaml` when the hook fires,
so a stale adapter and a fresh tree still meet. `manifest.yaml` remains the single authority for
which bundle runs — it is simply read later, at a moment when both halves are on disk together.
"""
from __future__ import annotations

import os
import re
import sys

MANIFEST = os.path.join(".agents", "manifest.yaml")
FALLBACK = os.path.join(".agents", "hooks", "bin", "hook.py")


def repo_root() -> str:
    """The checkout holding `.agents/manifest.yaml`, working directory first.

    Deliberately smaller than hook.py's rule, and it does not have to agree with it: this answer
    only locates the manifest and the vendored file. Which repository's *rules* apply is decided by
    hook.py, from its own working directory, after this file has execed it.
    """
    for start in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd(),
                  os.path.dirname(os.path.abspath(__file__))):
        if not start:
            continue
        cur = os.path.abspath(start)
        while True:
            if os.path.exists(os.path.join(cur, MANIFEST)):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return os.path.abspath(os.getcwd())


def pinned(text: str) -> tuple[str, str] | None:
    """`(bundle, version)` from the first import naming both, or None.

    A regex rather than `yaml.safe_load`, for one reason: pyyaml may not be installed, and that is
    a condition the layer already handles well — hook.py reports it in the vendor's own wire format
    and applies the caller's `--on-error`. Failing here for a missing parser would replace a
    decision the layer knows how to make with a bare exit code.

    The manifest's own `version: 2` is not a candidate: only a `version:` following a `bundle:`
    inside the same list item is read, and the item ends at the next `- ` or the next unindented
    key.
    """
    bundle = None
    for line in text.splitlines():
        found = re.match(r"\s*-\s*bundle:\s*(\S+)", line) or (
            re.match(r"\s+bundle:\s*(\S+)", line) if bundle is None else None)
        if found:
            bundle = found.group(1).strip("'\"")
            continue
        if bundle is None:
            continue
        version = re.match(r"\s+version:\s*(\S+)", line)
        if version:
            return bundle, version.group(1).strip("'\"")
        if re.match(r"\s*-\s", line) or (line.strip() and not line[0].isspace()):
            bundle = None                     # that import ended without naming a version
    return None


def target(root: str) -> str | None:
    """The hook.py this call should run: the pinned vendored copy, else a repository-owned one."""
    manifest = os.path.join(root, MANIFEST)
    if os.path.exists(manifest):
        try:
            pin = pinned(open(manifest, encoding="utf-8").read())
        except OSError:
            pin = None
        if pin:
            vendored = os.path.join(root, ".agents", "vendor", f"{pin[0]}-{pin[1]}",
                                    "hooks", "bin", "hook.py")
            if os.path.exists(vendored):
                return vendored
    local = os.path.join(root, FALLBACK)
    return local if os.path.exists(local) else None


def refuse(argv: list[str], reason: str) -> int:
    """No hook.py to run. Honour the caller's own `--on-error`, not the interpreter's.

    This is the one decision this file makes on its own, and it makes it the way hook.py makes the
    same one for an unreadable config: fail closed where the *rule* says to, open where it does
    not. Exit 2 is the block channel on the runtimes that document one; the JSON channel belongs to
    hook.py, and reaching this line means hook.py is what could not be found.
    """
    on_error = "deny"
    if "--on-error" in argv:
        i = argv.index("--on-error")
        if i + 1 < len(argv):
            on_error = argv[i + 1]
    print(f"exeris hook dispatch: {reason}", file=sys.stderr)
    return 2 if on_error == "deny" else 0


def main(argv: list[str]) -> int:
    root = repo_root()
    hook = target(root)
    if not hook:
        return refuse(argv, f"no hook.py under {root} — the manifest pins no vendored bundle and "
                            f"{FALLBACK} is absent (run tools/agents_bundle.py vendor)")
    try:
        os.execv(sys.executable, [sys.executable, hook] + argv)
    except OSError as exc:                    # exec failed; the process is still this one
        return refuse(argv, f"cannot run {hook}: {type(exc).__name__}: {exc}")
    return 0                                  # unreachable after a successful execv


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
