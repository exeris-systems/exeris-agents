#!/usr/bin/env python3
"""Composition is checked, not assumed — agents-md-schema.md rule 5.

Every case here passed silently before. A profile could name a policy, a skill or a handoff target
that does not exist, or a `bundle:` policy in a repository pinning no bundle, and the check
reported `0 errors` — so an empty composition, a typo and a correct profile were indistinguishable.
Rule 5 says a profile composes by reference rather than by copying, which is only worth more than
copying if the references are known to point at something.

Run: python3 tests/test_composition.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "tools", "agents_file_check.py")
RENDERER = os.path.join(ROOT, "tools", "agents_render.py")

MANIFEST = """\
version: 2
repository: fixture
imports: []
agents: [alpha]
skills: [some-skill]
workflows: []
policies: [house-rule]
references: []
schemas: []
nested: []
# The renderer reads this to know which vendors to render for: without it the
# missing-required-field test renders nothing and asserts against an empty run.
adapters:
  claude:
    target: .claude
    agents: ".claude/agents/{name}.md"
    workflows: ".claude/skills/{name}/SKILL.md"
    skills: symlink
    skills-link: ".claude/skills"
degradations: {}
provider-owned: []
"""

PROFILE = """\
---
name: alpha
description: A role that exists, for a repository that exists, composing things that exist.
role: reviewer
mode: read-only
capabilities: [read]
{extra}---

