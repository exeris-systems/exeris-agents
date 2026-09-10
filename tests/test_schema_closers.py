#!/usr/bin/env python3
"""The closer moved from the base to the composition.

Rule 13 makes the files in `.agents/schemas/` what a decision conforms to and says nothing about
where a `$ref` sits or what closes an object; that a composition must refuse what the base does not
name is this bundle's own contract, written in each base's `description` and in `CHANGELOG.md`.

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
  * `agents_file_check.py` is what keeps the closers there, and it asks the same question the same
    way: it adds a property to a probe and reports the locations where nothing refuses it.

The last section is one case per cause of the three review rounds this check spent being a walker
that re-derived what a validator already knows. Each of those defects had a shape — a composition
under `$defs`, an object declared without `type`, a base that closes what it forwards, a property
name a pointer must escape, a shape reached through another file, a reference into a tree the
manifest does not pin — and each is here because the mechanism that answered them by reading
syntax answered at least one of them wrongly.

The instances are graded by `bundle/evals/run.py`'s own `validate()`, loaded from where vendoring
puts it, so what is exercised is the grader a consumer's evals actually run.

Run: python3 tests/test_schema_closers.py
"""
from __future__ import annotations

import importlib.util
import json
import re
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
DEFAULT_PIN = "2.0.0"
VENDORED = f"exeris-agents-{DEFAULT_PIN}"
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
        _RUNS.pop(d, None)
        shutil.rmtree(d, ignore_errors=True)


_RUNS: dict = {}


def annotations(repo: str) -> tuple[list[str], list[str]]:
    """`(errors, warnings)` from one run of the checker, cached per fixture.

    One run, because several cases ask two questions of one tree and the checker is a subprocess;
    and after asserting it ran at all, because ignoring the return code let every "expect nothing"
    assertion pass on a crash.
    """
    if repo not in _RUNS:
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        proc = subprocess.run([sys.executable, CHECKER, "--root", repo], capture_output=True,
                              text=True, env=env)
        if proc.returncode not in (0, 1) or "Traceback" in proc.stderr:
            raise AssertionError(f"checker did not run: rc={proc.returncode}\n{proc.stderr[-500:]}")
        if "agents_file_check" not in proc.stdout:
            raise AssertionError(f"checker produced no report:\n{proc.stdout[-500:]}")
        lines = proc.stdout.splitlines()
        _RUNS[repo] = ([l for l in lines if l.startswith("::error")],
                       [l for l in lines if l.startswith("::warning")])
    return _RUNS[repo]


def errors(repo: str) -> list[str]:
    return annotations(repo)[0]


def warnings(repo: str) -> list[str]:
    """What the checker could not measure, as against what it refuses."""
    return annotations(repo)[1]


OPEN_AT = "may carry any property at "


def open_locations(repo: str) -> list[str]:
    """The instance locations the checker says nothing refuses a property at."""
    return sorted(l.split(OPEN_AT, 1)[1].split(" ", 1)[0] for l in errors(repo) if OPEN_AT in l)


def closer_errors(**shape) -> list[str]:
    d = consumer(**shape)
    try:
        return open_locations(d)
    finally:
        _RUNS.pop(d, None)                 # or a recovered mkdtemp name reads another run's result
        shutil.rmtree(d, ignore_errors=True)


def open_locations_for(composed: dict, **kwargs) -> list[str]:
    """The open locations of one composed schema; the fixture it builds is consumed."""
    d = custom(composed, **kwargs)
    try:
        return open_locations(d)
    finally:
        _RUNS.pop(d, None)
        shutil.rmtree(d, ignore_errors=True)


def custom(composed: dict, *, vendored: dict | None = None, pin: str = DEFAULT_PIN,
           name: str = "verdict.schema.json", manifest: str | None = None) -> str:
    """A consumer with a composed schema of the case's own making, and optionally its own bases.

    The standard fixture above answers "does the rule hold for the real bundle". These cases ask
    the harder question: what does the rule do when the tree is not the tidy one — a `$ref` into
    the wrong vendored version, a closer parked somewhere that never applies, a property name a
    JSON pointer has to escape.
    """
    d = tempfile.mkdtemp(prefix="closers-")
    os.makedirs(os.path.join(d, ".git"))
    os.makedirs(os.path.join(d, ".agents", "schemas"))
    vendor = os.path.join(d, ".agents", "vendor", f"exeris-agents-{pin}")
    shutil.copytree(SCHEMAS, os.path.join(vendor, "schemas"))
    os.makedirs(os.path.join(vendor, "evals"))
    shutil.copy(RUNNER, os.path.join(vendor, "evals", "run.py"))
    for rel, doc in (vendored or {}).items():
        path = os.path.join(d, ".agents", "vendor", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as fh:
        fh.write("# fixture\n\nPoints at `.agents/` for the semantics.\n")
    with open(os.path.join(d, ".agents", "manifest.yaml"), "w", encoding="utf-8") as fh:
        body = (manifest or MANIFEST).replace("verdict.schema.json", name)
        fh.write(body.replace(DEFAULT_PIN, pin, 1))
    with open(os.path.join(d, ".agents", "schemas", name), "w", encoding="utf-8") as fh:
        json.dump(composed, fh, indent=2)
    return d


def findings_then_drop(repo: str) -> list[str]:
    """The schema findings for a fixture, which this then deletes — the name says so, because a
    getter that removes what it was handed is not a getter."""
    try:
        return [l.split("schema::", 1)[-1] for l in errors(repo) if "schema::" in l]
    finally:
        _RUNS.pop(repo, None)
        shutil.rmtree(repo)


OPEN_OBJECT = {"type": "object", "properties": {"a": {"type": "string"}}}


# ── where the rule used to answer wrongly: silence one way, noise the other ───────────────────

STALE = "exeris-agents-1.4.0/schemas/verdict.base.schema.json"


def stray(closed_base: bool) -> str:
    """A repository mid-bump: the manifest pins 2.0.0, the schema still names the tree it left.

    `closed_base` is what the old tree holds. A bundle whose bases still close themselves leaves
    nothing for the closer rule to say, which is the case that has to be heard from anyway.
    """
    if closed_base:
        vendored = {STALE: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                            "type": "object", "additionalProperties": False,
                            "properties": {"agent": {"type": "string"}}}}
    else:
        # A whole vendored tree, not one file: the base references its neighbours, and a stale tree
        # missing them would be measuring a broken checkout rather than a stale pin.
        vendored = {f"exeris-agents-1.4.0/schemas/{name}":
                    json.load(open(os.path.join(SCHEMAS, name), encoding="utf-8"))
                    for name in sorted(os.listdir(SCHEMAS))}
    return custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "allOf": [{"$ref": f"../vendor/{STALE}"}]},
                  vendored=vendored)


