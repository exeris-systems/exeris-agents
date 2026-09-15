#!/usr/bin/env python3
"""Regression tests for defects a consuming repository hits and this repository does not.

It vendors nothing and grades nothing, so every case here needs a consumer built to find it. The
first two shipped in 1.1.0, both silent, both found by running the tools against a second
consumer rather than by reading them; the grader cases below were found the same way, against a
real composed schema:

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
SCHEMAS = os.path.join(ROOT, "bundle", "schemas")

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


def test_a_base_ref_resolves_beside_the_base_that_names_it():
    """The third path defect, and the one this file's second entry above is the pattern for.

    A base has relative `$ref`s of its own — `handoff.base.schema.json`, beside it in the vendored
    tree — and they were resolved against the COMPOSED schema's directory. That directory is only
    right for the composition's own references. Worse, the join was relative to relative:
    `urljoin("../vendor/<pin>/schemas/verdict.base.schema.json", "handoff.base.schema.json")` is
    `"vendor/<pin>/schemas/handoff.base.schema.json"` — the leading `../` normalised away, a
    directory no repository has. So a verdict carrying a handoff raised `Unresolvable` out of the
    grader, and a grader that raises reports nothing at all: not a failed case, a lost run.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "evalrun_baseref")

        def verdict(handoff):
            return {"agent": "fixture-reviewer", "decision": "PASS", "scope_class": "docs-only",
                    "findings": [], "checks_run": [{"check": "vale", "result": "pass"}],
                    "handoffs": [handoff]}

        good = {"from": "fixture-reviewer", "to": "human", "blocking": True,
                "reason": "the receiving role owns the number"}
        try:
            clean = mod.validate(verdict(good), composed)
            bad = mod.validate(verdict({"from": "fixture-reviewer", "to": "human",
                                        "blocking": True}), composed)
        except Exception as exc:                       # the defect: it raised out of the grader
            check(f"a handoff is graded, not raised on ({type(exc).__name__})", False, True)
            return
        check("a well-formed handoff validates", clean, [])
        check("and a handoff missing `reason` is a graded failure, so the base really applied",
              bool(bad) and "reason" in bad[0], True)
    finally:
        shutil.rmtree(d)


def grader_tree() -> tuple[str, str, str]:
    """(repo, composed schema path, vendored runner path) — a consumer with the real bases."""
    d = tempfile.mkdtemp(prefix="grader-")
    vendor = os.path.join(d, ".agents", "vendor", "exeris-agents-2.0.0")
    os.makedirs(os.path.join(d, ".git"))
    shutil.copytree(SCHEMAS, os.path.join(vendor, "schemas"))
    os.makedirs(os.path.join(vendor, "evals"))
    shutil.copy(RUNNER, os.path.join(vendor, "evals", "run.py"))
    composed = os.path.join(d, ".agents", "schemas", "verdict.schema.json")
    write(composed, json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [{"$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                  {"properties": {"agent": {"enum": ["fixture-reviewer"]}}}],
        "unevaluatedProperties": False}))
    return d, composed, os.path.join(vendor, "evals", "run.py")


def load_runner(path: str, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ABSENT = object()

VERDICT = {"agent": "fixture-reviewer", "decision": "PASS", "scope_class": "docs-only",
           "findings": [], "checks_run": [{"check": "vale", "result": "pass"}]}


def test_a_reference_that_cannot_resolve_is_a_graded_failure():
    """The failure class this release claims to have closed, by its likelier trigger: not a base
    whose neighbour moved, but a `$ref` that names something which is not there. It reached the
    caller as an exception, and `run()` catches nothing around `grade()`, so one bad reference in
    one case ended the whole run with a traceback instead of failing that case."""
    d, composed, runner = grader_tree()
    try:
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [{"$ref": "../vendor/exeris-agents-2.0.0/schemas/gone.base.schema.json"}],
            "unevaluatedProperties": False}))
        mod = load_runner(runner, "evalrun_unresolvable")
        try:
            out = mod.validate(VERDICT, composed)
        except BaseException as exc:            # SystemExit included: the guard exits the process
            check(f"an unresolvable $ref is graded, not raised ({type(exc).__name__})", False, True)
            return
        check("it comes back as a failure the case can carry",
              bool(out) and "resolve" in out[0].lower(), True)
    finally:
        shutil.rmtree(d)


def test_a_grader_that_cannot_build_its_registry_says_so():
    """The fallback rebuilt the validator without the registry and without the location `$id` — the
    two things that make a vendored `$ref` resolve — and returned its verdict as if nothing had
    happened. A grader that quietly weakens is the failure this file has now fixed three times."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "evalrun_noregistry")
        import jsonschema                             # noqa: F401 — see below
        # jsonschema is imported first, deliberately: it imports `referencing` itself, so stubbing
        # that out beforehand would fail jsonschema's own import and exercise the other branch.
        # This passed for the right reason only because the suite happens to run alphabetically.
        was = sys.modules.get("referencing", _ABSENT)
        sys.modules["referencing"] = None            # force the ImportError branch
        try:
            out = mod.validate(VERDICT, composed)
        except BaseException as exc:
            check(f"a grader without a registry answers, it does not raise ({type(exc).__name__})",
                  False, True)
            return
        finally:
            # One entry back, not the whole module table: clearing `sys.modules` to undo a single
            # stub takes every other module's identity with it.
            if was is _ABSENT:
                sys.modules.pop("referencing", None)
            else:
                sys.modules["referencing"] = was
        check("it says it could not validate rather than reporting a clean instance",
              bool(out) and "cannot validate" in out[0], True)
    finally:
        shutil.rmtree(d)


def test_a_case_pointed_at_a_bundle_base_is_refused():
    """From 2.0.0 a base refuses almost nothing on its own, so a case graded against one reports a
    clean run over answers no repository would accept. The runner names it instead."""
    d, composed, runner = grader_tree()
    try:
        write(os.path.join(d, ".agents", "evals", "scenarios.yaml"),
              "version: 1\ndefaults:\n  schema_dir: ../schemas\n  fixture_dir: fixtures\n"
              "cases:\n  - id: graded-against-a-base\n    agent: a\n    prompt: p\n"
              "    expect:\n      schema: "
              "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json\n")
        p = subprocess.run([sys.executable, runner, "--dry-run", "--report",
                            os.path.join(d, "report.json"), "--scenarios",
                            os.path.join(".agents", "evals", "scenarios.yaml")],
                           capture_output=True, text=True, cwd=d)
        check("a case naming a vendored base is an error, not an `ok`",
              ("ok    graded-against-a-base" in p.stdout, "base" in p.stdout.lower()),
              (False, True))
    finally:
        shutil.rmtree(d)


def test_a_composed_schema_that_declares_its_own_id_still_resolves():
    """`located()` supplies the file a schema was read from as its `$id`, which is what makes a
    relative `$ref` into the vendored tree resolve. It declined to do so when the schema already
    declared one — and a repository that gives its schema an `$id`, as JSON Schema invites, then
    has every `$ref` joined onto that identifier instead of onto the file. The failure arrives as a
    missing file, which points the reader at the vendored tree rather than at the `$id`."""
    d, composed, runner = grader_tree()
    try:
        body = json.load(open(composed, encoding="utf-8"))
        write(composed, json.dumps({"$id": "https://exeris.example/schemas/verdict", **body}))
        mod = load_runner(runner, "evalrun_ownid")
        out = mod.validate(VERDICT, composed)
        check("a schema with its own `$id` validates against the vendored base all the same",
              out, [])
    finally:
        shutil.rmtree(d)


def test_a_ref_leaving_the_checkout_fails_the_case_rather_than_the_run():
    """`within_repo()` refuses by exiting the process — right for a CLI argument read once at
    startup, wrong inside a grader, where it takes every later case with it. `except Exception`
    does not catch `SystemExit`, so this was still the failure class `### Fixed` claims to close."""
    d, composed, runner = grader_tree()
    try:
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [{"$ref": "../../../../../../etc/passwd"}]}))
        mod = load_runner(runner, "evalrun_escape")
        try:
            out = mod.validate(VERDICT, composed)
        except BaseException as exc:
            check(f"a $ref out of the checkout is graded, not exited ({type(exc).__name__})",
                  False, True)
            return
        check("and the failure says the reference left the repository",
              bool(out) and "outside the repository" in out[0], True)
    finally:
        shutil.rmtree(d)


def test_a_failure_that_is_not_about_a_path_is_not_reported_as_one():
    """The catch-all told every case the same story — a `$ref` that did not resolve, a path that is
    not there. A grader that misnames what went wrong sends its reader to the wrong file."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "evalrun_mislabel")

        class Exploding(dict):
            def __contains__(self, key): raise RuntimeError("not a path problem at all")

        try:
            out = mod.validate(Exploding(), composed)
        except BaseException as exc:
            check(f"an unexpected failure is caught ({type(exc).__name__})", False, True)
            return
        check("and it is not dressed up as a missing file",
              bool(out) and "path that is not there" not in out[0], True)
    finally:
        shutil.rmtree(d)