Body.
"""


def repo(extra: str = "", *, pin: bool = False, vendored_policy: bool = False) -> str:
    d = tempfile.mkdtemp(prefix="composition-")
    os.makedirs(os.path.join(d, ".git"))
    os.makedirs(os.path.join(d, ".agents", "agents", "alpha"))
    os.makedirs(os.path.join(d, ".agents", "policies"))
    os.makedirs(os.path.join(d, ".agents", "skills", "some-skill"))
    open(os.path.join(d, "AGENTS.md"), "w").write("# fixture\n\nPoints at `.agents/`.\n")
    open(os.path.join(d, ".agents", "policies", "house-rule.md"), "w").write("# house rule\n")
    open(os.path.join(d, ".agents", "skills", "some-skill", "SKILL.md"), "w").write(
        "---\nname: some-skill\ndescription: A skill that exists and says when it applies, at length.\n---\n\nBody.\n")
    manifest = MANIFEST
    if pin:
        manifest = manifest.replace(
            "imports: []",
            "imports:\n  - bundle: exeris-agents\n    version: 9.9.9\n    ref: deadbeef\n"
            "    sha256: 'sha256:00'")
        vd = os.path.join(d, ".agents", "vendor", "exeris-agents-9.9.9", "policies")
        os.makedirs(vd)
        if vendored_policy:
            open(os.path.join(vd, "shared-rule.md"), "w").write("# shared\n")
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(manifest)
    open(os.path.join(d, ".agents", "agents", "alpha", "AGENT.md"), "w").write(
        PROFILE.format(extra=extra))
    return d


def errors(d: str) -> list[str]:
    """Annotations, after asserting the checker actually ran.

    Ignoring the return code and stderr let every "expect 0 hits" assertion pass when the checker
    crashed and emitted nothing — the report-success-while-doing-nothing shape this repository has
    now fixed three times. A crash is a failure of the test, not a clean result.
    """
    proc = subprocess.run([sys.executable, CHECKER, "--root", d], capture_output=True, text=True)
    if proc.returncode not in (0, 1) or "Traceback" in proc.stderr:
        raise AssertionError(f"checker did not run: rc={proc.returncode}\n{proc.stderr[-500:]}")
    if "agents_file_check" not in proc.stdout:
        raise AssertionError(f"checker produced no report:\n{proc.stdout[-500:]}")
    return [l for l in proc.stdout.splitlines() if l.startswith("::error")]


def rule_hits(d: str, rule: str) -> int:
    return sum(1 for l in errors(d) if f" {rule}::" in l)


# ── the reference points at something, or it is not a reference ───────────────────────────────

def test_a_correct_profile_is_clean():
    d = repo("policies: [house-rule]\nskills: [some-skill]\n")
    check("a profile whose references all resolve", rule_hits(d, "composition"), 0)
    shutil.rmtree(d)


def test_a_policy_that_does_not_exist():
    d = repo("policies: [house-rule, no-such-policy]\n")
    check("a policy that does not exist", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_a_reference_that_does_not_exist():
    d = repo("references: [nowhere]\n")
    check("a reference that does not exist", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_a_skill_that_does_not_exist():
    d = repo("skills: [some-skill, ghost]\n")
    check("a skill that does not exist", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_a_handoff_to_a_role_that_does_not_exist():
    d = repo("handoffs:\n  - {agent: nobody, when: always, blocking: false}\n")
    check("a handoff to a role that does not exist", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_a_handoff_to_human_is_allowed():
    d = repo("handoffs:\n  - {agent: human, when: always, blocking: true}\n")
    check("`human` is a legitimate handoff target", rule_hits(d, "composition"), 0)
    shutil.rmtree(d)


def test_a_handoff_forward_to_a_role_listed_later():
    """Two passes, or ordering decides the verdict."""
    d = repo("handoffs:\n  - {agent: alpha, when: self-reference, blocking: false}\n")
    check("a handoff naming a role the listing has not reached yet",
          rule_hits(d, "composition"), 0)
    shutil.rmtree(d)


# ── the bundle prefix ─────────────────────────────────────────────────────────────────────────

def test_bundle_policy_without_a_pinned_bundle():
    d = repo("policies: [bundle:shared-rule]\n")
    check("`bundle:` in a repository pinning no bundle", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_bundle_policy_that_is_not_in_the_vendored_tree():
    d = repo("policies: [bundle:shared-rule]\n", pin=True, vendored_policy=False)
    check("`bundle:` naming a policy the vendored tree does not hold",
          rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_bundle_policy_that_is_vendored():
    d = repo("policies: [bundle:shared-rule]\n", pin=True, vendored_policy=True)
    check("`bundle:` resolving into the vendored tree", rule_hits(d, "composition"), 0)
    shutil.rmtree(d)


# ── the renderer names what is missing ────────────────────────────────────────────────────────

def test_renderer_names_a_missing_required_field():
    d = repo()
    path = os.path.join(d, ".agents", "agents", "alpha", "AGENT.md")
    open(path, "w").write("---\nname: alpha\nrole: reviewer\nmode: read-only\n"
                          "capabilities: [read]\n---\n\nBody.\n")
    adapters = os.path.join(ROOT, "tools", "adapters")
    proc = subprocess.run([sys.executable, RENDERER, "--root", d, "--adapters", adapters],
                          capture_output=True, text=True)
    check("a profile without `description` is named, not a KeyError",
          "KeyError" in proc.stderr, False)
    check("and the message says which file and which field",
          "no 'description'" in proc.stderr and "alpha" in proc.stderr, True)
    shutil.rmtree(d)



# ── shapes that used to produce one finding per character, or none at all ─────────────────────

def test_a_scalar_where_a_list_belongs_is_one_finding():
    d = repo("policies: house\n")
    check("a scalar `policies:` is one finding, not one per character",
          rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_a_pinned_but_unvendored_bundle_is_one_finding():
    d = repo("policies: [bundle:a, bundle:b, bundle:c]\n", pin=True)
    shutil.rmtree(os.path.join(d, ".agents", "vendor"))
    check("a bundle pinned but not vendored names the one cause, not N missing files",
          rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_evals_accepts_the_dot_agents_spelling():
    d = repo("evals: .agents/evals\n")
    os.makedirs(os.path.join(d, ".agents", "evals"), exist_ok=True)
    p = subprocess.run([sys.executable, CHECKER, "--root", d], capture_output=True, text=True)
    check("`.agents/`-prefixed evals is accepted, as `output` is",
          "evals directory" in p.stdout, False)
    shutil.rmtree(d)


def test_a_role_directory_without_an_agent_md_is_not_a_role():
    d = repo("handoffs:\n  - {agent: hollow, when: x, blocking: false}\n")
    os.makedirs(os.path.join(d, ".agents", "agents", "hollow"))
    check("an empty directory does not satisfy 'is a role'", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_skills_does_not_silently_accept_the_bundle_prefix():
    d = repo("skills: [bundle:some-skill]\n")
    p = subprocess.run([sys.executable, CHECKER, "--root", d], capture_output=True, text=True)
    check("the message says the prefix is unsupported rather than 'not a skill'",
          "only policies and references support" in p.stdout, True)
    shutil.rmtree(d)


def test_a_default_workflow_is_validated():
    d = repo("workflow: no-such-workflow\n")
    check("rule 5's fourth reference kind", rule_hits(d, "composition"), 1)
    shutil.rmtree(d)


def test_renderer_names_a_missing_field_in_a_workflow_too():
    d = repo()
    os.makedirs(os.path.join(d, ".agents", "workflows"), exist_ok=True)
    open(os.path.join(d, ".agents", "workflows", "w.md"), "w").write(
        "---\nname: w\n---\n\nBody.\n")
    m = os.path.join(d, ".agents", "manifest.yaml")
    # Read fully, THEN write: `open(m, "w")` truncates before the read on the same line would run.
    body = open(m, encoding="utf-8").read().replace("workflows: []", "workflows: [w]")
    open(m, "w", encoding="utf-8").write(body)
    proc = subprocess.run([sys.executable, RENDERER, "--root", d,
                           "--adapters", os.path.join(ROOT, "tools", "adapters")],
                          capture_output=True, text=True)
    check("a workflow without `description` is named, not a KeyError",
          ("KeyError" in proc.stderr, "no 'description'" in proc.stderr), (False, True))
    shutil.rmtree(d)


if __name__ == "__main__":
    main(globals())
