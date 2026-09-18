#!/usr/bin/env python3
"""The bundle's own contract rules, as a program rather than as prose.

`AGENTS.md` states seven rules under "Operating contract" and nothing has ever read them. Three of
them are mechanical — they are true or false about a diff, with no judgement in between — and those
are `gate` below, which fails. The rest need a reader, and for them this file is `locate`: it finds
the candidates and says nothing about whether they are defects, because a script that decided that
would be a reviewer nobody appointed.

The split matters more than either half. A rule enforced by a gate is enforced; a rule named in
`docs/repo-review-rules.md` and handed a locator's output is *reported*; a rule that is only prose
is described. This file is where the first two are kept apart from the third on purpose, and
`tests/test_contract_check.py` holds one case per way of failing each gate — a rule nothing can
fail on is not enforced.

Not in `tools/`, and that is not tidiness: `package.json` publishes `tools` in the npm tarball and
`docs-lint` checks the tree out into consuming repositories. This asserts things about THIS
repository's history and would be dead weight in twenty others.

    python3 ci/contract_check.py gate   [--root .] [--base <ref>]   # exits 1 on a violation
    python3 ci/contract_check.py locate [--root .] [--base <ref>]   # exits 1 when it found candidates

A diff-scoped rule with no base to diff against is REPORTED as not-run, never skipped silently
(`bundle/policies/error-handling-and-fallback.md` rule 1): the base is always there on a pull
request, so the quiet case is a local run, and a local run that says "0 problems" about checks it
never performed is the shape that policy exists to refuse.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from _common import Report                                             # noqa: E402

# The three keys that close a schema. A base that carries one cannot be extended by the schema that
# composes it — which is the whole of the 2.0.0 release, and the state a later edit can undo in one
# line with nothing going red. `unevaluatedItems` is here beside the other two because an array
# closed in the base is closed for a composition just as an object is.
CLOSERS = ("additionalProperties", "unevaluatedProperties", "unevaluatedItems")
# Role vocabulary is per-repository by design: the base leaves these open and the repository
# narrows them (agents-md-schema.md rule 10 keeps the repository prefix). A name that appears in a
# base is a leak, and it reaches every composed schema at once.
ROLE_KEYS = ("agent", "task_class", "scope_class")
RELEASE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")
ADDED_RELEASE = re.compile(r"^\+## \[(\d+\.\d+\.\d+)\]")
ADDED_HEADING = re.compile(r"^\+#{1,3} +(.*)$")
VERSION_IN_HEADING = re.compile(r"\d+\.\d+(?:\.\d+)?")
# The executable surface: a change here is a change to what runs in twenty checkouts.
EXECUTABLE = ("tools/", "bundle/hooks/bin/", "bundle/evals/run.py", "ci/")
PUBLICATION = (".github/workflows/release.yml", "bundle/policies/agent-safety-and-autonomy.md")


# --------------------------------------------------------------------------- git

def git(root: str, *args: str) -> str | None:
    """stdout of a git command, or None when git could not answer."""
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    except (OSError, ValueError):
        return None
    return out.stdout if out.returncode == 0 else None


def resolve_base(root: str, explicit: str | None) -> str | None:
    """The ref to diff against, first of the candidates that git can actually resolve.

    `GUARDRAILS_BASE` is the name `docs-lint.yml` already passes a base SHA under, so a caller that
    sets it for one check sets it for both rather than learning a second spelling.
    """
    branch = os.environ.get("GITHUB_BASE_REF")
    for candidate in (explicit, os.environ.get("GUARDRAILS_BASE"),
                      f"origin/{branch}" if branch else None, "origin/main", "main"):
        if candidate and git(root, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    return None


def changed_paths(root: str, base: str) -> set[str]:
    out = git(root, "diff", "--name-only", f"{base}...HEAD")
    if out is None:                      # a shallow clone has no merge base; the two-dot form still answers
        out = git(root, "diff", "--name-only", base) or ""
    return {line.strip() for line in out.splitlines() if line.strip()}


def diff_of(root: str, base: str, path: str) -> str:
    out = git(root, "diff", "-U0", f"{base}...HEAD", "--", path)
    if out is None:
        out = git(root, "diff", "-U0", base, "--", path) or ""
    return out


# --------------------------------------------------------------- changelog reading

def release_section(text: str, version: str) -> str | None:
    """The body of one release's section, bounded at the next release heading.

    Bounded rather than windowed: a fixed number of lines after the heading spills into the
    neighbouring release, so a version with no `### Breaking` of its own passes on somebody else's.
    `release.yml` learned this the same way and says so in its own comment.
    """
    lines, inside, body = text.splitlines(), False, []
    for line in lines:
        m = RELEASE.match(line)
        if m:
            if inside:
                break
            inside = m.group(1) == version
            continue
        if inside:
            body.append(line)
    return "\n".join(body) if inside or body else None


def breaking_body(section: str) -> str | None:
    """What stands under `### Breaking`, or None when the section does not carry one."""
    inside, body = False, []
    for line in section.splitlines():
        if line.startswith("### "):
            if inside:
                break
            inside = line.strip() == "### Breaking"
            continue
        if inside:
            body.append(line)
    return "\n".join(body) if inside else None


def breaking_is_empty(body: str) -> bool:
    """`Breaking: nothing` is an answer — the preamble says so, and this is what reads it.

    Stripped of the list marker and the bold that the house style puts round the first words, an
    empty section opens with "nothing". Anything else is content, and content is what decides the
    version number.
    """
    text = re.sub(r"[*_`]", "", body).strip()
    text = re.sub(r"^[-*]\s*", "", text)
    return not text or text.lower().startswith("nothing")


def released_versions(text: str) -> list[str]:
    return [m.group(1) for m in (RELEASE.match(l) for l in text.splitlines()) if m]


def migration_covers(text: str, version: str) -> bool:
    """Does `MIGRATION.md` carry a heading for migrating TO this version?

    The file's convention is a range between two `major.minor` — `## 1.4 → 2.0` is the section for
    2.0.0 — so the version a heading covers is the LAST one it names, not any one it mentions. Read
    the other way round, that heading also claims to cover 1.4.0, whose own breaking change it says
    nothing about: the first draft of this function did exactly that and hid a standing debt behind
    the section that supersedes it.
    """
    short = ".".join(version.split(".")[:2])
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        named = VERSION_IN_HEADING.findall(line)
        if named and ".".join(named[-1].split(".")[:2]) == short:
            return True
    return False


def read(root: str, name: str) -> str:
    try:
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ------------------------------------------------------------------------- gates

def gate_bundle_carries_changelog(changed: set[str], rep: Report) -> None:
    """G1 — a change to `bundle/` is a version change, and the changelog moves with it.

    ADR-085 §H.27 puts `CHANGELOG.md` in the same pull request; `AGENTS.md` says the same in its
    own words. Flat over the whole directory, `BUNDLE.md` included: everything under `bundle/` is
    vendored into a consumer, so a documentation-only change there still ships.
    """
    touched = sorted(p for p in changed if p.startswith("bundle/"))
    if touched and "CHANGELOG.md" not in changed:
        rep.error("CHANGELOG.md",
                  f"{len(touched)} file(s) under bundle/ changed and CHANGELOG.md did not "
                  f"(first: {touched[0]}). A change to bundle/ is a version change and the "
                  f"changelog moves in the same pull request (ADR-085 §H.27).",
                  rule="G1")


def gate_bases_are_open(root: str, rep: Report) -> None:
    """G2 — a base refuses nothing and names no role.

    Tree-scoped rather than diff-scoped on purpose: the property is about the file as it stands,
    and a base that acquired a closer through a merge nobody diffed is exactly as broken as one
    that acquired it here.
    """
    schema_dir = os.path.join(root, "bundle", "schemas")
    for name in sorted(os.listdir(schema_dir)) if os.path.isdir(schema_dir) else []:
        if not name.endswith(".base.schema.json"):
            continue
        rel = f"bundle/schemas/{name}"
        rep.checked += 1
        try:
            with open(os.path.join(schema_dir, name), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            rep.error(rel, f"unreadable as JSON ({type(exc).__name__}: {exc})", rule="G2")
            continue
        for where, msg in walk_schema(doc):
            rep.error(rel, f"{where}: {msg}", rule="G2")


def walk_schema(node, path: str = "<root>"):
    """Every closer and every role enum a schema carries, with where it sits."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CLOSERS and value is False:
                yield path, (f"a base carries `{key}: false`. From 2.0.0 the bases refuse nothing "
                             f"and the composition closes every object; a closer here cannot be "
                             f"extended by the schema that composes it (BUNDLE.md, "
                             f"MIGRATION.md 1.4 → 2.0).")
            if key == "properties" and isinstance(value, dict):
                for role in ROLE_KEYS:
                    sub = value.get(role)
                    if isinstance(sub, dict) and ("enum" in sub or "const" in sub):
                        yield f"{path}/properties/{role}", (
                            "a base names the role vocabulary. Role names are per-repository "
                            "(agents-md-schema.md rule 10); the base leaves `agent`, `task_class` "
                            "and `scope_class` open and the repository narrows them, so a name "
                            "here is a leak into every composed schema at once.")
            yield from walk_schema(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk_schema(value, f"{path}[{i}]")


def gate_release_sections(root: str, changed: set[str], changelog_diff: str,
                          migration_diff: str, rep: Report) -> None:
    """G3 and G4 — a release added HERE carries a Breaking section, and pays for a non-empty one.

    Forward-only, and measured rather than chosen that way: `1.1.1` carries no `### Breaking`
    section at all, and `1.1.0` and `1.4.0` carry non-empty ones with no section in `MIGRATION.md`.
    A whole-tree form of this rule would be red on history that cannot be edited, which is the
    permanently-red gate `commit-lint.yml` describes and works around for the same reason. The
    standing gaps are reported by `locate` instead, where a reader can decide what they are worth.
    """
    text = read(root, "CHANGELOG.md")
    migration = read(root, "MIGRATION.md")
    for line in changelog_diff.splitlines():
        m = ADDED_RELEASE.match(line)
        if not m:
            continue
        version = m.group(1)
        rep.checked += 1
        section = release_section(text, version)
        if section is None:
            rep.error("CHANGELOG.md",
                      f"release {version} is added here but its section cannot be read back",
                      rule="G4")
            continue
        body = breaking_body(section)
        if body is None:
            rep.error("CHANGELOG.md",
                      f"release {version} carries no `### Breaking` section. It is mandatory in "
                      f"every release (ADR-085 §H.27) — 'Breaking: nothing' is an answer and an "
                      f"absent section is not.",
                      rule="G4")
            continue
        if breaking_is_empty(body):
            continue
        added = "\n".join("#" + m.group(1) for m in
                          (ADDED_HEADING.match(l) for l in migration_diff.splitlines()) if m)
        if not (migration_covers(added, version)
                or ("MIGRATION.md" in changed and migration_covers(migration, version))):
            rep.error("MIGRATION.md",
                      f"release {version} has a non-empty `### Breaking` section and this pull "
                      f"request adds no MIGRATION.md section naming it. A release with a non-empty "
                      f"Breaking section gains one before its tag (changelog-conventions.md rule "
                      f"8): the changelog says what moved, the migration guide says what to type.",
                      rule="G3")


# ---------------------------------------------------------------------- locators

def locate_policy_relaxation(root: str, base: str, changed: set[str], rep: Report) -> None:
    """R4 — `bundle/policies/` may only restrict. These are the lines this change removed."""
    for path in sorted(p for p in changed if p.startswith("bundle/policies/")):
        removed = [l[1:].strip() for l in diff_of(root, base, path).splitlines()
                   if l.startswith("-") and not l.startswith("---") and l[1:].strip()]
        if removed:
            rep.warning(path,
                        f"{len(removed)} line(s) removed or rewritten. A policy may restrict "
                        f"further and never relax, and a relaxation here relaxes twenty "
                        f"repositories at once. First: {removed[0][:120]}",
                        rule="R4")


def locate_rule_without_case(changed: set[str], rep: Report) -> None:
    """R5 — a rule arrives with a case that can fail it."""
    touched = sorted(p for p in changed
                     if p.endswith(".py") and any(p.startswith(e) for e in EXECUTABLE))
    if touched and not any(p.startswith("tests/") for p in changed):
        rep.warning(touched[0],
                    f"{len(touched)} executable file(s) changed and nothing under tests/ did. "
                    f"A rule nothing can fail on is not enforced, it is described.",
                    rule="R5")


def locate_adapter_change(changed: set[str], rep: Report) -> None:
    """R7 — an adapter is written from the runtime's documentation, never from memory."""
    for path in sorted(p for p in changed if p.startswith("tools/adapters/")):
        rep.warning(path,
                    "a vendor mapping changed. A guessed tool name silently grants or withholds a "
                    "capability, so the pull request cites what it was read from.",
                    rule="R7")


def locate_publication_surface(changed: set[str], rep: Report) -> None:
    """R8 — publishing is human-in-the-loop, and this is the surface that can stop being."""
    for path in sorted(p for p in changed if p in PUBLICATION):
        rep.warning(path,
                    "the publication surface changed. A published version is what twenty "
                    "repositories pin against and cannot be recalled from a checkout that already "
                    "vendored it.",
                    rule="R8")


def locate_standing_debt(root: str, rep: Report) -> None:
    """The releases the forward-only gate cannot reach. Tree-scoped, so it needs no base."""
    text, migration = read(root, "CHANGELOG.md"), read(root, "MIGRATION.md")
    for version in released_versions(text):
        section = release_section(text, version)
        if section is None:
            continue
        body = breaking_body(section)
        if body is None:
            rep.warning("CHANGELOG.md",
                        f"released {version} carries no `### Breaking` section (ADR-085 §H.27 "
                        f"makes it mandatory). Standing debt: history, not this change.",
                        rule="debt")
        elif not breaking_is_empty(body) and not migration_covers(migration, version):
            rep.warning("MIGRATION.md",
                        f"released {version} has a non-empty `### Breaking` section and no "
                        f"MIGRATION.md section names it. Standing debt: history, not this change.",
                        rule="debt")


def locate_version_coherence(root: str, rep: Report) -> None:
    """`package.json` and the newest release heading are one fact, and `release.yml` asserts it at
    the tag. Here it is evidence: a mismatch on a branch is normal mid-release and tells a reader
    which half of the release is done."""
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
            pkg = json.load(fh).get("version")
    except (OSError, ValueError):
        return
    newest = next(iter(released_versions(read(root, "CHANGELOG.md"))), None)
    if pkg and newest and pkg != newest:
        rep.warning("package.json",
                    f"package.json is {pkg} and the newest CHANGELOG release is {newest}. "
                    f"`release.yml` refuses a tag where these disagree.",
                    rule="version")


# --------------------------------------------------------------------------- CLI

def run_gate(root: str, base: str | None, rep: Report) -> None:
    gate_bases_are_open(root, rep)
    if base is None:
        print("::notice::contract_check: no base ref resolved — G1, G3 and G4 are diff-scoped and "
              "DID NOT RUN. G2 is tree-scoped and did.")
        return
    changed = changed_paths(root, base)
    gate_bundle_carries_changelog(changed, rep)
    gate_release_sections(root, changed, diff_of(root, base, "CHANGELOG.md"),
                          diff_of(root, base, "MIGRATION.md"), rep)


def run_locate(root: str, base: str | None, rep: Report) -> None:
    locate_standing_debt(root, rep)
    locate_version_coherence(root, rep)
    if base is None:
        print("::notice::contract_check: no base ref resolved — the diff-scoped locators "
              "(R4, R5, R7, R8) DID NOT RUN.")
        return
    changed = changed_paths(root, base)
    rep.checked += len(changed)
    locate_policy_relaxation(root, base, changed, rep)
    locate_rule_without_case(changed, rep)
    locate_adapter_change(changed, rep)
    locate_publication_surface(changed, rep)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("gate", "locate"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default=None,
                    help="ref to diff against; falls back to GUARDRAILS_BASE, origin/<base branch>, origin/main")
    a = ap.parse_args(argv)
    base = resolve_base(a.root, a.base)
    rep = Report(name=f"contract_check ({a.mode})")
    if a.mode == "gate":
        run_gate(a.root, base, rep)
        return rep.emit()
    run_locate(a.root, base, rep)
    rep.emit()
    # A locator exits non-zero when it FINDS something. That is evidence for the reviewer, never a
    # verdict — `docs-review.yml` runs it with `set +e` and hands the code over in the file.
    return 1 if rep.findings else 0


if __name__ == "__main__":
    sys.exit(main())
