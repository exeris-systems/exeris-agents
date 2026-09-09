#!/usr/bin/env python3
"""Regression tests for two defects a consuming repository hits and this repository does not.

Both shipped in 1.1.0, both are silent, and both were found by running the tools against a second
consumer rather than by reading them:

  1. `provider-owned` in the mapping spelling rule 7 requires — `{path: …, generated-region: …}` —
     was read with `set(...)`, which raises TypeError on an unhashable dict inside a bare
     `except: pass`. One mapping entry discarded the WHOLE list, every plain string included, and
     the adapter check then reported provider-owned operational files as unmarked semantics.

  2. The eval runner resolved `schema_dir` and `fixture_dir` against ITSELF. That is right only
     while the runner sits at `.agents/evals/`; vendored it sits at
     `.agents/vendor/<bundle>-<v>/evals/`, so the documented defaults pointed at the bundle's base
     schemas and at a fixtures directory the vendored tree does not have. Not one case resolved,
     in any consumer, with the values the schema documents.

Run: python3 tests/test_consumer_paths.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "tools", "agents_file_check.py")
RUNNER = os.path.join(ROOT, "bundle", "evals", "run.py")

FAILURES: list[str] = []
PASSES = 0


def check(name: str, got, want) -> None:
    global PASSES
    if got == want:
        PASSES += 1
    else:
        FAILURES.append(f"{name}\n      expected {want!r}\n      got      {got!r}")


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ── 1. provider-owned ────────────────────────────────────────────────────────────────────────────

def load_provider_owned(manifest_body: str) -> set[str]:
    """Call the checker's own helper with a temporary repository as the working directory."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("afc", CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tempfile.mkdtemp(prefix="po-")
    try:
        write(os.path.join(d, ".agents", "manifest.yaml"), manifest_body)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            return mod.provider_owned_paths()
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(d)


def test_plain_strings_survive_a_mapping_entry():
    owned = load_provider_owned(
        "version: 2\n"
        "provider-owned:\n"
        "  - .claude/settings.local.json\n"
        "  - .github/workflows\n"
        "  - path: .claude/settings.json\n"
        "    generated-region: hooks\n"
    )
    check("mapping entry contributes its path",
          ".claude/settings.json" in owned, True)
    check("plain strings are not discarded by the mapping entry",
          owned, {".claude/settings.local.json", ".github/workflows", ".claude/settings.json"})


def test_a_list_of_only_strings_still_works():
    owned = load_provider_owned("version: 2\nprovider-owned: [a/b, c]\n")
    check("string-only list", owned, {"a/b", "c"})


def test_absent_key_is_empty_not_an_error():
    check("no provider-owned key", load_provider_owned("version: 2\n"), set())


def test_a_malformed_entry_is_skipped_not_fatal():
    owned = load_provider_owned("version: 2\nprovider-owned:\n  - 5\n  - keep/this\n")
    check("an entry that is neither a string nor a {path: …} mapping is skipped",
          owned, {"keep/this"})


def test_end_to_end_the_check_honours_a_list_carrying_a_mapping():
    """The behaviour, not the helper: a mapping entry must not resurrect findings against the
    plain-string entries beside it. On 1.1.0 this reports `.claude/skills/` as provider-authored
    semantics, because the whole list was discarded before it was consulted."""
    d = tempfile.mkdtemp(prefix="e2e-")
    try:
        write(os.path.join(d, "AGENTS.md"), "# x\n\nPoints at `.agents/` for the semantics.\n")
        write(os.path.join(d, ".agents", "manifest.yaml"),
              "version: 1\nrepository: t\nimports: []\n"
              "provider-owned:\n"
              "  - .claude/skills\n"
              "  - path: .claude/settings.json\n"
              "    generated-region: hooks\n")
        # Unmarked, and would be a finding if the list were not honoured.
        write(os.path.join(d, ".claude", "skills", "s", "SKILL.md"),
              "---\nname: s\ndescription: d\n---\n\nbody\n")
        p = subprocess.run([sys.executable, CHECK, "--root", d, "--strict-adapters"],
                           capture_output=True, text=True)
        check("a provider-owned directory listed beside a mapping entry is still exempt",
              ("provider-authored" in p.stdout or "no generated-from marker" in p.stdout), False)
    finally:
        shutil.rmtree(d)


# ── 2. eval runner paths ─────────────────────────────────────────────────────────────────────────

SCENARIOS = """\
version: 1
defaults:
  schema_dir: ../schemas
  fixture_dir: fixtures
cases:
  - id: only-case
    agent: some-agent
    fixture: f.md
    prompt: "go"
    expect:
      schema: verdict.schema.json
"""


def vendored_consumer() -> str:
    """A consumer laid out the way `agents_bundle.py vendor` lays one out."""
    d = tempfile.mkdtemp(prefix="consumer-")
    os.makedirs(os.path.join(d, ".git"))
    write(os.path.join(d, ".agents", "evals", "scenarios.yaml"), SCENARIOS)
    write(os.path.join(d, ".agents", "evals", "fixtures", "f.md"), "a fixture\n")
    write(os.path.join(d, ".agents", "schemas", "verdict.schema.json"),
          json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
                      "type": "object"}))
    vendor = os.path.join(d, ".agents", "vendor", "exeris-agents-1.1.0", "evals")
    os.makedirs(vendor)
    shutil.copy(RUNNER, os.path.join(vendor, "run.py"))
    # The base schemas the broken resolution used to land on, so the test distinguishes
    # "resolved correctly" from "resolved onto whatever happened to exist".
    write(os.path.join(d, ".agents", "vendor", "exeris-agents-1.1.0", "schemas",
                       "verdict.base.schema.json"), json.dumps({"type": "object"}))
    return d


def run_dry(repo: str, *args: str) -> subprocess.CompletedProcess:
    runner = os.path.join(repo, ".agents", "vendor", "exeris-agents-1.1.0", "evals", "run.py")
    report = os.path.join(repo, "report.json")
    return subprocess.run([sys.executable, runner, "--dry-run", "--report", report, *args],
                          capture_output=True, text=True, cwd=repo)


def test_vendored_runner_finds_the_repositorys_scenarios():
    r = vendored_consumer()
    try:
        p = run_dry(r)
        check("the default --scenarios is the repository's, not the vendored tree's",
              (p.returncode, "only-case" in p.stdout), (0, True))
    finally:
        shutil.rmtree(r)


def test_defaults_resolve_against_the_scenarios_file():
    r = vendored_consumer()
    try:
        p = run_dry(r, "--scenarios", os.path.join(".agents", "evals", "scenarios.yaml"))
        check("documented defaults resolve to .agents/schemas and .agents/evals/fixtures",
              (p.returncode, "ok    only-case" in p.stdout), (0, True))
        # It must be the REPOSITORY's schema, not the base it used to fall onto.
        report = json.load(open(os.path.join(r, "report.json"), encoding="utf-8"))
        check("one case, resolved", len(report["cases"]), 1)
        check("case resolved rather than errored", report["cases"][0]["status"], "resolved")
    finally:
        shutil.rmtree(r)


def test_a_missing_fixture_is_still_an_error():
    """The fix must not turn every path into 'found something'."""
    r = vendored_consumer()
    try:
        os.remove(os.path.join(r, ".agents", "evals", "fixtures", "f.md"))
        p = run_dry(r)
        check("a genuinely missing fixture still fails", p.returncode != 0, True)
    finally:
        shutil.rmtree(r)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as exc:  # a broken test is a failure, not a skip
            FAILURES.append(f"{t.__name__} raised {type(exc).__name__}: {exc}")
    print(f"{PASSES} assertions passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL  {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