def off_pin(out: list[str]) -> list[str]:
    return [f for f in out if "no import in the manifest pins" in f]


def test_a_ref_into_an_unpinned_vendored_tree_is_not_a_skip():
    """The bump state. A repository re-pins its manifest and re-vendors, and until every schema's
    `$ref` is retargeted the composition still names the old directory — which exists, so no
    "target does not exist" fires. The closer rule used to answer that by recognising nothing at
    all, so the one moment it is there for was the one moment it said nothing."""
    out = findings_then_drop(stray(closed_base=False))
    check("composing over a vendored tree the manifest does not pin is reported once — two checks "
          "touch that reference and only the one that judges references reports it",
          len(off_pin(out)), 1)
    check("and the objects behind it are still asked the question — an instance is validated "
          "against them whichever tree they were reached through",
          sum(1 for f in out if OPEN_AT in f) > 0, True)


def test_a_stray_reference_is_heard_from_with_nothing_else_wrong():
    """The case that moved the report out of the closer check. The old tree's bases still close
    themselves, so the closer rule has nothing to say about this schema at all — and a repository
    whose reference points at a tree its pin does not vouch for should not need a second defect
    before anything tells it."""
    out = findings_then_drop(stray(closed_base=True))
    check("the stray reference is reported on its own", len(off_pin(out)), 1)
    check("and it is the only thing reported", len(out), 1)


def test_a_closer_in_a_dead_branch_does_not_launder_an_open_one():
    """`$defs` is not applied to anything unless something references it. A closer parked there
    was counted as closing the object for the whole document, so the live composition — the one
    every instance is actually validated against — could be wide open with nothing red."""
    out = findings_then_drop(custom(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": BASE}, {"properties": {"agent": {"enum": ["r"]}}}],
         "$defs": {"legacy": {"$ref": BASE, "unevaluatedProperties": False}}}))
    check("the live root, which closes nothing, is reported",
          any(f.startswith(OPEN_AT.strip() + " <root>") or OPEN_AT + "<root>" in f for f in out),
          True)


def test_composing_one_object_is_not_ordered_to_close_the_rest():
    """A repository may compose a single sub-object — a finding, for a schema about findings. The
    requirement was computed per base FILE, so it was told to close a root, a check entry and a
    handoff it never pulls in."""
    out = findings_then_drop(custom(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": BASE + "#/properties/findings/items"},
                   {"properties": {"tag": {"enum": ["style"]}}}],
         "unevaluatedProperties": False},
        name="finding.schema.json"))
    check("a schema that pulls in one object is asked to close that one and nothing else",
          [f for f in out if "unevaluatedProperties" in f], [])


def test_following_a_forwarding_ref_keeps_the_pointer():
    """`handoffs` forwards into another file at a pointer. The walk dropped the pointer and
    enumerated the whole target document, so objects the composition never names became work."""
    other = "exeris-agents-2.0.0/schemas/other.base.schema.json"
    out = findings_then_drop(custom(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": "../vendor/" + other + "#/$defs/wanted"}],
         "unevaluatedProperties": False},
        vendored={other: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                          "type": "object",
                          "properties": {"unrelated": dict(OPEN_OBJECT)},
                          "$defs": {"wanted": dict(OPEN_OBJECT)}}},
        name="wanted.schema.json"))
    check("only the object the pointer names is required",
          [f for f in out if "unevaluatedProperties" in f], [])


def test_a_property_name_that_a_pointer_must_escape_keeps_its_identity():
    """One half built pointers raw and the other unescaped them, so a property named `a/b` was one
    object to the requirement and another to the closure — and the two never met."""
    odd = "exeris-agents-2.0.0/schemas/odd.base.schema.json"
    ref = "../vendor/" + odd
    out = findings_then_drop(custom(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": ref},
                   {"properties": {"a/b": {"$ref": ref + "#/properties/a~1b",
                                           "unevaluatedProperties": False}}}],
         "unevaluatedProperties": False},
        vendored={odd: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object", "properties": {"a/b": dict(OPEN_OBJECT)}}},
        name="odd.schema.json"))
    check("an escaped property name resolves to the same object on both sides",
          [f for f in out if "unevaluatedProperties" in f], [])


def test_a_ref_whose_pointer_does_not_resolve_is_reported():
    """A `$ref` used to be checked as a file and never as a pointer. This release makes pointer
    refs the documented way to close a nested object, so a typo in one is a closer that closes
    nothing — and the file behind it exists, so nothing else notices."""
    out = findings_then_drop(custom(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": BASE}],
         "unevaluatedProperties": False,
         "properties": {"findings": {"items": {"$ref": BASE + "#/properties/nope",
                                               "unevaluatedProperties": False}}}}))
    check("a pointer that names nothing in the target is reported",
          any("pointer" in f for f in out), True)


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


def test_the_root_left_open_is_named():
    check("the instance location, which is where a fixer acts", closer_errors(root=False),
          ["<root>"])


def test_a_finding_left_open_is_named():
    check("an extended object that refuses nothing", closer_errors(finding=False),
          ["findings/0"])


def test_a_check_entry_left_open_is_named():
    """The case the instance measurement above pairs with: what validates silently is red here."""
    check("an unextended object that refuses nothing", closer_errors(checks=False),
          ["checks_run/0"])


