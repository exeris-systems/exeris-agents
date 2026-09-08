"""Minimal reporting helpers for the agent-layer tools.

Deliberately small. This file used to be a verbatim copy of the docs-guardrails `_common.py` from
`exeris-systems/.github`: 216 lines of which two names were ever imported, shipped in the published
npm tarball, and — worse than the dead weight — a second copy of a file another repository owns,
free to drift from it. What is needed here is a reporter and a frontmatter reader.

`path_prefix` exists because GitHub resolves `::error file=…` against GITHUB_WORKSPACE, while the
checker chdirs into the tree it is checking. Without it, a run against a second checked-out
repository annotates same-named files in the wrong one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


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
    path_prefix: str = ""

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

    def _annotate(self, path: str) -> str:
        return os.path.join(self.path_prefix, path) if self.path_prefix else path

    def emit(self) -> int:
        for f in self.findings:
            rule = (" " + f.rule) if f.rule else ""
            print(f"::{f.level} file={self._annotate(f.path)},line={f.line},"
                  f"title={self.name}{rule}::{f.msg}")
        summary = [f"## {self.name}", "",
                   f"Checked **{self.checked}** files — **{len(self.errors)} errors**, "
                   f"{len(self.warnings)} warnings.", ""]
        if self.findings:
            summary += ["| Level | File | Line | Rule | Message |", "|:--|:--|--:|:--|:--|"]
            for f in sorted(self.findings, key=lambda x: (x.level != "error", x.path, x.line)):
                summary.append(f"| {f.level} | `{self._annotate(f.path)}` | {f.line} | "
                               f"{f.rule} | {f.msg} |")
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
    except (UnicodeDecodeError, OSError):
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