def test_an_unparseable_schema_fails_the_case_rather_than_the_run():
    """`json.load` on the composed schema sat outside every guard in `validate()`, and `main()`
    checks that the file exists without ever checking that it parses. Both branches read it — the
    jsonschema one and the shallow fallback — so a repository with one broken schema lost the whole
    run, which is the class three of this release's `### Fixed` entries are about."""
    d, composed, runner = grader_tree()
    try:
        write(composed, "{ this is not json")
        mod = load_runner(runner, "evalrun_unparseable")
        # One case, not two: the read happens before either branch is chosen, so stubbing
        # jsonschema out would exercise the same three lines and assert nothing further.
        try:
            out = mod.validate({}, composed)
        except BaseException as exc:
            check(f"an unparseable schema is graded, not raised ({type(exc).__name__})",
                  False, True)
            return
        check("it says the schema itself will not parse",
              bool(out) and "not readable JSON" in out[0], True)
    finally:
        shutil.rmtree(d)


def test_a_path_that_is_not_there_is_a_typo_not_a_base():
    """The vendored-base refusal ran before the existence check, so a case naming
    `.../verdikt.base.schema.json` was told to name the composed schema instead — advice about the
    wrong problem."""
    d, composed, runner = grader_tree()
    try:
        write(os.path.join(d, ".agents", "evals", "scenarios.yaml"),
              "version: 1\ndefaults:\n  schema_dir: ../schemas\n  fixture_dir: fixtures\n"
              "cases:\n  - id: typo\n    agent: a\n    prompt: p\n    expect:\n      schema: "
              "../vendor/exeris-agents-2.0.0/schemas/verdikt.base.schema.json\n")
        p = subprocess.run([sys.executable, runner, "--dry-run", "--report",
                            os.path.join(d, "report.json"), "--scenarios",
                            os.path.join(".agents", "evals", "scenarios.yaml")],
                           capture_output=True, text=True, cwd=d)
        check("the missing file is what the case is told about",
              ("schema not found" in p.stdout, "names a bundle base" in p.stdout), (True, False))
    finally:
        shutil.rmtree(d)


def test_a_case_schema_outside_checkout_fails_the_case_not_the_run():
    """`expect.schema` resolving outside the repository fails that specific case rather than
    aborting the whole run, matching the behavior of $ref resolution in validate()."""
    d, composed, runner = grader_tree()
    try:
        write(os.path.join(d, ".agents", "evals", "scenarios.yaml"),
              "version: 1\ndefaults:\n  schema_dir: ../schemas\n  fixture_dir: fixtures\n"
              "cases:\n"
              "  - id: escape-case\n    agent: a\n    prompt: p\n"
              "    expect:\n      schema: ../../../../../../etc/passwd\n"
              "  - id: valid-case\n    agent: a\n    prompt: p\n"
              "    expect:\n      schema: verdict.schema.json\n")
        p = subprocess.run([sys.executable, runner, "--dry-run", "--report",
                            os.path.join(d, "report.json"), "--scenarios",
                            os.path.join(".agents", "evals", "scenarios.yaml")],
                           capture_output=True, text=True, cwd=d)
        check("the escaping case fails without aborting the second case",
              ("ERROR escape-case: expect.schema resolves outside repository" in p.stdout,
               "ok    valid-case" in p.stdout), (True, True))
    finally:
        shutil.rmtree(d)


def test_a_case_fixture_outside_checkout_fails_the_case_not_the_run():
    """`fixture` resolving outside the repository fails that specific case rather than
    aborting the whole run via within_repo's SystemExit."""
    d, composed, runner = grader_tree()
    try:
        write(os.path.join(d, ".agents", "evals", "scenarios.yaml"),
              "version: 1\ndefaults:\n  schema_dir: ../schemas\n  fixture_dir: fixtures\n"
              "cases:\n"
              "  - id: escape-fixture\n    agent: a\n    prompt: p\n"
              "    fixture: ../../../../../../etc/passwd\n"
              "    expect:\n      schema: verdict.schema.json\n"
              "  - id: valid-case\n    agent: a\n    prompt: p\n"
              "    expect:\n      schema: verdict.schema.json\n")
        p = subprocess.run([sys.executable, runner, "--dry-run", "--report",
                            os.path.join(d, "report.json"), "--scenarios",
                            os.path.join(".agents", "evals", "scenarios.yaml")],
                           capture_output=True, text=True, cwd=d)
        check("the escaping fixture fails without aborting the second case",
              ("ERROR escape-fixture: fixture resolves outside repository" in p.stdout,
               "ok    valid-case" in p.stdout), (True, True))
    finally:
        shutil.rmtree(d)