def test_an_object_the_composition_never_mentions_is_named_the_same_way():
    """One finding for one fact. The old check had two messages here — a keyword missing from a
    subschema, or no subschema at all — and the fix is the same either way: close that object."""
    check("an object with no subschema over it at all", closer_errors(compose_checks=False),
          ["checks_run/0"])


def test_the_handoff_object_counts_although_it_lives_in_another_file():
    check("a shape the base pulls in by `$ref` is one of its objects",
          closer_errors(handoffs=False), ["handoffs/0"])


def test_a_base_wrapped_under_a_property_is_measured_where_it_sits():
    """Two rounds ago this was unmeasurable and then a warning, because the probe was built from
    what applies at an instance's ROOT. The decision is generated from the schema now, so a base
    wrapped under a property is an object at `verdict` and is asked the same question as any
    other."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"verdict": {"allOf": [{"$ref": BASE}],
                                           "properties": {"findings": {"items": {"allOf": [
                                               {"$ref": BASE + "#/properties/findings/items"}],
                                               "unevaluatedProperties": False}},
                                               "checks_run": {"items": {"allOf": [
                                                   {"$ref": BASE + "#/properties/checks_run/items"}],
                                                   "unevaluatedProperties": False}},
                                               "handoffs": {"items": {"allOf": [
                                                   {"$ref": BASE + "#/properties/handoffs/items"}],
                                                   "unevaluatedProperties": False}}}}}})
    try:
        check("a base referenced away from the root is measured where it sits: the decision is "
              "generated from this schema, so the objects are wherever it puts them",
              [l for l in open_locations(d)], ["<root>", "verdict"])
    finally:
        shutil.rmtree(d)


def test_no_base_declares_a_closer_of_its_own():
    """The release's own claim, re-measured against all three files rather than one. `### Breaking`
    says no object in any base declares `additionalProperties` or `unevaluatedProperties`; only the
    verdict base was ever measured, and restoring one to `triage-result.base` would have shipped
    green while silently taking every consumer's ability to extend a gate."""
    def keywords(node):
        """Every key in every object of a document — the keys, not the prose: each base's
        `description` names both closers on purpose."""
        if isinstance(node, dict):
            yield from node
            for value in node.values():
                yield from keywords(value)
        elif isinstance(node, list):
            for value in node:
                yield from keywords(value)

    for name in sorted(os.listdir(SCHEMAS)):
        with open(os.path.join(SCHEMAS, name), encoding="utf-8") as fh:
            declared = set(keywords(json.load(fh)))
        check(f"{name} declares no closer of its own",
              sorted(declared & {"additionalProperties", "unevaluatedProperties"}), [])


def test_two_bases_disagreeing_about_a_property_do_not_kill_the_run():
    """One base declares `findings` an object, the other an array. The probe took one shape and the
    locations kept both, so the walk into the value raised `KeyError` out of `check_closers` — and
    `rep.emit()` never ran, so a crash reached CI as zero annotations rather than as a failure."""
    other = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"findings": {"type": "object",
                                         "properties": {"a": {"type": "string"}}}}}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/other.base.schema.json"},
                          {"$ref": BASE}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/other.base.schema.json": other})
    try:
        out = errors(d)          # raises AssertionError of its own if the checker crashed
        check("the run survives, and the schema is reported as rejecting what was built for it — "
              "no instance can satisfy two bases that disagree about one property",
              any("rejects a decision built to satisfy it" in l for l in out), True)
    finally:
        shutil.rmtree(d)


def test_a_closer_that_refuses_the_base_itself_is_reported():
    """`additionalProperties: false` in a sibling branch is the obvious migration move and the one
    each base's `description` warns against: it sees only the properties named beside it, so it
    refuses everything the base declares. Asking only whether a property can get IN reads that as
    closed — the probe property is refused along with all the rest."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE},
                          {"properties": {"agent": {"enum": ["r"]}},
                           "additionalProperties": False}]})
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a schema that rejects the base's own properties is reported",
              any("rejects a decision built to satisfy it" in f for f in out), True)
    finally:
        shutil.rmtree(d)


def test_a_root_that_composes_through_another_file_is_reported():
    """`{"$ref": "inner.json"}` at the root, with the base composed one file over: `applies_at_the
    _root` stops at a file boundary, so the schema was neither probed nor reported."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": "../core/inner.json"})
    try:
        core = os.path.join(d, ".agents", "core")
        os.makedirs(core)
        with open(os.path.join(core, "inner.json"), "w", encoding="utf-8") as fh:
            json.dump({"$schema": "https://json-schema.org/draft/2020-12/schema",
                       "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/verdict.base.schema.json"}]},
                      fh)
        check("a base composed through a file this check does not follow is reported",
              any("through" in w and "does not follow" in w for w in warnings(d)), True)
    finally:
        shutil.rmtree(d)


def test_an_object_two_levels_down_is_probed():
    """The probe read one level of a base, so an object nested inside another was never asked."""
    deep = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "properties": {"outer": {"type": "object",
                                     "properties": {"inner": {"type": "object",
                                                              "properties": {"a": {"type": "string"}}}}}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/deep.base.schema.json"}],
         "unevaluatedProperties": False,
         "properties": {"outer": {"allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/deep.base.schema.json#/properties/outer"}],
                                  "unevaluatedProperties": False}}},
        vendored={f"{VENDORED}/schemas/deep.base.schema.json": deep})
    check("the object below the first level is named", out, ["outer/inner"])


