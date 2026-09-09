#!/usr/bin/env python3
"""Agent adapter renderer — ADR-085 §C.11, agents-md-schema.md rules 7, 11 and 12.

One implementation, shared by every repository. A repository that renders with its own script is a
fork of the standard and will drift from it, which is what happened while exeris-docs had none and
exeris-kernel had two shell scripts of its own.

    python3 agents_render.py --root <repo> [--vendor claude] [--check] [--skills-copy]

`--check` renders into memory and diffs against what is on disk, exiting 1 on any difference. That
is the CI form: an adapter edited by hand is a diff, and so is an adapter whose source moved.

What is rendered, per vendor mapping file in `agents/adapters/<vendor>.yaml`:

  .agents/agents/<n>/AGENT.md   -> the vendor's subagent file. The authored body is copied
                                   byte-for-byte; `skills`, `policies`, `references`, `handoffs`
                                   and `output` become a generated section appended under a marker,
                                   so the check can still assert the authored half is untouched.
  .agents/workflows/<n>.md      -> the vendor's user-invoked prompt.
  .agents/skills/               -> a symlink where the runtime cannot read .agents/skills natively.
  .agents/hooks/hooks.yaml      -> the vendor's hook config, which carries no patterns of its own:
                                   it invokes .agents/hooks/bin/dispatch.py — a version-free copy
                                   of the shim, written here too — which finds the pinned hook.py
                                   and lets it read the canonical definitions at runtime.

Vendors other than Claude need one mapping file each and no change here. They are deliberately not
shipped with a guessed tool vocabulary: a mapping written from memory would silently grant or
withhold tools, which is worse than an absent adapter.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _compose  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# Mappings sit beside the renderer. `--adapters` exists so a repository can try a vendor
# mapping out of tree before it is proposed to the bundle.
ADAPTER_DIR = os.environ.get("EXERIS_ADAPTER_DIR") or os.path.join(HERE, "adapters")

MARK_BEGIN = "<!-- BEGIN GENERATED: composition (agents-md-schema.md rule 5) -->"
MARK_END = "<!-- END GENERATED -->"

CAPABILITIES = {"read", "search", "edit", "shell", "web", "subagents"}
# How a rendered hook entry is recognised as this renderer's on the next run. BOTH spellings, and
# the old one stays for good: entries rendered before the shim existed name the vendored hook.py
# directly, and an entry this function does not recognise is treated as hand-authored and kept. A
# repository upgrading from <=1.2.0 would otherwise get every hook twice — the second copy pointing
# into a vendored tree the same bump has just removed.
DISPATCHER_MARKS = ("hooks/bin/hook.py", "hooks/bin/dispatch.py")
# The canonical tree, and the declarations inside it. Named once: a path spelled at each call site
# is a path that can be corrected at some of them.
AGENTS = ".agents"
# The version-free path the rendered config names, and the file the renderer copies there.
DISPATCH_REL = f"{AGENTS}/hooks/bin/dispatch.py"   # posix: it is written into a command string
HOOKS_YAML = os.path.join(AGENTS, "hooks", "hooks.yaml")
# A hook id, a vendor and an event become words in a command string that a runtime hands to a
# shell, and the shim then checks the vector it receives against this same alphabet. Validating
# here is what keeps the two from disagreeing: the renderer cannot emit a command the shim would
# refuse. `bundle/hooks/bin/dispatch.py` carries the matching copy and names this one.
COMMAND_WORD = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:@+-]*\Z")


def die(msg: str):
    print(f"agents_render: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_yaml(path: str):
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def split_frontmatter(path: str) -> tuple[dict, str]:
    import yaml
    text = open(path, encoding="utf-8").read()
    if not text.startswith("---\n"):
        die(f"{path}: no YAML frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        die(f"{path}: unterminated frontmatter")
    return yaml.safe_load(text[4:end]) or {}, text[end + 4:].lstrip("\n")


def marker(source: str) -> str:
    return (f"<!-- DO NOT EDIT. Generated from {source} by agents_render.py\n"
            f"     (exeris-systems/exeris-agents; agents-md-schema.md rule 7). Edit the source. -->")


def tools_for(fm: dict, mapping: dict, path: str) -> list[str]:
    caps = fm.get("capabilities") or []
    if not caps:
        die(f"{path}: 'capabilities' is required (rule 11)")
    if fm.get("tools"):
        die(f"{path}: canonical frontmatter must not carry a vendor 'tools' list (rule 11) — "
            f"declare capabilities, and put a vendor list under adapters.<vendor>.tools")
    if fm.get("mode") == "read-only" and "edit" in caps:
        die(f"{path}: mode is read-only but capabilities include 'edit'")
    out: list[str] = []
    for cap in caps:
        if cap.startswith("mcp:"):
            out.append(mapping["mcp-template"].format(server=cap.split(":", 1)[1]))
            continue
        if cap not in CAPABILITIES:
            die(f"{path}: unknown capability '{cap}' (rule 11)")
        for tool in mapping["capabilities"].get(cap, []):
            if tool not in out:
                out.append(tool)
    return out


def resolve(kind: str, name: str, vendor_root: str | None) -> str:
    """One implementation, shared with the checker (`tools/_compose.py`).

    Written twice these must agree, and a drift is silent in the direction that matters: the
    renderer writing a link the checker already called valid, or the reverse.
    """
    path, err = _compose.resolve(kind, name, vendor_root)
    if err:
        die(f"profile references '{name}': {err}")
    return path.replace(os.sep, "/")


def generated_section(fm: dict, vendor_root: str | None = None) -> str:
    """The composition, rendered into the body. Rule 5 keeps it by reference in the source; a
    runtime that only reads a body needs it spelled out, and a marker keeps the two halves apart."""
    parts: list[str] = []
    if fm.get("skills"):
        parts.append("## Skills\n\nLoad these before working; each is the single owner of its "
                     "procedure.\n\n" +
                     "\n".join(f"- `.agents/skills/{s}/SKILL.md`" for s in fm["skills"]))
    applies = [f"- `{resolve('policies', p, vendor_root)}`" for p in fm.get("policies") or []]
    applies += [f"- `{resolve('references', r, vendor_root)}`" for r in fm.get("references") or []]
    if applies:
        parts.append("## Applies\n\nRead the ones your change touches. Each is authoritative for "
                     "its own list; do not work from a remembered subset.\n\n" + "\n".join(applies))
    if fm.get("handoffs"):
        rows = ["| To | When | Blocking |", "|:--|:--|:--|"]
        for h in fm["handoffs"]:
            rows.append(f"| `{h.get('agent')}` | {h.get('when', '')} | "
                        f"{'yes' if h.get('blocking') else 'no'} |")
        parts.append("## Handoffs\n\n" + "\n".join(rows))
    if fm.get("output"):
        out = fm["output"]
        out = out if out.startswith(".agents/") else f".agents/{out}"
        parts.append("## Response contract\n\nAfter the Markdown response above, emit the same "
                     f"content as a fenced `json` block conforming to `{out}`. "
                     "The Markdown is for the human; the JSON is what the eval runner and the CI "
                     "review consume. If the two cannot be made to agree, the Markdown is wrong.")
    if not parts:
        return ""
    return f"\n\n{MARK_BEGIN}\n\n" + "\n\n".join(parts) + f"\n\n{MARK_END}\n"


def yaml_scalar(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def yaml_frontmatter(pairs: list[tuple[str, str]]) -> str:
    """Serialise frontmatter with a YAML dumper.

    An f-string is wrong here and fails silently: a description containing ': ' produces a mapping
    where a scalar was meant, and one opening with '*', '&', '[' or '{' produces an alias, an
    anchor or a flow collection. The renderer then reports success over an adapter whose
    frontmatter no runtime can parse.
    """
    import yaml
    lines = []
    for k, v in pairs:
        dumped = yaml.safe_dump({k: v}, default_flow_style=False, allow_unicode=True,
                                width=10 ** 6, sort_keys=False).rstrip("\n")
        lines.append(dumped)
    return "---\n" + "\n".join(lines) + "\n---\n"


def render_agent(src: str, mapping: dict, rel: str, vendor_root: str | None = None) -> str:
    fm, body = split_frontmatter(src)
    # Every other missing thing in this renderer is reported by name; `name` and `description`
    # were read straight out of the mapping, so a profile without one exited on a KeyError
    # traceback that says which key but not which file.
    for required in ("name", "description"):
        if not str(fm.get(required) or "").strip():
            die(f"{rel}: frontmatter has no '{required}' (rule 11) — every runtime reads it")
    over = ((fm.get("adapters") or {}).get(mapping["vendor"]) or {})
    out = {
        "name": fm["name"],
        "description": fm["description"],
        "tools": ", ".join(tools_for(fm, mapping, src)),
        "model": mapping["models"].get(fm.get("model", "inherit"), "inherit"),
    }
    out.update({k: yaml_scalar(v) for k, v in over.items()})
    keys = [k for k in mapping["frontmatter"] if k in out] + \
           [k for k in out if k not in mapping["frontmatter"]]
    head = yaml_frontmatter([(k, out[k]) for k in keys])
    return f"{head}\n{marker(rel)}\n{body.rstrip()}{generated_section(fm, vendor_root)}"


def render_workflow(src: str, mapping: dict, rel: str) -> str:
    fm, body = split_frontmatter(src)
    # Same as render_agent: a missing required field is named, not a bare KeyError traceback. The
    # fix landed in one of the two renderers and the CHANGELOG announced it for both.
    for required in ("name", "description"):
        if not str(fm.get(required) or "").strip():
            die(f"{rel}: workflow frontmatter has no '{required}' — every runtime reads it")
    # The user invokes it with /name; the model must not pick it up on its own, which is what the
    # legacy commands/ form guaranteed by being a different file kind.
    head = yaml_frontmatter([("name", fm["name"]),
                             ("description", fm["description"]),
                             ("disable-model-invocation", True)])
    return f"{head}\n{marker(rel)}\n{body.rstrip()}\n"


def dispatch_source(root: str, vendor_root: str | None) -> str | None:
    """The shim to copy, confined to the vendored tree, or None if this bundle ships none.

    One resolver, because two were a way for the renderer to name a file it had declined to write:
    `dispatcher_path` asked `os.path.exists` (which follows a symlink out of the tree) while
    `write_dispatch` asked for containment, so a symlinked vendor directory produced a rendered
    command pointing at a shim that was never written — the exact "names a file that is not there"
    state this whole change exists to remove.
    """
    if not vendor_root:
        return None
    src = _compose.contained(root, os.path.join(root, vendor_root, "hooks", "bin", "dispatch.py"))
    return src if src and os.path.isfile(src) else None


def dispatcher_path(root: str, vendor_root: str | None) -> str:
    """What the rendered config invokes — and it carries no version.

    It used to name the vendored hook.py, whose path contains the pin. That put the version in two
    places with different lifetimes: the adapter, written when the renderer last ran, and the
    vendored tree, replaced at every bump. Wherever the two are not in step the command points at a
    file that is not there, `python3` exits 2, and exit 2 from a PreToolUse hook is a block — so
    every tool call is denied by a stale string rather than by a rule.

    The shim at DISPATCH_REL is the same dispatcher reached by a path that never moves; it reads
    the pin from the manifest when the hook fires. A bundle vendored before the shim existed has
    none to copy, so the old stamped path is still rendered for it: upgrading the renderer alone
    must not rewrite a repository's adapters into a file its pinned bundle does not ship.
    """
    if vendor_root:
        if dispatch_source(root, vendor_root):
            return DISPATCH_REL
        vendored = os.path.join(vendor_root, "hooks", "bin", "hook.py")
        if os.path.exists(os.path.join(root, vendored)):
            return vendored.replace(os.sep, "/")
    return ".agents/hooks/bin/hook.py"


def write_dispatch(root: str, vendor_root: str | None, check: bool, changes: list[str]) -> None:
    """Copy the version-free shim out of the vendored tree to DISPATCH_REL.

    Generated, and inside the canonical tree on purpose. The path the adapters name must not move
    when the pin moves, which rules out the vendored tree; and one copy serves every vendor, which
    is why it is not under `.claude/`. The v2 layout already reserves `.agents/plugins/` for
    rendered output on the same reasoning.
    """
    # The destination is read and, when stale, deleted. It is a fixed path under `--root`, but
    # `--root` is an argument and the entry itself may be a symlink: `os.path.isfile` follows one,
    # so without this the renderer could read a file outside the checkout to decide whether to
    # delete a link to it. A generated adapter that is a link out of the tree is tampering, not a
    # state to write through, so it stops the render rather than being skipped quietly.
    dest = _compose.contained(root, os.path.join(root, DISPATCH_REL))
    if not dest:
        die(f"{DISPATCH_REL} resolves outside the repository — that is a generated file, and a "
            f"link leading out of the checkout is not something this renderer writes through")
    src = dispatch_source(root, vendor_root)
    if not src:
        # No shim to write, so a shim left over from a bundle that had one is stale: the adapters
        # no longer name it and nothing else would ever remove it. Same rule as a renamed profile's
        # adapter, and it is `prune`'s reason for existing.
        if os.path.isfile(dest) and "DO NOT EDIT" in open(dest, encoding="utf-8").read(400):
            if check:
                changes.append(f"{DISPATCH_REL} is generated but no pinned bundle ships one")
            else:
                os.remove(dest)
                changes.append(f"removed stale {DISPATCH_REL}")
        return
    body = open(src, encoding="utf-8").read()
    # The marker names the bundle path, not the vendored one. A version in this header would make
    # the one file whose whole purpose is not to move change on every bump, and a copy left behind
    # by an older render would differ from a fresh one for no reason a reader could act on.
    line = ("# DO NOT EDIT. Generated from bundle/hooks/bin/dispatch.py by agents_render.py\n"
            "# (exeris-systems/exeris-agents; agents-md-schema.md rule 7). Edit the source.\n")
    if body.startswith("#!"):
        shebang, rest = body.split("\n", 1)
        body = f"{shebang}\n{line}{rest}"
    else:
        body = line + body
    # Not covered by the pin's digest, and it does not need to be: it is a generated adapter, not
    # a vendored file, and `--check` compares it against its source byte for byte the way it does
    # every other adapter. A hand-edit is a CI failure there, which is the same guarantee rule 8
    # gives the vendored tree, arrived at by the mechanism that owns generated files.
    write(dest, body, check, changes, root)


def render_hooks(root: str, mapping: dict, vendor_root: str | None = None) -> str:
    """The vendor's hook config. It carries no patterns: hook.py reads hooks.yaml at runtime."""
    spec = load_yaml(os.path.join(root, HOOKS_YAML))
    dispatcher = dispatcher_path(root, vendor_root)
    hm = mapping["hooks"]
    events: dict[str, list] = {}
    for h in spec.get("hooks") or []:
        name = hm["events"].get(h.get("event"))
        if not name:
            # Dropping it quietly is how a hook stops existing on one vendor with nobody knowing.
            # Either the mapping gains the event or the manifest records losing it.
            die(f"vendor '{mapping['vendor']}' has no mapping for canonical event "
                f"'{h.get('event')}' (hook '{h.get('id')}') — add it to "
                f"agents/adapters/{mapping['vendor']}.yaml, or remove the hook. A silently "
                f"dropped hook is an unrecorded degradation.")
        if h.get("event") == "stop" and not hm.get("can-block-stop"):
            continue
        for label, word in (("hook id", h["id"]), ("vendor", mapping["vendor"]),
                            ("event", h.get("event", "pre-tool"))):
            if not COMMAND_WORD.match(str(word)):
                die(f"{label} {word!r} is not a plain word — it becomes part of a command string a "
                    f"runtime hands to a shell, and the dispatcher refuses a vector it cannot "
                    f"recognise, so a command built from it would be rendered and never run")
        entry = {
            "matcher": hm["matchers"].get(h.get("tool"), "") if h.get("tool") else "",
            "hooks": [{
                "type": "command",
                # --on-error is rendered from the hook's own `decision`, so whether it fails
                # closed is a property of the rule rather than of the hook's name.
                "command": f"python3 {dispatcher} --hook {h['id']} "
                           f"--vendor {mapping['vendor']} "
                           f"--event {h.get('event', 'pre-tool')} "
                           f"--on-error {'deny' if h.get('decision') in ('deny', 'block-or-allow') else 'allow'}",
                "timeout": h.get("timeout", 15),
            }],
        }
        if not entry["matcher"]:
            entry.pop("matcher")
        events.setdefault(name, []).append(entry)

    path = os.path.join(root, mapping["targets"]["hooks"])
    settings = {}
    if os.path.exists(path):
        settings = json.loads(open(path, encoding="utf-8").read() or "{}")

    # Merge, never replace. `settings.json` is shared with the human: replacing the whole `hooks`
    # key deletes every hand-authored hook, silently and irreversibly. Ours are identifiable by the
    # dispatcher they invoke, so they can be swapped out without touching anything else.
    def ours(entry) -> bool:
        return any(any(m in (h or {}).get("command", "") for m in DISPATCHER_MARKS)
                   for h in (entry or {}).get("hooks") or [])

    existing = settings.get("hooks") or {}
    merged: dict[str, list] = {}
    for name in sorted(set(existing) | set(events)):
        kept = [e for e in existing.get(name, []) if not ours(e)]
        merged[name] = kept + events.get(name, [])
        if not merged[name]:
            del merged[name]
    if merged:
        settings["hooks"] = merged
    elif "hooks" in settings:
        del settings["hooks"]
    # No `_generated` key: settings.json has a schema this renderer does not own, and an
    # unrecognised top-level key is the renderer editing a document it only contributes to.
    # `.claude/README.md` is where the generated block is described.
    settings.pop("_generated", None)
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def write(path: str, content: str, check: bool, changes: list[str], root: str) -> None:
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old == content:
        return
    rel = os.path.relpath(path, root)
    if check:
        diff = "\n".join(difflib.unified_diff((old or "").splitlines(), content.splitlines(),
                                              fromfile=f"{rel} (on disk)",
                                              tofile=f"{rel} (rendered)", lineterm=""))
        changes.append(f"{rel}\n{diff}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    changes.append(rel)


def link_skills(root: str, skills_dir: str, names: list[str], check: bool, copy: bool,
                changes: list[str]) -> None:
    """One symlink per skill, not one for the whole directory.

    A single `.claude/skills -> ../.agents/skills` link is the obvious form and it is wrong here:
    the same directory is where workflows render (a workflow is a user-invoked skill on this
    runtime), so linking the directory makes the two kinds fight over one path — the render writes
    the workflows and the link then replaces them. Per-skill links keep both, and still store no
    copy of any skill.
    """
    dest_root = os.path.join(root, skills_dir)
    os.makedirs(dest_root, exist_ok=True)
    for name in names:
        src = os.path.join(root, AGENTS, "skills", name)
        dest = os.path.join(dest_root, name)
        rel = os.path.relpath(os.path.join(skills_dir, name))
        if copy:
            import shutil
            # filecmp.dircmp compares one level. A skill's references/ and scripts/ are exactly
            # where its content lives, so a top-level comparison reports "up to date" over a
            # stale copy — the silent green this whole review was about.
            if os.path.isdir(dest) and not os.path.islink(dest) and tree_equal(src, dest):
                continue
            if check:
                changes.append(f"{rel} is not an up-to-date copy of .agents/skills/{name}")
                continue
            if os.path.lexists(dest):
                shutil.rmtree(dest) if os.path.isdir(dest) and not os.path.islink(dest) else os.remove(dest)
            shutil.copytree(src, dest)
            changes.append(f"{rel} (copy)")
            continue
        want = os.path.relpath(src, dest_root)
        if os.path.islink(dest) and os.readlink(dest) == want:
            continue
        if check:
            changes.append(f"{rel} is not a symlink to {want}")
            continue
        if os.path.lexists(dest):
            import shutil
            shutil.rmtree(dest) if os.path.isdir(dest) and not os.path.islink(dest) else os.remove(dest)
        os.symlink(want, dest)
        changes.append(f"{rel} -> {want}")


def tree_equal(a: str, b: str) -> bool:
    """Byte-for-byte over the whole tree, both directions."""
    def files(base):
        out = {}
        for dirpath, dirnames, names in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for n in names:
                full = os.path.join(dirpath, n)
                out[os.path.relpath(full, base).replace(os.sep, "/")] = full
        return out
    fa, fb = files(a), files(b)
    if set(fa) != set(fb):
        return False
    return all(open(fa[k], "rb").read() == open(fb[k], "rb").read() for k in fa)


def prune(root: str, directory: str, keep: set[str], check: bool, changes: list[str]) -> None:
    """Remove generated entries the manifest no longer names.

    Without this a renamed profile leaves its old adapter behind, and the runtime lists an agent
    that no source produces — the drift the marker check cannot see, because the stale file carries
    a perfectly good marker.
    """
    d = os.path.join(root, directory)
    if not os.path.isdir(d):
        return
    for entry in sorted(os.listdir(d)):
        if entry in keep or entry.startswith("."):
            continue
        path = os.path.join(d, entry)
        head = ""
        # islink FIRST. os.path.isdir follows a symlink, finds the canonical SKILL.md — which
        # carries no marker by design — and concludes the entry is not ours, so a stale per-skill
        # link would never be pruned and the islink branch would be unreachable.
        if os.path.islink(path):
            head = "generated from"          # a per-skill link this renderer made
        elif os.path.isfile(path):
            head = open(path, encoding="utf-8", errors="replace").read(600)
        elif os.path.isdir(path) and os.path.exists(os.path.join(path, "SKILL.md")):
            head = open(os.path.join(path, "SKILL.md"), encoding="utf-8", errors="replace").read(600)
        if "DO NOT EDIT" not in head and "generated from" not in head.lower():
            continue                          # not ours; leave it and let the check report it
        rel = os.path.join(directory, entry)
        if check:
            changes.append(f"{rel} is generated but the manifest no longer names it")
            continue
        import shutil
        shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
        changes.append(f"removed stale {rel}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--vendor", action="append", help="repeatable; default: every enabled vendor")
    ap.add_argument("--check", action="store_true", help="diff against disk, write nothing")
    ap.add_argument("--adapters", help="directory of vendor mapping files (default: beside this script)")
    ap.add_argument("--skills-copy", action="store_true",
                    help="copy skills instead of symlinking (checkouts without symlink support)")
    a = ap.parse_args()

    global ADAPTER_DIR
    if a.adapters:
        ADAPTER_DIR = os.path.abspath(a.adapters)
    root = os.path.abspath(a.root)
    manifest_path = os.path.join(root, AGENTS, "manifest.yaml")
    if not os.path.exists(manifest_path):
        print("agents_render: no .agents/manifest.yaml — nothing to render")
        return 0
    manifest = load_yaml(manifest_path)
    if str(manifest.get("version")) != "2":
        print(f"agents_render: manifest is version {manifest.get('version')!r}, not 2 — "
              f"this repository has not migrated to the v2 layout; nothing to render")
        return 0
    vendor_root = None
    for imp in manifest.get("imports") or []:
        if isinstance(imp, dict) and imp.get("bundle") and imp.get("version"):
            bad = _compose.unsafe_pin(imp)
            if bad:
                die(f"manifest import has {bad}: {imp[bad]!r}, which is not a plain name — the pin "
                    f"becomes a path under .agents/vendor/, so a separator, an absolute value or a "
                    f"leading dot names a tree the digest in rule 8 does not cover")
            vendor_root = f".agents/vendor/{imp['bundle']}-{imp['version']}"
            if not os.path.isdir(os.path.join(root, vendor_root)):
                die(f"manifest pins {imp['bundle']} {imp['version']} but {vendor_root} is not "
                    f"vendored — run tools/agents_bundle.py vendor")
            break

    adapters = manifest.get("adapters") or {}
    vendors = a.vendor or [v for v, c in adapters.items()
                           if (c or {}).get("status") != "deferred"]

    changes: list[str] = []
    # Once, not per vendor: the shim is vendor-neutral and every adapter names the same copy.
    if os.path.exists(os.path.join(root, HOOKS_YAML)):
        write_dispatch(root, vendor_root, a.check, changes)
    for vendor in vendors:
        mpath = os.path.join(ADAPTER_DIR, f"{vendor}.yaml")
        if not os.path.exists(mpath):
            if a.vendor:
                die(f"no mapping for vendor '{vendor}' at agents/adapters/{vendor}.yaml — "
                    f"a vendor is one mapping file, and one written from memory is worse than none")
            print(f"agents_render: no mapping for '{vendor}'; skipping (mark it "
                  f"`status: deferred` in the manifest to say so on purpose)", file=sys.stderr)
            continue
        mapping = load_yaml(mpath)
        t = mapping["targets"]

        for name in manifest.get("agents") or []:
            src = os.path.join(root, AGENTS, "agents", name, "AGENT.md")
            if not os.path.exists(src):
                die(f"manifest lists agent '{name}' but {os.path.relpath(src, root)} does not exist")
            write(os.path.join(root, t["agents"].format(name=name)),
                  render_agent(src, mapping, os.path.relpath(src, root), vendor_root), a.check, changes, root)

        for name in manifest.get("workflows") or []:
            src = os.path.join(root, AGENTS, "workflows", f"{name}.md")
            if not os.path.exists(src):
                die(f"manifest lists workflow '{name}' but {name}.md does not exist")
            write(os.path.join(root, t["workflows"].format(name=name)),
                  render_workflow(src, mapping, os.path.relpath(src, root)), a.check, changes, root)

        agents_dir = os.path.dirname(t["agents"])
        prune(root, agents_dir,
              {f"{n}.md" for n in manifest.get("agents") or []}, a.check, changes)

        if t.get("skills") == "symlink":
            skills_dir = mapping["targets"]["skills-link"]
            link_skills(root, skills_dir, manifest.get("skills") or [],
                        a.check, a.skills_copy, changes)
            prune(root, skills_dir,
                  set(manifest.get("skills") or []) | set(manifest.get("workflows") or []),
                  a.check, changes)

        if t.get("hooks") and os.path.exists(os.path.join(root, HOOKS_YAML)):
            write(os.path.join(root, t["hooks"]), render_hooks(root, mapping, vendor_root), a.check, changes, root)

    if a.check:
        if changes:
            print("agents_render --check: adapters differ from their source\n")
            for c in changes:
                print(c)
            print(f"\n{len(changes)} adapter(s) out of date — run agents_render.py and commit.")
            return 1
        print("agents_render --check: every adapter matches its source.")
        return 0

    for c in changes:
        print(f"rendered {c}")
    print(f"{len(changes)} file(s) written for: {', '.join(vendors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
