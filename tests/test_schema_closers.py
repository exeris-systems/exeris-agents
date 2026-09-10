#!/usr/bin/env python3
"""The closer moved from the base to the composition — agents-md-schema.md rule 13.

A base that closes itself cannot be extended. `additionalProperties: false` refuses an added
field, and `unevaluatedProperties: false` in the base refuses it just as flatly, because that
keyword sees only the annotations of its own schema object and its in-place applicators and never
a sibling `allOf` branch in the composing schema. So from 2.0.0 the bases carry no closer at all
and the composition carries them — one per object, because a closer at the root does not reach
into an array's items: with `checks_run` left open a foreign property in a check entry validates
however tightly the root is closed. `test_an_unclosed_object_takes_any_property` is that
measurement.

Two halves, and each is worthless without the other:

  * the refusals a repository relies on — a foreign property, a value outside an enum — now exist
    only in the composed schema. `test_the_bare_base_refuses_nothing` says how little is left
    without it, and is why the instance cases here are graded against a composition rather than
    against `verdict.base.schema.json`;
  * `agents_file_check.py` is what keeps the closers there. Without that rule a repository bumps
    the bundle, changes nothing, and gets an open contract with no check red.

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
BASE = f"../vendor/{VENDORED}/schemas/verdict.base.schema.json"

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


def composition(*, root: bool = True, finding: bool = True, checks: bool = True,
                handoffs: bool = True, compose_checks: bool = True) -> dict:
    """This repository's verdict schema: the bundle's shape, narrowed, plus a `tag` on a finding.

    Every object the base declares is closed here, whether or not this repository extends it — a
    `findings` item because the `tag` is added there, a check entry and a handoff because leaving
    either open is a hole the root cannot cover.
    """
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Review verdict (fixture)",
        "allOf": [
            {"$ref": BASE},
            {"properties": {
                "agent": {"enum": ["fixture-reviewer"]},
                "scope_class": {"enum": ["docs-only"]},
                "findings": {"items": closed(
                    {"allOf": [{"$ref": BASE + "#/properties/findings/items"},
                               {"properties": {"tag": {"enum": ["style", "correctness"]}}}]},
                    finding)},
                "handoffs": {"items": closed({"$ref": BASE + "#/properties/handoffs/items"},
                                             handoffs)},
            }},
        ],
    }
    if compose_checks:
        schema["allOf"][1]["properties"]["checks_run"] = {
            "items": closed({"$ref": BASE + "#/properties/checks_run/items"}, checks)}
    return closed(schema, root)


def closed(node: dict, yes: bool) -> dict:
    return {**node, "unevaluatedProperties": False} if yes else node


def consumer(**shape) -> str:
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
        json.dump(composition(**shape), fh, indent=2)
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


FINDING = {"what": "a finding long enough to clear minLength", "why": "adr-conventions.md#7",
           "fix": "name the clause", "tag": "style"}
HANDOFF = {"from": "fixture-reviewer", "to": "human", "blocking": True,
           "reason": "the receiving role owns the number"}


def verdict(**over) -> dict:
    v = {"agent": "fixture-reviewer", "decision": "CONDITIONAL", "scope_class": "docs-only",
         "findings": [dict(FINDING)], "checks_run": [{"check": "vale", "result": "pass"}],
         "handoffs": [dict(HANDOFF)]}
    v.update(over)
    return v


COMPOSED = os.path.join(".agents", "schemas", "verdict.schema.json")
BARE_BASE = os.path.join(".agents", "vendor", VENDORED, "schemas", "verdict.base.schema.json")


def refusals(instance: dict, *, schema: str = COMPOSED, **shape) -> list[str]:
    """What the grader says about one instance, against a schema named repository-relative."""
    d = consumer(**shape)
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


def closer_errors(**shape) -> list[str]:
    d = consumer(**shape)
    try:
        return [l for l in errors(d) if "unevaluatedProperties" in l]
    finally:
        shutil.rmtree(d)


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
    check("an undeclared root property is refused", bool(out) and "sneaky" in out[0], True)


def test_a_foreign_property_inside_a_finding_is_refused():
    out = refusals(verdict(findings=[dict(FINDING, sneaky="x")]))
    check("an undeclared property inside a finding is refused",
          bool(out) and "sneaky" in out[0], True)


def test_a_foreign_property_inside_a_check_entry_is_refused():
    out = refusals(verdict(checks_run=[{"check": "vale", "result": "pass", "sneaky": "x"}]))
    check("an undeclared property inside a check entry is refused",
          bool(out) and "sneaky" in out[0], True)


def test_a_foreign_property_inside_a_handoff_is_refused():
    """The object the verdict base does not declare itself — `handoffs` items are `handoff.base`,
    one file over, and closing them is the composition's job just the same."""
    out = refusals(verdict(handoffs=[dict(HANDOFF, sneaky="x")]))
    check("an undeclared property inside a handoff is refused",
          bool(out) and "sneaky" in out[0], True)


