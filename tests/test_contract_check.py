#!/usr/bin/env python3
"""One case per way of failing each contract gate — `ci/contract_check.py`.

The suite exists for the same reason `.github`'s own R2 does: a rule nothing can fail on is not
enforced, it is described. So every case here was run against a checker with the rule it covers
removed, which is the only sense in which a case "covers" anything: G1, both halves of G2, G3, G4,
the R5 locator, `migration_covers`, the ref shape check, the stated-base resolution check, the root
directory check and the read allowlist each turned this suite red when their rule was taken out.

One mutation did not, and it is recorded rather than tidied away. Removing the `break` that ends
`release_section` at the next release heading changes nothing the suite can see, because the loop
also leaves the section when that heading flips `inside` to False. The boundary is enforced twice
and neither half is load-bearing alone, so the case below asserts the behaviour and not that one
line. A green run that means less than it looks is worth saying out loud.

Two of them are regressions rather than requirements. `test_migration_range_covers_its_target_only`
is the bug this checker shipped with for an hour: `## 1.4 → 2.0` was read as covering 1.4.0 as well
as 2.0.0, which hid a standing debt behind the section that supersedes it. And
`test_release_section_is_bounded` is `release.yml`'s own lesson — a fixed window after a heading
spills into the neighbouring release, so a version with no Breaking section of its own passes on
somebody else's.

Run: python3 tests/test_contract_check.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main                                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ci"))
import contract_check as cc                                            # noqa: E402

BASE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "agent": {"type": "string", "maxLength": 80},
        "decision": {"enum": ["PASS", "CONDITIONAL", "BLOCKED"]},
        "findings": {"type": "array", "items": {"type": "object",
                                                "properties": {"reason": {"type": "string"}}}},
    },
}


def rep():
    return cc.Report(name="test")


def rules(r) -> list[str]:
    return sorted(f.rule for f in r.findings)


def tree(changelog: str = "", migration: str = "", schema: dict | None = None) -> str:
    """A scratch root carrying only what the rule under test reads."""
    d = tempfile.mkdtemp(prefix="contract-check-")
    if changelog:
        open(os.path.join(d, "CHANGELOG.md"), "w", encoding="utf-8").write(changelog)
    if migration:
        open(os.path.join(d, "MIGRATION.md"), "w", encoding="utf-8").write(migration)
    if schema is not None:
        os.makedirs(os.path.join(d, "bundle", "schemas"))
        with open(os.path.join(d, "bundle", "schemas", "verdict.base.schema.json"),
                  "w", encoding="utf-8") as fh:
            json.dump(schema, fh)
    return d


# --------------------------------------------------------------- G1: bundle ↔ changelog

def test_bundle_change_without_changelog_is_an_error():
    r = rep()
    cc.gate_bundle_carries_changelog({"bundle/schemas/verdict.base.schema.json"}, r)
    check("G1 fires", rules(r), ["G1"])


def test_bundle_change_with_changelog_passes():
    r = rep()
    cc.gate_bundle_carries_changelog({"bundle/policies/x.md", "CHANGELOG.md"}, r)
    check("G1 satisfied", rules(r), [])


def test_g1_covers_documentation_inside_the_bundle():
    """`bundle/BUNDLE.md` is vendored into a consumer like everything else under `bundle/`."""
    r = rep()
    cc.gate_bundle_carries_changelog({"bundle/BUNDLE.md"}, r)
    check("BUNDLE.md counts", rules(r), ["G1"])


def test_g1_ignores_changes_outside_the_bundle():
    r = rep()
    cc.gate_bundle_carries_changelog({"tools/agents_render.py", "README.md"}, r)
    check("tools/ is not bundle/", rules(r), [])


# ------------------------------------------------------------------ G2: the bases are open

def test_clean_base_passes():
    r = rep()
    cc.gate_bases_are_open(tree(schema=BASE_SCHEMA), r)
    check("clean base", rules(r), [])
    check("and was read", r.checked, 1)


def test_root_closer_in_a_base_is_an_error():
    s = dict(BASE_SCHEMA, **{"additionalProperties": False})
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("root additionalProperties", rules(r), ["G2"])


def test_nested_closer_in_a_base_is_an_error():
    """The closer that matters most is the one a root-only check would miss."""
    s = json.loads(json.dumps(BASE_SCHEMA))
    s["properties"]["findings"]["items"]["unevaluatedProperties"] = False
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("nested unevaluatedProperties", rules(r), ["G2"])


def test_unevaluated_items_in_a_base_is_an_error():
    s = json.loads(json.dumps(BASE_SCHEMA))
    s["properties"]["findings"]["unevaluatedItems"] = False
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("unevaluatedItems", rules(r), ["G2"])


def test_a_closer_that_is_a_schema_is_not_a_closer():
    """`additionalProperties: {…}` constrains extra properties; it does not refuse them."""
    s = dict(BASE_SCHEMA, **{"additionalProperties": {"type": "string"}})
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("schema-valued keyword", rules(r), [])


def test_role_enum_in_a_base_is_an_error():
    s = json.loads(json.dumps(BASE_SCHEMA))
    s["properties"]["agent"] = {"enum": ["exeris-agents-reviewer"]}
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("agent enum leaks", rules(r), ["G2"])


def test_role_const_in_a_base_is_an_error():
    s = json.loads(json.dumps(BASE_SCHEMA))
    s["properties"]["scope_class"] = {"const": "docs-only"}
    r = rep()
    cc.gate_bases_are_open(tree(schema=s), r)
    check("scope_class const leaks", rules(r), ["G2"])


def test_a_non_role_enum_is_left_alone():
    """`decision` is the base's own three-state vocabulary and belongs there."""
    r = rep()
    cc.gate_bases_are_open(tree(schema=BASE_SCHEMA), r)
    check("decision enum is fine", rules(r), [])


