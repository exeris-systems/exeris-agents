#!/usr/bin/env python3
"""Agent-file checker — ADR-085 §I.29, agents-md-schema.md hard rules 1, 2, 4, 5, 7, 8, 10-13.

Rule 8's checksum half is delegated to agents_bundle.py, which owns the digest.

Replaces claude_md_check.py, which enforced the superseded CLAUDE.md schema: a fixed list of
verbatim headings. The current schema puts the canonical entry point at AGENTS.md, the canonical
semantics under .agents/, and leaves the prose and heading names to the repository — so this
checker verifies structure, size, skill layout, manifest pinning and adapter discipline, and
deliberately checks nothing about wording.

Schema v2 (2026-09-08) added five rules and this checker covers four of them: role profiles live
at .agents/agents/<name>/AGENT.md and never at a lowercase agent.md (10), canonical frontmatter is
vendor-neutral and carries no tool names (11), hooks are declared once and their dispatcher exists
(12), and a declared output schema is a real, valid JSON Schema (13). Rule 14 — that a covered
change reran its evals — is not checkable from a checkout and stays [L2].

Not mechanically checkable, and therefore left to review ([L2] in the schema): whether AGENTS.md
covers its six concerns in order, whether a rule is encoded as the right kind of artefact, and
whether a reference is linked rather than copied.
"""
from __future__ import annotations
import argparse, io, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import _compose
from _common import Report, read_frontmatter

ROOT_LIMIT = 8 * 1024          # rule 1: AGENTS.md is an index and a safety boundary
NESTED_LIMIT = 4 * 1024        # nested AGENTS.md add scope-specific rules only
ADAPTER_MAX_LINES = 20         # a provider entry file points at the canonical source

# Provider directories are adapters (rule 7). Semantic content must not be authored here (rule 2).
PROVIDER_DIRS = [".claude", ".github", ".codex", ".cursor", ".gemini", ".clinerules"]
# Subtrees inside them that carry semantics rather than operational configuration.
SEMANTIC_SUBDIRS = ["agents", "prompts", "skills", "rules", "policies", "workflows"]
# Rule 7 keeps operational configuration provider-owned: GitHub Actions are not a semantic adapter,
# and .github/workflows collides by name with the semantic .agents/workflows.
OPERATIONAL = {os.path.join(".github", "workflows")}
# Provider entry files that may exist only as thin adapters.
ADAPTER_FILES = ["CLAUDE.md", "GEMINI.md", ".cursorrules", ".github/copilot-instructions.md"]
# A generated adapter says so and says where it came from (rule 7).
GENERATED = re.compile(r"do[- ]not[- ]edit|generated from|@generated", re.I)

# A path rooted in somebody's home directory is true on one machine. In a public repository an
# instruction built on one does not fail for a reader who does not have it — the grep finds nothing
# and they draw a conclusion from the silence. Repository names are public and carry no such
# problem, so the fix is to name the repository and leave the sibling checkout as a convenience,
# not to delete the reference.
#
# `/home/runner/` is excluded: that is the GitHub Actions user, and an agent file describing what
# CI does is describing a real, shared machine.
MACHINE_PATH = re.compile(
    r"(?<![\w/~])~/[\w.]"                                    # ~/exeris-systems, ~/.m2 — not a bare ~ or ~~struck~~
    r"|(?<![\w/])/home/(?!runner/)[a-z_][a-z0-9_-]*/"        # /home/<someone>/ but not the Actions user
    r"|(?<![\w/])/Users/[A-Za-z][\w .-]*/"                   # macOS
    r"|(?<![\w])[A-Za-z]:\\Users\\[^\\\s]+")               # Windows, which has no trailing-slash rule

# `.agents-tools` and `.guardrails` are TOOLING CHECKED OUT INTO THE WORKSPACE this checker walks:
# docs-lint.yml fetches this bundle into the first and the organisation guardrails into the second.
# Without them here, the bundle's OWN `AGENTS.md` is read as a nested file of whatever repository
# is being checked and measured against the 4 KB nested cap — which it exceeds, so every consumer
# failed on a file that is not theirs and that they cannot edit. `nested_checkout` does not save
# it: `actions/checkout` leaves a `.git`, but the organisation repository's own run rsyncs the tree
# with `--exclude .git` and the marker is gone.
SKIP = (".git", "node_modules", "target", "build", "dist", ".agents-tools", ".guardrails")
# .agents/vendor/ holds a pinned copy of the shared bundle. Its portability and its
# contents are the bundle repository's to check; here it is verified by digest, and
# re-reporting its findings would put them on a worklist nobody can act on locally.
VENDOR = os.path.join(".agents", "vendor")


def nested_checkout(dirpath: str, name: str) -> bool:
    """True for a directory that is its own git checkout — a worktree parked under .claude/, a
    submodule. Its files belong to that repository and are reported when *it* is checked; CI never
    sees them at all, because a fresh clone has none. Before this, a worktree left under
    .claude/worktrees/ produced a size ERROR against an AGENTS.md that is not this repo's copy.
    """
    return os.path.exists(os.path.join(dirpath, name, ".git"))


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64

# rule 11 — the canonical vocabulary. A vendor's tool names are not in it, which is the point:
# a profile that names them has been written against one runtime.
CAPABILITIES = {"read", "search", "edit", "shell", "web", "subagents"}
MCP_CAPABILITY = re.compile(r"^mcp:[a-z0-9][a-z0-9_-]*$")
ROLES = {"router", "reviewer", "implementer", "evaluator", "specialist"}
MODES = {"read-only", "edit", "autonomous"}
MODEL_TIERS = {"inherit", "fast", "balanced", "strong"}

# Vendor tool names seen in the wild, used only to give a better message than "unknown capability"
# when someone pastes a v1 profile in.
VENDOR_TOOL_HINT = re.compile(r"^(Read|Write|Edit|Grep|Glob|Bash|WebFetch|WebSearch|Task|TodoWrite)$")

# rule 1 / naming table — the tightest cap each vendor imposes on a body.
PROFILE_BODY_MAX = 30_000
WORKFLOW_BODY_MAX = 12_000
# rule 8: an import is pinned to a version, never to a moving target.
FLOATING = re.compile(r"^(latest|main|master|head|\*|~|\^)", re.I)


def check_agents_md(path: str, rep: Report, limit: int, kind: str):
    size = os.path.getsize(path)
    if size > limit:
        rep.error(path, f"{kind} AGENTS.md is {size // 1024} KB (limit {limit // 1024} KB) — "
                        f"move detail into .agents/ or docs/ and link it", rule="size")
    text = open(path, encoding="utf-8", errors="replace").read()
    if not text.strip():
        rep.error(path, "AGENTS.md is empty", rule="content")
    return text


def check_skill(d: str, rep: Report):
    """rule 4 — .agents/skills/<name>/SKILL.md, with name and a precise description."""
    name = os.path.basename(d)
    path = os.path.join(d, "SKILL.md")
    rel = os.path.relpath(path)
    if not os.path.exists(path):
        rep.error(os.path.relpath(d), f"skill directory '{name}' has no SKILL.md", rule="skill-path")
        return
    if not SKILL_NAME.match(name):
        rep.error(rel, f"skill directory '{name}' is not lowercase kebab-case", rule="skill-path")
    fm, _ = read_frontmatter(path)
    if fm is None or fm.get("__invalid__"):
        rep.error(rel, "SKILL.md needs YAML frontmatter with 'name' and 'description'", rule="skill-metadata")
        return
    if fm.get("name") != name:
        rep.error(rel, f"frontmatter name '{fm.get('name')}' does not match the directory '{name}'",
                  rule="skill-metadata")
    desc = (fm.get("description") or "").strip()
    if not desc:
        rep.error(rel, "SKILL.md frontmatter needs a 'description'", rule="skill-metadata")
    elif len(desc) < 40:
        rep.warning(rel, "description should name both what the skill does and when it applies "
                         f"(got {len(desc)} characters)", rule="skill-metadata")