def test_eval_grader_filters_unevaluated_properties_artefacts():
    """When a decision fails an enum or required field, unevaluatedProperties reports every field
    as unexpected because the branch failed. The grader filters that artefact out without
    masking genuine unexpected properties across multiple levels."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "evalrun_artefact_filter")
        # Case 1: branch failure generates secondary artefact, which is filtered out
        invalid_verdict = dict(VERDICT)
        invalid_verdict["decision"] = "INVALID_DECISION"
        out = mod.validate(invalid_verdict, composed)
        check("the real failure is reported", any("INVALID_DECISION" in err for err in out), True)
        check("the unevaluatedProperties artefact is filtered out",
              any("Unevaluated properties are not allowed" in err for err in out), False)

        # Case 2: two genuine unevaluatedProperties at different depths are NOT mutually suppressed
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                {"properties": {
                    "agent": {"enum": ["fixture-reviewer"]},
                    "checks_run": {
                        "items": {"unevaluatedProperties": False}
                    }
                }}
            ],
            "unevaluatedProperties": False
        }))
        spurious_verdict = dict(VERDICT)
        spurious_verdict["spurious_root"] = "extra"
        spurious_verdict["checks_run"] = [{"check": "vale", "result": "pass", "spurious_check": 1}]
        out2 = mod.validate(spurious_verdict, composed)
        check("both root and nested unexpected properties are reported",
              (any("spurious_root" in err for err in out2),
               any("spurious_check" in err for err in out2)), (True, True))

        # Case 3: mixed branch error and genuine unexpected property.
        # A branch failure must not mask genuine unexpected properties, nor leak declared artefacts.
        broken_and_spurious = dict(VERDICT)
        broken_and_spurious["decision"] = "INVALID_DECISION"
        broken_and_spurious["spurious_root"] = "extra"
        out3 = mod.validate(broken_and_spurious, composed)
        check("the branch error is reported", any("INVALID_DECISION" in err for err in out3), True)
        check("the genuine unexpected root property is reported despite branch failure",
              any("spurious_root" in err for err in out3), True)
        # This assertion used to say the opposite, and it was green only while the filter read
        # branch failures document-wide: `decision` failing at the ROOT switched the artefact
        # filter on inside `checks_run/0`, an object that had not failed, and the line vanished
        # because the base declares those fields somewhere. It measured the unscoped gate.
        #
        # Measured now, and the same either way: the nested closer here carries NO `$ref`, so
        # nothing is evaluated at `checks_run/0` and the base's own `check` / `result` are
        # unevaluated there. Case 2 above already reports it with no failure anywhere, and with
        # the composition 2.0.0 actually asks for — `items` carrying the `$ref` AND the closer —
        # the line does not exist at all (measured against the real base). So it is not an
        # artefact of a failed branch; it is this composition's own defect, the one
        # `agents_file_check.py`'s closer rule enumerates, and a report that appears only when
        # something unrelated fails is the shape `artefact()` is now scoped against.
        check("a closer carrying no `$ref` reports the base's own fields, either way",
              (any("checks_run/0" in err and "'check'" in err for err in out3),
               any("checks_run/0" in err and "'check'" in err for err in out2)), (True, True))
    finally:
        shutil.rmtree(d)


def test_within_repo_raises_path_escape_error_not_system_exit():
    """`within_repo()` raises a specific `PathEscapeError` instead of invoking `sys.exit()` directly,
    ensuring clean domain error handling across layer boundaries."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_escape_error")
        check("PathEscapeError is defined on runner module", hasattr(mod, "PathEscapeError"), True)
        raised_escape = False
        esc_cls = getattr(mod, "PathEscapeError", ())
        try:
            mod.within_repo("../../../../../../etc/passwd", "test probe")
        except esc_cls:
            raised_escape = True
        except SystemExit:
            raised_escape = False
        check("within_repo raises PathEscapeError instead of SystemExit", raised_escape, True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_find_declared_props_handles_internal_defs():
    """`find_declared_props()` resolves internal `$defs` / JSON pointers, so that schemas using
    `#/$defs/...` (such as handoff.base.schema.json) do not leak declared properties as
    spurious unevaluatedProperties artefacts on branch failure."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_defs")
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "CheckDetail": {
                    "type": "object",
                    "properties": {
                        "check": {"type": "string"},
                        "result": {"enum": ["pass", "fail"]},
                        "detail_text": {"type": "string"}
                    },
                    "unevaluatedProperties": False
                }
            },
            "allOf": [
                {"$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                {"properties": {
                    "agent": {"enum": ["fixture-reviewer"]},
                    "checks_run": {
                        "items": {"$ref": "#/$defs/CheckDetail"}
                    }
                }}
            ],
            "unevaluatedProperties": False
        }))
        # Case A: branch error on root ('INVALID_DECISION') with valid checks_run using $defs
        inst_a = dict(VERDICT)
        inst_a["decision"] = "INVALID_DECISION"
        inst_a["checks_run"] = [{"check": "vale", "result": "pass", "detail_text": "clean"}]
        out_a = mod.validate(inst_a, composed)
        check("branch error is reported", any("INVALID_DECISION" in err for err in out_a), True)
        check("declared properties in $defs are NOT reported as unexpected artefacts",
              any("detail_text" in err for err in out_a), False)
        check("checks_run/0 is clean of unevaluated artefacts",
              any("checks_run/0" in err for err in out_a), False)

        # Case B: branch error + genuine unexpected property inside checks_run[0]
        inst_b = dict(VERDICT)
        inst_b["decision"] = "INVALID_DECISION"
        inst_b["checks_run"] = [{"check": "vale", "result": "pass", "detail_text": "clean", "rogue_check_field": 123}]
        out_b = mod.validate(inst_b, composed)
        check("genuine rogue property inside $defs is reported",
              any("rogue_check_field" in err for err in out_b), True)
        check("declared detail_text is still not reported as unexpected",
              any("detail_text" in err for err in out_b), False)
    finally:
        shutil.rmtree(d)


def test_eval_grader_find_declared_props_external_file_with_fragment():
    """`find_declared_props()` resolves external schema references with URI fragments
    (e.g., `file.json#/$defs/Name`), correctly traversing both the file and the inner pointer."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_ext_fragment")
        helper_path = os.path.join(d, ".agents", "schemas", "helpers.json")
        write(helper_path, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "AuditInfo": {
                    "type": "object",
                    "properties": {
                        "auditor": {"type": "string"},
                        "stamp": {"type": "string"}
                    },
                    "unevaluatedProperties": False
                }
            }
        }))
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                {"properties": {
                    "agent": {"enum": ["fixture-reviewer"]},
                    "audit": {"$ref": "helpers.json#/$defs/AuditInfo"}
                }}
            ],
            "unevaluatedProperties": False
        }))
        inst = dict(VERDICT)
        inst["decision"] = "INVALID_DECISION"
        inst["audit"] = {"auditor": "bot", "stamp": "2026-09-13", "spurious_audit_field": 42}
        out = mod.validate(inst, composed)
        check("branch error reported", any("INVALID_DECISION" in err for err in out), True)
        check("spurious audit field reported", any("spurious_audit_field" in err for err in out), True)
        check("declared auditor field from external fragment is not reported",
              any("'auditor'" in err for err in out), False)
        check("declared stamp field from external fragment is not reported",
              any("'stamp'" in err for err in out), False)
    finally:
        shutil.rmtree(d)