def test_unreadable_base_is_an_error_not_a_pass():
    d = tempfile.mkdtemp(prefix="contract-check-")
    os.makedirs(os.path.join(d, "bundle", "schemas"))
    open(os.path.join(d, "bundle", "schemas", "verdict.base.schema.json"), "w").write("{oops")
    r = rep()
    cc.gate_bases_are_open(d, r)
    check("broken JSON", rules(r), ["G2"])


def test_the_shipped_bases_pass_their_own_gate():
    r = rep()
    cc.gate_bases_are_open(ROOT, r)
    check("this repository's bases", rules(r), [])
    check("all three read", r.checked, 3)


# --------------------------------------------------- G3/G4: a release pays for its Breaking

RELEASED = """\
# Changelog

## [3.0.0] - 2026-09-18

### Breaking

- **A required field arrives.** Every consumer edits its schema.

### Added

- Something.

## [2.0.0] - 2026-09-15

### Breaking

- **The bases refuse nothing on their own.**
"""

NOTHING = RELEASED.replace("- **A required field arrives.** Every consumer edits its schema.",
                           "- Nothing. A new check, and a repository that was conforming stays so.")
NO_SECTION = """\
# Changelog

## [3.0.0] - 2026-09-18

### Fixed

- Something.

## [2.0.0] - 2026-09-15

### Breaking

- **The bases refuse nothing on their own.**
"""
ADDS_RELEASE = "+## [3.0.0] - 2026-09-18\n"


def test_new_release_with_breaking_needs_a_migration_section():
    d = tree(RELEASED, "# Migration\n\n## 1.4 → 2.0\n")
    r = rep()
    cc.gate_release_sections(d, set(), ADDS_RELEASE, "", r)
    check("G3 fires", rules(r), ["G3"])


def test_migration_section_added_in_the_same_change_satisfies_it():
    d = tree(RELEASED, "# Migration\n\n## 2.0 → 3.0\n\n## 1.4 → 2.0\n")
    r = rep()
    cc.gate_release_sections(d, {"MIGRATION.md"}, ADDS_RELEASE, "+## 2.0 → 3.0\n", r)
    check("G3 satisfied by the diff", rules(r), [])


def test_a_migration_section_already_there_and_touched_satisfies_it():
    d = tree(RELEASED, "# Migration\n\n## 2.0 → 3.0\n")
    r = rep()
    cc.gate_release_sections(d, {"MIGRATION.md"}, ADDS_RELEASE, "+Some rewording.\n", r)
    check("G3 satisfied by the tree", rules(r), [])


def test_breaking_nothing_needs_no_migration_section():
    d = tree(NOTHING, "# Migration\n")
    r = rep()
    cc.gate_release_sections(d, set(), ADDS_RELEASE, "", r)
    check("'Breaking: nothing' is an answer", rules(r), [])


def test_a_release_without_a_breaking_section_is_an_error():
    d = tree(NO_SECTION, "# Migration\n")
    r = rep()
    cc.gate_release_sections(d, set(), ADDS_RELEASE, "", r)
    check("G4 fires", rules(r), ["G4"])


