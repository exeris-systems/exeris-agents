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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main  # noqa: E402  (after the sys.path line it needs)


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ── 1. provider-owned ────────────────────────────────────────────────────────────────────────────

_CHECKER = None


def checker_module():
    """Load and execute the checker once. Re-executing it per call also re-ran its imports and
    appended to `sys.path` each time."""
    global _CHECKER
    if _CHECKER is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("afc", CHECK)
        _CHECKER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CHECKER)
    return _CHECKER


def load_provider_owned(manifest_body: str) -> set[str]:
    """Call the checker's own helper with a temporary repository as the working directory."""
    mod = checker_module()
    d = tempfile.mkdtemp(prefix="po-")
    try:
        write(os.path.join(d, ".agents", "manifest.yaml"), manifest_body)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            import yaml
            manifest = yaml.safe_load(open(os.path.join(".agents", "manifest.yaml"),
                                           encoding="utf-8")) or {}
            return mod.provider_owned_paths(manifest)
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
    # One assertion, not two: an equality over the whole set already says the mapping entry
    # contributed its path, so the membership check could never fail independently of it.
    check("the mapping entry contributes its path and discards no plain string",
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
        # Asserting only the ABSENCE of a string passes on an empty directory, on a crash, on any
        # run that never reached the adapter check at all — which is the vacuous-green shape this
        # suite exists to catch, reproduced inside the suite. Assert that the checker ran to
        # completion and produced its report, then that the exemption held.
        check("the checker ran to completion", p.returncode in (0, 1), True)
        check("and reached its report", "agents_file_check" in p.stdout, True)
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


def test_a_path_leaving_the_checkout_is_refused():
    """The guard the three `open()` sinks share: a scenarios file, a `schema_dir` or a fixture that
    resolves outside the repository is a typo or an escape, and either way not something the runner
    should read."""
    r = vendored_consumer()
    try:
        write(os.path.join(r, ".agents", "evals", "escape.yaml"),
              SCENARIOS.replace("schema_dir: ../schemas", "schema_dir: ../../../../../etc"))
        p = run_dry(r, "--scenarios", os.path.join(".agents", "evals", "escape.yaml"))
        check("a schema_dir outside the checkout is refused",
              (p.returncode != 0, "outside the repository" in (p.stderr + p.stdout)), (True, True))
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



# ── 3. every path sink, in both directions ───────────────────────────────────────────────────────

def test_scenarios_outside_the_repo_is_refused_before_it_is_read():
    """The guard used to run seven lines after load_yaml(), so the read it exists for happened."""
    r = vendored_consumer()
    outside = tempfile.mkdtemp(prefix="outside-")
    bad = os.path.join(outside, "not-yaml.yaml")
    write(bad, "to: [nie: jest: poprawny: yaml\n")
    try:
        p = run_dry(r, "--scenarios", bad)
        check("refused, and by the guard rather than by the YAML parser",
              ("resolves outside the repository" in p.stdout + p.stderr,
               "yaml" in p.stderr.lower() and "scanner" in p.stderr.lower()),
              (True, False))
    finally:
        shutil.rmtree(r); shutil.rmtree(outside)


def test_report_outside_the_repo_is_refused():
    """The only WRITE sink, and it was the one left unguarded."""
    r = vendored_consumer()
    outside = tempfile.mkdtemp(prefix="outside-")
    target = os.path.join(outside, "deep", "report.json")
    try:
        runner = os.path.join(r, ".agents", "vendor", "exeris-agents-1.1.0", "evals", "run.py")
        p = subprocess.run([sys.executable, runner, "--dry-run", "--report", target,
                            "--scenarios", os.path.join(".agents", "evals", "scenarios.yaml")],
                           capture_output=True, text=True, cwd=r)
        check("--report outside the checkout is refused",
              "resolves outside the repository" in p.stdout + p.stderr, True)
        check("and nothing was created there", os.path.exists(os.path.dirname(target)), False)
    finally:
        shutil.rmtree(r); shutil.rmtree(outside)


def test_a_case_naming_no_schema_is_an_error_not_ok():
    r = vendored_consumer()
    try:
        path = os.path.join(r, ".agents", "evals", "scenarios.yaml")
        write(path, open(path, encoding="utf-8").read() +
              "  - id: no-schema\n    agent: a\n    fixture: f.md\n"
              "    prompt: p\n    expect: {fields: {x: y}}\n")
        p = run_dry(r, "--scenarios", os.path.join(".agents", "evals", "scenarios.yaml"))
        check("a case with no expect.schema does not resolve to the schema DIRECTORY",
              ("ERROR no-schema" in p.stdout, "ok    no-schema" in p.stdout), (True, False))
    finally:
        shutil.rmtree(r)


def test_a_missing_fixture_does_not_abort_the_run():
    r = vendored_consumer()
    try:
        path = os.path.join(r, ".agents", "evals", "scenarios.yaml")
        write(path, open(path, encoding="utf-8").read() +
              "  - id: ghost-fixture\n    agent: a\n    fixture: nope.md\n"
              "    prompt: p\n    expect: {schema: verdict.schema.json}\n")
        p = run_dry(r, "--scenarios", os.path.join(".agents", "evals", "scenarios.yaml"))
        check("a missing fixture is recorded, not raised",
              ("Traceback" in p.stderr, "ERROR ghost-fixture" in p.stdout), (False, True))
        check("and the earlier case still reported", "ok    only-case" in p.stdout, True)
    finally:
        shutil.rmtree(r)


def test_the_no_jsonschema_fallback_says_when_it_cannot_validate():
    """A composed schema — an `allOf` of a `$ref` plus enums — carries no top-level `required`, so
    the shallow path validated ZERO fields and returned "valid": a grader silently weakening to
    nothing, which its own docstring calls worse than one that is missing. It now reads the
    `required` that IS reachable, and says so when there is none."""
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("evalrun", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tempfile.mkdtemp(prefix="val-")
    try:
        opaque = os.path.join(d, "opaque.json")
        write(opaque, json.dumps({"allOf": [{"$ref": "base.json"}]}))
        reachable = os.path.join(d, "reachable.json")
        write(reachable, json.dumps({"allOf": [{"$ref": "base.json"},
                                               {"required": ["decision"]}]}))
        real = dict(sys.modules)
        sys.modules["jsonschema"] = None            # force the ImportError branch
        try:
            out = mod.validate({}, opaque)
            check("nothing reachable -> says it cannot validate",
                  bool(out) and "cannot validate" in out[0], True)
            out2 = mod.validate({}, reachable)
            check("a reachable `required` is checked rather than given up on",
                  bool(out2) and "decision" in out2[0], True)
            check("and a conforming instance passes it",
                  mod.validate({"decision": "PASS"}, reachable), [])
        finally:
            sys.modules.clear(); sys.modules.update(real)
    finally:
        shutil.rmtree(d)


def test_a_policy_nothing_composes_is_reported():
    """rule 5's unchecked direction. The forward one — a profile naming a policy that does not
    resolve — is an error. The reverse was invisible: on disk, in the manifest, composed by nobody.

    The manifest must NOT count as a mention. It is where the declaration lives, so counting it
    makes every declared file trivially referenced — measured, with it included an orphan planted
    in a real tree was not reported at all."""
    def tree(compose: bool) -> str:
        d = tempfile.mkdtemp(prefix="orphan-")
        write(os.path.join(d, "AGENTS.md"), "# x\n\nPoints at `.agents/` for the semantics.\n")
        write(os.path.join(d, ".agents", "manifest.yaml"),
              "version: 2\nrepository: t\nimports: []\n"
              "agents: [r]\nskills: []\nworkflows: []\n"
              "policies: [used, orphan]\nreferences: []\n")
        for name in ("used", "orphan"):
            write(os.path.join(d, ".agents", "policies", f"{name}.md"), f"# {name}\n\nbody\n")
        listed = "[used, orphan]" if compose else "[used]"
        write(os.path.join(d, ".agents", "agents", "r", "AGENT.md"),
              "---\nname: r\ndescription: a role that exists so the tree is well-formed and this "
              "case is about composition and nothing else\nrole: reviewer\nmode: read-only\n"
              f"capabilities: [read]\npolicies: {listed}\n---\n\nbody\n")
        return d

    d = tree(compose=False)
    try:
        p = subprocess.run([sys.executable, CHECK, "--root", d], capture_output=True, text=True)
        check("an uncomposed policy is reported", "orphan" in p.stdout, True)
        check("and the one that IS composed is not", p.stdout.count("'used'"), 0)
    finally:
        shutil.rmtree(d)

    d = tree(compose=True)
    try:
        p = subprocess.run([sys.executable, CHECK, "--root", d], capture_output=True, text=True)
        check("composing it clears the finding", "orphan" in p.stdout, False)
    finally:
        shutil.rmtree(d)


def test_tooling_checked_out_into_the_workspace_is_not_the_consumers():
    """docs-lint fetches this bundle into `.agents-tools/` and the organisation guardrails into
    `.guardrails/`, inside the very tree the checker walks. Without them skipped, THIS repository's
    `AGENTS.md` — 4 KB and change — is read as a nested file of whichever consumer is being checked
    and fails the 4 KB nested cap: a finding about a file that is not theirs and that they cannot
    edit. `nested_checkout` does not save it, because the organisation repository's own run rsyncs
    the tree with `--exclude .git` and the marker is gone."""
    d = tempfile.mkdtemp(prefix="tooling-")
    try:
        write(os.path.join(d, "AGENTS.md"), "# x\n\nPoints at `.agents/` for the semantics.\n")
        write(os.path.join(d, ".agents", "manifest.yaml"), "version: 1\nrepository: t\nimports: []\n")
        for tooling in (".agents-tools", ".guardrails"):
            write(os.path.join(d, tooling, "AGENTS.md"), "# not the consumer's\n\n" + ("x " * 3000))
        p = subprocess.run([sys.executable, CHECK, "--root", d], capture_output=True, text=True)
        check("tooling checked out into the workspace is not read as the consumer's",
              (p.returncode, "agents-tools" in p.stdout, "guardrails" in p.stdout),
              (0, False, False))
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