def test_find_declared_props_refuses_escaping_ref():
    """`find_declared_props()` must never read outside the repository when encountering
    an escaping `$ref`."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_escape_introspect")
        escaping_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"$ref": "../../../../../../etc/passwd"},
                {"properties": {"safe_field": {"type": "string"}}}
            ]
        }
        # find_declared_props should not crash and must not access files outside the repo
        props = mod.find_declared_props(escaping_schema, (), os.path.dirname(composed))
        check("safe field within schema is found", "safe_field" in props, True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_diamond_and_circular_ref_handling():
    """`find_declared_props()` properly handles diamond `$ref` dependencies without
    prematurely suppressing subsequent lookups, and safely terminates on circular references."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_diamond_circular")
        shared_path = os.path.join(d, ".agents", "schemas", "shared.json")
        write(shared_path, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"shared_key": {"type": "string"}}
        }))
        diamond_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"properties": {"first": {"$ref": "shared.json"}}},
                {"properties": {"second": {"$ref": "shared.json"}}}
            ]
        }
        props_first = mod.find_declared_props(diamond_schema, ("first",), os.path.dirname(composed))
        props_second = mod.find_declared_props(diamond_schema, ("second",), os.path.dirname(composed))
        check("first branch finds shared_key", "shared_key" in props_first, True)
        check("second branch finds shared_key (no diamond suppression)", "shared_key" in props_second, True)

        # Circular ref
        circ_a = os.path.join(d, ".agents", "schemas", "circ_a.json")
        circ_b = os.path.join(d, ".agents", "schemas", "circ_b.json")
        write(circ_a, json.dumps({"$ref": "circ_b.json", "properties": {"a_prop": {"type": "string"}}}))
        write(circ_b, json.dumps({"$ref": "circ_a.json", "properties": {"b_prop": {"type": "string"}}}))
        circ_props = mod.find_declared_props({"$ref": "circ_a.json"}, (), os.path.dirname(composed))
        check("circular refs terminate and collect properties",
              ("a_prop" in circ_props, "b_prop" in circ_props), (True, True))
    finally:
        shutil.rmtree(d)


def test_eval_grader_refuses_rogue_properties_in_oneof_and_anyof():
    """`validate()` must never silence rogue properties from alternative `oneOf` or `anyOf`
    branches under `unevaluatedProperties: false`."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_oneof_anyof")
        poly_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "oneOf": [
                {"properties": {"type": {"const": "A"}, "variant_a": {"type": "string"}}, "required": ["type", "variant_a"]},
                {"properties": {"type": {"const": "B"}, "variant_b": {"type": "number"}}, "required": ["type", "variant_b"]}
            ],
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(poly_schema))
        # Variant A instance with rogue field from Variant B
        out = mod.validate({"type": "A", "variant_a": "valid", "variant_b": 123}, composed)
        check("rogue property from alternative oneOf branch is refused",
              any("variant_b" in err for err in out), True)

        # anyOf schema
        anyof_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "anyOf": [
                {"properties": {"type": {"const": "base"}, "core": {"type": "string"}}, "required": ["type", "core"]},
                {"properties": {"type": {"const": "extra"}, "extra_feature": {"type": "boolean"}}, "required": ["type", "extra_feature"]}
            ],
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(anyof_schema))
        out_any = mod.validate({"type": "base", "core": "ok", "extra_feature": True}, composed)
        check("rogue property from unsatisfied anyOf branch is refused",
              any("extra_feature" in err for err in out_any), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_recognizes_conditional_then_else_and_dependent_properties():
    """`find_declared_props()` traverses active `then`, `else` and `dependentSchemas` so that properties
    declared in active conditional branches are recognized as declared and not flagged as unevaluated artefacts,
    while properties from inactive branches remain strictly refused."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_conditional_props")
        cond_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                {
                    "if": {"properties": {"decision": {"const": "CONDITIONAL"}}},
                    "then": {"properties": {"conditional_note": {"type": "string"}}},
                    "else": {"properties": {"unconditional_stamp": {"type": "string"}}}
                },
                {
                    "dependentSchemas": {
                        "scope_class": {"properties": {"scope_detail": {"type": "string"}}}
                    }
                }
            ],
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(cond_schema))

        # Case 1: decision is CONDITIONAL with branch failure on root ('agent' invalid).
        # 'then' is active -> conditional_note is declared and not reported as unevaluated artefact.
        # scope_class is present -> scope_detail is declared and not reported.
        # unconditional_stamp from inactive 'else' is rogue and MUST be reported.
        inst_cond = dict(VERDICT)
        inst_cond["agent"] = "INVALID_AGENT"
        inst_cond["decision"] = "CONDITIONAL"
        inst_cond["conditional_note"] = "pending fix"
        inst_cond["unconditional_stamp"] = "2026-09-13"
        inst_cond["scope_detail"] = "deep"
        out_cond = mod.validate(inst_cond, composed)
        check("branch failure is reported", any("INVALID_AGENT" in err for err in out_cond), True)
        check("declared conditional_note from active 'then' is not reported as unevaluated artefact",
              any("conditional_note" in err for err in out_cond), False)
        check("declared scope_detail from active 'dependentSchemas' is not reported as unevaluated artefact",
              any("scope_detail" in err for err in out_cond), False)
        check("rogue unconditional_stamp from inactive 'else' is reported as unexpected",
              any("unconditional_stamp" in err for err in out_cond), True)

        # Case 2: decision is PASS with branch failure on root ('agent' invalid).
        # 'else' is active -> unconditional_stamp is declared.
        # conditional_note from inactive 'then' is rogue and MUST be reported.
        inst_pass = dict(VERDICT)
        inst_pass["agent"] = "INVALID_AGENT"
        inst_pass["decision"] = "PASS"
        inst_pass["conditional_note"] = "pending fix"
        inst_pass["unconditional_stamp"] = "2026-09-13"
        inst_pass["scope_detail"] = "deep"
        out_pass = mod.validate(inst_pass, composed)
        check("branch failure is reported on PASS case", any("INVALID_AGENT" in err for err in out_pass), True)
        check("declared unconditional_stamp from active 'else' is not reported as unevaluated artefact",
              any("unconditional_stamp" in err for err in out_pass), False)
        check("rogue conditional_note from inactive 'then' is reported as unexpected",
              any("conditional_note" in err for err in out_pass), True)
    finally:
        shutil.rmtree(d)


def test_resolve_pointer_rfc6901_percent_encoded_tilde():
    """`resolve_pointer()` conforms to RFC 6901 §6 by evaluating percent-encoding before unescaping ~1 and ~0."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_percent_tilde")
        doc = {
            "$defs": {
                "slash/key": {"properties": {"val1": {"type": "string"}}},
                "tilde~key": {"properties": {"val2": {"type": "string"}}}
            }
        }
        res_slash = mod.resolve_pointer(doc, "#/$defs/slash%7E1key")
        check("percent-encoded tilde with 1 (%7E1) resolves to slash",
              isinstance(res_slash, dict) and "val1" in res_slash.get("properties", {}), True)
        res_tilde = mod.resolve_pointer(doc, "#/$defs/tilde%7E0key")
        check("percent-encoded tilde with 0 (%7E0) resolves to tilde",
              isinstance(res_tilde, dict) and "val2" in res_tilde.get("properties", {}), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_dynamic_ref_declared_props():
    """`find_declared_props()` resolves `$dynamicRef` references just like `$ref`, preventing
    declared properties from leaking as unevaluated artefacts on branch failure."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_dynamic_ref")
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"$dynamicRef": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json"},
                {"properties": {"agent": {"enum": ["fixture-reviewer"]}}}
            ],
            "unevaluatedProperties": False
        }))
        inst = dict(VERDICT)
        inst["decision"] = "INVALID_DECISION"
        out = mod.validate(inst, composed)
        check("branch failure is reported", any("INVALID_DECISION" in err for err in out), True)
        check("declared properties in $dynamicRef base are not reported as unevaluated artefacts",
              any("'checks_run'" in err or "'decision'" in err or "'findings'" in err for err in out if "Unevaluated" in err), False)
    finally:
        shutil.rmtree(d)