def test_a_pull_request_that_adds_no_release_is_not_asked_for_one():
    d = tree(RELEASED, "# Migration\n")
    r = rep()
    cc.gate_release_sections(d, {"bundle/policies/x.md"}, "+- A new entry under Unreleased\n", "", r)
    check("no release, no rule", rules(r), [])


def test_unreleased_is_not_a_release():
    d = tree("# Changelog\n\n## [Unreleased]\n\n### Breaking\n\n- Nothing.\n", "# Migration\n")
    r = rep()
    cc.gate_release_sections(d, set(), "+## [Unreleased]\n", "", r)
    check("Unreleased is skipped", rules(r), [])


# ------------------------------------------------------------------- the readers themselves

def test_release_section_is_bounded():
    """A section must not borrow the next release's Breaking — `release.yml`'s own lesson."""
    section = cc.release_section(NO_SECTION, "3.0.0")
    check("3.0.0 carries no Breaking", cc.breaking_body(section), None)
    check("2.0.0 does", cc.breaking_body(cc.release_section(NO_SECTION, "2.0.0")) is None, False)


def test_breaking_emptiness_reads_the_house_style():
    check("bare nothing", cc.breaking_is_empty("- Nothing. The first release line."), True)
    check("bold nothing", cc.breaking_is_empty("- **Nothing moves the contract.** No manifest key"), True)
    check("empty section", cc.breaking_is_empty("\n\n"), True)
    check("content", cc.breaking_is_empty("- **The bases refuse nothing on their own.**"), False)


def test_migration_range_covers_its_target_only():
    """`## 1.4 → 2.0` is the section for 2.0.0. Read as covering 1.4.0 it hides a standing debt."""
    text = "# Migration\n\n## 1.4 → 2.0\n"
    check("covers the target", cc.migration_covers(text, "2.0.0"), True)
    check("not the source", cc.migration_covers(text, "1.4.0"), False)
    check("nor an unrelated one", cc.migration_covers(text, "3.0.0"), False)


def test_a_plain_heading_covers_its_own_version():
    check("plain heading", cc.migration_covers("## 3.0\n", "3.0.0"), True)
    check("full triple", cc.migration_covers("### 3.0.1\n", "3.0.1"), True)


# ----------------------------------------------------------------- the diff plumbing, for real

