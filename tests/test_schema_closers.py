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


OPEN_AT = "may carry any property at "


def open_locations(repo: str) -> list[str]:
    """The instance locations the checker says nothing refuses a property at."""
    return sorted(l.split(OPEN_AT, 1)[1].split(" ", 1)[0] for l in errors(repo) if OPEN_AT in l)


def closer_errors(**shape) -> list[str]:
    d = consumer(**shape)
    try:
        return open_locations(d)
    finally:
        shutil.rmtree(d)


def open_locations_for(composed: dict, **kwargs) -> list[str]:
    d = custom(composed, **kwargs)
    try:
        return open_locations(d)
    finally:
        shutil.rmtree(d)


def custom(composed: dict, *, vendored: dict | None = None, pin: str = "2.0.0",
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
        body = manifest or MANIFEST
        fh.write(body.replace("verdict.schema.json", name) if pin == "2.0.0"
                 else body.replace("verdict.schema.json", name).replace("2.0.0", pin, 1))
    with open(os.path.join(d, ".agents", "schemas", name), "w", encoding="utf-8") as fh:
        json.dump(composed, fh, indent=2)
    return d


def findings_for(repo: str) -> list[str]:
    try:
        return [l.split("schema::", 1)[-1] for l in errors(repo) if "schema::" in l]
    finally:
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
    out = findings_for(stray(closed_base=False))
    check("composing over a vendored tree the manifest does not pin is reported",
          len(off_pin(out)), 1)
    check("and the objects behind it are still asked the question — an instance is validated "
          "against them whichever tree they were reached through",
          sum(1 for f in out if OPEN_AT in f) > 0, True)


def test_a_stray_reference_is_heard_from_with_nothing_else_wrong():
    """The case that moved the report out of the closer check. The old tree's bases still close
    themselves, so the closer rule has nothing to say about this schema at all — and a repository
    whose reference points at a tree its pin does not vouch for should not need a second defect
    before anything tells it."""
    out = findings_for(stray(closed_base=True))
    check("the stray reference is reported on its own", len(off_pin(out)), 1)
    check("and it is the only thing reported", len(out), 1)


def test_a_stray_reference_is_reported_once_beside_a_closure_problem():
    """Two checks now touch the same reference. The one that judges references reports it; the one
    that judges closure uses it and says nothing about it."""
    out = findings_for(stray(closed_base=False))
    check("one annotation for the stray tree, not one per check that noticed",
          len(off_pin(out)), 1)
    check("and the closure findings are still there beside it",
          len([f for f in out if OPEN_AT in f]) > 0, True)


def test_a_closer_in_a_dead_branch_does_not_launder_an_open_one():
    """`$defs` is not applied to anything unless something references it. A closer parked there
    was counted as closing the object for the whole document, so the live composition — the one
    every instance is actually validated against — could be wide open with nothing red."""
    out = findings_for(custom(
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
    out = findings_for(custom(
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
    out = findings_for(custom(
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
    out = findings_for(custom(
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
    out = findings_for(custom(
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


def test_a_base_referenced_somewhere_an_instance_never_meets_is_reported():
    """The last branch that returned in silence. The probe is built from what applies at an
    instance's root, so a `$ref` into a base parked anywhere else leaves the question unasked —
    and unasked was indistinguishable from answered: no closer check and no message, on a schema
    that may well carry an open contract."""
    d = custom({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"verdict": {"$ref": BASE}}})
    try:
        out = [l.split("schema::", 1)[-1] for l in errors(d) if "schema::" in l]
        check("a base referenced away from the root is reported rather than skipped",
              any("not where an instance meets" in f for f in out), True)
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
    object to the requirement and another to the closure. There are no pointers here now — a
    location is where it is in the instance."""
    odd = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
           "properties": {"a/b~c": {"type": "object", "properties": {"x": {"type": "string"}}}}}
    out = open_locations_for(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "allOf": [{"$ref": f"../vendor/{VENDORED}/schemas/odd.base.schema.json"}],
         "unevaluatedProperties": False},
        vendored={f"{VENDORED}/schemas/odd.base.schema.json": odd})
    check("the location is named as an instance carries it", out, ["a/b~c"])


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