def check_manifest(path: str, rep: Report) -> dict:
    """rules 5 and 8 — the manifest composes, and imports are pinned to an approved bundle."""
    import yaml
    rel = os.path.relpath(path)
    try:
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except Exception as e:
        rep.error(rel, f"manifest.yaml is not valid YAML ({type(e).__name__})", rule="manifest")
        return {}
    if not isinstance(data, dict):
        rep.error(rel, "manifest.yaml must be a mapping", rule="manifest")
        return {}
    for key in ("version", "imports"):
        if key not in data:
            rep.warning(rel, f"manifest.yaml has no '{key}' key", rule="manifest")
    imports = data.get("imports") or []
    if isinstance(imports, dict):
        imports = [{"name": k, **(v if isinstance(v, dict) else {"version": v})} for k, v in imports.items()]
    for imp in imports if isinstance(imports, list) else []:
        if not isinstance(imp, dict):
            rep.error(rel, f"import entry is not a mapping: {imp!r}", rule="pinned-import")
            continue
        nm = imp.get("bundle") or imp.get("name") or "?"
        ver = str(imp.get("version", "")).strip()
        if not ver:
            rep.error(rel, f"import '{nm}' has no version — rule 8 requires a version-pinned bundle",
                      rule="pinned-import")
        elif FLOATING.match(ver):
            rep.error(rel, f"import '{nm}' is pinned to a moving target ('{ver}')", rule="pinned-import")
        src = str(imp.get("url", "") or imp.get("source", ""))
        if src.startswith(("http://", "https://")) and not (imp.get("checksum") or imp.get("sha256")):
            rep.error(rel, f"import '{nm}' fetches from {src} without a checksum", rule="pinned-import")
    return data