def git_repo() -> str:
    d = tempfile.mkdtemp(prefix="contract-check-git-")
    run = lambda *a: subprocess.run(a, cwd=d, capture_output=True, text=True, check=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    os.makedirs(os.path.join(d, "bundle", "policies"))
    open(os.path.join(d, "bundle", "policies", "p.md"), "w").write("Never do X.\nAlways do Y.\n")
    open(os.path.join(d, "CHANGELOG.md"), "w").write("# Changelog\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    run("git", "branch", "base-ref")
    return d


def test_the_diff_plumbing_answers_over_a_real_repository():
    no_ambient_base()
    d = git_repo()
    open(os.path.join(d, "bundle", "policies", "p.md"), "w").write("Always do Y.\n")
    subprocess.run(["git", "commit", "-qam", "relax"], cwd=d, check=True, capture_output=True)
    check("base resolves", cc.resolve_base(d, "base-ref"), "base-ref")
    check("the change is seen", cc.changed_paths(d, "base-ref"), {"bundle/policies/p.md"})
    r = rep()
    cc.locate_policy_relaxation(d, "base-ref", {"bundle/policies/p.md"}, r)
    check("the removed line is evidence", rules(r), ["R4"])
    g = rep()
    cc.gate_bundle_carries_changelog(cc.changed_paths(d, "base-ref"), g)
    check("and G1 fires on the same diff", rules(g), ["G1"])


def refused(fn) -> str:
    """Why the run stopped, not merely that it did.

    The reason is the assertion: with the shape check taken out, `--upload-pack=…` still stops the
    run — git cannot resolve it either — so a case asserting only "it stopped" passes against a
    checker that no longer has the guard it is supposed to cover. The two refusals say different
    things and the cases read which one spoke.
    """
    try:
        fn()
    except SystemExit as exc:
        text = str(exc)
        if "not a plain ref name" in text:
            return "shape"
        if "does not resolve" in text:
            return "unresolved"
        if "is not a directory" in text:
            return "not-a-directory"
        return f"stopped: {text}"
    return "returned"


def no_ambient_base():
    """`resolve_base` reads the environment, so a case about it has to own the environment.

    Without this the suite passes or fails by where it is run: `GUARDRAILS_BASE` is set in the
    `contract` job and absent in `tools`, and a case asserting the derived chain would be answering
    a different question in each.
    """
    for key in ("GUARDRAILS_BASE", "GITHUB_BASE_REF"):
        os.environ.pop(key, None)


def test_a_stated_base_that_is_an_option_stops_the_run():
    """`git` reads a leading `-` as an option, not a revision — `--upload-pack=` is the shape.

    It stops rather than falling back: a base the caller named and this refused, answered by
    diffing against `main` instead, is a gate reporting on a question nobody asked.
    """
    no_ambient_base()
    d = git_repo()
    check("an option", refused(lambda: cc.resolve_base(d, "--upload-pack=touch /tmp/x")), "shape")
    check("a shell attempt", refused(lambda: cc.resolve_base(d, "base-ref; rm -rf /")), "shape")
    check("a plain ref is not refused", cc.resolve_base(d, "base-ref"), "base-ref")


def test_a_stated_base_that_does_not_resolve_stops_the_run():
    """The shape check is a filter in front of git, never a substitute for asking it."""
    no_ambient_base()
    d = git_repo()
    check("well-shaped and absent", refused(lambda: cc.resolve_base(d, "no-such-branch")), "unresolved")


def test_a_derived_candidate_only_yields_to_the_next():
    """Nothing stated, so the chain is this function's own guesswork and a miss is not an error."""
    no_ambient_base()
    d = git_repo()
    check("falls through to main", cc.resolve_base(d, None), "main")


def test_the_root_must_be_a_directory():
    missing = os.path.join(tempfile.mkdtemp(), "not-there")
    check("a missing root", refused(lambda: cc.resolved_root(missing)), "not-a-directory")


def test_the_checker_reads_only_the_files_it_names():
    """Three files and no others. A name outside the set is a programming error, not an empty read."""
    d = tree("# Changelog\n")
    check("a named file", cc.read(d, "CHANGELOG.md"), "# Changelog\n")
    try:
        cc.read(d, "../../../etc/passwd")
        check("anything else raises", "returned", "ValueError")
    except ValueError:
        check("anything else raises", "ValueError", "ValueError")


def test_no_base_is_reported_not_skipped():
    """A diff-scoped rule with nothing to diff says so; the tree-scoped one still runs."""
    d = tree(schema=BASE_SCHEMA)
    r = rep()
    cc.run_gate(d, None, r)
    check("gate is green", r.emit(), 0)
    check("and G2 still read the bases", r.checked, 1)


# ------------------------------------------------------------------------- the locators

def test_an_executable_change_without_a_case_is_located():
    r = rep()
    cc.locate_rule_without_case({"tools/agents_render.py"}, r)
    check("R5 fires", rules(r), ["R5"])


def test_an_executable_change_with_a_case_is_not():
    r = rep()
    cc.locate_rule_without_case({"tools/agents_render.py", "tests/test_render.py"}, r)
    check("R5 satisfied", rules(r), [])


def test_a_documentation_change_is_not_an_executable_one():
    r = rep()
    cc.locate_rule_without_case({"README.md", "bundle/BUNDLE.md"}, r)
    check("prose needs no case", rules(r), [])


def test_an_adapter_change_is_located():
    r = rep()
    cc.locate_adapter_change({"tools/adapters/claude.yaml"}, r)
    check("R7 fires", rules(r), ["R7"])


def test_the_publication_surface_is_located():
    r = rep()
    cc.locate_publication_surface({".github/workflows/release.yml"}, r)
    check("R8 fires", rules(r), ["R8"])


def test_a_policy_change_that_only_adds_is_not_a_relaxation():
    d = git_repo()
    with open(os.path.join(d, "bundle", "policies", "p.md"), "a") as fh:
        fh.write("Also never do Z.\n")
    subprocess.run(["git", "commit", "-qam", "restrict"], cwd=d, check=True, capture_output=True)
    r = rep()
    cc.locate_policy_relaxation(d, "base-ref", {"bundle/policies/p.md"}, r)
    check("adding is not relaxing", rules(r), [])


def test_the_standing_debt_is_this_repository_s_own():
    """Measured, and the reason G3/G4 are forward-only: three releases cannot satisfy them."""
    r = rep()
    cc.locate_standing_debt(ROOT, r)
    check("three standing gaps", len(r.findings), 3)
    check("all of them debt", set(rules(r)), {"debt"})


if __name__ == "__main__":
    main(globals())
