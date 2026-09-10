#!/usr/bin/env python3
"""The closer moved from the base to the composition — agents-md-schema.md rule 13.

A base that closes itself cannot be extended. `additionalProperties: false` refuses an added
field, and `unevaluatedProperties: false` in the base refuses it just as flatly, because that
keyword sees only the annotations of its own schema object and its in-place applicators and never
a sibling `allOf` branch in the composing schema. So from 2.0.0 the bases carry no closer at all
and the composition carries it — at its root, and inside every subschema that extends a shape the
bundle owns.

Two halves, and each is worthless without the other:

  * the refusals a repository actually relies on — a foreign property, a value outside an enum —
    now exist only in the composed schema. `test_the_bare_base_refuses_nothing` is the measurement
    that says so, and it is why the instance cases here are graded against a composition rather
    than against `verdict.base.schema.json`;
  * `agents_file_check.py` is what keeps the closer there. Without that rule a repository bumps the
    bundle, changes nothing, and gets an open contract with no check red.

The instances are graded by `bundle/evals/run.py`'s own `validate()`, loaded from where vendoring
puts it, so what is exercised is the grader a consumer's evals actually run.

Run: python3 tests/test_schema_closers.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "tools", "agents_file_check.py")
RUNNER = os.path.join(ROOT, "bundle", "evals", "run.py")
SCHEMAS = os.path.join(ROOT, "bundle", "schemas")
VENDORED = "exeris-agents-2.0.0"
BASE_REL = f"../vendor/{VENDORED}/schemas/verdict.base.schema.json"

MANIFEST = f"""\
version: 2
repository: fixture
imports:
  - bundle: exeris-agents
    version: 2.0.0
    ref: deadbeef
    sha256: 'sha256:00'
agents: []
skills: []
workflows: []
policies: []
references: []
schemas: [verdict.schema.json]
nested: []
adapters: {{}}
degradations: {{}}
provider-owned: []
"""


def composition(*, root_closer: bool, nested_closer: bool, extend: bool) -> dict:
    """This repository's verdict schema: the bundle's shape, narrowed, plus a `tag` on a finding."""
    finding = {"allOf": [{"$ref": BASE_REL + "#/properties/findings/items"},
                         {"properties": {"tag": {"enum": ["style", "correctness"]}}}]}
    if not extend:
        finding = {"$ref": BASE_REL + "#/properties/findings/items"}
    if nested_closer:
        finding["unevaluatedProperties"] = False
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Review verdict (fixture)",
        "allOf": [
            {"$ref": BASE_REL},
            {"properties": {"agent": {"enum": ["fixture-reviewer"]},
                            "scope_class": {"enum": ["docs-only"]},
                            "findings": {"items": finding}}},
        ],
    }
    if root_closer:
        schema["unevaluatedProperties"] = False
    return schema


def consumer(*, root_closer: bool = True, nested_closer: bool = True, extend: bool = True) -> str:
    """A repository laid out the way `agents_bundle.py vendor` lays one out."""
    d = tempfile.mkdtemp(prefix="closers-")
    os.makedirs(os.path.join(d, ".git"))
    os.makedirs(os.path.join(d, ".agents", "schemas"))
    vendor = os.path.join(d, ".agents", "vendor", VENDORED)
    shutil.copytree(SCHEMAS, os.path.join(vendor, "schemas"))
    os.makedirs(os.path.join(vendor, "evals"))
    shutil.copy(RUNNER, os.path.join(vendor, "evals", "run.py"))
    with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as fh:
        fh.write("# fixture\n\nPoints at `.agents/` for the semantics.\n")
    with open(os.path.join(d, ".agents", "manifest.yaml"), "w", encoding="utf-8") as fh:
        fh.write(MANIFEST)
    with open(os.path.join(d, ".agents", "schemas", "verdict.schema.json"), "w",
              encoding="utf-8") as fh:
        json.dump(composition(root_closer=root_closer, nested_closer=nested_closer,
                              extend=extend), fh, indent=2)
    return d


def grader(repo: str):
    """`validate()` out of the vendored runner.

    Loaded from inside the fixture, not from `bundle/evals/`: the runner resolves the checkout it
    may read from at import time, and one imported from this repository refuses every path in the
    temporary tree.
    """
    path = os.path.join(repo, ".agents", "vendor", VENDORED, "evals", "run.py")
    spec = importlib.util.spec_from_file_location("evalrun_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verdict(**over) -> dict:
    v = {"agent": "fixture-reviewer", "decision": "CONDITIONAL", "scope_class": "docs-only",
         "findings": [{"what": "a finding long enough to clear minLength",
                       "why": "adr-conventions.md#7", "fix": "name the clause", "tag": "style"}],
         "checks_run": [{"check": "vale", "result": "pass"}]}
    v.update(over)
    return v


COMPOSED = os.path.join(".agents", "schemas", "verdict.schema.json")
BARE_BASE = os.path.join(".agents", "vendor", VENDORED, "schemas", "verdict.base.schema.json")


def refusals(instance: dict, *, schema: str = COMPOSED) -> list[str]:
    """What the grader says about one instance, against a schema named repository-relative."""
    d = consumer()
    try:
        return grader(d).validate(instance, os.path.join(d, schema))
    finally:
        shutil.rmtree(d)


def errors(repo: str) -> list[str]:
    """The checker's error annotations, after asserting it ran at all."""
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    proc = subprocess.run([sys.executable, CHECKER, "--root", repo], capture_output=True,
                          text=True, env=env)
    if proc.returncode not in (0, 1) or "Traceback" in proc.stderr:
        raise AssertionError(f"checker did not run: rc={proc.returncode}\n{proc.stderr[-500:]}")
    if "agents_file_check" not in proc.stdout:
        raise AssertionError(f"checker produced no report:\n{proc.stdout[-500:]}")
    return [l for l in proc.stdout.splitlines() if l.startswith("::error")]