def check_pinned_bundle(rep: Report, manifest: dict, root: str):
    """rule 8, the half that needs the bytes — a pin nobody verifies is a comment.

    The digest lives in agents_bundle.py so there is one definition of it; this is the CI entry
    point that makes `[L1: pinned-import and checksum check]` name a check that exists.
    """
    if not _compose.pinned_import(manifest):
        return
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents_bundle.py")
    if not os.path.exists(script):
        rep.warning(os.path.join(".agents", "manifest.yaml"),
                    "a bundle is pinned but agents_bundle.py is not beside this checker, so the "
                    "digest was not verified", rule="pinned-import")
        return
    rep.checked += 1
    proc = subprocess.run([sys.executable, script, "verify", "--root", root],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return
    # agents_bundle.py already emitted its own annotations, with the two digests in them. Record
    # exactly one finding here so emit() exits 1, rather than paraphrasing them at a second
    # severity and making one problem look like three.
    rep.error(os.path.join(".agents", "manifest.yaml"),
              "the pinned bundle import does not verify — see the agents_bundle annotation above "
              "for which half is wrong (rule 8)", rule="pinned-import")


def check_machine_paths(path: str, rep: Report):
    """agents-md-schema rule 4 — an agent file is portable to whoever checks the repository out.

    Warning, not error: nothing here is wrong on the machine that wrote it, and every finding
    needs a human to decide what the reference should say instead.
    """
    try:
        lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return
    hits = [(i, m.group(0)) for i, l in enumerate(lines, 1)
            for m in [MACHINE_PATH.search(l)] if m]
    if not hits:
        return
    first_line, first = hits[0]
    rep.warning(os.path.relpath(path),
                f"{len(hits)} line(s) hard-code a path under someone's home directory "
                f"(first: '{first}…'). An agent reading this repository on another machine has no "
                f"such directory and the instruction fails silently — name the repository, and make "
                f"the sibling path a stated convenience",
                line=first_line, rule="machine-path")


def _body_of(path: str) -> str:
    text = open(path, encoding="utf-8", errors="replace").read()
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    return text[end + 4:] if end >= 0 else text


def check_composition(rel: str, fm: dict, rep: Report, fail, ctx: dict) -> None:
    """rule 5 — a profile composes by reference, so every reference must resolve.

    Nothing checked these, so an empty composition and a typo were the same result. Rule 5 names
    four kinds: skills, policies, references and a default workflow. Three have a field in the
    `AGENT.md` table; the fourth does not, so `workflow:` is validated when a profile carries one
    and its absence from the table is a `[DOC DEBT]` note rather than a silent omission here.
    """
    root = ctx.get("vendor_root")

    def items(key):
        values, scalar = _compose.as_list(fm.get(key))
        if scalar:
            fail(rel, f"'{key}' is a single value where a list belongs — YAML iterates a scalar "
                      f"character by character, so this is one mistake and not {len(str(values[0]))}",
                 rule="composition")
            return []
        return values

    # A bundle pinned but not vendored is ONE cause. Reporting it per reference points the author
    # at N individually-missing files instead of the one thing to fix.
    skip_bundle = False
    if root and not os.path.isdir(root):
        uses_bundle = any(str(v).startswith(_compose.BUNDLE_PREFIX)
                          for k in ("policies", "references") for v in items(k))
        if uses_bundle:
            fail(rel, f"references bundle content but {root} is not vendored — run "
                      f"`agents_bundle.py vendor` (rule 8)", rule="composition")
            # Reported once. Continuing to resolve each `bundle:` reference would point the author
            # at N individually-missing files instead of the single thing to fix.
            skip_bundle = True

    for kind, singular in (("policies", "policy"), ("references", "reference")):
        for name in items(kind):
            if skip_bundle and str(name).startswith(_compose.BUNDLE_PREFIX):
                continue
            path, err = _compose.resolve(kind, name, root)
            if err:
                fail(rel, f"{singular} '{name}' {err}", rule="composition")
            elif path and not os.path.exists(path):
                fail(rel, f"{singular} '{name}' does not exist at {path} (rule 5)",
                     rule="composition")

    for name in items("skills"):
        if str(name).startswith(_compose.BUNDLE_PREFIX):
            fail(rel, f"skill '{name}' uses the `bundle:` prefix, which only policies and "
                      f"references support — a skill is loaded by name from .agents/skills/",
                 rule="composition")
        elif name not in ctx.get("skill_names", set()):
            fail(rel, f"skill '{name}' is not a skill in this repository (rule 5)",
                 rule="composition")

    known = ctx.get("profile_names", set()) | {"human"}
    for h in items("handoffs"):
        if not isinstance(h, dict):
            fail(rel, f"handoff entry is not a mapping: {h!r}", rule="composition")
            continue
        target = h.get("agent")
        if not target:
            fail(rel, "handoff has no 'agent'", rule="composition")
        elif target not in known:
            fail(rel, f"handoff names '{target}', which is not a role in this repository (rule 5)",
                 rule="composition")

    wf = fm.get("workflow")
    if wf and not os.path.exists(os.path.join(".agents", "workflows", f"{wf}.md")):
        fail(rel, f"default workflow '{wf}' does not exist at .agents/workflows/{wf}.md (rule 5)",
             rule="composition")

    evals = fm.get("evals")
    if evals:
        # Accept the `.agents/`-prefixed spelling, as the `output` check below does. Rejecting one
        # of two spellings the rest of the tool accepts is a warning about a directory that exists.
        raw = str(evals).rstrip("/")
        candidates = [os.path.join(os.path.dirname(rel), raw), raw,
                      raw if raw.startswith(".agents") else os.path.join(".agents", raw)]
        if not any(os.path.isdir(c) for c in candidates):
            rep.warning(rel, f"evals directory '{evals}' does not exist beside the profile or "
                             f"under .agents/", rule="composition")


def check_profile(d: str, rep: Report, strict: bool = True, ctx: dict | None = None):
    """rules 10 and 11 — the AGENT.md layout, and frontmatter that is safe to be read raw.

    `strict` follows the repository's own manifest version. A repository still on v1 gets these as
    warnings: the checker ships to every repository at once and the migrations land one at a time,
    so binding v2 before a repository has migrated turns its CI red for work it has not been asked
    to do yet. Declaring `version: 2` is what opts a repository in — the same shape as
    frontmatter_check.py's ramp/strict, and the same reason.
    """
    fail = rep.error if strict else rep.warning
    ctx = ctx or {}
    name = os.path.basename(d)
    path = os.path.join(d, "AGENT.md")
    rel = os.path.relpath(path)
    if not os.path.exists(path):
        fail(os.path.relpath(d), f"role profile '{name}' has no AGENT.md — a profile lives at "
                                      f".agents/agents/<name>/AGENT.md (rule 10)", rule="profile-path")
        return
    if not SKILL_NAME.match(name) or len(name) > NAME_MAX:
        fail(rel, f"profile directory '{name}' must be lowercase kebab-case, at most "
                       f"{NAME_MAX} characters (rule 10)", rule="profile-path")
    fm, _ = read_frontmatter(path)
    if fm is None or fm.get("__invalid__"):
        fail(rel, "AGENT.md needs YAML frontmatter (rule 11)", rule="profile-metadata")
        return
    if fm.get("name") != name:
        fail(rel, f"frontmatter name '{fm.get('name')}' does not match the directory '{name}'",
                  rule="profile-metadata")
    if not (fm.get("description") or "").strip():
        fail(rel, "AGENT.md frontmatter needs a 'description' — it is the routing text every "
                       "runtime reads", rule="profile-metadata")

    if "tools" in fm:
        fail(rel, "canonical frontmatter must not carry a vendor 'tools' list (rule 11): "
                       "declare `capabilities`, and put a vendor list under `adapters.<vendor>.tools`",
                  rule="vendor-neutral")
    caps = fm.get("capabilities")
    if not caps:
        fail(rel, "'capabilities' is required (rule 11)", rule="vendor-neutral")
    else:
        for c in caps if isinstance(caps, list) else [caps]:
            if c in CAPABILITIES or MCP_CAPABILITY.match(str(c)):
                continue
            hint = (" — that is a vendor tool name, not a capability" if VENDOR_TOOL_HINT.match(str(c))
                    else f" — expected one of {', '.join(sorted(CAPABILITIES))} or mcp:<server>")
            fail(rel, f"unknown capability '{c}'{hint}", rule="vendor-neutral")
    for key, allowed in (("role", ROLES), ("mode", MODES)):
        if key not in fm:
            fail(rel, f"'{key}' is required (rule 11)", rule="profile-metadata")
        elif fm[key] not in allowed:
            fail(rel, f"{key} '{fm[key]}' is not one of {', '.join(sorted(allowed))}",
                      rule="profile-metadata")
    if fm.get("model", "inherit") not in MODEL_TIERS:
        fail(rel, f"model '{fm.get('model')}' is a model id, not a tier — use one of "
                       f"{', '.join(sorted(MODEL_TIERS))} and map it in the vendor adapter (rule 11)",
                  rule="vendor-neutral")
    # A read-only role holding edit capability is the contradiction that makes `mode` worth having.
    if fm.get("mode") == "read-only" and isinstance(caps, list) and "edit" in caps:
        fail(rel, "mode is read-only but capabilities include 'edit'", rule="vendor-neutral")

    body = _body_of(path)
    if len(body) > PROFILE_BODY_MAX:
        fail(rel, f"profile body is {len(body)} characters (cap {PROFILE_BODY_MAX}) — the "
                       f"tightest vendor agent-body limit", rule="size")
    check_composition(rel, fm, rep, fail, ctx)

    out = fm.get("output")
    if out:
        # The renderer accepts both spellings, so the checker must too — otherwise the
        # renderer-supported form is a CI error nobody can act on.
        target = out if out.startswith(".agents/") else os.path.join(".agents", out)
        if not os.path.exists(target):
            fail(rel, f"output schema '{target}' does not exist (rule 13)", rule="schema")
    return fm


def check_no_lowercase_agent_md(rep: Report, strict: bool = True):
    """rule 10 — one runtime discovers .agents/agents/<name>/agent.md natively, which is the same
    path as the canonical file on a case-insensitive filesystem. A single file must not be both the
    source and an adapter."""
    fail = rep.error if strict else rep.warning
    root = os.path.join(".agents", "agents")
    if not os.path.isdir(root):
        return
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f == "agent.md":
                fail(os.path.relpath(os.path.join(dirpath, f)),
                          "a lowercase 'agent.md' under .agents/agents/ collides with the canonical "
                          "AGENT.md on a case-insensitive filesystem (rule 10) — rename it, or let "
                          "the renderer emit the vendor adapter elsewhere", rule="profile-path")
        if os.path.normpath(dirpath) == os.path.normpath(root):
            for f in files:
                if f.endswith(".md"):
                    fail(os.path.relpath(os.path.join(dirpath, f)),
                              "a role profile lives at .agents/agents/<name>/AGENT.md, not as a flat "
                              "file (rule 10)", rule="profile-path")


def check_workflows(rep: Report, agents: set[str], skills: set[str]):
    """rule 5 plus the workflow header — declared steps and gates must name things that exist."""
    d = os.path.join(".agents", "workflows")
    if not os.path.isdir(d):
        return
    for f in sorted(os.listdir(d)):
        if not f.endswith(".md"):
            continue
        path, rel = os.path.join(d, f), os.path.relpath(os.path.join(d, f))
        rep.checked += 1
        fm, _ = read_frontmatter(path)
        if fm is None or fm.get("__invalid__"):
            rep.error(rel, "workflow needs YAML frontmatter with a name and a description",
                      rule="workflow")
            continue
        stem = f[:-3]
        if fm.get("name") and fm["name"] != stem:
            rep.error(rel, f"frontmatter name '{fm['name']}' does not match the filename '{stem}'",
                      rule="workflow")
        if not (fm.get("description") or "").strip():
            rep.error(rel, "workflow frontmatter needs a 'description'", rule="workflow")
        body = _body_of(path)
        if len(body) > WORKFLOW_BODY_MAX:
            rep.error(rel, f"workflow body is {len(body)} characters (cap {WORKFLOW_BODY_MAX})",
                      rule="size")
        for step in fm.get("steps") or []:
            if not isinstance(step, dict):
                rep.error(rel, f"step is not a mapping: {step!r}", rule="workflow")
                continue
            if step.get("agent") and step["agent"] not in agents:
                rep.error(rel, f"step names agent '{step['agent']}', which does not exist",
                          rule="workflow")
            if step.get("skill") and step["skill"] not in skills:
                rep.error(rel, f"step names skill '{step['skill']}', which does not exist",
                          rule="workflow")


def subschemas(node):
    """Every node in a schema. The one tree walker."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from subschemas(v)
    elif isinstance(node, list):
        for v in node:
            yield from subschemas(v)


def ref_target(ref, from_dir: str, roots):
    """`(path, pointer, pinned)` for a `$ref` that lands in the vendored tree, else None.

    A composition is recognised by where its reference resolves, not by a filename: `.base.` in a
    target's name is a convention the bundle happens to follow, and a repository that renamed one
    would silently stop being checked. `_compose` owns the one definition of the vendored root, so
    the checker cannot disagree with the renderer about which tree the bundle's shapes live in.

    `pinned` is false when the reference lands in a vendored directory that no import pins — every
    import, not the first one, or a repository pinning two bundles is told the second is a stray.
    No pinned tree at all means nothing is pinned: a manifest whose import is missing its version
    yields no roots, and reading that as "everything counts as pinned" turned the check off in the
    state most likely to need it.
    """
    if not isinstance(ref, str) or ref.startswith("#") or ref.startswith(("http://", "https://")):
        return None
    path, _, pointer = ref.partition("#")
    target = os.path.normpath(os.path.join(from_dir, path))
    if _compose.contained(_compose.VENDOR, target) is None:
        return None
    pinned = any(_compose.contained(r, target) for r in roots)
    return target, pointer, pinned


def node_at(doc, pointer: str):
    """The node a JSON pointer names, or None."""
    node = doc
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


_PARSED: dict = {}


def load_schema(path: str):
    """Parsed once per file, and never from outside the tree being checked.

    The containment sits here, against the `open()`, and not only in `ref_target()` where a `$ref`
    becomes a path. Every caller today arrives through that resolver, which refuses anything not
    landing in `.agents/vendor/` — but that is an invariant held by calling convention, and the
    next caller inherits none of it: it reaches this `open()` with whatever path it has and no test
    fails. What protects a sink belongs where the sink is.

    Two checks on one path is the intended shape, and they answer different questions. The resolver
    decides whether a reference is a composition over the bundle, and REPORTS; this decides whether
    a file may be read at all, and REFUSES. Deleting either as redundant is how this class comes
    back.

    The boundary is the checkout rather than the vendored subtree, because this also parses the
    repository's own schemas — a `$ref` naming a pointer inside a `.agents/schemas/` neighbour is
    an ordinary thing to write, and measured, it is clean today. `None` is the refusal: every
    caller already reads `None` as a `$ref` that led nowhere, which is what a path leaving the tree
    is. The reference itself is reported by `check_schemas`, where references are judged and a
    finding can carry a reason.
    """
    inside = _compose.contained(os.getcwd(), path)
    if inside is None:
        return None
    if inside not in _PARSED:
        try:
            with open(inside, encoding="utf-8") as fh:
                _PARSED[inside] = json.load(fh)
        except Exception:
            _PARSED[inside] = None    # a missing or broken target is reported as a bad $ref
    return _PARSED[inside]


def vendored_tree(path: str) -> str:
    """The `.agents/vendor/<bundle>-<version>` a path sits in."""
    rel = os.path.relpath(path, _compose.VENDOR)
    return os.path.join(_compose.VENDOR, rel.split(os.sep)[0])


# The property injected to ask whether a location refuses one. Long and ugly on purpose: it has to
# be a name no schema declares and no `patternProperties` expects.
PROBE_PROPERTY = "exeris-closer-probe-property"

# What an error at a location says about the probe VALUE rather than about the object: the probe
# put `{}` where a string or an enum belongs, so that location is not an object and nothing is
# owed there. `required` and the rest stay, because they are what an unfinished object looks like
# and they appear identically on both sides of the comparison.
WRONG_KIND = {"type", "enum", "const"}


def applies_at_the_root(schema):
    """Every schema node that applies to an instance's root.

    This node, its `allOf` branches, and whatever a local `$ref` at either of those names — one
    indirection or twenty, resolved rather than guessed. A repository may keep its composition
    under `$defs` and name it from the root, which is ordinary JSON Schema and which the previous
    mechanism answered by calling anything under `$defs` dead: a composition written that way was
    not checked at all.
    """
    out, seen, stack = [], set(), [schema]
    while stack:
        node = stack.pop(0)        # document order: which base wins a name must not depend on it
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            stack.append(node_at(schema, ref[1:]))
        stack += [b for b in node.get("allOf") or [] if isinstance(b, dict)]
    return out


def probe_for(node, depth: int = 5):
    """A structural instance of one base object, and the locations it puts an object at.

    `{}` for a property that holds an object, `[{}]` for an array of them, and the same again for
    whatever those declare — an object two levels down is as open as one at the top, and probing
    only the first level accepted a composition that left it unclosed. Nothing here tries to
    satisfy `required`, `minLength`, `pattern`, `minItems` or an enum: a conforming verdict is not
    needed and chasing one is its own trap. Only the DIFFERENCE between two validations is read,
    and both sides carry the same unmet requirements.

    Every declared property is a candidate, including the ones that plainly hold a string — being
    wrong here costs a location that gets skipped by `WRONG_KIND` a moment later, while being
    clever about which properties are "really" objects is how the previous mechanism lost them.

    Two shapes are still out of reach, and no base uses either: an object reachable only through
    `patternProperties` (the probe would have to invent a name matching the pattern) or through an
    object-valued `additionalProperties`. `depth` bounds literal nesting; a `$ref` is not followed
    here, the validator resolves those.
    """
    instance, locations = {}, []
    for name, sub in (node.get("properties") or {}).items():
        if not isinstance(sub, dict):
            continue
        array = sub.get("items") if "items" in sub or "prefixItems" in sub else None
        below = array if isinstance(array, dict) else sub
        nested, deeper = probe_for(below, depth - 1) if depth > 0 else ({}, [])
        if array is not None:
            instance[name], here = [nested], (name, 0)
        else:
            instance[name], here = nested, (name,)
        locations.append(here)
        locations += [here + l for l in deeper]
    return instance, locations


def probe_validator(schema, schema_path: str):
    """A validator for one composed schema, resolving `$ref` from the filesystem.

    The same two moves the eval grader makes in `bundle/evals/run.py`: the document is identified
    by the file it was read from, and a reference is retrieved from disk. Not imported from there —
    that module resolves at import time which checkout it may read, and it would resolve the
    bundle's own rather than the tree being checked — so this is the checker's own, guarded by
    `_compose.contained` against the tree it has chdir'd into.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    base_dir = os.path.dirname(os.path.abspath(schema_path))

    def retrieve(uri: str):
        # Through `load_schema`, which contains the path against the checkout and parses each file
        # once. Reading here directly re-opened and re-parsed a base for every probed location —
        # measured at twelve reads of one base for one composed schema.
        target = (unquote(urlparse(uri).path) if uri.startswith("file:")
                  else os.path.normpath(os.path.join(base_dir, uri)))
        document = load_schema(target)
        if document is None:
            raise FileNotFoundError(f"$ref '{uri}' is not readable JSON inside this repository")
        return Resource.from_contents(document, default_specification=DRAFT202012)

    rooted = {**schema, "$id": Path(os.path.abspath(schema_path)).as_uri()}
    return Draft202012Validator(rooted, registry=Registry(retrieve=retrieve))


def failures(validator, instance) -> set:
    """One validation, as the set of (instance location, keyword) pairs that failed.

    The oracle's own answer, read structurally: `e.validator` is the keyword that refused and
    `e.absolute_path` is where.
    """
    return {(tuple(e.absolute_path), e.validator) for e in validator.iter_errors(instance)}


def names_the_probe(validator, instance) -> bool:
    """Whether anything in the schema refuses the property that was just added.

    Not "did the error set change", which is the comparison this started as and which measurement
    ruled out. A probe deliberately does not satisfy `required`, `type` or an enum, so the base's
    own branch fails — and a failing subschema contributes no annotations, so an
    `unevaluatedProperties` above it reports every property present, in both runs, at the same
    location under the same keyword. The two error sets are then identical whether or not the
    location closes, and reading them would call every closed object open.

    What separates the two runs is which properties the refusal names. The probe property is a
    token nothing else can produce, so its appearance — in the message a refusal renders, as the
    instance a `propertyNames` refusal rejects, or in the path an error is anchored at — is the
    schema saying it will not take this property. Which keyword did the refusing is not asked:
    `unevaluatedProperties`, `additionalProperties` and `propertyNames` all answer the question a
    consumer has, which is what an instance may carry.
    """
    for e in validator.iter_errors(instance):
        if e.instance == PROBE_PROPERTY or PROBE_PROPERTY in str(e.message):
            return True
        if any(str(step) == PROBE_PROPERTY for step in e.absolute_path):
            return True
    return False


def carrying(instance, location):
    """The probe with one foreign property added at one location."""
    import copy
    out = copy.deepcopy(instance)
    node = out
    for step in location:
        node = node[step]
    node[PROBE_PROPERTY] = "probe"
    return out


def where(location) -> str:
    return "<root>" if not location else "/".join(str(step) for step in location)


def check_closers(rep: Report, rel: str, schema, schema_path: str, roots):
    """A composition refuses a property the base does not name — measured, not read.

    The bases close nothing: a base that closes itself cannot be extended, and
    `unevaluatedProperties: false` in the base refuses an added field just as flatly, since it sees
    only the annotations of its own schema object and its in-place applicators, never a sibling
    `allOf` branch in the composing schema. So the refusal lives in the composition or nowhere, and
    "nowhere" is what a repository gets by bumping the bundle and changing nothing: a contract that
    accepts any property, with no check red.

    The question is behavioural, so it is asked behaviourally. A structural probe is built from the
    base and validated against the composed schema; then one foreign property is added at one
    object location and it is validated again. If nothing in the second run names that property,
    nothing refuses it and the location is open. This replaces a walker that re-derived what the validator already knows —
    which objects exist, which subschemas apply, what a pointer means across a file — and got a
    different corner of it wrong in each of three review rounds. The keyword that does the refusing
    is not this check's business: `unevaluatedProperties`, `additionalProperties` or anything else
    a repository reaches for counts, because the contract is what an instance may carry.

    What is reported is the INSTANCE location — `<root>`, `findings/0` — which is where a fixer has
    to act, and which the old mechanism computed with pointer arithmetic in order to report a
    schema location instead.
    """
    if not isinstance(schema, dict):
        return
    refs = [node["$ref"] for node in applies_at_the_root(schema)
            if isinstance(node.get("$ref"), str)]
    probe, locations, owner = {}, [], {}
    composes = False
    for ref in refs:
        target = ref_target(ref, os.path.dirname(schema_path), roots)
        if not target:
            continue
        composes = True
        doc = load_schema(target[0])
        node = doc if not target[1] else node_at(doc, target[1])
        if not isinstance(node, dict):
            continue
        instance, found = probe_for(node)
        # Two bases at the root may declare the same property with different shapes — an object in
        # one, an array in the other. The first to declare it fixes the probe's shape there, and
        # only its locations are kept: mixing them left a location naming an index into a value
        # that is no longer a list, and the walk into it raised out of the whole run.
        for name, value in instance.items():
            owner.setdefault(name, ref)
            if owner[name] == ref:
                probe.setdefault(name, value)
        locations += [l for l in found
                      if l and owner.get(l[0]) == ref and l not in locations]
    if not composes:
        # Nothing of the bundle's applies where an instance meets this schema, so the probe has no
        # root to stand on and the question cannot be asked. Asked-and-answered and never-asked
        # used to look identical from outside — a clean run either way — which is the shape this
        # check spent three rounds removing from everywhere else.
        here = os.path.dirname(schema_path)
        elsewhere = any(ref_target(node["$ref"], here, roots)
                        for node in subschemas(schema) if isinstance(node.get("$ref"), str))
        # Or through a file the root names: `applies_at_the_root` follows a local `#/` pointer and
        # stops at a file boundary, so a root that is `{"$ref": "inner.schema.json"}` composed the
        # base one file over and was neither probed nor reported.
        for node in applies_at_the_root(schema) if not elsewhere else []:
            ref = node.get("$ref")
            if not isinstance(ref, str) or ref.startswith("#") or "://" in ref:
                continue
            through = os.path.normpath(os.path.join(here, ref.partition("#")[0]))
            document = load_schema(through)
            if isinstance(document, dict) and any(
                    ref_target(n["$ref"], os.path.dirname(through), roots)
                    for n in subschemas(document) if isinstance(n.get("$ref"), str)):
                elsewhere = True
                break
        if elsewhere:
            rep.error(rel, "references a bundle base, but not where an instance meets this schema "
                           "— the reference sits under a shape this repository owns, or in a file "
                           "this root names, so whether a property the base does not name is "
                           "refused cannot be measured here and has not been. Compose the base at "
                           "the root, which is the shape README.md documents and every base's "
                           "`description` assumes, or move that reference into a schema of its "
                           "own", rule="schema")
        return

    try:
        validator = probe_validator(schema, schema_path)
        before = failures(validator, probe)

        # A closer that refuses everything is not a closer. `additionalProperties: false` in a
        # sibling `allOf` branch — the obvious migration move — sees only the properties named in
        # its own schema object, so it rejects the ones the base declares, and it rejects the probe
        # property too, which is why asking only "can something get in" reads it as closed. Its
        # verdict does not depend on annotations from a branch that failed, unlike
        # `unevaluatedProperties`, so what it says about this probe is what it will say about a
        # conforming decision: the probe carries exactly what the base declares and nothing else.
        for at, keyword in sorted(before, key=lambda e: (len(e[0]), str(e[0]))):
            if keyword == "additionalProperties":
                rep.error(rel, f"refuses properties the base itself declares, at {where(at)} — an "
                               f"`additionalProperties: false` here sees only the properties named "
                               f"beside it, so a conforming decision is rejected for carrying what "
                               f"the base defines. The closer that composes rather than replaces "
                               f"is `unevaluatedProperties: false`, in the object that pulls the "
                               f"base in", rule="schema")

        for location in [()] + locations:
            if any(at == location and keyword in WRONG_KIND for at, keyword in before):
                continue
            if names_the_probe(validator, carrying(probe, location)):
                continue
            rep.error(rel, f"an instance may carry any property at {where(location)} — one was "
                           f"added there and nothing in this schema refused it, so the object "
                           f"takes whatever the base does not name. Close it: the base closes "
                           f"nothing, and a closer at the root does not reach into an array's "
                           f"items. Rule 13 makes this file what a decision conforms to; that the "
                           f"closers are yours is this bundle's contract, in the base's own "
                           f"`description`", rule="schema")
    except Exception as exc:
        rep.error(rel, f"composes over a bundle base but could not be decided, so whether it "
                       f"closes what the base leaves open is unknown ({type(exc).__name__}: "
                       f"{exc})", rule="schema")


def check_schemas(rep: Report, roots=()):
    """The schema files a decision conforms to are real, valid JSON Schema — rule 13 — and each
    reference in them resolves. That a composition over a vendored base must refuse what the base
    does not name is this bundle's own contract rather than a clause of the standard, and it is
    written in each base's `description`; rule 13 is what makes these files the contract at all."""
    d = os.path.join(".agents", "schemas")
    if not os.path.isdir(d):
        return
    # One degraded path, the one that was already here. The closer rule needs the same two packages
    # the validity check needs — `referencing` is how a vendored `$ref` resolves offline — so when
    # either is missing this reports what it did check and stops, rather than growing a second mode
    # that half-answers.
    missing = None
    try:
        from jsonschema import Draft202012Validator as Validator
        import referencing                                              # noqa: F401
    except ImportError as exc:
        Validator, missing = None, getattr(exc, "name", None) or "jsonschema"
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        rel = os.path.relpath(os.path.join(d, f))
        rep.checked += 1
        try:
            schema = json.load(open(os.path.join(d, f), encoding="utf-8"))
        except Exception as e:
            rep.error(rel, f"not valid JSON ({type(e).__name__}: {e})", rule="schema")
            continue
        if not f.endswith(".schema.json"):
            rep.error(rel, "a schema file is named <name>.schema.json", rule="schema")
        # A relative $ref is how a repository narrows a vendored base without copying it. It is
        # also the thing that silently stops resolving when a bundle version is bumped and the
        # composing schema is not, so the target is checked as a file rather than assumed.
        # Three questions about one reference, asked where a reference is judged: does the target
        # exist, does its pointer resolve, and does it land in the tree the manifest pins. An
        # off-pin tree is reported once however many references reach it — it is one fact with one
        # fix, retargeting this schema, and a composition closing four objects against a stale tree
        # would otherwise answer for it four times.
        off_pin = []
        for node in subschemas(schema):
            ref = node.get("$ref") if isinstance(node, dict) else None
            if not isinstance(ref, str):
                continue
            # A URL is the one that has to be refused: resolving one needs a fetch, and rule 8
            # forbids fetching. A fragment-only ref points inside this same document.
            if ref.startswith(("http://", "https://")):
                rep.error(rel, f"$ref '{ref}' is a URL — a vendored bundle resolves from the "
                               f"filesystem, and fetching one at validation time is what rule 8 "
                               f"forbids. Use a relative path into .agents/vendor/.", rule="schema")
                continue
            file_part, _, fragment = ref.partition("#")
            if file_part:
                target = os.path.normpath(os.path.join(d, file_part))
                if _compose.contained(os.getcwd(), target) is None:
                    rep.error(rel, f"$ref '{ref}' resolves outside the repository — a reference "
                                   f"resolves from the filesystem, inside the checkout, and one "
                                   f"that leaves it names a file this repository cannot vouch for "
                                   f"and a reader cannot see (rule 8)", rule="schema")
                    continue
                if not os.path.exists(target):
                    rep.error(rel, f"$ref target does not exist: {ref}", rule="schema")
                    continue
                document = load_schema(target)
                if document is None:
                    rep.error(rel, f"$ref target is not readable JSON: {ref} — the file is there, "
                                   f"so this is the file to look at, not the pointer",
                              rule="schema")
                    continue
            else:
                document = schema
            # And the pointer, which nothing checked. The file existing was taken for the whole
            # answer, while this release makes `<file>#/properties/<name>/items` the documented way
            # to close a nested object — a typo there is a closer over nothing, on a `$ref` whose
            # file is right there.
            if fragment.startswith("/") and node_at(document, fragment) is None:
                rep.error(rel, f"$ref '{ref}' names a pointer that does not resolve in "
                               f"{file_part or 'this document'}", rule="schema")
            landed = ref_target(ref, d, roots)
            if landed and not landed[2]:
                off_pin.append(vendored_tree(landed[0]))
        for tree in dict.fromkeys(off_pin):
            rep.error(rel, f"composes over {tree}, which no import in the manifest pins — a "
                           f"pin's digest vouches for the tree it names and for nothing else, and "
                           f"a reference into another vendored tree still resolves, so no other "
                           f"check reports it. This is the state a half-finished bump is in "
                           f"(rule 8)", rule="schema")
        if Validator is None:
            rep.warning(rel, f"{missing} is not installed: JSON syntax, every `$ref` target and "
                             f"pointer, and the pinning of each vendored tree were checked; "
                             f"whether this is valid JSON Schema and whether it closes what the "
                             f"base leaves open were not", rule="schema")
            continue
        try:
            Validator.check_schema(schema)
        except Exception as e:
            rep.error(rel, f"not a valid JSON Schema: {e}", rule="schema")
            continue
        check_closers(rep, rel, schema, os.path.join(d, f), roots)


def check_hooks(rep: Report, manifest: dict):
    """rule 12 — hooks are authored once, their dispatcher exists, and what a vendor cannot do is
    written down rather than assumed."""
    import yaml
    spec_path = os.path.join(".agents", "hooks", "hooks.yaml")
    if not os.path.exists(spec_path):
        return
    rel = os.path.relpath(spec_path)
    rep.checked += 1
    try:
        spec = yaml.safe_load(open(spec_path, encoding="utf-8")) or {}
    except Exception as e:
        rep.error(rel, f"hooks.yaml is not valid YAML ({type(e).__name__})", rule="hooks")
        return
    events = {"pre-tool", "post-tool", "stop", "session-start"}
    seen = set()
    for h in spec.get("hooks") or []:
        hid = h.get("id")
        if not hid:
            rep.error(rel, f"hook without an id: {h!r}", rule="hooks")
            continue
        if hid in seen:
            rep.error(rel, f"duplicate hook id '{hid}'", rule="hooks")
        seen.add(hid)
        if h.get("event") not in events:
            rep.error(rel, f"hook '{hid}' has event '{h.get('event')}' — expected one of "
                           f"{', '.join(sorted(events))}", rule="hooks")
        for pat in h.get("match") or []:
            try:
                re.compile(pat)
            except re.error as e:
                rep.error(rel, f"hook '{hid}' has an invalid pattern {pat!r}: {e}", rule="hooks")
        if h.get("decision") == "deny" and not (h.get("reason") or "").strip():
            rep.error(rel, f"hook '{hid}' denies without a reason — a denial names the policy "
                           f"clause it enforces (rule 12)", rule="hooks")
    # The dispatcher normally arrives with the pinned bundle; a repository that imports none keeps
    # its own copy. Either is fine, neither is optional.
    candidates = [os.path.join(".agents", "hooks", "bin", "hook.py")]
    imp = _compose.pinned_import(manifest)
    if imp:
        candidates.insert(0, os.path.join(VENDOR, f"{imp['bundle']}-{imp.get('version')}",
                                          "hooks", "bin", "hook.py"))
    dispatcher = next((c for c in candidates if os.path.exists(c)), None)
    if dispatcher is None:
        rep.error(rel, f"no hook dispatcher at any of {', '.join(candidates)} — the rendered "
                       f"vendor configs invoke it and carry no patterns of their own", rule="hooks")
    elif not os.access(dispatcher, os.X_OK):
        rep.warning(dispatcher, "dispatcher is not executable", rule="hooks")
    state = (spec.get("state-dir") or ".agents-state").rstrip("/")
    ignored = ""
    if os.path.exists(".gitignore"):
        ignored = open(".gitignore", encoding="utf-8", errors="replace").read()
    if state not in ignored:
        rep.error(".gitignore", f"hook state directory '{state}/' is not git-ignored — session "
                                f"state is not repository content", rule="hooks")
    # A stop hook that cannot block everywhere must say where it degrades.
    if any(h.get("event") == "stop" for h in spec.get("hooks") or []):
        if not manifest.get("degradations"):
            rep.error(os.path.join(".agents", "manifest.yaml"),
                      "hooks include a stop gate but the manifest records no `degradations` — only "
                      "some runtimes can block a stop, and an unrecorded degradation is an operator "
                      "believing a gate runs where it does not (rule 12)", rule="hooks")


def check_manifest_agreement(rep: Report, manifest: dict):
    """rule 5 — the manifest and the filesystem agree in BOTH directions."""
    rel = os.path.join(".agents", "manifest.yaml")

    def on_disk_dirs(sub, marker_file):
        d = os.path.join(".agents", sub)
        if not os.path.isdir(d):
            return set()
        return {n for n in os.listdir(d)
                if os.path.exists(os.path.join(d, n, marker_file))}

    def on_disk_files(sub, suffix=".md"):
        d = os.path.join(".agents", sub)
        if not os.path.isdir(d):
            return set()
        return {n[:-len(suffix)] for n in os.listdir(d) if n.endswith(suffix)}

    def stems(values, sub):
        """v1 listed paths ('agents/x.md'); v2 lists names. Accept both, compare names."""
        out = set()
        for v in values or []:
            v = str(v)
            v = v[len(sub) + 1:] if v.startswith(sub + "/") else v
            # Longest first, and stop at the first match: ".md" tested first would reduce
            # "x/AGENT.md" to "x/AGENT" and then match nothing else, so the very v1 forms this
            # helper's docstring promises to accept came out mangled.
            for suffix in ("/SKILL.md", "/AGENT.md", ".md"):
                if v.endswith(suffix):
                    v = v[: -len(suffix)]
                    break
            out.add(v.rstrip("/"))
        return out

    for key, sub, disk in (
        ("agents", "agents", on_disk_dirs("agents", "AGENT.md")),
        ("skills", "skills", on_disk_dirs("skills", "SKILL.md")),
        ("workflows", "workflows", on_disk_files("workflows")),
        ("policies", "policies", on_disk_files("policies")),
        ("references", "references", on_disk_files("references")),
    ):
        declared = stems(manifest.get(key), sub)
        for missing in sorted(declared - disk):
            rep.error(rel, f"manifest lists {key[:-1]} '{missing}' but it does not exist on disk",
                      rule="manifest")
        for undeclared in sorted(disk - declared):
            rep.error(rel, f".agents/{sub}/{undeclared} exists but the manifest does not name it — "
                           f"an unlisted file is invisible to the renderer and to the reviewer",
                      rule="manifest")
    declared_schemas = {str(s).replace(".schema.json", "") for s in manifest.get("schemas") or []}
    disk_schemas = {n[: -len(".schema.json")] for n in
                    (os.listdir(os.path.join(".agents", "schemas"))
                     if os.path.isdir(os.path.join(".agents", "schemas")) else [])
                    if n.endswith(".schema.json")}
    for missing in sorted(declared_schemas - disk_schemas):
        rep.error(rel, f"manifest lists schema '{missing}' but it does not exist", rule="manifest")
    for undeclared in sorted(disk_schemas - declared_schemas):
        rep.error(rel, f".agents/schemas/{undeclared}.schema.json exists but is not in the manifest",
                  rule="manifest")


def check_adapters(rep: Report, strict: bool, provider_owned: set[str] | None = None):
    """rules 2 and 7 — provider directories adapt; they do not author."""
    level = rep.error if strict else rep.warning
    for pf in ADAPTER_FILES:
        if not os.path.exists(pf):
            continue
        lines = [l for l in open(pf, encoding="utf-8", errors="replace").read().splitlines() if l.strip()]
        if len(lines) > ADAPTER_MAX_LINES and not GENERATED.search("\n".join(lines[:10])):
            level(pf, f"{pf} has {len(lines)} non-empty lines — a provider entry file is a thin adapter "
                      f"(<= {ADAPTER_MAX_LINES} lines) pointing at AGENTS.md, or is generated and says so",
                  rule="adapter")
    owned = OPERATIONAL | {p.rstrip("/") for p in (provider_owned or set())}

    def is_owned(rel_path: str) -> bool:
        rel_path = rel_path.replace(os.sep, "/")
        return any(rel_path == o or rel_path.startswith(o.rstrip("/") + "/") for o in owned)

    for pd in PROVIDER_DIRS:
        for sub in SEMANTIC_SUBDIRS:
            d = os.path.join(pd, sub)
            if not os.path.isdir(d) or is_owned(d):
                continue
            authored = []
            # followlinks stays off: a per-skill symlink into .agents/skills/ points at the
            # canonical source, which carries no marker by design. Following it would report every
            # skill as provider-authored — the exact opposite of what the link achieves.
            for dirpath, _, files in os.walk(d):
                for f in files:
                    if not f.endswith((".md", ".mdc", ".yaml", ".yml")):
                        continue
                    p = os.path.join(dirpath, f)
                    if is_owned(os.path.relpath(p)):
                        continue
                    head = open(p, encoding="utf-8", errors="replace").read(600)
                    if not GENERATED.search(head):
                        authored.append(os.path.relpath(p))
            if authored:
                level(d, f"{len(authored)} file(s) under {d}/ carry no generated-from marker — "
                         f"semantic content belongs in .agents/ (first: {authored[0]})", rule="adapter")


def provider_owned_paths(manifest: dict, rep: Report | None = None) -> set[str]:
    """rule 7's `provider-owned` list, in both spellings the rule gives it.

    A plain string is a file or directory the renderer does not own at all. A mapping —
    `{path: …, generated-region: …}` — is a file the renderer writes PART of, which rule 7 requires
    to be declared here because a JSON settings file has no comment to carry a marker.

    Takes the parsed manifest rather than re-reading it. It used to open and parse `manifest.yaml`
    a third time inside its own `except: pass`, so an unparseable manifest produced an empty list
    here and exactly the false findings this entry was added to remove — silently, and while
    `check_manifest` had already reported the parse failure properly one caller up.

    `generated-region` is read rather than decorative: the named key must be present in the file,
    or the declaration exempts a region that is not there.
    """
    entries = manifest.get("provider-owned") or []
    out: set[str] = set()
    mpath = os.path.join(".agents", "manifest.yaml")
    if not isinstance(entries, list):
        if rep:
            rep.error(mpath, "provider-owned must be a list", rule="adapter")
        return out
    for entry in entries:
        if isinstance(entry, str):
            out.add(entry.rstrip("/"))
            continue
        if not isinstance(entry, dict) or not entry.get("path"):
            if rep:
                rep.error(mpath, f"provider-owned entry is neither a path nor a mapping with one: "
                                 f"{entry!r}", rule="adapter")
            continue
        path = str(entry["path"])
        out.add(path.rstrip("/"))
        region = entry.get("generated-region")
        if not (rep and region):
            continue
        if not os.path.exists(path):
            rep.warning(mpath, f"provider-owned '{path}' declares a generated region but the file "
                               f"does not exist", rule="adapter")
            continue
        try:
            body = json.load(open(path, encoding="utf-8")) if path.endswith(".json") \
                else open(path, encoding="utf-8").read()
        except Exception:
            continue
        if region not in body:
            rep.error(mpath, f"provider-owned '{path}' declares generated region '{region}', which "
                             f"the file does not contain — the declaration exempts a region that is "
                             f"not there (rule 7)", rule="adapter")
    return out


def check_composition_is_used(rep: Report, manifest: dict, profiles_dir: str):
    """rule 5, the direction nothing checked: a policy or reference that nothing composes.

    The forward direction — a profile naming a policy that does not resolve — became an error when
    composition started being checked at all. The reverse stayed invisible: a file on disk, listed
    in the manifest, and referenced by no profile and no prose. Both halves of "the manifest and
    the filesystem must agree in both directions" were about EXISTENCE; this is about USE.

    A warning, not an error, and deliberately. An unreferenced policy is not a broken contract — it
    may be about to be composed, or kept for a role not yet written. But a policy no role loads is
    a rule nobody reads, which is how a rule quietly stops applying while still looking enforced.

    A mention counts from anywhere a reader would follow it: a profile's `policies:` or
    `references:` list, or the text of any other agent file or `AGENTS.md`. Only the file's own
    text is excluded, so a policy that merely names itself is still an orphan.
    """
    composed: set[str] = set()
    for name in sorted(os.listdir(profiles_dir) if os.path.isdir(profiles_dir) else []):
        fm, _ = read_frontmatter(os.path.join(profiles_dir, name, "AGENT.md"))
        if not isinstance(fm, dict):
            continue
        for key in ("policies", "references"):
            values, _ = _compose.as_list(fm.get(key))
            for entry in values or []:
                composed.add(str(entry).split(":", 1)[-1])

    # The manifest is excluded on purpose: it is where the DECLARATION lives, so counting it as a
    # mention makes every declared file trivially "referenced" and the check answers yes to
    # everything. Measured — with it included, an orphan planted in a real tree was not reported.
    manifest_path = os.path.normpath(os.path.join(".agents", "manifest.yaml"))
    texts: list[tuple[str, str]] = []
    for dirpath, dirnames, files in os.walk(".agents"):
        if os.path.relpath(dirpath).startswith(VENDOR):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for f in files:
            if f.endswith((".md", ".yaml", ".yml", ".json")):
                fp = os.path.join(dirpath, f)
                texts.append((os.path.normpath(fp),
                              open(fp, encoding="utf-8", errors="replace").read()))
    for dirpath, dirnames, files in os.walk("."):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not nested_checkout(dirpath, d)]
        if "AGENTS.md" in files:
            fp = os.path.join(dirpath, "AGENTS.md")
            texts.append((os.path.normpath(fp),
                          open(fp, encoding="utf-8", errors="replace").read()))

    for kind, sub in (("policy", "policies"), ("reference", "references")):
        for raw in manifest.get(sub) or []:
            name = str(raw)
            name = name[len(sub) + 1:] if name.startswith(sub + "/") else name
            name = name[:-3] if name.endswith(".md") else name
            own = os.path.normpath(os.path.join(".agents", sub, name + ".md"))
            if name in composed:
                continue
            if any(name in body for path, body in texts
                   if path != own and path != manifest_path):
                continue
            rep.warning(own, f"{kind} '{name}' is declared and on disk, and NOTHING composes or "
                             f"mentions it — no profile lists it and no other agent file names it. "
                             f"A rule no role loads is a rule nobody reads (rule 5)",
                        rule="composition")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict-adapters", action="store_true",
                    help="fail on provider-authored semantics (schema rule 2 — error once the renderer is adopted)")
    a = ap.parse_args()
    # GitHub resolves ::error file=… against GITHUB_WORKSPACE, and this checker chdirs into the
    # tree under test. Checking a second checked-out repository would otherwise annotate
    # same-named files in the first one — the `against-consumer` job annotating the bundle's own
    # AGENTS.md instead of the consumer's.
    prefix = os.path.relpath(os.path.abspath(a.root), os.getcwd())
    os.chdir(a.root)
    rep = Report("agents_file_check", path_prefix="" if prefix == "." else prefix)
    manifest: dict = {}   # a repository with no .agents/ still runs every other check

    if not os.path.exists("AGENTS.md"):
        rep.error("AGENTS.md", "AGENTS.md is required at the repository root and is the canonical "
                               "agent entry point (agents-md-schema.md rule 1)", rule="present")
    else:
        rep.checked += 1
        text = check_agents_md("AGENTS.md", rep, ROOT_LIMIT, "root")
        if os.path.isdir(".agents") and ".agents" not in text:
            rep.warning("AGENTS.md", "AGENTS.md does not point at .agents/, which holds this repo's "
                                     "canonical semantics", rule="discovery")

    for dirpath, dirnames, files in os.walk("."):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not nested_checkout(dirpath, d)]
        if "AGENTS.md" in files and os.path.relpath(dirpath) != ".":
            rep.checked += 1
            check_agents_md(os.path.join(dirpath, "AGENTS.md"), rep, NESTED_LIMIT, "nested")

    if os.path.isdir(".agents"):
        skills = os.path.join(".agents", "skills")
        if os.path.isdir(skills):
            for name in sorted(os.listdir(skills)):
                d = os.path.join(skills, name)
                if os.path.isdir(d):
                    rep.checked += 1
                    check_skill(d, rep)
        for dirpath, _, files in os.walk(".agents"):
            if os.path.relpath(dirpath).startswith(VENDOR):
                continue
            for f in files:
                if f == "SKILL.md":
                    p = os.path.join(dirpath, f)
                    parent = os.path.dirname(os.path.dirname(p))
                    if os.path.normpath(parent) != os.path.normpath(skills):
                        rep.error(os.path.relpath(p),
                                  "a skill lives at .agents/skills/<name>/SKILL.md", rule="skill-path")
        manifest_path = os.path.join(".agents", "manifest.yaml")
        declared_v2 = False
        manifest_data: dict = {}
        if os.path.exists(manifest_path):
            import yaml
            try:
                manifest_data = yaml.safe_load(open(manifest_path, encoding="utf-8")) or {}
                declared_v2 = str(manifest_data.get("version")) == "2"
            except Exception:
                # check_manifest reports the parse failure properly further down; this early read
                # exists only to know which severity the v2 rules carry.
                pass

        profiles = os.path.join(".agents", "agents")
        # Two passes: a handoff may name a role defined later in the listing, and a composition
        # check that depended on ordering would be a check with a false negative built in.
        # A directory is a role only if it holds an AGENT.md, and a skill only with a SKILL.md —
        # the same definition check_manifest_agreement uses four hundred lines below. A bare
        # listing let an empty directory satisfy "is a role".
        def _named(base, marker):
            return {n for n in (os.listdir(base) if os.path.isdir(base) else [])
                    if os.path.exists(os.path.join(base, n, marker))}
        profile_names = _named(profiles, "AGENT.md")
        skill_names = _named(skills, "SKILL.md")
        ctx = {"vendor_root": _compose.vendor_root(manifest_data), "profile_names": profile_names,
               "skill_names": skill_names}
        for name in sorted(profile_names):
            rep.checked += 1
            check_profile(os.path.join(profiles, name), rep, declared_v2, ctx)
        check_no_lowercase_agent_md(rep, declared_v2)
        check_workflows(rep, profile_names, skill_names)
        check_schemas(rep, _compose.vendor_roots(manifest_data))

        manifest: dict = {}
        if os.path.exists(manifest_path):
            rep.checked += 1
            manifest = check_manifest(manifest_path, rep) or {}
        else:
            rep.error(manifest_path, ".agents/ has no manifest.yaml — it records composition and the "
                                     "version-pinned bundles the repo imports (rules 5, 8)",
                      rule="manifest")
        if manifest:
            check_pinned_bundle(rep, manifest, os.getcwd())
            check_hooks(rep, manifest)
            if declared_v2:
                check_manifest_agreement(rep, manifest)
                check_composition_is_used(rep, manifest, profiles)
            else:
                rep.warning(manifest_path,
                            "manifest is version 1 — the v2 rules (10-13) are reported as warnings "
                            "here. Migrating means moving profiles to agents/<name>/AGENT.md, "
                            "replacing `tools:` with `capabilities:`, and setting version: 2 "
                            "(agents-md-schema.md, Migration)", rule="schema-version")

    # Portability sweep over the files a human authors. Generated adapters are skipped on purpose:
    # check_adapters already ties them to their source, so reporting the same path twice would
    # double a worklist whose only actionable copy is the one under .agents/.
    authored = []
    if os.path.exists("AGENTS.md"):
        authored.append("AGENTS.md")
    for dirpath, dirnames, files in os.walk("."):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not nested_checkout(dirpath, d)]
        for f in files:
            fp = os.path.join(dirpath, f)
            rel = os.path.relpath(fp)
            if rel == "AGENTS.md":
                continue
            if rel.startswith(VENDOR + os.sep):
                continue
            in_agents = rel == ".agents" or rel.startswith(".agents" + os.sep)
            if not (in_agents or f == "AGENTS.md" or rel in ADAPTER_FILES):
                continue
            if not f.endswith((".md", ".mdc", ".yaml", ".yml")):
                continue
            if GENERATED.search(open(fp, encoding="utf-8", errors="replace").read(600)):
                continue
            authored.append(rel)
    for fp in sorted(set(authored)):
        check_machine_paths(fp, rep)

    provider_owned = provider_owned_paths(manifest, rep)
    check_adapters(rep, a.strict_adapters, provider_owned)
    sys.exit(rep.emit())


if __name__ == "__main__":
    main()