def test_a_ref_target_that_is_not_readable_json_says_so():
    """It was reported as a pointer that does not resolve, which sends the reader to the pointer
    rather than to the file that will not parse."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {"x": {"$ref": "notjson.schema.json#/$defs/thing"}}})
    try:
        with open(os.path.join(d, ".agents", "schemas", "notjson.schema.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("this is not json\n")
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("the file is named as the thing to look at",
              any("not readable JSON" in f for f in out), True)
        check("and it is not called a pointer that does not resolve",
              any("pointer that does not resolve" in f for f in out), False)
    finally:
        shutil.rmtree(d)


def test_a_malformed_pin_does_not_switch_the_off_pin_check_off():
    """`vendor_roots()` skips an import missing its version, so a malformed pin left no roots at
    all — and "no roots" was read as "everything is pinned", turning the check off in the state it
    exists for."""
    no_version = MANIFEST.replace("    version: 2.0.0\n", "")
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "unevaluatedProperties": False},
               manifest=no_version)
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a reference into a tree nothing pins is reported", len(off_pin(out)), 1)
    finally:
        shutil.rmtree(d)


def test_a_composition_below_the_root_that_is_correctly_closed_is_not_an_error():
    """An envelope wrapping the composition under `properties.verdict`, closed at the root, at the
    wrapper and at each items level: a conforming decision validates and a foreign property is
    refused at every level. The check cannot measure it from the root, and saying so is a warning —
    an error here fires where the repository was already conforming, which the changelog's own
    MINOR rule forbids."""
    def closed(pointer):
        return {"allOf": [{"$ref": BASE + pointer}], "unevaluatedProperties": False}
    inner = {"allOf": [{"$ref": BASE},
                       {"properties": {
                           "agent": {"enum": ["r"]},
                           "findings": {"items": closed("#/properties/findings/items")},
                           "checks_run": {"items": closed("#/properties/checks_run/items")},
                           "handoffs": {"items": closed("#/properties/handoffs/items")}}}],
             "unevaluatedProperties": False}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                "properties": {"verdict": inner}, "unevaluatedProperties": False},
               name="envelope.schema.json")
    try:
        check("a correctly closed composition below the root is no error",
              [e.split("schema::", 1)[-1] for e in errors(d) if "schema::" in e], [])
        check("and it is measured rather than passed over: the wrapper is an object like any other",
              "verdict" in open_locations(d), False)
    finally:
        shutil.rmtree(d)


def test_a_closer_parked_in_a_sibling_branch_is_reported():
    """`unevaluatedProperties: false` inside the branch beside the one carrying the base sees only
    that branch's own properties, so every conforming decision is refused for carrying what the
    base declares. The probe cannot tell it from a correct closer — both refuse the probe property
    — so the shape is named instead."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE},
                          {"properties": {"agent": {"enum": ["r"]}},
                           "unevaluatedProperties": False}]})
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("the misplaced closer is reported, as the rejection it causes",
              any(f.startswith("rejects a decision built to satisfy it") and "at <root>" in f
                  for f in out), True)
    finally:
        shutil.rmtree(d)
    clean = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "allOf": [{"$ref": BASE, "unevaluatedProperties": False}]})
    try:
        check("and the same keyword on the branch that carries the base is left alone — measured, "
              "a conforming decision passes it",
              any("rejects a decision" in e for e in errors(clean)), False)
    finally:
        shutil.rmtree(clean)


def test_an_object_a_base_declares_behind_a_ref_is_probed():
    """`handoffs` items are `handoff.base`, one file over. The probe stopped at the file boundary,
    so an object that base declares had no location and its openness was never measured."""
    referenced = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                  "properties": {"nested": {"type": "object",
                                            "properties": {"a": {"type": "string"}}}}}
    outer = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"h": {"items": {"$ref": "ref.base.schema.json"}}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/outer.base.schema.json"}],
         "unevaluatedProperties": False,
         "properties": {"h": {"items": {"allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/ref.base.schema.json"}],
                                        "unevaluatedProperties": False}}}},
        vendored={f"{VENDORED}/schemas/outer.base.schema.json": outer,
                  f"{VENDORED}/schemas/ref.base.schema.json": referenced})
    check("the object behind the reference is measured", out, ["h/0/nested"])


def test_a_manifest_that_does_not_parse_does_not_make_every_reference_a_stray():
    """No vendor roots means "nothing is pinned" only when the manifest was read. When it was not,
    what it pins is unknown — and telling the reader to retarget schemas whose targets are exactly
    right buries the YAML error that is the actual finding."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "unevaluatedProperties": False},
               manifest="version: 2\n  broken: [\n")
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("no off-pin finding against an unreadable manifest", off_pin(out), [])
        check("and the manifest itself is reported",
              any("not valid YAML" in e for e in errors(d)), True)
    finally:
        shutil.rmtree(d)


def test_a_tree_reached_through_a_symlink_is_named_by_where_it_lands():
    """Containment was decided on the resolved path and the name was taken from the text, so a
    reference through a symlink was reported as composing over `.agents/vendor/..`."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": "../lib/schemas/verdict.base.schema.json"}],
                "unevaluatedProperties": False})
    try:
        stale = os.path.join(d, ".agents", "vendor", "exeris-agents-1.4.0")
        shutil.copytree(os.path.join(d, ".agents", "vendor", VENDORED), stale)
        os.symlink(stale, os.path.join(d, ".agents", "lib"))
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("the tree is named by where the reference lands",
              [f.split(",")[0] for f in off_pin(out)],
              ["composes over .agents/vendor/exeris-agents-1.4.0"])
    finally:
        shutil.rmtree(d)


def test_an_anchor_fragment_is_not_read_as_the_whole_document():
    """`#agentRef` is a plain-name anchor, not a pointer. Splitting it produced an empty path, so
    the document itself came back and the probe was built from a base's root while the reference
    named something inside it."""
    mod = checker_module()
    check("an anchor resolves to nothing this check can probe",
          mod.node_at({"$defs": {"agentRef": {"type": "string"}}}, "agentRef"), None)
    check("and a pointer still resolves",
          mod.node_at({"$defs": {"agentRef": {"type": "string"}}}, "/$defs/agentRef"),
          {"type": "string"})