def test_eval_grader_refuses_rogue_property_in_inactive_conditional_then_else():
    """Rogue properties from inactive conditional branches (e.g. 'then' when condition is false,
    or 'else' when condition is true) are strictly refused by unevaluatedProperties."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_rogue_conditional")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [
                {"properties": {"type": {"enum": ["admin", "guest"]}}, "required": ["type"]},
                {
                    "if": {"properties": {"type": {"const": "admin"}}},
                    "then": {"properties": {"admin_token": {"type": "string"}}},
                    "else": {"properties": {"guest_id": {"type": "string"}}}
                }
            ],
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # Case A: type=guest with illegal admin_token (no other error) -> must be refused!
        out_a = mod.validate({"type": "guest", "guest_id": "123", "admin_token": "rogue"}, composed)
        check("rogue admin_token on guest is refused when clean",
              any("admin_token" in err for err in out_a), True)

        # Case B: type=guest with illegal admin_token AND type error on guest_id (int instead of str)
        out_b = mod.validate({"type": "guest", "guest_id": 999, "admin_token": "rogue"}, composed)
        check("guest_id type error is reported", any("guest_id" in err for err in out_b), True)
        check("rogue admin_token on guest is refused even with branch failure",
              any("admin_token" in err for err in out_b), True)

        # Case C: type=admin with illegal guest_id -> must be refused!
        out_c = mod.validate({"type": "admin", "admin_token": "token123", "guest_id": "rogue"}, composed)
        check("rogue guest_id on admin is refused when clean",
              any("guest_id" in err for err in out_c), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_refuses_rogue_property_in_inactive_dependent_schemas():
    """Properties from dependentSchemas are strictly refused when the triggering property is absent."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_rogue_dependent")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"kind": {"type": "string"}, "credit_card": {"type": "string"}},
            "dependentSchemas": {
                "credit_card": {
                    "properties": {"billing_address": {"type": "string"}}
                }
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # Case A: clean instance without credit_card, but carries billing_address -> refused!
        out_a = mod.validate({"kind": "cash", "billing_address": "nowhere"}, composed)
        check("rogue billing_address without trigger is refused when clean",
              any("billing_address" in err for err in out_a), True)

        # Case B: branch error (kind is int) + billing_address -> both refused!
        out_b = mod.validate({"kind": 123, "billing_address": "nowhere"}, composed)
        check("kind type error is reported", any("kind" in err for err in out_b), True)
        check("rogue billing_address without trigger is refused with branch failure",
              any("billing_address" in err for err in out_b), True)

        # Case C: valid trigger present -> billing_address is allowed
        out_c = mod.validate({"kind": "card", "credit_card": "1234", "billing_address": "Main St"}, composed)
        check("valid dependent property with trigger validates clean", out_c, [])
    finally:
        shutil.rmtree(d)


def test_eval_grader_internal_defs_same_dir_no_collision():
    """`find_declared_props()` does not falsely treat identical def names in separate files in the same
    directory as circular references."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_same_dir_defs")
        schemas_dir = os.path.dirname(composed)
        file_a = os.path.join(schemas_dir, "part_a.json")
        file_b = os.path.join(schemas_dir, "part_b.json")
        write(file_a, json.dumps({
            "$defs": {"Common": {"allOf": [{"properties": {"field_a": {"type": "string"}}}, {"$ref": "part_b.json"}]}},
            "$ref": "#/$defs/Common"
        }))
        write(file_b, json.dumps({
            "$defs": {"Common": {"properties": {"field_b": {"type": "string"}}}},
            "$ref": "#/$defs/Common"
        }))
        props = mod.find_declared_props({"$ref": "part_a.json"}, (), schemas_dir)
        check("both field_a and field_b resolved without collision",
              ("field_a" in props, "field_b" in props), (True, True))
    finally:
        shutil.rmtree(d)


def test_resolve_pointer_handles_percent_encoded_fragments():
    """`resolve_pointer()` conforms to RFC 6901 §6 by unescaping percent-encoded characters in URI fragments."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_percent_encoded")
        doc = {
            "$defs": {
                "Special Key": {"properties": {"spaced_prop": {"type": "string"}}},
                "Slash/Key": {"properties": {"slashed_prop": {"type": "string"}}}
            }
        }
        res_space = mod.resolve_pointer(doc, "#/$defs/Special%20Key")
        check("percent-encoded space is unescaped", isinstance(res_space, dict) and "spaced_prop" in res_space.get("properties", {}), True)
        res_slash = mod.resolve_pointer(doc, "#/$defs/Slash~1Key")
        check("escaped slash ~1 is unescaped", isinstance(res_slash, dict) and "slashed_prop" in res_slash.get("properties", {}), True)
    finally:
        shutil.rmtree(d)



def test_eval_grader_property_names_with_commas_and_special_chars():
    """Rogue properties containing commas or special characters are preserved accurately
    without being split into spurious tokens."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_special_chars")
        inst = dict(VERDICT)
        inst["decision"] = "INVALID_DECISION"
        inst["bad,prop,name"] = "test"
        out = mod.validate(inst, composed)
        check("branch failure is reported", any("INVALID_DECISION" in err for err in out), True)
        check("rogue property with commas is reported whole",
              any("'bad,prop,name'" in err for err in out), True)
        check("rogue property was not split into fragments",
              any("'name'" in err for err in out), False)
    finally:
        shutil.rmtree(d)


def test_within_repo_and_validate_path_escape_integration():
    """`validate()` gracefully intercepts `PathEscapeError` caused by relative or `file:` `$ref`s
    resolving outside the repository, and symlinks escaping the checkout are refused."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_escape_integ")
        # Relative escape in $ref
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [{"$ref": "../../../../../../etc/passwd"}]
        }))
        out_rel = mod.validate(VERDICT, composed)
        check("relative escaping $ref produces clean refusal message",
              bool(out_rel) and "resolved outside the repository and was refused" in out_rel[0], True)

        # file:// escape in $ref
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "allOf": [{"$ref": "file:///etc/passwd"}]
        }))
        out_file = mod.validate(VERDICT, composed)
        check("file:// escaping $ref produces clean refusal message",
              bool(out_file) and "resolved outside the repository and was refused" in out_file[0], True)

        # Symlink escaping the checkout
        symlink_path = os.path.join(d, "escape_link")
        try:
            os.symlink("/etc", symlink_path)
            raised_symlink = False
            try:
                mod.within_repo(symlink_path, "symlink escape probe")
            except mod.PathEscapeError:
                raised_symlink = True
            check("within_repo refuses escaping symlink", raised_symlink, True)
        except OSError:
            pass  # Filesystem doesn't permit symlinks
    finally:
        shutil.rmtree(d)


