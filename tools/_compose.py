"""One implementation of how a composition reference resolves.

The checker and the renderer both turn `bundle:agent-safety-and-autonomy` into a path, and both
derive `.agents/vendor/<bundle>-<version>` from the manifest. Written twice they must agree, and a
drift between them is silent in the direction that matters: the renderer writes a link the checker
has already called valid, or the checker rejects a reference the renderer resolved. One definition,
imported by both.
"""
from __future__ import annotations

import os
import re

VENDOR = os.path.join(".agents", "vendor")
BUNDLE_PREFIX = "bundle:"
# A pin component is a plain name. `bundle` and `version` are joined into a path under `.agents/`
# and the result is read, written and — through the hook shim — executed, so a component carrying
# a separator, an absolute path or a leading dot would name a tree the pin's digest cannot vouch
# for. An absolute `bundle` is the sharp one: os.path.join swallows the base it was joined to.
SAFE_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def unsafe_pin(imp: dict) -> str | None:
    """The first pin component that is not a plain name, or None."""
    for key in ("bundle", "version"):
        value = imp.get(key)
        if value is not None and not SAFE_COMPONENT.match(str(value)):
            return key
    return None


def contained(base: str, path: str) -> str | None:
    """`path` resolved, or None if it does not stay inside `base`.

    Both ends are realpath'd, so a symlink out of the checkout is caught as well as a `..`.
    """
    root = os.path.realpath(base)
    full = os.path.realpath(path)
    return full if full == root or full.startswith(root + os.sep) else None


def pinned_import(manifest: dict) -> dict | None:
    """The bundle import, by one definition.

    `vendor_root()` used to require `bundle` and `version` while the pinned-import check required
    only `bundle`, so a manifest with more than one import could have the two resolve to different
    bundles. Both go through here.
    """
    for imp in manifest.get("imports") or []:
        if isinstance(imp, dict) and imp.get("bundle"):
            return imp
    return None


def vendor_root(manifest: dict) -> str | None:
    """`.agents/vendor/<bundle>-<version>`, or None when nothing usable is pinned."""
    imp = pinned_import(manifest)
    if not imp or not imp.get("version"):
        return None
    return os.path.join(VENDOR, f"{imp['bundle']}-{imp['version']}")


def resolve(kind: str, name: str, root: str | None) -> tuple[str | None, str | None]:
    """Return (path, error). `kind` is a directory under `.agents/` — policies, references.

    A bare name is this repository's own; `bundle:<name>` comes from the vendored tree. The prefix
    is explicit rather than inferred from where a file happens to sit, so a reader of the profile
    can tell whose rule it is.
    """
    text = str(name)
    if not text.startswith(BUNDLE_PREFIX):
        return os.path.join(".agents", kind, f"{text}.md"), None
    bare = text[len(BUNDLE_PREFIX):]
    if not root:
        return None, ("references a bundle policy but the manifest pins no bundle with a version, "
                      "so there is no vendored tree for it to come from (rule 8)")
    return os.path.join(root, kind, f"{bare}.md"), None


def as_list(value) -> tuple[list, bool]:
    """Return (items, was_scalar).

    A YAML scalar where a list belongs iterates character by character, which produced one bogus
    finding per character. The caller reports the shape once instead.
    """
    if value is None:
        return [], False
    if isinstance(value, (list, tuple)):
        return list(value), False
    return [value], True