def test_the_probe_says_where_it_stopped():
    """`depth` truncating in silence made an unmeasured location indistinguishable from a measured
    and closed one."""
    deep = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
    for level in range(12):
        deep = {"type": "object", "properties": {f"l{level}": deep}}
    deep["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/deep.base.schema.json"}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/deep.base.schema.json": deep})
    try:
        check("the depth the generator stopped at is named",
              any("depth bound" in w for w in warnings(d)), True)
    finally:
        shutil.rmtree(d)


# ── the shapes the probe declines, said out loud ──────────────────────────────────────────────

def test_what_the_probe_declines_is_reported():
    """The inversion. The probe walks the constructs it understands and used to be silent
    everywhere else, so an object introduced through a construct it does not walk was
    indistinguishable from one measured and closed — five review rounds, five more such shapes. It
    now names what it met and left alone."""
    odd = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
           "properties": {"a": {"type": "string"}},
           "patternProperties": {"^x-": {"type": "object", "properties": {"deep": {"type": "object"}}}}}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/odd.base.schema.json"}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/odd.base.schema.json": odd})
    try:
        check("the construct it did not walk is named",
              any("not measured" in w and "patternProperties" in w for w in warnings(d)), True)
    finally:
        shutil.rmtree(d)


def test_a_branch_that_constrains_what_is_already_probed_is_not_declined():
    """The bases' own `allOf` of `if`/`then` tightens `findings` and `decision`, both already
    probed. Declining every branching keyword would put four warnings on every composition in the
    ecosystem and teach the reader to skip them."""
    d = consumer()
    try:
        check("no noise from the bundle's own bases",
              [w for w in warnings(d) if "not measured at" in w], [])
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


def test_an_object_behind_a_same_document_ref_is_probed():
    """`handoff.base` reaches its own `$defs` this way today. Only vendored-tree references were
    followed, so an object a base declares under its own `$defs` had no location at all."""
    defs_base = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                 "properties": {"inner": {"$ref": "#/$defs/thing"}},
                 "$defs": {"thing": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/defs.base.schema.json"}],
         "unevaluatedProperties": False},
        vendored={f"{VENDORED}/schemas/defs.base.schema.json": defs_base})
    check("the object behind the fragment is measured", out, ["inner"])


def test_one_neighbour_referenced_twice_keeps_both_sets_of_locations():
    """`seen` was a visited-set for the whole traversal rather than the ancestors of a node, so a
    base referencing one neighbour from two properties lost every location under the second."""
    neighbour = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                 "properties": {"nested": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    twice = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"first": {"$ref": "nb.base.schema.json"},
                            "second": {"$ref": "nb.base.schema.json"}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/twice.base.schema.json"}],
         "unevaluatedProperties": False,
         "properties": {"first": {"allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/nb.base.schema.json"}],
                                  "unevaluatedProperties": False},
                        "second": {"allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/nb.base.schema.json"}],
                                   "unevaluatedProperties": False}}},
        vendored={f"{VENDORED}/schemas/twice.base.schema.json": twice,
                  f"{VENDORED}/schemas/nb.base.schema.json": neighbour})
    check("both occurrences keep their nested location", out, ["first/nested", "second/nested"])


def test_a_tuple_array_is_probed_position_by_position():
    """`prefixItems` was read in the guard and never used, so a tuple-form array of objects was
    probed as though it were an object and its elements never measured."""
    tuple_base = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                  "properties": {"pair": {"prefixItems": [
                      {"type": "object", "properties": {"a": {"type": "string"}}},
                      {"type": "object", "properties": {"b": {"type": "string"}}}]}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/tuple.base.schema.json"}],
         "unevaluatedProperties": False},
        vendored={f"{VENDORED}/schemas/tuple.base.schema.json": tuple_base})
    check("each position is a location of its own", out, ["pair/0", "pair/1"])


def test_an_array_whose_item_schema_is_a_boolean_is_declined():
    """`items: true` is legal and describes no object, so building a location out of the array's
    own properties was measuring something that does not exist."""
    weird = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"anything": {"items": True}}}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/weird.base.schema.json"}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/weird.base.schema.json": weird})
    try:
        check("it says so rather than inventing a location",
              any("item schema is not a schema object" in w for w in warnings(d)), True)
    finally:
        shutil.rmtree(d)


def test_a_root_composition_under_one_of_is_measured_through_a_branch():
    """Per-scope rules are written this way. Generating past the `oneOf` produced a decision no
    branch accepts, so the schema was declined; one branch at a time, keeping the first that
    validates, measures it instead — and the selection is verified like any other instance."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "oneOf": [{"allOf": [{"$ref": BASE}], "unevaluatedProperties": False}]})
    try:
        check("a branch is selected and the composition measured through it — the branch closes "
              "the root, and the objects inside it are reported",
              ("<root>" in open_locations(d), "findings/0" in open_locations(d)), (False, True))
    finally:
        shutil.rmtree(d)


def test_a_closer_parked_in_a_branch_one_level_down_is_reported():
    """The shape README documents for a finding, written wrongly: the closer inside the `allOf`
    branch next to the `$ref` rather than on the object that holds them. The check ran only over
    what applies at the root, so this passed green while every real finding was refused."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "unevaluatedProperties": False,
                "properties": {"findings": {"items": {"allOf": [
                    {"$ref": BASE + "#/properties/findings/items"},
                    {"properties": {"tag": {"type": "string"}},
                     "unevaluatedProperties": False}]}}}})
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("caught by measurement, at the level it happens",
              any(f.startswith("rejects a decision built to satisfy it") and "at findings/0" in f
                  for f in out), True)
    finally:
        shutil.rmtree(d)