def test_eval_grader_nested_conditional_properties_recognized():
    """`find_declared_props()` properly passes instance context down nested paths so that
    conditional then/else properties and dependentSchemas inside array items or nested objects
    are recognized as declared rather than leaked as unevaluated artefacts on branch failure."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_nested_cond")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "items_list": {
                    "type": "array",
                    "items": {
                        "properties": {"type": {"type": "string"}, "status": {"type": "string"}},
                        "required": ["type"],
                        "if": {"properties": {"type": {"const": "audit"}}, "required": ["type"]},
                        "then": {"properties": {"auditor": {"type": "string"}}},
                        "else": {"properties": {"guest_token": {"type": "string"}}},
                        "dependentSchemas": {
                            "auditor": {"properties": {"stamp": {"type": "string"}}}
                        },
                        "unevaluatedProperties": False
                    }
                }
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # Case A: type=audit (active then), auditor present (active dependentSchema for stamp),
        # with branch type error on status (int instead of str).
        # Both auditor and stamp must be recognized as declared and NOT reported as unevaluated!
        inst_a = {
            "items_list": [
                {"type": "audit", "status": 999, "auditor": "Alice", "stamp": "2026-09-14"}
            ]
        }
        out_a = mod.validate(inst_a, composed)
        check("nested branch error (status type) is reported", any("status" in err for err in out_a), True)
        check("declared auditor from nested active 'then' is not reported as unevaluated artefact",
              any("auditor" in err for err in out_a), False)
        check("declared stamp from nested active dependentSchema is not reported as unevaluated artefact",
              any("stamp" in err for err in out_a), False)

        # Case B: type=audit with rogue guest_token from inactive 'else'
        inst_b = {
            "items_list": [
                {"type": "audit", "status": 999, "auditor": "Alice", "guest_token": "rogue"}
            ]
        }
        out_b = mod.validate(inst_b, composed)
        check("rogue guest_token from inactive else is refused", any("guest_token" in err for err in out_b), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_root_condition_with_nested_path():
    """`find_declared_props()` correctly evaluates root conditionals against the root instance
    when evaluating nested paths (such as array items), rather than testing root conditions
    against the child node."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_root_cond_nested")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"env": {"type": "string"}},
            "if": {"properties": {"env": {"const": "prod"}}, "required": ["env"]},
            "then": {
                "properties": {
                    "audit_trail": {
                        "type": "array",
                        "items": {
                            "properties": {"action": {"type": "string"}, "code": {"type": "integer"}},
                            "unevaluatedProperties": False
                        }
                    }
                }
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # Root instance has env=prod, audit_trail[0] has branch error on 'code' (str instead of int)
        # and genuine rogue 'rogue_metric'.
        # 'action' declared in then must be recognized as declared!
        inst = {
            "env": "prod",
            "audit_trail": [{"action": "login", "code": "bad_code", "rogue_metric": 42}]
        }
        out = mod.validate(inst, composed)
        check("nested code branch error is reported", any("code" in err for err in out), True)
        check("declared action under root condition is not reported as unevaluated artefact",
              any("action" in err for err in out), False)
        check("genuine rogue_metric in nested item is refused",
              any("rogue_metric" in err for err in out), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_pattern_properties_recognized():
    """`find_declared_props()` recognizes properties evaluated by `patternProperties`
    so that matching properties are not flagged as unevaluated artefacts on branch failure,
    while non-matching properties remain strictly refused."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_pattern_props")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"base_id": {"type": "string"}},
            "patternProperties": {
                "^x-custom-[a-z]+$": {"type": "string"}
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # Case A: valid pattern property + branch failure on base_id (int instead of str)
        inst_a = {"base_id": 123, "x-custom-metric": "ok"}
        out_a = mod.validate(inst_a, composed)
        check("base_id branch failure is reported", any("base_id" in err for err in out_a), True)
        check("pattern property x-custom-metric is not reported as unevaluated artefact",
              any("x-custom-metric" in err for err in out_a), False)

        # Case B: non-matching property -> refused!
        inst_b = {"base_id": 123, "not_custom": "rogue"}
        out_b = mod.validate(inst_b, composed)
        check("non-matching property is reported as unexpected", any("not_custom" in err for err in out_b), True)
    finally:
        shutil.rmtree(d)


def test_eval_grader_branch_failure_strictly_scoped_to_path():
    """A branch failure in one object does not turn a genuine unexpected property in another
    object into an artefact.

    What this measures is the outcome, not the scoping: `spurious_b` is reported here because
    nothing in the schema declares it at `group_b`, which held before the rule was scoped too.
    The scope itself is measured directly, on the rule, in
    `test_the_artefact_rule_is_scoped_to_the_path_it_is_asked_about` — a docstring claiming a
    guard that the case cannot fail on is how the last one of these came to be green for the
    wrong reason.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_branch_scope")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "group_a": {
                    "type": "object",
                    "properties": {"req_str": {"type": "string"}},
                    "unevaluatedProperties": False
                },
                "group_b": {
                    "type": "object",
                    "properties": {"valid_num": {"type": "number"}},
                    "unevaluatedProperties": False
                }
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        # group_a has branch failure (req_str is int).
        # group_b has NO branch failure, but has unexpected property 'spurious_b'.
        # spurious_b in group_b must be reported!
        inst = {
            "group_a": {"req_str": 999},
            "group_b": {"valid_num": 10, "spurious_b": "rogue"}
        }
        out = mod.validate(inst, composed)
        check("group_a branch error is reported", any("group_a" in err and "req_str" in err for err in out), True)
        check("spurious_b in group_b is reported", any("group_b" in err and "spurious_b" in err for err in out), True)
    finally:
        shutil.rmtree(d)



def test_schema_cache_cleared_on_run_checks():
    """`_PARSED` schema cache in tools/agents_file_check.py is cleared on run_checks()
    to prevent cross-tree schema pollution during in-process runs."""
    import argparse
    mod = checker_module()
    mod._PARSED["stale_key"] = {"dummy": "schema"}
    d = tempfile.mkdtemp(prefix="cache-")
    try:
        write(os.path.join(d, "AGENTS.md"), "---\ntitle: T\ntype: reference\nstatus: active\n---\n# T\n")
        rep = mod.Report("test")
        old_cwd = os.getcwd()
        try:
            os.chdir(d)
            mod.run_checks(argparse.Namespace(root=d, verbose=False, strict_adapters=False), rep)
            check("stale schema cache was cleared", "stale_key" not in mod._PARSED, True)
        finally:
            os.chdir(old_cwd)
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


def test_branch_failure_isolation_between_sibling_objects():
    """An invalid property in a sibling object (e.g. group_b with 'then' without 'if')
    is not suppressed when another object (group_a) has a branch failure."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_branch_isolation")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {
                "group_a": {
                    "type": "object",
                    "properties": {"req_str": {"type": "string"}},
                    "unevaluatedProperties": False
                },
                "group_b": {
                    "type": "object",
                    "properties": {"valid_num": {"type": "number"}},
                    "then": {"properties": {"rogue_then": {"type": "string"}}},
                    "unevaluatedProperties": False
                }
            },
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        inst = {
            "group_a": {"req_str": 999},
            "group_b": {"valid_num": 10, "rogue_then": "should_be_rejected"}
        }
        out = mod.validate(inst, composed)
        check("group_a branch error is reported", any("group_a" in err and "req_str" in err for err in out), True)
        check("rogue_then in group_b is reported despite group_a failure",
              any("group_b" in err and "rogue_then" in err for err in out), True)
    finally:
        shutil.rmtree(d)


def test_check_if_match_file_uri_path_escape_blocked():
    """`check_if_match()` blocks path escape via 'file://' URIs targeting paths outside the repository."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_if_file_escape")
        esc_file = "/tmp/test_outside_if_escape.json"
        try:
            write(esc_file, json.dumps({"properties": {"leak": {"const": "secret"}}, "required": ["leak"]}))
            # if condition with file:// URI pointing outside repository
            res = mod.check_if_match({"$ref": "file://" + esc_file}, {"leak": "secret"}, base_dir=mod.REPO)
            check("check_if_match refuses file:// escape outside repository", res, False)
        finally:
            if os.path.exists(esc_file):
                os.remove(esc_file)
    finally:
        shutil.rmtree(d)


