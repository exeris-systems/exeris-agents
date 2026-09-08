"""Shared helpers for the Exeris docs-guardrails validators (ADR-085 §J).

Dependency-free except PyYAML. Every validator:
  * emits GitHub workflow annotations (::error / ::warning) so findings show on the PR diff,
  * appends a Markdown table to $GITHUB_STEP_SUMMARY when set,
  * exits 1 only on errors (warnings never fail a build).
"""
from __future__ import annotations
import os, re, subprocess, sys
from dataclasses import dataclass, field

# Organisation repositories whose content is enterprise-private under ADR-020. A public file must never
# link into these. The set names GitHub repositories only: local working directories are out of scope
# here, because nothing that never reaches a remote can be linked to from a published document, and this
# file ships in a public repository where the set itself would otherwise disclose them.
PRIVATE_REPOS = {
    "exeris-kernel-enterprise", "exeris-benchmarks-enterprise", "exeris-enterprise-observability",
    "exeris-telemetry-spec",
}

DOC_TYPES = {
    "adr", "adr-link", "rfc", "research", "design-note", "subsystem", "module", "tutorial", "howto",
    "reference", "explanation", "operations", "release-notes", "changelog", "roadmap",
    "benchmark-report", "claims", "methodology", "refactor-note", "working-note", "migration-guide",
}
STATUSES = {"draft", "active", "stale", "superseded", "retracted"}
VISIBILITY = {"public", "enterprise-private"}