def test_a_tag_outside_the_enum_is_refused():
    out = refusals(verdict(findings=[dict(FINDING, tag="vibes")]))
    check("the added property is constrained, not merely permitted",
          bool(out) and "vibes" in out[0], True)


def test_the_bare_base_refuses_nothing():
    """The measurement behind every line above. Graded against the base, every mutant passes — so
    an evaluation pointed at `verdict.base.schema.json` reports a clean run over answers no
    repository would accept."""
    for name, instance in (("a foreign root property", verdict(sneaky="x")),
                           ("a foreign property in a finding",
                            verdict(findings=[dict(FINDING, sneaky="x")])),
                           ("a foreign property in a check entry",
                            verdict(checks_run=[{"check": "vale", "result": "pass",
                                                 "sneaky": "x"}])),
                           ("a value outside an enum the repository added",
                            verdict(findings=[dict(FINDING, tag="vibes")]))):
        check(f"the open base accepts {name}", refusals(instance, schema=BARE_BASE), [])


def test_an_unclosed_object_takes_any_property():
    """Why the rule is every object and not every extension: the root's closer stops at the root.
    A composition that closes everything but `checks_run` refuses a foreign property in two places
    and waves it through in the third."""
    check("the root is closed, so a foreign root property is refused",
          bool(refusals(verdict(sneaky="x"), checks=False)), True)
    check("a finding is closed, so a foreign property there is refused",
          bool(refusals(verdict(findings=[dict(FINDING, sneaky="x")]), checks=False)), True)
    check("the unclosed check entry takes it",
          refusals(verdict(checks_run=[{"check": "vale", "result": "pass", "sneaky": "x"}]),
                   checks=False), [])


# ── the checker keeps a closer over every object the base leaves open ─────────────────────────

def test_a_composition_that_closes_every_object_is_clean():
    check("root, finding, check entry and handoff all closed", closer_errors(), [])


def test_a_composition_without_the_root_closer_is_an_error():
    out = closer_errors(root=False)
    check("a root that closes nothing is reported", len(out), 1)
    check("and the message says it is the root", "the root composes" in out[0], True)


def test_a_composition_without_the_finding_closer_is_an_error():
    out = closer_errors(finding=False)
    check("an extended object that closes nothing is reported", len(out), 1)
    check("and the message names the site",
          "verdict.base.schema.json#/properties/findings/items" in out[0], True)


def test_a_composition_without_the_check_entry_closer_is_an_error():
    """The case the instance measurement above pairs with: what validates silently is red here."""
    out = closer_errors(checks=False)
    check("an unextended object that closes nothing is reported too", len(out), 1)
    check("and the message names the site",
          "verdict.base.schema.json#/properties/checks_run/items" in out[0], True)


def test_an_object_the_composition_never_mentions_is_an_error():
    out = closer_errors(compose_checks=False)
    check("an object with no subschema over it at all is reported", len(out), 1)
    check("and the message asks for the subschema, not for a keyword in one that is not there",
          "nothing here closes" in out[0], True)


def test_the_handoff_object_counts_although_it_lives_in_another_file():
    out = closer_errors(handoffs=False)
    check("a shape the base pulls in by `$ref` is one of its open objects", len(out), 1)
    check("and the message names the file that declares it",
          "handoff.base.schema.json" in out[0], True)


if __name__ == "__main__":
    main(globals())
