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
import argparse, copy, io, json, math, os, re, sys
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
    """Every node in a schema, in document order. The one tree walker.

    Iterative, because a schema is an input: one nested past the interpreter's recursion limit
    raised out of here, in the middle of `check_schemas`, and the run ended with no report. The
    validator's own metaschema check still recurses and still raises on such a file — inside a
    guard, as a finding against that file.
    """
    stack = [node]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))


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
    state most likely to need it. `roots` of None is the third state — the manifest did not parse,
    so what it pins is unknown and every reference would otherwise be called a stray while the real
    finding, the YAML error, is already reported.
    """
    if not isinstance(ref, str) or ref.startswith("#") or ref.startswith(("http://", "https://")):
        return None
    path, _, pointer = ref.partition("#")
    target = os.path.normpath(os.path.join(from_dir, path))
    if _compose.contained(_compose.VENDOR, target) is None:
        return None
    pinned = roots is None or any(_compose.contained(r, target) for r in roots)
    return target, pointer, pinned


def node_at(doc, pointer: str):
    """The node a JSON pointer names, or None.

    A fragment that does not begin with `/` is a plain-name anchor, not a pointer. Splitting one
    yielded an empty path, the loop never ran, and the whole document came back as though the
    anchor had named it — so a probe was built from a base's root while the reference named
    something inside it.
    """
    if pointer and not pointer.startswith("/"):
        return None
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


class Unreadable:
    """Why a file could not be read, kept in the cache so nobody re-opens it to find out."""

    def __init__(self, why: str):
        self.why = why


def read_schema(path: str):
    """`(document, problem)` — the parsed document, or the reason there is none.

    One read per file, and the reason travels with the result: the error path used to re-open and
    re-parse the file to name the failure, which for a path refused for leaving the checkout meant
    opening it after the guard had said no.
    """
    inside = _compose.contained(os.getcwd(), path)
    if inside is None:
        return None, "resolves outside the repository"
    if inside not in _PARSED:
        try:
            with open(inside, encoding="utf-8") as fh:
                _PARSED[inside] = json.load(fh)
        except Exception as exc:
            _PARSED[inside] = Unreadable(f"{type(exc).__name__}: {exc}")
    document = _PARSED[inside]
    if isinstance(document, Unreadable):
        return None, document.why
    return document, None


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
    document, problem = read_schema(path)
    return None if problem else document


def vendored_tree(path: str) -> str:
    """The `.agents/vendor/<bundle>-<version>` a path sits in.

    Named from the resolved path, because containment was decided on the resolved path: a
    reference reaching the tree through a symlink is textually somewhere else entirely, and
    naming it from the text produced `.agents/vendor/..`.
    """
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(_compose.VENDOR))
    return os.path.join(_compose.VENDOR, rel.split(os.sep)[0])


# ── the closer rule: build a decision the schema accepts, then try to add a property ───────────
#
# Everything here rests on one thing: the instance is CONFORMING. The check spent five review
# rounds on machinery that existed only because it was not — matching rendered error text for a
# probe name, counting keywords, two probes read as one boolean, a syntactic scan for a closer in
# the wrong branch. A probe that fails the base's own `required` makes the base's branch fail, a
# failing branch contributes no annotations, and from there nothing downstream can be read
# straight. So the instance is generated to satisfy the schema, VERIFIED against it, and the
# question becomes one boolean: add a property, is it still valid.
#
# The validator sees the whole schema; the generator walks a stated subset of it. Three things
# follow from that gap, and each has one answer here rather than a patch per keyword:
#
#   * a value the generator built may be refused through a keyword it did not walk or satisfy — a
#     `then` requiring a property it never built, a `contains` it never aimed at. The refusal's
#     schema path says which keyword it came through, so a refusal through one the generator does
#     not honour is this check's own limit, reported as not measured, and only a refusal through
#     keywords it did honour is the schema rejecting a conforming decision;
#   * an object may be left unbuilt where a keyword the generator does not walk would put one. It
#     is never probed, and the construct and the object are named;
#   * a probe name may be refused for its SHAPE. A closer refuses undeclared names, not names that
#     look a certain way, so the probe uses names no name rule at that object refuses, and says so
#     when it has none.

# Several name shapes — hyphenated, underscored, plain letters, capitalised, with a digit, one
# letter — because a `propertyNames` or `patternProperties` rule refuses a name for its shape, not
# for being undeclared, and one shape refused proves nothing about the others. The probe uses the
# ones no rule at the object refuses; any of them getting in means the object takes what the base
# does not name.
PROBE_PROPERTIES = ("exeris-closer-probe-property", "exeris_closer_probe_two",
                    "exerisclosercandidate", "ExerisProbeProperty", "zz9probe", "q")

# And several value shapes under each name. A closer refuses an undeclared NAME whatever stands
# under it; `additionalProperties: {"type": "integer"}` refuses a string for its type and lets
# every number in, and one string refused read as a closed object. An object is closed only when
# every name is refused with every value.
PROBE_VALUES = ("probe", 7, 1.5, True, None, {}, [])

# Candidate strings, tried in order against whatever `pattern`, `minLength` and `maxLength` apply.
# A generator that solves regular expressions is a different program; these cover the shapes the
# bundle's own bases use, and anything they do not cover is declined rather than guessed at.
CANDIDATE_STRINGS = ("a", "abc", "human", "abc-def", "a.md#1", "ABC", "ABC_DEF", "a1", "0",
                     "docs-only", "templates/A.md", "a-b-c")

# The 2020-12 keywords that can hold a subschema, and what this check does with each. Every one is
# either walked by the generator or declined by name where it appears — `tests/test_schema_
# closers.py` fails if a keyword is in neither, so the next keyword is caught by CI rather than by
# a sixth review round. `$dynamicRef` is walked like `$ref`: it resolves the same way until its
# fragment names a dynamic anchor, and a composition written with it is a composition.
HANDLED_KEYWORDS = frozenset({"properties", "items", "prefixItems", "$ref", "$dynamicRef", "allOf",
                              "$defs", "definitions"})

# And the assertions. A keyword the generator does not satisfy is DECLINED where it appears —
# never ignored, because an ignored assertion produces a value the schema refuses and turns a
# conforming repository red. `uniqueItems` counts as honoured by never being violated: the
# generator declines an array it would have to pad with copies rather than padding it.
HONOURED_ASSERTIONS = frozenset({"type", "required", "enum", "const", "pattern", "minLength",
                                 "maxLength", "minItems", "maxItems", "uniqueItems", "minimum",
                                 "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"})
DECLINED_ASSERTIONS = frozenset({"format", "minProperties", "maxProperties", "dependentRequired",
                                 "minContains", "maxContains"})
# Not walked when a value is built. Two of them are honoured later, when the probe chooses a
# name: `patternProperties` and `propertyNames` refuse names for their shape, and the probe uses
# names they do not refuse. The branching ones are compared with what the generator did build,
# and reported where they would put something it did not.
DECLINED_KEYWORDS = frozenset({"additionalItems", "patternProperties", "additionalProperties",
                               "propertyNames", "anyOf", "oneOf", "not", "if", "then", "else",
                               "dependentSchemas", "contains", "unevaluatedItems",
                               "unevaluatedProperties"})

# The keywords that hold a subschema over what the properties do not name; an object under one
# is not built, and is declared as such.
SCHEMA_VALUED = frozenset({"additionalProperties", "unevaluatedProperties", "unevaluatedItems"})
# Keywords followed in a schema path by a NAME rather than a keyword — a property, a pattern, a
# definition. Reading the name as a keyword would make a property called `anyOf` a blind spot.
NAMED_BY_KEY = frozenset({"properties", "patternProperties", "dependentSchemas", "$defs",
                          "definitions"})
REFERENCE_KEYWORDS = ("$ref", "$dynamicRef")


def object_ish(node) -> bool:
    """Whether a subschema could describe an object with properties of its own.

    A declared `type` decides; without one, the keywords that only make sense of an object do —
    including a reference, which names a shape this cannot see from here, and a branch whose
    alternatives could each be one.
    """
    if not isinstance(node, dict):
        return False
    declared = node.get("type")
    if declared == "object" or (isinstance(declared, list) and "object" in declared):
        return True
    if declared is not None:
        return False
    if {"properties", "patternProperties", "required", "items", "prefixItems"} & set(node) \
            or any(True for _ in references_of(node)):
        return True
    for keyword in ("allOf", "anyOf", "oneOf", "then", "else"):
        value = node.get(keyword)
        if any(object_ish(sub) for sub in (value if isinstance(value, list) else [value])):
            return True
    return any(object_ish(sub) for sub in (node.get("dependentSchemas") or {}).values())


def references_of(node):
    """`(keyword, target)` for each reference a schema object carries."""
    if isinstance(node, dict):
        for keyword in REFERENCE_KEYWORDS:
            if isinstance(node.get(keyword), str):
                yield keyword, node[keyword]


def resolved_ref(ref: str, document, base_dir: str):
    """`(node, document, directory, declined)` for one reference.

    Followed: a fragment inside the document that named it, and a file inside the checkout — a
    vendored base, a neighbour under `.agents/schemas/`, a wrapper of the repository's own. That is
    what the validator resolves too, so the generator and the validator now read the same tree.
    Declined by name: a URL, a path leaving the checkout, a file that is not a schema object, and a
    fragment naming an anchor rather than a pointer — a `$dynamicRef` to a dynamic anchor resolves
    through the dynamic scope, which this does not model.
    """
    if "://" in ref:
        return None, document, base_dir, f"a reference this check does not fetch ('{ref}')"
    path, _, pointer = ref.partition("#")
    if pointer and not pointer.startswith("/"):
        return None, document, base_dir, f"a reference to a named anchor ('{ref}')"
    if path:
        target = os.path.normpath(os.path.join(base_dir, path))
        onward = load_schema(target)                # None outside the checkout, or unreadable
        if not isinstance(onward, dict):
            return None, document, base_dir, f"a reference that names no readable schema ('{ref}')"
        document, base_dir = onward, os.path.dirname(target)
    node = node_at(document, pointer) if pointer else document
    if not isinstance(node, dict):
        return None, document, base_dir, f"a reference that names no object ('{ref}')"
    return node, document, base_dir, None


def reaches_bundle(schema, here: str, roots, seen=None) -> bool:
    """Whether a schema composes a bundle base: a reference into the vendored tree, at any depth,
    directly or through a file of the repository's own. Recognised by where a reference resolves,
    never by a filename."""
    seen = set() if seen is None else seen
    for node in subschemas(schema):
        for _, ref in references_of(node):
            if ref_target(ref, here, roots):
                return True
            if ref.startswith("#") or "://" in ref:
                continue
            through = os.path.normpath(os.path.join(here, ref.partition("#")[0]))
            if through in seen:
                continue
            seen.add(through)
            document = load_schema(through)
            if isinstance(document, dict) and reaches_bundle(document, os.path.dirname(through),
                                                              roots, seen):
                return True
    return False


def applying(parts):
    """Every subschema that applies to one value, as `(node, document, directory)`.

    A reference brings in what it names **and keeps its siblings**: `{"$ref": …, "properties": {…}}`
    declares both, and reading only the target lost the second. `allOf` branches apply for the same
    reason. Each entry carries the ancestors it was reached through, so a cycle stops while one
    neighbour referenced from two properties is still expanded at both — a single visited-set for
    the whole walk silently dropped the second.
    """
    out, declined = [], []
    stack = [(node, document, base_dir, frozenset()) for node, document, base_dir in parts]
    while stack:
        node, document, base_dir, ancestors = stack.pop(0)
        if not isinstance(node, dict) or id(node) in ancestors:
            continue
        below = ancestors | {id(node)}
        out.append((node, document, base_dir))
        for _, ref in references_of(node):
            target, onward, directory, refusal = resolved_ref(ref, document, base_dir)
            if refusal:
                declined.append(refusal)
            elif target is not None:
                stack.append((target, onward, directory, below))
        for branch in node.get("allOf") or []:
            stack.append((branch, document, base_dir, below))
    return out, declined


def parts_of_property(applied, name: str):
    """The subschemas that apply to one property of an object the parts describe."""
    return [(sub, document, base_dir) for node, document, base_dir in applied
            for n, sub in (node.get("properties") or {}).items() if n == name]


def parts_of_items(applied):
    """The subschemas that apply to some element of an array the parts describe."""
    out = []
    for node, document, base_dir in applied:
        if isinstance(node.get("items"), dict):
            out.append((node["items"], document, base_dir))
        for item in node.get("prefixItems") or []:
            if isinstance(item, dict):
                out.append((item, document, base_dir))
    return out


def novel(branch, parts, depth: int = 6):
    """What a branch the generator does not walk would put where the decision has nothing.

    The first such thing, named — a property no part applying there declares, an object under
    names that cannot be enumerated — or None when the branch only constrains what is already
    built. That is the difference between the bases' own `if`/`then` over `findings`, which
    introduces nothing and must not put a warning on every composition in the ecosystem, and a
    `then` that requires a property the generator never built, whose object is never probed.
    Compared structurally, level by level, against the parts that apply at each level.
    """
    node, document, base_dir = branch
    if depth <= 0 or not isinstance(node, dict):
        return None
    applied, _ = applying(parts)
    names: set = set()
    for part, _, _ in applied:
        names.update(part.get("properties") or {})
        names.update(part.get("required") or [])
    expanded, _ = applying([branch])
    for sub, doc, where_ in expanded:
        wanted = list(sub.get("properties") or {}) + list(sub.get("required") or [])
        for more in (sub.get("dependentRequired") or {}).values():
            wanted += list(more) if isinstance(more, list) else []
        for name in wanted:
            if name not in names:
                return f"a property '{name}'"
        for name, below in (sub.get("properties") or {}).items():
            found = novel((below, doc, where_), parts_of_property(applied, name), depth - 1)
            if found:
                return found
        for pattern, below in (sub.get("patternProperties") or {}).items():
            if object_ish(below):
                return f"an object under `patternProperties` '{pattern}'"
        if object_ish(sub.get("additionalProperties")):
            return "an object under `additionalProperties`"
        for key in ("items", "contains"):
            if isinstance(sub.get(key), dict):
                found = novel((sub[key], doc, where_), parts_of_items(applied), depth - 1)
                if found:
                    return found
        for item in sub.get("prefixItems") or []:
            found = novel((item, doc, where_), parts_of_items(applied), depth - 1)
            if found:
                return found
        for key in ("anyOf", "oneOf", "then", "else"):
            value = sub.get(key)
            for below in (value if isinstance(value, list) else [value]):
                found = novel((below, doc, where_), parts, depth - 1)
                if found:
                    return found
        for below in (sub.get("dependentSchemas") or {}).values():
            found = novel((below, doc, where_), parts, depth - 1)
            if found:
                return found
    return None


def quantifier(pattern: str, i: int):
    """`(how many times, where the pattern continues)` for whatever follows an atom.

    `{3}` and `{2,4}` were read a character at a time, so `^ADR-[0-9]{3}$` produced `ADR-0` and
    `^[A-Z]{2,4}$` produced `A,4` — a built string that does not match the pattern it was built
    from, which the caller then reports as a value it could not construct or, worse, uses.
    """
    if i >= len(pattern):
        return 1, i
    if pattern[i] in "+*":
        return 1, i + 1
    if pattern[i] == "?":
        return 0, i + 1
    if pattern[i] == "{":
        close = pattern.find("}", i)
        if close < 0:
            return None, i
        counts = pattern[i + 1:close].split(",")
        try:
            return max(1, int(counts[0])) if counts[0] else 1, close + 1
        except ValueError:
            return None, i
    return 1, i


def from_pattern(pattern: str):
    """A string built to match a simple pattern, or None.

    Not a regular-expression solver: literal runs, escaped characters, a character class with a
    quantifier, and the first alternative of a group. That covers what a repository writes to
    narrow a path or a name — `^templates/[A-Z-]+-TEMPLATE\\.md$` is a real one — and anything it
    cannot build is declined rather than guessed at.
    """
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch in "^$":
            i += 1
        elif ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i + 1] if pattern[i + 1] not in "dwsDWS" else "a")
            i += 2
        elif ch == "[":
            close = pattern.find("]", i + 1)
            if close < 0:
                return None
            inside = pattern[i + 1:close]
            pick = "a" if inside.startswith("^") else next(
                (c for c in inside if c.isalnum()), None)
            if pick is None:
                return None
            repeat, i = quantifier(pattern, close + 1)
            if repeat is None:
                return None
            out.append(pick * repeat)
        elif ch == "(":
            end, level = i, 0
            for j in range(i, len(pattern)):
                level += (pattern[j] == "(") - (pattern[j] == ")")
                if level == 0:
                    end = j
                    break
            group = pattern[i + 1:end].split("|")[0].lstrip("?:")
            if end + 1 < len(pattern) and pattern[end + 1] in "*?":
                i = end + 2                       # an optional group contributes nothing
                continue
            built = from_pattern(group)
            if built is None:
                return None
            out.append(built)
            i = end + 1 + (1 if end + 1 < len(pattern) and pattern[end + 1] == "+" else 0)
        elif ch in ".+*?{}":
            if ch == ".":
                out.append("a")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def a_string(parts):
    """A string every part accepts, or None and the reason."""
    patterns = [n.get("pattern") for n, _, _ in parts if isinstance(n.get("pattern"), str)]
    low = max([n.get("minLength", 0) for n, _, _ in parts] or [0])
    high = min([n.get("maxLength", 1 << 20) for n, _, _ in parts] or [1 << 20])
    built = [from_pattern(p) for p in patterns] if patterns else []
    for candidate in [b for b in built if b] + list(CANDIDATE_STRINGS):
        for text in (candidate, candidate + "a" * max(0, low - len(candidate)), "a" * low):
            if not (low <= len(text) <= high):
                continue
            if all(re.search(p, text) for p in patterns):
                return text, None
    return None, ("a string matching " + " and ".join(patterns) if patterns
                  else f"a string of length {low}..{high}")


def a_number(parts, integer: bool):
    """A number every part accepts, or None and the reason.

    `minimum` alone was honoured and the rest — `exclusiveMinimum`, `maximum`,
    `exclusiveMaximum`, `multipleOf` — were neither satisfied nor declined, so an out-of-range
    value reached the schema and came back as an error against a repository that had done nothing
    wrong.
    """
    def declared(keyword):
        return [n[keyword] for n, _, _ in parts if isinstance(n.get(keyword), (int, float))
                and not isinstance(n.get(keyword), bool)]

    step = 1 if integer else 1e-9
    low = max(declared("minimum") + [e + step for e in declared("exclusiveMinimum")] + [0])
    high = min(declared("maximum") + [e - step for e in declared("exclusiveMaximum")]
               + [float("inf")])
    value = int(low) if integer else float(low)
    for multiple in declared("multipleOf"):
        if multiple > 0:
            factor = math.ceil(value / multiple)
            value = factor * multiple
            value = int(value) if integer else float(value)
    if value > high:
        return None, (f"a number in {low}..{high}"
                      + (" that is a multiple of " + ", ".join(str(m) for m in declared("multipleOf"))
                         if declared("multipleOf") else ""))
    return value, None


def kind_of(parts) -> str:
    """The type to build. A declared `type` wins; otherwise it is read off the keywords present."""
    for node, _, _ in parts:
        declared = node.get("type")
        if isinstance(declared, str):
            return declared
        if isinstance(declared, list) and declared:
            return declared[0]
    for node, _, _ in parts:
        if {"properties", "required", "patternProperties"} & set(node):
            return "object"
        if {"items", "prefixItems", "minItems"} & set(node):
            return "array"
        if {"pattern", "minLength", "maxLength"} & set(node):
            return "string"
    return "object"


class Probe:
    """What one attempt at building a decision learned beside the value.

    Three lists, because they cost different things. `declined` is what the reader hears about —
    a construct not walked, a reference not followed, a value not built. `unbuilt` is the subset
    that is a missing VALUE: pruned from the instance and not probed. A location built without a
    part the generator could not follow is neither: kept, and probed — the validator saw the
    whole schema, so a probe on an instance it accepted is sound. `names` is the name rules at
    each object, which the probe must be fair to. Conflating the first two skipped the very
    object the reader was being warned about.
    """

    def __init__(self):
        self.declined: list = []
        self.unbuilt: list = []
        self.names: dict = {}


def build(parts, probe: Probe, depth: int = 8, at=()):
    """A value built to satisfy every subschema that applies, or None where none could be.

    A location is what makes a decline cost one property rather than the schema. A pattern this
    cannot construct used to end the measurement — "no decision could be built" — and an object
    three levels away went unreported although the run knew perfectly well what was wrong with it.
    Now the location is dropped from the instance, the rest is measured, and the location is named.

    Best effort by design, and checked: what it produces is validated against the schema before
    anything is concluded from it, so a wrong guess becomes a refusal to measure and never a wrong
    verdict.
    """
    parts, refusals = applying(parts)
    for why in refusals:
        probe.declined.append((at, f"{why} was not followed, so what it declares was not built "
                                   f"here; the values are built from the rest, and the validator "
                                   f"still sees all of it"))
    if depth <= 0:
        probe.declined.append((at, "the depth bound stopped the generator, so nothing below "
                                   "this location was built or probed"))
        probe.unbuilt.append(at)
        return None
    if not parts:
        return {}

    # What the generator does not walk, and where each would put something it did not build.
    # `if` selects and `not` forbids; neither puts anything in a decision. `propertyNames` is
    # honoured when the probe picks a name.
    for node, document, base_dir in parts:
        for keyword in sorted(DECLINED_KEYWORDS & set(node)):
            value = node[keyword]
            if keyword == "patternProperties":
                for pattern, sub in (value.items() if isinstance(value, dict) else []):
                    if object_ish(sub):
                        probe.declined.append((at, f"an object under `patternProperties` "
                                                   f"'{pattern}', which this generator does not "
                                                   f"build, so it was never probed"))
            elif keyword in SCHEMA_VALUED or keyword == "additionalItems":
                if object_ish(value):
                    probe.declined.append((at, f"`{keyword}` as a schema — an object under it "
                                               f"is not built, so it was never probed"))
            elif keyword in ("anyOf", "oneOf"):
                for sub in (value if isinstance(value, list) else []):
                    found = novel((sub, document, base_dir), parts)
                    if found:
                        probe.declined.append((at, f"`{keyword}`, a branch of which introduces "
                                                   f"{found} that this generator does not build, "
                                                   f"so it was never probed"))
                        break
            elif keyword in ("then", "else", "contains"):
                found = novel((value, document, base_dir),
                              parts_of_items(parts) if keyword == "contains" else parts)
                if found:
                    probe.declined.append((at, f"`{keyword}` introduces {found} that this "
                                               f"generator does not build, so it was never probed"))
            elif keyword == "dependentSchemas":
                for name, sub in (value.items() if isinstance(value, dict) else []):
                    found = novel((sub, document, base_dir), parts)
                    if found:
                        probe.declined.append((at, f"`dependentSchemas` under '{name}' introduces "
                                                   f"{found} that this generator does not build, "
                                                   f"so it was never probed"))
                        break

    for node, _, _ in parts:
        if "const" in node:
            return node["const"]
    enums = [n["enum"] for n, _, _ in parts if isinstance(n.get("enum"), list) and n["enum"]]
    if enums:
        shared = [v for v in enums[0] if all(v in other for other in enums[1:])]
        if not shared:
            probe.declined.append((at, "a value every `enum` accepts could not be built, so the "
                                       "value was left out of the decision"))
            probe.unbuilt.append(at)
            return None
        return shared[0]

    kind = kind_of(parts)
    if kind == "object":
        value, declared = {}, {}
        for node, document, base_dir in parts:
            for name, sub in (node.get("properties") or {}).items():
                declared.setdefault(name, []).append((sub, document, base_dir))
            for name in node.get("required") or []:
                declared.setdefault(name, [])
        patterns = [p for node, _, _ in parts
                    for p in (node.get("patternProperties") or {}) if isinstance(p, str)]
        naming = [(node["propertyNames"], document, base_dir)
                  for node, document, base_dir in parts if "propertyNames" in node]
        if patterns or naming:
            probe.names[at] = (patterns, naming, frozenset(declared))
        for name, subs in declared.items():
            if any(sub is False for sub, _, _ in subs):
                continue                          # a `false` schema forbids the property outright
            below = build(subs, probe, depth - 1, at + (name,)) if subs else {}
            if below is not None:
                value[name] = below
        return value
    if kind == "array":
        positions, tail, closed_tail = [], [], False
        for node, document, base_dir in parts:
            if isinstance(node.get("prefixItems"), list):
                for index, item in enumerate(node["prefixItems"]):
                    while len(positions) <= index:
                        positions.append([])
                    positions[index].append((item, document, base_dir))
            items = node.get("items")
            if isinstance(items, dict):
                tail.append((items, document, base_dir))
            elif items is False:
                closed_tail = True                # nothing past the prefix, by declaration
            elif "items" in node:
                probe.declined.append((at, "an array whose item schema is not a schema object, "
                                           "so no element was built from it"))
        built = []
        for index, subs in enumerate(positions):
            below = build(subs, probe, depth - 1, at + (index,))
            built.append({} if below is None else below)
        # An element of `items` as well as the prefix positions: with both declared, `items` covers
        # everything past the prefix, and building only the prefix left that shape unmeasured.
        if tail and not closed_tail:
            below = build(tail, probe, depth - 1, at + (len(built),))
            if below is not None:
                built.append(below)
        low = max([n.get("minItems", 0) for n, _, _ in parts] or [0])
        unique = any(n.get("uniqueItems") is True for n, _, _ in parts)
        if len(built) < low:
            if unique or not built:
                # Padding with copies violates `uniqueItems`, and an array with nothing to build
                # from would be padded with an object the schema never declared. Either way the
                # array is what could not be built, not the schema's fault.
                probe.declined.append((at, (f"an array of {low} elements this generator cannot "
                                            f"make distinct" if unique else
                                            f"an array of {low} elements with no item schema")
                                       + " could not be built, so the value was left out of the "
                                         "decision"))
                probe.unbuilt.append(at)
                return None
            while len(built) < low:
                built.append(copy.deepcopy(built[-1]))
        high = min([n.get("maxItems", 1 << 20) for n, _, _ in parts] or [1 << 20])
        return built[:high] if len(built) > high else built
    if kind in ("string", "integer", "number"):
        value, refusal = (a_string(parts) if kind == "string"
                          else a_number(parts, kind == "integer"))
        if value is None:
            probe.declined.append((at, f"{refusal} could not be built, so the value was left out "
                                       f"of the decision"))
            probe.unbuilt.append(at)
        return value
    if kind == "boolean":
        return True
    if kind == "null":
        probe.unbuilt.append(at)                  # `null` is a value; pruning it keeps it out
        return None
    probe.declined.append((at, f"a value of type '{kind}' could not be built, so it was left out "
                               f"of the decision"))
    probe.unbuilt.append(at)
    return None


def prune(instance, locations):
    """The instance with the values that could not be built removed.

    Absence validates wherever the value was optional, which is what lets the rest of the decision
    be measured. Where it was required the instance fails and the schema is declined whole — that
    is the one case where a schema-wide decline is the honest answer.
    """
    out = copy.deepcopy(instance)
    for location in sorted(locations, key=len, reverse=True):
        if not location:
            continue
        node = out
        for step in location[:-1]:
            if isinstance(node, dict) and step in node:
                node = node[step]
            elif isinstance(node, list) and isinstance(step, int) and step < len(node):
                node = node[step]
            else:
                node = None
                break
        last = location[-1]
        if isinstance(node, dict) and last in node:
            del node[last]
        elif isinstance(node, list) and isinstance(last, int) and last < len(node):
            node.pop(last)
    return out


def pruned_away(path, unbuilt) -> bool:
    """Whether an instance path is at or under a value that could not be built, or is the object
    a removed property was required by."""
    for location in unbuilt:
        if tuple(path[:len(location)]) == location:
            return True
        if location[:len(path)] == tuple(path) and len(location) == len(path) + 1:
            return True
    return False


def keywords_on(path):
    """The keywords along a schema path, without the names and indices between them.

    `dependentSchemas/agent/required` is two keywords and a property; `properties/anyOf/pattern`
    is a property that happens to be called `anyOf`. `$ref` never appears: the validator splices
    the target's path in where the reference stood.
    """
    out, name_next = [], False
    for step in path:
        if name_next:
            name_next = False
            continue
        if isinstance(step, int):
            continue
        out.append(step)
        name_next = step in NAMED_BY_KEY
    return out


def artefact(error, errors) -> bool:
    """Whether an `unevaluated*` error is the annotation artefact `BUNDLE.md` tells readers to
    skip: a subschema below it failed, contributed no annotations, and it reported everything."""
    if error.validator not in ("unevaluatedProperties", "unevaluatedItems"):
        return False
    path = tuple(error.absolute_path)
    return any(other is not error and tuple(other.absolute_path)[:len(path)] == path
               for other in errors)


def probe_validator(schema, schema_path: str):
    """A validator for one composed schema, resolving `$ref` from the filesystem.

    The two moves live in `_compose`, beside the other definitions the checker and the renderer
    share: a document is identified by the file it was read from, and a reference is retrieved from
    disk. What is local is the reader — `load_schema`, which contains a path against the tree being
    checked and parses each file once.
    """
    from jsonschema import Draft202012Validator
    base_dir = os.path.dirname(os.path.abspath(schema_path))
    return Draft202012Validator(_compose.located(schema, schema_path),
                                registry=_compose.registry_over(load_schema, base_dir))


def where(location) -> str:
    """An instance location, with the separator escaped out of a step that contains it.

    A property named `a/b~c` read exactly like a nested path. The same defect was fixed once in
    JSON pointers and came back in instance paths, so the escaping is the pointer one: `~0` for a
    tilde, `~1` for a slash.
    """
    if not location:
        return "<root>"
    return "/".join(str(step).replace("~", "~0").replace("/", "~1") for step in location)


def object_locations(instance, path=()):
    """Every object in an instance, by the path that reaches it.

    Locations come from the instance now, not from a walk over the schema. An object is somewhere a
    decision actually carries one, which is the only place a property can be added — and it needs
    no knowledge of which keyword put it there.
    """
    if isinstance(instance, dict):
        yield path
        for name, value in instance.items():
            yield from object_locations(value, path + (name,))
    elif isinstance(instance, list):
        for index, value in enumerate(instance):
            yield from object_locations(value, path + (index,))


def carrying(instance, location, name: str, value="probe"):
    """The instance with one foreign property, carrying one value, added at one location."""
    out = copy.deepcopy(instance)
    node = out
    for step in location:
        node = node[step]
    node[name] = copy.deepcopy(value)
    return out


def fair_names(validator, rules):
    """`(names, closed by enumeration)` — the probe names no name rule at one object refuses for
    their shape.

    A `patternProperties` entry or a `propertyNames` pattern refuses a name for how it looks, and a
    probe refused that way says nothing about whether the object is closed: `^exeris` refused and
    `zz9probe` walking in is an open object. So the candidates are filtered through every name
    rule that applies, and a name built from a `propertyNames` pattern joins them. A `propertyNames`
    that enumerates its names is the one rule that IS a closer, when every name it allows is one
    the base declares — and when it allows one the base does not, that name is the probe.
    """
    if not rules:
        return list(PROBE_PROPERTIES), False
    patterns, naming, declared = rules
    candidates, finite = list(PROBE_PROPERTIES), False
    for node, _, _ in naming:
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("pattern"), str):
            built = from_pattern(node["pattern"])
            if built:
                candidates.append(built)
        listed = (node["enum"] if isinstance(node.get("enum"), list)
                  else [node["const"]] if "const" in node else None)
        if listed is not None:
            finite = True
            candidates += [v for v in listed if isinstance(v, str)]
    fair = []
    for name in dict.fromkeys(candidates):
        if name in declared or any(re.search(p, name) for p in patterns):
            continue
        if all(validator.evolve(schema=node).is_valid(name) for node, _, _ in naming):
            fair.append(name)
    return fair, finite and not fair


def branches_of(parts):
    """Each `oneOf` / `anyOf` branch that applies to a value, as a part of its own."""
    applied, _ = applying(parts)
    for node, document, base_dir in applied:
        for keyword in ("oneOf", "anyOf"):
            for branch in node.get(keyword) or []:
                if isinstance(branch, dict):
                    yield branch, document, base_dir


def check_closers(rep: Report, rel: str, schema, schema_path: str, roots):
    """A composition refuses a property the base does not name — measured on a decision it accepts.

    The bases close nothing: a base that closes itself cannot be extended, and
    `unevaluatedProperties: false` in the base refuses an added field just as flatly, since it sees
    only the annotations of its own schema object and its in-place applicators, never a sibling
    `allOf` branch in the composing schema. So the refusal lives in the composition or nowhere, and
    "nowhere" is what a repository gets by bumping the bundle and changing nothing.

    Three steps, and the first is the one that matters. A decision is generated to satisfy this
    schema; it is validated against this schema; and only then is a property added at each object
    it carries — under several names and with several values, one boolean per object: closed
    when every witness is refused. Nothing reads an error message, counts a keyword or looks at a
    path to reach a verdict.

    Only what is measured is an error: an object that took a property the base does not name,
    on a decision the schema accepted. A schema that refuses the decision built for it is
    reported as not measured, at warning level, whatever keyword the refusal came through — the
    refusal is a conclusion drawn from this generator's output, and telling the schema's fault
    from this check's own by the shape of the refusal's path was a guess that turned conforming
    repositories red. The keyword, and the one shape that is always a schema's own doing (a
    closer parked inside an `allOf` branch), are named for the reader.
    """
    if not isinstance(schema, dict):
        return
    here = os.path.dirname(schema_path)
    if not reaches_bundle(schema, here, roots):
        return
    parts = [(schema, schema, here)]
    try:
        # Inside the guard, every step: a base carrying `{"type": "string", "minLength": "3"}` —
        # a string where an int belongs, which nothing validates because a vendored base never
        # goes through `check_schema` — raised out of the generator; a `$ref` reached only by the
        # probe, under a `patternProperties` entry no built property matched, raised out of the
        # probe. Either took every finding in the repository with it before `rep.emit()`.
        validator = probe_validator(schema, schema_path)
        probe = Probe()
        candidate = prune(build(parts, probe), probe.unbuilt)
        errors = list(validator.iter_errors(candidate))
        if errors:
            # A `oneOf` or `anyOf` at the root is a real composition — per-scope rules are written
            # that way — and generating past it produces a decision no branch accepts. One branch
            # at a time, keeping the first that validates.
            for branch in branches_of(parts):
                attempt = Probe()
                pruned = prune(build(parts + [branch], attempt), attempt.unbuilt)
                if validator.is_valid(pruned):
                    probe, candidate, errors = attempt, pruned, []
                    break

        # Reported after the retry, and from the instance that was actually probed: a decline from
        # an attempt that was abandoned describes nothing the reader can act on.
        for location, what in dict.fromkeys(probe.declined):
            rep.warning(rel, f"not measured at {where(location)}: {what}", rule="schema")

        if errors:
            # A refusal of the decision this check built is a conclusion drawn from the
            # generator's output, not a measurement: the refusal is as likely to be a value this
            # could not construct, or a keyword it does not walk, as the schema refusing what its
            # base declares — and telling the two apart by the shape of the refusal's path is a
            # guess, which is how a conforming repository was turned red. What can be said is that
            # nothing here was measured; the keyword the refusal came through, and the one shape
            # that is always the schema's own doing, are named for the reader to judge.
            quoted = [e for e in errors if not artefact(e, errors)] or errors
            through = [k for k in keywords_on(quoted[0].absolute_schema_path)
                       if k in DECLINED_KEYWORDS or k in DECLINED_ASSERTIONS]
            parked = any(e.validator in ("unevaluatedProperties", "additionalProperties")
                         and "allOf" in keywords_on(e.absolute_schema_path) for e in quoted)
            rep.warning(rel, "no decision could be built that this schema accepts — refused "
                             + "; ".join(f"at {where(tuple(e.absolute_path))}: {e.message}"
                                         for e in quoted[:2])
                             + (f" — through `{through[0]}`" if through else "")
                             + ". A probe is only evidence when the instance conforms, so nothing "
                               "about what this schema refuses was measured. Either a value this "
                               "check could not construct, or the schema refusing what its base "
                               "declares"
                             + (" — a closer inside an `allOf` branch does this: it sees only the "
                                "properties named beside it" if parked else "")
                             + ". Not measured", rule="schema")
            return

        for location in object_locations(candidate):
            if pruned_away(location, probe.unbuilt):
                continue
            names, enumerated = fair_names(validator, probe.names.get(location))
            if not names:
                if not enumerated:
                    rep.warning(rel, f"not measured at {where(location)}: no property name this "
                                     f"check can construct passes the name rules there "
                                     f"(`propertyNames`, `patternProperties`), so whether the "
                                     f"object takes a name the base does not declare is unknown",
                                rule="schema")
                continue
            # One boolean per object, from several witnesses: a name is refused for being
            # undeclared only when every value under it is refused too.
            accepted = next(((name, value) for name in names for value in PROBE_VALUES
                             if validator.is_valid(carrying(candidate, location, name, value))),
                            None)
            if accepted is None:
                continue
            rep.error(rel, f"an instance may carry any property at {where(location)} — a decision "
                           f"this schema accepts still validates with '{accepted[0]}': "
                           f"{json.dumps(accepted[1])} added there, "
                           f"so the object takes whatever the base does not name. Close it: the "
                           f"base closes nothing, and a closer at the root does not reach into an "
                           f"array's items. Rule 13 makes this file what a decision conforms to; "
                           f"that the closers are yours is this bundle's contract, in `BUNDLE.md`",
                      rule="schema")
    except Exception as exc:
        # This check's own failure on one schema: unmeasured, not a verdict on the schema.
        rep.warning(rel, f"not measured: composes over a bundle base and this check failed on it "
                         f"({type(exc).__name__}: {exc}), so whether it closes what the base "
                         f"leaves open is unknown", rule="schema")


def check_schemas(rep: Report, roots=None):
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
        schema, problem = read_schema(os.path.join(d, f))
        if problem:
            rep.error(rel, f"could not be read: {problem}", rule="schema")
            continue
        if not isinstance(schema, (dict, bool)):
            # `null` and an array parse and are not schemas. Saying "unreadable" of a file that
            # reads perfectly well sends the reader to look for a syntax error there is none of.
            rep.error(rel, f"parses as {type(schema).__name__}, which is not a JSON Schema — a "
                           f"schema is an object or a boolean", rule="schema")
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
        off_pin, unresolved = [], 0
        for node in subschemas(schema):
            for keyword, ref in references_of(node):
                # A URL is the one that has to be refused: resolving one needs a fetch, and rule 8
                # forbids fetching. A fragment-only ref points inside this same document.
                if ref.startswith(("http://", "https://")):
                    rep.error(rel, f"{keyword} '{ref}' is a URL — a vendored bundle resolves from the "
                                   f"filesystem, and fetching one at validation time is what rule 8 "
                                   f"forbids. Use a relative path into .agents/vendor/.", rule="schema")
                    unresolved += 1
                    continue
                file_part, _, fragment = ref.partition("#")
                if file_part:
                    target = os.path.normpath(os.path.join(d, file_part))
                    if _compose.contained(os.getcwd(), target) is None:
                        rep.error(rel, f"{keyword} '{ref}' resolves outside the repository — a "
                                       f"reference resolves from the filesystem, inside the checkout, "
                                       f"and one that leaves it names a file this repository cannot "
                                       f"vouch for and a reader cannot see (rule 8)", rule="schema")
                        unresolved += 1
                        continue
                    if not os.path.exists(target):
                        rep.error(rel, f"{keyword} target does not exist: {ref}", rule="schema")
                        unresolved += 1
                        continue
                    document = load_schema(target)
                    if document is None:
                        rep.error(rel, f"{keyword} target is not readable JSON: {ref} — the file is "
                                       f"there, so this is the file to look at, not the pointer",
                                  rule="schema")
                        unresolved += 1
                        continue
                else:
                    document = schema
                # And the pointer, which nothing checked. The file existing was taken for the whole
                # answer, while this release makes `<file>#/properties/<name>/items` the documented way
                # to close a nested object — a typo there is a closer over nothing, on a `$ref` whose
                # file is right there.
                if fragment.startswith("/") and node_at(document, fragment) is None:
                    rep.error(rel, f"{keyword} '{ref}' names a pointer that does not resolve in "
                                   f"{file_part or 'this document'}", rule="schema")
                    unresolved += 1
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
        if unresolved:
            # A reference the validator cannot resolve raises the moment an instance reaches it —
            # which, for one under a `patternProperties` entry, is the moment the probe adds a
            # name. The reference is the finding, already reported; measuring past it is not.
            rep.warning(rel, f"closers not measured: {unresolved} reference(s) in this file do "
                             f"not resolve, and a decision cannot be validated through a "
                             f"reference the validator cannot follow. Fix those first",
                        rule="schema")
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
    try:
        run_checks(a, rep)
    except Exception as exc:
        # The report is the contract. Every check here reads an input somebody else wrote, and
        # a run that raised out of one wrote no report at all — so a consumer saw a traceback
        # where the findings should have been, and a CI step that was red for a reason nobody
        # could annotate. The failure is a finding, named to the frame, and everything reported
        # before it still reaches the reader; what the failing check would have reported past
        # that point is missing, and the finding says so.
        import traceback
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        rep.error("agents_file_check", f"the checker itself failed in {frame.name}() at "
                                       f"{os.path.basename(frame.filename)}:{frame.lineno} — "
                                       f"{type(exc).__name__}: {exc}. Every finding beside this "
                                       f"one is real; whatever that check would have reported "
                                       f"after this point is missing", rule="checker")
    sys.exit(rep.emit())


def run_checks(a, rep: Report) -> None:
    """Every check, in order, against the tree `main()` moved into."""
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
        manifest_parsed = False
        manifest_data: dict = {}
        if os.path.exists(manifest_path):
            import yaml
            try:
                with open(manifest_path, encoding="utf-8") as fh:
                    manifest_data = yaml.safe_load(fh) or {}
                declared_v2 = str(manifest_data.get("version")) == "2"
                manifest_parsed = True
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
        # None, not an empty list, when the manifest did not parse: what it pins is unknown, and
        # calling every correctly-targeted reference a stray buries the finding that matters.
        check_schemas(rep, _compose.vendor_roots(manifest_data) if manifest_parsed else None)

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


if __name__ == "__main__":
    main()