def test_check_if_match_resolves_a_nested_ref_condition():
    """A `$ref` nested inside an `if` used to make the condition unsatisfiable.

    `check_if_match()` handed the condition to `Draft202012Validator` as a document of its own,
    so `#/$defs/is_prod` had nowhere to resolve, `referencing` raised `Unresolvable`, and the
    `except` around it read the raise as "the condition does not hold". Measured on the instance
    that DOES hold: the `then` branch's own field came back as unexpected, beside the real
    failure — the grader accusing a decision of a field its schema declares.

    The case the test that stood here asserted — the condition unmet — is kept below, because it
    was right. It was also the only one, and that is what let `False` look like the contract
    instead of the defect: its docstring called fail-closed the behaviour under test.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_nested_if_ref")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"is_prod": {"properties": {"env": {"const": "prod"}},
                                  "required": ["env"]}},
            "properties": {"env": {"type": "string"}, "status": {"type": "string"}},
            "allOf": [{
                "required": ["must"],
                "if": {"properties": {"sub": {"$ref": "#/$defs/is_prod"}}, "required": ["sub"]},
                "then": {"properties": {"prod_secret": {"type": "string"}}},
            }],
            "unevaluatedProperties": False,
        }
        write(composed, json.dumps(schema))

        met = mod.validate({"env": "prod", "sub": {"env": "prod"}, "prod_secret": "fine"},
                           composed)
        check("the real failure is reported", any("'must'" in err for err in met), True)
        check("the then-branch's field is not called unexpected once the condition resolves",
              any("prod_secret" in err for err in met), False)
        check("nor is the field the condition itself declares",
              any("'sub'" in err for err in met), False)

        unmet = mod.validate({"env": "dev", "sub": {"env": "dev"}, "prod_secret": "hacked"},
                             composed)
        check("and the same field stays refused when the condition does not hold",
              any("prod_secret" in err for err in unmet), True)

        # A condition that cannot resolve even in context still fails closed, which is what the
        # `except` is for now that it is no longer catching the ordinary case.
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"env": {"type": "string"}},
            "allOf": [{"required": ["must"],
                       "if": {"properties": {"sub": {"$ref": "#/$defs/nowhere"}},
                              "required": ["sub"]},
                       "then": {"properties": {"prod_secret": {"type": "string"}}}}],
            "unevaluatedProperties": False}))
        broken = mod.validate({"env": "prod", "sub": {"env": "prod"}, "prod_secret": "hacked"},
                              composed)
        check("a reference that resolves nowhere leaves the then-branch inactive",
              any("prod_secret" in err for err in broken), True)
    finally:
        shutil.rmtree(d)


def test_a_condition_resolves_without_a_registry_being_handed_in():
    """The same defect as the nested `$ref` one above, through a different door.

    `registry=None` is not "no registry": it replaces jsonschema's default with None and the
    validator raises `AttributeError` on first use, which `check_if_match()`'s own `except` reads
    as "the condition does not hold". Measured: a satisfied condition answered `False`, and
    `find_declared_props()` — called the way five cases in this file call it, positionally and
    with no registry — lost the whole `then` branch and fell back to the union of both `oneOf`
    variants. Invisible, because the catch is what answered.

    Inside `validate()` the registry is always real, so this is about the helpers' own contract:
    a default that cannot be used is not a default.
    """
    mod = load_runner(RUNNER, "test_evalrun_default_registry")
    condition = {"properties": {"env": {"const": "prod"}}, "required": ["env"]}
    check("a satisfied condition holds when no registry is handed in",
          mod.check_if_match(condition, {"env": "prod"}), True)
    check("and an unsatisfied one still does not",
          mod.check_if_match(condition, {"env": "dev"}), False)

    conditional = {"properties": {"env": {"type": "string"}},
                   "if": condition,
                   "then": {"properties": {"prod_secret": {"type": "string"}}}}
    check("the active `then` branch is declared without a registry too",
          sorted(mod.find_declared_props(conditional, (), ".",
                                         inst_node={"env": "prod", "prod_secret": "x"})),
          ["env", "prod_secret"])

    poly = {"oneOf": [{"properties": {"kind": {"const": "a"}, "a_field": {"type": "string"}},
                       "required": ["kind"]},
                      {"properties": {"kind": {"const": "b"}, "b_field": {"type": "string"}},
                       "required": ["kind"]}],
            "properties": {"kind": {"type": "string"}}}
    check("and the variant that holds is the one taken, rather than the union of both",
          sorted(mod.find_declared_props(poly, (), ".", inst_node={"kind": "a", "a_field": "x"})),
          ["a_field", "kind"])


def test_the_matched_if_subschema_declares_its_own_properties():
    """When the condition holds, the `if` subschema's OWN properties are evaluated too.

    jsonschema credits them beside `then`'s, and the walk credited neither — so a schema that
    names a property only in its condition had that property reported as foreign. Measured:
    `flagged`, named nowhere but in the `if`, came back as unexpected.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_if_own_props")
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"env": {"type": "string"}},
            "allOf": [{"required": ["must"],
                       "if": {"properties": {"env": {"const": "prod"},
                                             "flagged": {"type": "boolean"}},
                              "required": ["env"]},
                       "then": {"properties": {"prod_secret": {"type": "string"}}}}],
            "unevaluatedProperties": False}))

        out = mod.validate({"env": "prod", "flagged": True, "prod_secret": "fine"}, composed)
        check("the real failure is reported", any("'must'" in err for err in out), True)
        check("a property the condition declares is not called unexpected",
              any("flagged" in err for err in out), False)

        # And when the condition does not hold, its annotations are dropped — jsonschema's rule,
        # so the same field is refused there and this is not a licence to name anything.
        out_unmet = mod.validate({"env": "dev", "flagged": True}, composed)
        check("the condition's own property is refused when the condition fails",
              any("flagged" in err for err in out_unmet), True)
    finally:
        shutil.rmtree(d)