ADR_FILE = re.compile(r"^ADR-(\d{3})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ADR_LINK = re.compile(r"^ADR-(\d{3})\.link\.md$")
RFC_FILE = re.compile(r"^RFC-\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
RESEARCH_FILE = re.compile(r"^RESEARCH-\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


@dataclass
class Finding:
    level: str          # "error" | "warning"
    path: str
    msg: str
    line: int = 1
    rule: str = ""


@dataclass
class Report:
    name: str
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    def error(self, path, msg, line=1, rule=""):
        self.findings.append(Finding("error", path, msg, line, rule))

    def warning(self, path, msg, line=1, rule=""):
        self.findings.append(Finding("warning", path, msg, line, rule))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]

    def emit(self) -> int:
        for f in self.findings:
            print(f"::{f.level} file={f.path},line={f.line},title={self.name}{(' ' + f.rule) if f.rule else ''}::{f.msg}")
        summary = [f"## {self.name}", "",
                   f"Checked **{self.checked}** files — **{len(self.errors)} errors**, {len(self.warnings)} warnings.", ""]
        if self.findings:
            summary += ["| Level | File | Line | Rule | Message |", "|:--|:--|--:|:--|:--|"]
            for f in sorted(self.findings, key=lambda x: (x.level != "error", x.path, x.line)):
                summary.append(f"| {f.level} | `{f.path}` | {f.line} | {f.rule} | {f.msg} |")
        text = "\n".join(summary) + "\n"
        step = os.environ.get("GITHUB_STEP_SUMMARY")
        if step:
            with open(step, "a", encoding="utf-8") as fh:
                fh.write(text)
        else:
            print(text)
        return 1 if self.errors else 0


def read_frontmatter(path: str):
    """Return (dict | None, body_start_line). None when the file has no leading '---' block."""
    import yaml
    try:
        text = open(path, encoding="utf-8").read()
    except UnicodeDecodeError:
        return None, 1
    if not text.startswith("---\n"):
        return None, 1
    end = text.find("\n---", 4)
    if end < 0:
        return None, 1
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {"__invalid__": True}, 1
    return data, text[:end].count("\n") + 2


def changed_files(base: str | None) -> set[str] | None:
    """Files changed vs base ref (for ramp mode). None when no base is given."""
    if not base:
        return None
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True)
    if out.returncode != 0:
        out = subprocess.run(["git", "diff", "--name-only", base], capture_output=True, text=True)
    return {l.strip() for l in out.stdout.splitlines() if l.strip()}


# Documentation is every Markdown file in the repository except the four kinds below. The default
# lint root is the repository, so what is *not* documentation has to be named here rather than left
# out of an opt-in path list — a path forgotten from such a list is silently unchecked, which is how
# exeris-docs/standards/ went unlinted while the standard it holds was being made binding.
GENERATED_DIRS = ("node_modules", "target", "build", "dist", ".docusaurus", ".next", "out")
# The guardrail bundle is checked out into the workspace it lints; .git is not content.
TOOLING_DIRS = (".git", ".guardrails")
# Agent trees carry their own schema (agents-md-schema.md) and are checked by agents_file_check.py.
# A SKILL.md cannot also carry the docs frontmatter without breaking the agent runtime that reads it.
AGENT_DIRS = (".agents", ".claude", ".codex", ".cursor", ".gemini", ".clinerules")
# .github is not wholly an agent tree: it holds CONTRIBUTING.md and the PR template, which are
# documentation, alongside the semantic subdirectories agents_file_check.py governs (its
# SEMANTIC_SUBDIRS). Skip those by path, not the whole provider directory.
SKIP_PATHS = tuple(os.path.join(".github", d) for d in
                   ("agents", "prompts", "skills", "rules", "policies", "instructions"))
# Third-party source vendored into a repository. exeris-kernel-enterprise carries the OpenSSL
# distribution under native-libs/, whose doc/ tree alone is 70+ Markdown files written to somebody
# else's conventions; linting it says nothing about this ecosystem and its findings can never be acted on.
VENDORED_DIRS = ("native-libs", "third-party", "3rdparty", "vendor")
# Local-only working material beyond the "_" prefix (handled below): exeris-kernel keeps untracked
# private drafts in docs/local-only/, and agent runtimes write session state into memories/.
LOCAL_ONLY_DIRS = ("local-only", "memories")

SKIP_DIRS = GENERATED_DIRS + TOOLING_DIRS + AGENT_DIRS + VENDORED_DIRS + LOCAL_ONLY_DIRS
# Links are checked everywhere content is published, agent trees and .github included: a rotted link
# in CONTRIBUTING.md is a rotted link. Only generated output and non-content are skipped.
LINK_SKIP_DIRS = GENERATED_DIRS + TOOLING_DIRS + VENDORED_DIRS


# ADR-085 §F.21d asks a repository to mark generated trees `linguist-generated` in `.gitattributes`
# (tsdoc-conventions.md rule 10 repeats it for TypeScript). That marker is the repository's own
# statement about what it generates, and until now nothing here read it: a committed api-extractor
# report under api/ is not one of the GENERATED_DIRS names, so it arrived as a documentation page
# and was asked for frontmatter that the next `api:accept` run strips — a check the repository
# cannot satisfy twice in a row. The alternative on offer was an `exclude:` in every caller, which
# makes each repo declare what it generates a second time, in a second syntax, and lets the two
# drift.
#
# Asked of git rather than parsed here. Nested `.gitattributes`, negation and precedence are git's
# rules; a second implementation of them would be wrong in a way nobody notices until a generated
# report is linted. `set` and `true` are the two spellings of the marker; `unset` (`-linguist-
# generated`) deliberately puts a file back under the checks.
GENERATED_ATTR_ON = frozenset(("set", "true"))


def linguist_generated(paths, cwd=None) -> set[str]:
    """The subset of `paths` that .gitattributes marks linguist-generated.

    Empty when git is unavailable or this is not a work tree — the directory taxonomy above still
    applies, so an absent marker under-skips rather than over-skips. A checker that silently
    skipped everything on a missing git would be worse than one that checks too much.
    """
    paths = list(paths)
    if not paths:
        return set()
    try:
        out = subprocess.run(["git", "check-attr", "-z", "--stdin", "linguist-generated"],
                             input="\0".join(paths) + "\0", capture_output=True, text=True, cwd=cwd)
    except (OSError, ValueError):
        return set()
    if out.returncode != 0:
        return set()
    f = out.stdout.split("\0")
    return {f[i] for i in range(0, len(f) - 2, 3) if f[i + 2] in GENERATED_ATTR_ON}


def _walk_md_all(root: str, skip=SKIP_DIRS, skip_paths=SKIP_PATHS):
    for d, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in skip and not x.startswith("_")]  # _inventory, _research, _org-github are exempt
        rel = os.path.relpath(d, root)
        if skip_paths and any(rel == p or rel.startswith(p + os.sep) for p in skip_paths):
            continue
        for f in fn:
            if f.endswith(".md"):
                yield os.path.join(d, f)


def _generated_split(root: str, found: list[str]) -> set[str]:
    """Which of `found` git calls generated. Paths go to git the way git can resolve them."""
    if os.path.isabs(root):
        cwd, asked = root, {os.path.relpath(p, root): p for p in found}
    else:
        cwd, asked = None, {os.path.normpath(p): p for p in found}
    return {asked[k] for k in linguist_generated(asked.keys(), cwd=cwd) if k in asked}


def generated_md(root: str, skip=SKIP_DIRS, skip_paths=SKIP_PATHS) -> set[str]:
    """Markdown files the directory taxonomy admits but `.gitattributes` calls generated.

    lint_globs.py needs these by name: markdownlint takes a glob list, and "whatever git says"
    is not expressible as a glob.
    """
    return _generated_split(root, list(_walk_md_all(root, skip, skip_paths)))


def walk_md(root: str, skip=SKIP_DIRS, skip_paths=SKIP_PATHS):
    found = list(_walk_md_all(root, skip, skip_paths))
    generated = _generated_split(root, found)
    for p in found:
        if p not in generated:
            yield p


def repo_name() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1] or os.path.basename(os.getcwd())