def test_a_refusal_of_one_name_shape_is_not_a_closed_object():
    """A `propertyNames` pattern refuses a name for its shape, not for being undeclared. One probe
    name was one shape, so `^[a-z_]+$` read as closed while `sneaky_extra` walked in. Two probes of
    different shapes, and both must be refused."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "propertyNames": {"pattern": "^[a-z_]+$"}})
    try:
        check("the object is reported open, because an ordinary name still gets in",
              "<root>" in open_locations(d), True)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


# The subschema-bearing vocabulary of draft 2020-12, written out here rather than derived, so that
# a keyword the checker learns to ignore has to be added in two places by two different hands.
SUBSCHEMA_KEYWORDS = {
    "properties", "patternProperties", "additionalProperties", "propertyNames", "items",
    "prefixItems", "additionalItems", "contains", "unevaluatedItems", "unevaluatedProperties",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "dependentSchemas", "$ref", "$defs",
    "definitions",
}


ASSERTION_KEYWORDS = {
    "type", "required", "enum", "const", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "format", "minItems",
    "maxItems", "uniqueItems", "minContains", "maxContains", "minProperties", "maxProperties",
    "dependentRequired",
}


def test_every_assertion_keyword_is_either_satisfied_or_declined():
    """The subschema vocabulary told the check where to look; this one tells it how to build a
    value, and an assertion neither satisfied nor declined is what turns a conforming repository
    red — a value that violates it reaches the schema and comes back as the schema's fault.
    Written out here rather than derived, so a keyword takes two hands to classify."""
    mod = checker_module()
    classified = mod.HONOURED_ASSERTIONS | mod.DECLINED_ASSERTIONS
    check("no assertion keyword is unclassified", sorted(ASSERTION_KEYWORDS - classified), [])
    check("and nothing is classified twice",
          sorted(mod.HONOURED_ASSERTIONS & mod.DECLINED_ASSERTIONS), [])
    check("nor classified without being an assertion keyword",
          sorted(classified - ASSERTION_KEYWORDS), [])


def test_every_subschema_keyword_is_either_walked_or_declined():
    """The check's promise is that it measures a stated set of shapes and says which ones it met
    and left alone. That is only true while every keyword that can hold a subschema is in one list
    or the other — and each of five review rounds found one that was in neither, silently."""
    mod = checker_module()
    classified = mod.HANDLED_KEYWORDS | mod.DECLINED_KEYWORDS
    check("no subschema keyword is unclassified", sorted(SUBSCHEMA_KEYWORDS - classified), [])
    check("and nothing is classified twice",
          sorted(mod.HANDLED_KEYWORDS & mod.DECLINED_KEYWORDS), [])
    check("nor classified without being a subschema keyword",
          sorted(classified - SUBSCHEMA_KEYWORDS), [])


def test_every_keyword_the_bundle_uses_is_one_the_generator_walks():
    """The other direction, and the one that catches the next keyword: a base may only use shapes
    the generator understands. A base reaching for `patternProperties` would make this fail, which
    is the conversation to have before it ships rather than after a consumer's contract is
    unmeasured."""
    mod = checker_module()

    def keywords(node):
        if isinstance(node, dict):
            yield from node
            for value in node.values():
                yield from keywords(value)
        elif isinstance(node, list):
            for value in node:
                yield from keywords(value)

    for name in sorted(os.listdir(SCHEMAS)):
        with open(os.path.join(SCHEMAS, name), encoding="utf-8") as fh:
            used = set(keywords(json.load(fh))) & SUBSCHEMA_KEYWORDS
        # `if`/`then` are used to constrain what is already built, which the generator does not
        # need to walk; what must not appear is a keyword that places an object somewhere the
        # generated decision has none.
        places_objects = used & (mod.DECLINED_KEYWORDS - {"if", "then", "else", "not", "anyOf",
                                                          "oneOf", "contains", "dependentSchemas"})
        check(f"{name} uses no shape the generator cannot build", sorted(places_objects), [])


def test_a_narrowed_pattern_is_built_rather_than_declined():
    """A repository narrows a path or a name with a pattern — `exeris-docs` writes
    `^templates/[A-Z-]+-TEMPLATE\\.md$` — and a generator that cannot satisfy it measures nothing at
    all for that schema. Literal runs, escapes, a class with a quantifier and the first alternative
    of a group are built; anything else is declined."""
    mod = checker_module()
    for pattern, expected in ((r"^templates/[A-Z-]+-TEMPLATE\.md$", "templates/A-TEMPLATE.md"),
                              (r"^[a-z0-9]+(-[a-z0-9]+)*$", "a"),
                              (r"^[^\s#]+#[A-Za-z0-9.§-]+$", "a#A"),
                              (r"^[A-Z][A-Z0-9_]*$", "AA"),
                              (r"^ADR-[0-9]{3}$", "ADR-000"),
                              (r"^[A-Z]{2,4}$", "AA")):
        built = mod.from_pattern(pattern)
        check(f"built for {pattern}", (built, bool(built and re.search(pattern, built))),
              (expected, True))


def test_a_value_the_generator_cannot_build_is_a_warning_not_a_verdict():
    """The failure mode this pair exists to prevent: the generator declines a value, carries on
    with a placeholder, the schema rejects the placeholder, and a repository that conforms is told
    it rejects a conforming decision. A rejection after a decline is this check's own limit as much
    as the schema's, and says so."""
    impossible = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "allOf": [{"$ref": BASE},
                            {"properties": {"scope_class": {"pattern": "(?=.*a)(?=.*b)^[ab]{2}$"}}}],
                  "unevaluatedProperties": False}
    d = custom(impossible)
    try:
        errs = [e.split("schema::", 1)[-1] for e in errors(d) if "schema::" in e]
        warns = [w.split("schema::", 1)[-1] for w in warnings(d) if "schema::" in w]
        check("it is not called a schema that rejects a conforming decision",
              any("rejects a decision built to satisfy it" in e for e in errs), False)
        check("and the reader is told nothing was measured, and why",
              any("no decision could be built" in w or "not measured" in w for w in warns), True)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


# ── what a value this check cannot build costs ────────────────────────────────────────────────

UNBUILDABLE = "(?=.*a)(?=.*b)^[ab]{2}$"        # two lookaheads; the generator does not solve these