def test_a_recursive_defs_declares_properties_below_the_first_hop():
    """The reference cycle guard was carried down the path, so recursion looked like a cycle.

    `child: {"$ref": "#/$defs/Node"}` is the same reference the root already expanded, and with
    the guard accumulated along the descent it was skipped as a loop. Measured: the properties
    declared at `('child',)` came back as none at all, so every one of them was reported as
    unexpected one level down.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_recursive_defs")
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {"Node": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "child": {"$ref": "#/$defs/Node"}},
                "allOf": [{"required": ["name"], "properties": {"tag": {"type": "string"}}}],
                "unevaluatedProperties": False}},
            "allOf": [{"$ref": "#/$defs/Node"}],
            "unevaluatedProperties": False}))

        out = mod.validate({"name": "root", "child": {"tag": "x"}}, composed)
        check("the real failure one level down is reported",
              any("child" in err and "'name'" in err for err in out), True)
        check("a property the recursive branch declares is not called unexpected",
              any("'tag'" in err for err in out), False)

        rogue = mod.validate({"name": "root", "child": {"rogue": "x"}}, composed)
        check("a property no level declares is still refused in the child",
              any("child" in err and "rogue" in err for err in rogue), True)

        deeper = mod.validate({"name": "a", "child": {"name": "b", "child": {"tag": "x"}}},
                              composed)
        check("and the same holds two hops down",
              (any("child/child" in err and "'name'" in err for err in deeper),
               any("'tag'" in err for err in deeper)), (True, False))
    finally:
        shutil.rmtree(d)


def test_a_polymorphic_variant_is_not_reported_as_a_rogue_property():
    """`oneOf` / `anyOf` were left out of the walk entirely.

    Measured on a discriminated union whose intended variant fails for its own reason: every
    field that variant declares came back as unexpected, beside the real failure. The branch that
    HOLDS is the one the validator counts, and when none holds the union is taken — the union's
    own failure is already on the path, so trimming its secondary line cannot turn a failing case
    green, while leaving it out accuses the answer of fields its variant declares.

    The direction this must not move is in
    `test_eval_grader_refuses_rogue_properties_in_oneof_and_anyof`, where a variant holds and the
    other variant's field is genuinely foreign.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_polymorphic")
        write(composed, json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "oneOf": [{"properties": {"kind": {"const": "a"}, "a_field": {"type": "string"}},
                       "required": ["kind", "a_field", "must"]},
                      {"properties": {"kind": {"const": "b"}, "b_field": {"type": "string"}},
                       "required": ["kind", "b_field"]}],
            "properties": {"kind": {"type": "string"}},
            "unevaluatedProperties": False}))

        out = mod.validate({"kind": "a", "a_field": "x"}, composed)
        check("the union's own failure is reported",
              any("is not valid under any" in err for err in out), True)
        check("the intended variant's field is not called unexpected",
              any("Unevaluated properties" in err and "a_field" in err for err in out), False)

        rogue = mod.validate({"kind": "a", "a_field": "x", "totally_rogue": 1}, composed)
        check("a property no variant declares is still refused",
              any("Unevaluated properties" in err and "totally_rogue" in err for err in rogue),
              True)
        check("and it is named alone, not beside the variant's own field",
              any("Unevaluated properties" in err and "a_field" in err for err in rogue), False)
    finally:
        shutil.rmtree(d)


def test_unevaluated_items_are_dropped_only_when_every_position_is_covered():
    """The array side of the same artefact, which was never cleaned at all.

    `unevaluatedItems` was excluded from the filter while being excluded from its evidence too,
    so an array under a failing branch carried a secondary line for good. It cannot be cleaned
    by halves: the message names the VALUES it refused — measured, `('a', 'b' were unexpected)`
    — and two equal items are one string in it, so there is no position to subtract. The whole
    line goes only where the schema accounts for every position, and stands otherwise.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_unevaluated_items")

        def schema(items_branch):
            return {"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "properties": {"xs": {"type": "array",
                                          "allOf": [items_branch],
                                          "unevaluatedItems": False}}}

        write(composed, json.dumps(schema({"items": {"type": "string"}, "minItems": 9})))
        covered = mod.validate({"xs": ["a", "b"]}, composed)
        check("the real failure is reported", any("too short" in err for err in covered), True)
        check("and `items` accounts for every position, so the items line is an artefact",
              any("Unevaluated items" in err for err in covered), False)

        write(composed, json.dumps(schema({"prefixItems": [{"type": "string"}], "minItems": 9})))
        partial = mod.validate({"xs": ["a", "b"]}, composed)
        check("the real failure is reported on the partial branch too",
              any("too short" in err for err in partial), True)
        check("but one `prefixItems` entry does not account for position 1, so the line stands",
              any("Unevaluated items" in err for err in partial), True)
    finally:
        shutil.rmtree(d)


def test_the_fallback_condition_ignores_a_property_the_instance_does_not_carry():
    """Without jsonschema, a condition naming an optional property could not be satisfied.

    The shallow matcher asked `inst_node.get(k)` for every key the condition names, so an absent
    one compared `None` against its `const`, `enum` or `type` and refused the instance. Measured:
    `{"env": "prod"}` did not satisfy `{"env": {"const": "prod"}, "note": {"type": "string"}}`,
    where `note` is not required and JSON Schema constrains only the members that are there.
    """
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_fallback_optional")
        condition = {"properties": {"env": {"const": "prod"}, "note": {"type": "string"}},
                     "required": ["env"]}
        real = dict(sys.modules)
        sys.modules["jsonschema"] = None            # force the ImportError branch
        try:
            check("an optional property the instance omits does not refuse the condition",
                  mod.check_if_match(condition, {"env": "prod"}, "", {}), True)
            check("carrying it, and carrying it well, still holds",
                  mod.check_if_match(condition, {"env": "prod", "note": "x"}, "", {}), True)
            check("carrying it badly does not",
                  mod.check_if_match(condition, {"env": "prod", "note": 7}, "", {}), False)
            check("and a required property the instance omits still refuses it",
                  mod.check_if_match(condition, {"note": "x"}, "", {}), False)
        finally:
            sys.modules.clear(); sys.modules.update(real)
    finally:
        shutil.rmtree(d)


def test_the_artefact_rule_is_scoped_to_the_path_it_is_asked_about():
    """`artefact()` itself, on the rule rather than through an instance.

    The runner read branch failures document-wide while its comment claimed this path, so one
    error at the root switched the filter on inside every other object. Asked directly, because
    an instance where the two readings differ is hard to build and a case that cannot fail on a
    guard is not a test of it — `tools/agents_file_check.py` states the same rule, and these are
    the assertions that keep the two agreeing.
    """
    mod = load_runner(RUNNER, "test_evalrun_artefact_rule")

    class Err:
        def __init__(self, validator, path):
            self.validator, self.absolute_path = validator, path

    line = Err("unevaluatedProperties", ("group_b",))
    elsewhere = Err("required", ("group_a",))
    at_path = Err("required", ("group_b",))
    under_path = Err("type", ("group_b", "x"))
    closer_below = Err("unevaluatedProperties", ("group_b", "x"))
    root_line = Err("unevaluatedItems", ())

    check("an error in another object is not evidence about this one",
          mod.artefact(line, [line, elsewhere]), False)
    check("an error at this path is", mod.artefact(line, [line, at_path]), True)
    check("an error under it is too", mod.artefact(line, [line, under_path]), True)
    check("a closer firing one level down is a cause, since it fails the branch that carries it",
          mod.artefact(line, [line, closer_below]), True)
    check("nothing justifies itself", mod.artefact(line, [line]), False)
    check("the root asks about everything, so any other error answers it",
          mod.artefact(root_line, [root_line, elsewhere]), True)
    check("and the rule answers only about an unevaluated* line",
          mod.artefact(at_path, [at_path, under_path]), False)


def test_then_else_without_if_is_ignored():
    """Per JSON Schema Draft 2020-12, 'then' and 'else' without 'if' have no effect,
    and their properties are not considered evaluated."""
    d, composed, runner = grader_tree()
    try:
        mod = load_runner(runner, "test_evalrun_then_without_if")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "properties": {"base_field": {"type": "string"}},
            "then": {"properties": {"standalone_then": {"type": "string"}}},
            "else": {"properties": {"standalone_else": {"type": "string"}}},
            "unevaluatedProperties": False
        }
        write(composed, json.dumps(schema))

        inst_then = {"base_field": "ok", "standalone_then": "rogue"}
        out_then = mod.validate(inst_then, composed)
        check("standalone then property is refused under unevaluatedProperties",
              any("standalone_then" in err for err in out_then), True)

        inst_else = {"base_field": "ok", "standalone_else": "rogue"}
        out_else = mod.validate(inst_else, composed)
        check("standalone else property is refused under unevaluatedProperties",
              any("standalone_else" in err for err in out_else), True)
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
