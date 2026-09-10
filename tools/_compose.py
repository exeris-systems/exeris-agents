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


def located(schema: dict, path: str) -> dict:
    """The schema, carrying the file it was read from as its `$id`.

    Nothing in the bundle carries one — an `$id` in a vendored base would make its canonical
    identifier a URL to fetch or register — and a validator without one starts from an empty base
    URI, where `urljoin` normalises the leading `../` off a relative reference and the second hop
    lands nowhere. The file is the identifier because rule 8 lets a reference resolve from the
    filesystem and nowhere else, so it wins over an `$id` a schema declares.
    """
    from pathlib import Path
    if not isinstance(schema, dict):
        return schema
    return {**schema, "$id": Path(os.path.abspath(path)).as_uri()}


def resolves_from(uri: str, base_dir: str) -> str:
    """The file a `$ref` names: a `file:` URI, an absolute path, or one relative to `base_dir`."""
    from urllib.parse import unquote, urlparse
    if uri.startswith("file:"):
        return unquote(urlparse(uri).path)
    if os.path.isabs(uri):
        return uri
    return os.path.normpath(os.path.join(base_dir, uri))


def registry_over(read, base_dir: str):
    """A `referencing` registry that retrieves through `read`, a caller's schema reader.

    The third copy of this was the one that had to move. `bundle/evals/run.py` keeps its own,
    because a vendored runner cannot import `tools/`; everything on this side comes here. `read`
    returns a parsed document or None, and what it does about containment is its own business —
    the grader guards against the repository it evaluates, the checker against the tree it walks.
    """
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    def retrieve(uri: str):
        document = read(resolves_from(uri, base_dir))
        if document is None:
            raise FileNotFoundError(f"$ref '{uri}' is not readable JSON inside this repository")
        return Resource.from_contents(document, default_specification=DRAFT202012)

    return Registry(retrieve=retrieve)


def vendor_roots(manifest: dict) -> list[str]:
    """Every `.agents/vendor/<bundle>-<version>` the manifest pins.

    `vendor_root()` above answers with the first import, which is right for what it is asked: a
    `bundle:` prefix resolves against the one bundle whose policies a profile composes. Whether a
    `$ref` lands in *a* pinned tree is a different question, and answering it with the first import
    calls every other pinned bundle a tree the manifest does not pin.
    """
    roots = []
    for imp in manifest.get("imports") or []:
        if isinstance(imp, dict) and imp.get("bundle") and imp.get("version"):
            roots.append(os.path.join(VENDOR, f"{imp['bundle']}-{imp['version']}"))
    return roots


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