def test_an_unbuildable_value_costs_its_own_location_and_nothing_else():
    """The rule. An optional property with a pattern this cannot construct used to end the
    measurement — "no decision could be built" — while the run knew perfectly well that `handoffs`
    was wide open three properties away. The value is dropped, the rest is measured, and the
    location is named."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE},
                          {"properties": {
                              "note": {"type": "string", "pattern": UNBUILDABLE},
                              "findings": {"items": {"allOf": [
                                  {"$ref": BASE + "#/properties/findings/items"}],
                                  "unevaluatedProperties": False}},
                              "checks_run": {"items": {"allOf": [
                                  {"$ref": BASE + "#/properties/checks_run/items"}],
                                  "unevaluatedProperties": False}}}}],
                "unevaluatedProperties": False})
    try:
        check("the location that could not be built is named",
              any("not measured at note" in w for w in warnings(d)), True)
        check("and the object left open elsewhere is still reported",
              open_locations(d), ["handoffs/0"])
        check("with no verdict about the schema itself",
              any("rejects a decision" in e for e in errors(d)), False)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


def test_a_schema_wide_decline_is_only_for_a_root_that_cannot_be_built():
    """The other half of the rule: when what cannot be built is REQUIRED, removing it leaves an
    instance the schema refuses, and there is nothing left to measure. That is the one case where
    declining the whole schema is the honest answer."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE},
                          {"properties": {"scope_class": {"pattern": UNBUILDABLE}}}]})
    try:
        check("the schema is declined whole",
              any("no decision could be built" in w for w in warnings(d)), True)
        check("and it is not called a schema that rejects a conforming decision",
              any("rejects a decision" in e for e in errors(d)), False)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


def test_an_array_that_cannot_be_padded_is_declined_not_invented():
    """`minItems: 2` with `uniqueItems: true` was padded with a deep copy of the last element — a
    duplicate, refused by the schema, reported as a hard error against a repository that did what
    the migration asks. An array with no item schema at all was padded with `{}`, an object the
    schema never declares."""
    for name, base, reason in (
            ("uniqueItems", {"type": "object", "properties": {"tags": {
                "type": "array", "minItems": 2, "uniqueItems": True,
                "items": {"type": "object", "properties": {"a": {"type": "string"}}}}}},
             "make distinct"),
            ("no item schema", {"type": "object", "properties": {
                "anything": {"type": "array", "minItems": 1}}}, "no item schema")):
        base["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/arr.base.schema.json"}],
                    "unevaluatedProperties": False},
                   vendored={f"{VENDORED}/schemas/arr.base.schema.json": base})
        try:
            check(f"{name}: declined rather than built wrong",
                  any(reason in w for w in warnings(d)), True)
            check(f"{name}: and no error against the repository",
                  [e.split("schema::", 1)[-1] for e in errors(d) if "schema::" in e], [])
        finally:
            _RUNS.pop(d, None); shutil.rmtree(d)


def test_the_numeric_family_is_satisfied_not_ignored():
    """`minimum` was honoured and `exclusiveMinimum`, `maximum`, `exclusiveMaximum` and
    `multipleOf` were neither satisfied nor declined, so a value outside the range reached the
    schema and came back as the schema's fault."""
    base = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "properties": {"score": {"type": "integer", "exclusiveMinimum": 3, "maximum": 20,
                                     "multipleOf": 5},
                           "inner": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/num.base.schema.json"}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/num.base.schema.json": base})
    try:
        check("the number is built inside every bound", open_locations(d), ["inner"])
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


def test_a_base_that_is_not_valid_json_schema_does_not_end_the_run():
    """A vendored base never goes through `check_schema` — only a repository's own schemas do — so
    `{"type": "string", "minLength": "3"}` reached the generator, raised, and took every other
    finding in the repository with it before the report was written."""
    broken = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
              "properties": {"odd": {"type": "string", "minLength": "3"}}}
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/broken.base.schema.json"}],
                "unevaluatedProperties": False},
               vendored={f"{VENDORED}/schemas/broken.base.schema.json": broken})
    try:
        out = errors(d)                     # raises of its own if the checker crashed
        check("the run survives and says what happened",
              any("could not be measured at all" in e or "no decision could be built" in e
                  for e in out + warnings(d)), True)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


def test_the_error_quoted_is_not_the_annotation_artefact():
    """Errors were sorted by depth and the first quoted, which is systematically the root
    `unevaluatedProperties` line — the one `BUNDLE.md` tells readers to skip. Here the real failure
    is at `findings/0` and the root line is the artefact beside it; where that line is the only
    error, as it is for a closer mis-parked at the root, it is the finding and is quoted."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "unevaluatedProperties": False,
                "properties": {"findings": {"items": {"allOf": [
                    {"$ref": BASE + "#/properties/findings/items"},
                    {"properties": {"tag": {"type": "string"}},
                     "unevaluatedProperties": False}]}}}})
    try:
        quoted = [e.split("schema::", 1)[-1] for e in errors(d) if "rejects a decision" in e]
        check("the rejection is reported", len(quoted), 1)
        check("and it leads with the failure, not with the root's unevaluated line",
              quoted[0].split("; ")[0].startswith("rejects a decision built to satisfy it: at "
                                                  "findings/0"), True)
    finally:
        _RUNS.pop(d, None); shutil.rmtree(d)


# ── what the schema check may read ────────────────────────────────────────────────────────────

def checker_module():
    """The checker itself, for the calls no fixture can reach through the CLI."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("afc_direct", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_schema_refuses_a_path_outside_the_tree_it_is_checking():
    """Called directly, without the resolver in front of it — the case with no coverage and the
    one that makes the invariant real. Every caller today arrives through `ref_target()`, which
    refuses anything not landing in `.agents/vendor/`; nothing makes that true of the next caller,
    and the file this opens is chosen by a `$ref` inside a repository the checker was pointed at.
    A JSON file outside the tree, not a broken one: the old refusal was `json.load` failing, which
    is not a refusal at all — it read the file first."""
    mod = checker_module()
    outside = tempfile.mkdtemp(prefix="outside-")
    inside = tempfile.mkdtemp(prefix="inside-")
    here = os.getcwd()
    try:
        secret = os.path.join(outside, "readable.json")
        with open(secret, "w", encoding="utf-8") as fh:
            json.dump({"read": "it"}, fh)
        neighbour = os.path.join(inside, ".agents", "schemas", "own.schema.json")
        os.makedirs(os.path.dirname(neighbour))
        with open(neighbour, "w", encoding="utf-8") as fh:
            json.dump({"type": "object"}, fh)
        os.chdir(inside)
        check("a JSON file outside the checkout is refused, not parsed",
              mod.load_schema(secret), None)
        check("and the same file by a traversing relative path is refused too",
              mod.load_schema(os.path.join(".agents", "schemas", "..", "..", "..",
                                           os.path.basename(outside), "readable.json")), None)
        check("while the repository's own schema is read",
              mod.load_schema(os.path.join(".agents", "schemas", "own.schema.json")),
              {"type": "object"})
    finally:
        os.chdir(here)
        shutil.rmtree(outside)
        shutil.rmtree(inside)