def closer_errors(repo: str) -> int:
    return sum(1 for l in errors(repo) if "unevaluatedProperties" in l)


# ── the grader has jsonschema, or it is grading nothing ───────────────────────────────────────

def test_the_grader_can_actually_validate():
    """Every case below is `[]` from a grader that cannot resolve a `$ref`, and `[]` is what a
    pass looks like. error-handling-and-fallback.md rule 1: say the check did not run."""
    try:
        import jsonschema, referencing                              # noqa: F401
        available = True
    except ImportError:
        available = False
    check("jsonschema and referencing are installed, so the instance cases below ran",
          available, True)


# ── what the composition refuses, and the base no longer does ─────────────────────────────────

def test_a_finding_may_carry_the_repositorys_own_property():
    check("a `tag` the composition adds validates", refusals(verdict()), [])


def test_a_foreign_property_at_the_root_is_refused():
    out = refusals(verdict(sneaky="x"))
    check("an undeclared root property is refused",
          bool(out) and "sneaky" in out[0], True)


def test_a_foreign_property_inside_a_finding_is_refused():
    out = refusals(verdict(findings=[{"what": "a finding long enough to clear minLength",
                                      "why": "adr-conventions.md#7", "fix": "name the clause",
                                      "tag": "style", "sneaky": "x"}]))
    check("an undeclared property inside a finding is refused",
          bool(out) and "sneaky" in out[0], True)


def test_a_tag_outside_the_enum_is_refused():
    out = refusals(verdict(findings=[{"what": "a finding long enough to clear minLength",
                                      "why": "adr-conventions.md#7", "fix": "name the clause",
                                      "tag": "vibes"}]))
    check("the added property is constrained, not merely permitted",
          bool(out) and "vibes" in out[0], True)


def test_the_bare_base_refuses_nothing():
    """The measurement behind every line above. Graded against the base, all three mutants pass —
    so an evaluation pointed at `verdict.base.schema.json` reports a clean run over answers no
    repository would accept."""
    base = BARE_BASE
    check("the open base accepts a foreign root property",
          refusals(verdict(sneaky="x"), schema=base), [])
    check("the open base accepts a foreign property inside a finding",
          refusals(verdict(findings=[{"what": "a finding long enough to clear minLength",
                                      "why": "adr-conventions.md#7", "fix": "name the clause",
                                      "sneaky": "x"}]), schema=base), [])
    check("and knows nothing of the enum the repository added",
          refusals(verdict(findings=[{"what": "a finding long enough to clear minLength",
                                      "why": "adr-conventions.md#7", "fix": "name the clause",
                                      "tag": "vibes"}]), schema=base), [])


# ── the checker keeps the closer where the refusals live ──────────────────────────────────────

def test_a_composition_that_carries_both_closers_is_clean():
    d = consumer()
    try:
        check("both closers present", closer_errors(d), 0)
    finally:
        shutil.rmtree(d)


def test_a_composition_without_the_root_closer_is_an_error():
    d = consumer(root_closer=False)
    try:
        check("a root that closes nothing is reported", closer_errors(d), 1)
    finally:
        shutil.rmtree(d)


def test_a_composition_without_the_nested_closer_is_an_error():
    d = consumer(nested_closer=False)
    try:
        out = [l for l in errors(d) if "unevaluatedProperties" in l]
        check("an extended subschema that closes nothing is reported", len(out), 1)
        check("and the message names the site", "/allOf/1/properties/findings/items" in out[0],
              True)
    finally:
        shutil.rmtree(d)


def test_a_subschema_that_adds_nothing_needs_no_closer():
    """`$ref` alone narrows nothing, so there is nothing for a closer to hold. Reporting it would
    be a finding a repository can only answer by writing a keyword that changes no outcome."""
    d = consumer(extend=False, nested_closer=False)
    try:
        check("a plain reference is not an extension", closer_errors(d), 0)
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