def test_a_ref_that_leaves_the_checkout_is_reported():
    """The other half: the sink refuses silently, because `None` is what a caller already reads as
    a reference that led nowhere, and the reference itself is named where references are judged."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"x": {"$ref": "/etc/hosts#/anything"}}})
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a reference out of the checkout is a finding with a reason",
              any("resolves outside the repository" in f for f in out), True)
    finally:
        shutil.rmtree(d)


def test_a_pointer_into_a_neighbouring_schema_still_resolves():
    """Why the boundary is the checkout and not the vendored subtree: a `$ref` at a pointer inside
    a `.agents/schemas/` neighbour is an ordinary thing for a repository to write, and containing
    the reader to the vendored tree would have reported it as a pointer that does not resolve."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"x": {"$ref": "shared.schema.json#/$defs/thing"}}})
    try:
        with open(os.path.join(d, ".agents", "schemas", "shared.schema.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"$schema": "https://json-schema.org/draft/2020-12/schema",
                       "$defs": {"thing": {"type": "object"}}}, fh)
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a neighbour's pointer resolves", [f for f in out if "pointer" in f], [])
    finally:
        shutil.rmtree(d)


# ── one case per cause of the walker this check used to be ────────────────────────────────────

def test_a_composition_declared_through_defs():
    """Cause: liveness was guessed from `/$defs/` in a pointer, so a composition kept there was
    read as applying to nothing and not checked at all. What applies at the root is now resolved:
    a local `$ref` is followed, however many hops."""
    def routed(closer: bool) -> dict:
        inner = {"allOf": [{"$ref": BASE}]}
        if closer:
            inner["unevaluatedProperties"] = False
        return {"$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": "#/$defs/verdict", "$defs": {"verdict": inner}}
    check("an open composition under `$defs` is reported, not skipped",
          "<root>" in open_locations_for(routed(closer=False)), True)
    check("and a closed one under `$defs` is accepted, because it does close",
          "<root>" in open_locations_for(routed(closer=True)), False)


def test_a_base_object_written_without_a_type_keyword():
    """Cause: an object was recognised by a literal `"type": "object"`, so a base declaring one
    with `properties` and `required` alone — valid, ordinary JSON Schema — was invisible and owed
    nothing."""
    typeless = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {"a": {"type": "string"}}, "required": ["a"]}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/typeless.base.schema.json"}]},
        vendored={f"{VENDORED}/schemas/typeless.base.schema.json": typeless})
    check("an object is whatever behaves like one", out, ["<root>"])


def test_a_base_that_closes_what_it_forwards():
    """Cause: a node carrying `$ref` plus `unevaluatedProperties: false` was read as a bare alias,
    because the closer was in the list of keywords that mean "this only forwards". The walker then
    stepped through the closer it was looking for and demanded one the base already had."""
    outer = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"inner": {"$ref": "inner.base.schema.json",
                                      "unevaluatedProperties": False}}}
    inner = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
             "properties": {"a": {"type": "string"}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/outer.base.schema.json"}],
         "unevaluatedProperties": False},
        vendored={f"{VENDORED}/schemas/outer.base.schema.json": outer,
                  f"{VENDORED}/schemas/inner.base.schema.json": inner})
    check("a closer the base already carries is not work for the repository", out, [])


def test_a_property_name_a_pointer_would_have_to_escape():
    """Cause: one half built JSON pointers raw and the other unescaped them, so `a/b~c` was one
    object to the requirement and another to the closure. The locations are instance paths now, and
    the same ambiguity came back in them — a property named `a/b~c` read exactly like a nested
    path — so a step containing the separator is escaped the way a pointer escapes it."""
    odd = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
           "properties": {"a/b~c": {"type": "object", "properties": {"x": {"type": "string"}}}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/odd.base.schema.json"}],
         "unevaluatedProperties": False},
        vendored={f"{VENDORED}/schemas/odd.base.schema.json": odd})
    check("a separator inside a name is escaped, so the location cannot be read as a path",
          out, ["a~1b~0c"])


def test_a_shape_reached_through_another_base_file():
    """Cause: following a forwarding `$ref` across files was pointer arithmetic, and it dropped the
    pointer — walking the whole target document instead of the subschema named. Nothing is followed
    now; the validator resolves what it resolves."""
    check("the object behind the hop is named, and nothing else is",
          closer_errors(handoffs=False), ["handoffs/0"])


def test_a_reference_into_a_tree_no_import_pins():
    """Cause: the pinned tree was read from the first import alone, so a repository pinning two
    bundles was told the second was a stray. And the closer question is asked of the objects behind
    the reference either way — an instance is validated against the shapes it actually reaches."""
    two_imports = MANIFEST.replace(
        "imports:\n  - bundle: exeris-agents",
        "imports:\n  - bundle: other-bundle\n    version: 1.0.0\n    ref: dead\n"
        "    sha256: 'sha256:00'\n  - bundle: exeris-agents")
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "allOf": [{"$ref": BASE}], "unevaluatedProperties": False},
               manifest=two_imports)
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a second pinned bundle is pinned", off_pin(out), [])
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
