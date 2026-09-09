#!/usr/bin/env python3
"""Regression tests for the L0 dispatcher.

Every case here is a defect that shipped. Three review rounds found the enforcement layer
reporting enforcement it did not perform, and each round was answered with a hand-run check that
was not kept — so the next change re-broke what the last one fixed. These are the kept version.

Run: python3 tests/test_hook.py   (no pytest; the bundle has no runtime dependencies but pyyaml)
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "bundle", "hooks", "bin", "hook.py")

HOOKS_YAML = """\
version: 1
state-dir: .agents-state
hooks:
  - id: deny-irreversible
    event: pre-tool
    tool: shell
    decision: deny
    reason: "test policy: irreversible"
    match:
      - 'git\\s+push\\b[^|;&]*(--force(?!-with-lease)|--mirror)'
      - 'git\\s+push\\b[^|;&]*\\s\\+?(main|master)(\\s|$)'
      - 'git\\s+push\\b[^|;&]*\\s\\+?[^\\s:]*:(refs/heads/)?(main|master)'
      - '(^|[|;&]\\s*)npm\\s+publish\\b'
      - 'git\\s+tag\\s+(?!-(l|-list|n\\d*)\\b)(-[a-zA-Z]+\\s+)*\\S'
  - id: record-guardrail-run
    event: post-tool
    tool: shell
    decision: allow
    match:
      - '(?:^|[|;&]\\s*|\\$\\(\\s*)(?:\\S*/)?adr-filename-check\\.sh\\b'
      - '(?:^|[|;&]\\s*|\\$\\(\\s*)(?:\\S*/)?taxonomy-check\\.sh\\b'
    record: guardrails-run
  - id: record-doc-edit
    event: post-tool
    tool: edit
    decision: allow
    paths: ['adr/ADR-*.md']
    record: docs-edited
  - id: guardrails-gate-on-stop
    event: stop
    decision: block-or-allow
    degrade: warn
    rules:
      - when-edited: ['adr/ADR-*.md']
        requires: ['adr-filename-check.sh', 'taxonomy-check.sh']
        reason: "test policy: ADR touched without its checks"
"""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main  # noqa: E402  (after the sys.path line it needs)


def call(repo: str, hook: str, payload: dict, *, on_error="deny", vendor="claude", env=None,
         event="pre-tool"):
    e = dict(os.environ, **(env or {}))
    e.pop("EXERIS_SESSION_ID", None)
    e.pop("CLAUDE_SESSION_ID", None)
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, HOOK, "--hook", hook, "--vendor", vendor, "--on-error", on_error,
         "--event", event],
        input=json.dumps(payload), capture_output=True, text=True, cwd=repo, env=e)
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        out = {"__unparseable__": proc.stdout}
    return out, proc


def decision_of(out: dict) -> str:
    if "hookSpecificOutput" in out:
        return out["hookSpecificOutput"].get("permissionDecision", "allow")
    if "decision" in out:
        return out["decision"]
    if "permission" in out:
        return out["permission"]
    return "allow"


def fresh_repo() -> str:
    d = tempfile.mkdtemp(prefix="l0-")
    os.makedirs(os.path.join(d, ".git"))
    os.makedirs(os.path.join(d, ".agents", "hooks"))
    with open(os.path.join(d, ".agents", "hooks", "hooks.yaml"), "w") as fh:
        fh.write(HOOKS_YAML)
    return d


def edit(repo, session, path):
    call(repo, "record-doc-edit", {"session_id": session, "tool_input": {"file_path": path}},
         on_error="allow", event="post-tool")


def ran(repo, session, cmd, response=None):
    ev = {"session_id": session, "tool_input": {"command": cmd}}
    if response is not None:
        ev["tool_response"] = response
    call(repo, "record-guardrail-run", ev, on_error="allow", event="post-tool")


def stop(repo, session, vendor="claude"):
    out, _ = call(repo, "guardrails-gate-on-stop", {"session_id": session},
                  on_error="deny", vendor=vendor, event="stop")
    return out


# ── the review findings this file is the kept version of ──────────────────────────────────────

def test_interrupted_false_is_not_a_success_claim():
    """`interrupted: false` accompanies every successful Claude Code Bash event — and every failing
    one. Reading it before `exit_code` made a script that exited 1 discharge the gate."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", "adr-filename-check.sh", {"interrupted": False, "exit_code": 1})
    check("a failing check reported with interrupted:false does not discharge",
          stop(r, "s").get("decision"), "block")
    shutil.rmtree(r)


def test_exit_code_zero_with_interrupted_false_does_discharge():
    """The paired positive: the fix must not make every result unreadable."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    # The fixture rule requires BOTH checks, so both are run — otherwise this asserts the
    # every-check rule rather than the result-reading it is here for.
    ran(r, "s", "adr-filename-check.sh", {"interrupted": False, "exit_code": 0})
    ran(r, "s", "taxonomy-check.sh", {"interrupted": False, "exit_code": 0})
    check("passing checks still discharge", stop(r, "s").get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_one_command_running_two_gates_discharges_both():
    """`a.sh && b.sh` is one tool event running two gates. Stopping at the first match left the
    second armed, so a session that ran both was blocked on the one it had run."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", "adr-filename-check.sh && taxonomy-check.sh", {"exit_code": 0})
    out = stop(r, "s")
    check("both gates discharged by one command", out.get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_a_stop_already_blocked_is_not_blocked_again():
    """A runtime sets `stop_hook_active` after it blocked once. Blocking again re-fires the gate on
    the turn the operator is using to satisfy it, which is how a session becomes unable to end."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    check("first stop blocks", stop(r, "s").get("decision"), "block")
    out, _ = call(r, "guardrails-gate-on-stop",
                  {"session_id": "s", "stop_hook_active": True},
                  on_error="deny", event="stop")
    check("the second does not", out.get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_a_yielded_stop_clears_its_state_like_a_clean_one():
    """The short-circuit is a stop being ALLOWED, so the session is over and its state must go.
    Returning without clearing left the `no-session` key to accumulate, and the next session then
    answered for edits it never made — the bleed the clean-stop branch exists to prevent."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    check("first stop blocks", stop(r, "s").get("decision"), "block")
    call(r, "guardrails-gate-on-stop", {"session_id": "s", "stop_hook_active": True},
         on_error="deny", event="stop")
    check("a fresh stop in the same session is clean, not blocked by the old edit",
          stop(r, "s").get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_a_short_circuit_does_not_credit_the_check_it_skipped():
    """`a.sh || b.sh` names two gates and runs one. Recording both as passed would credit a check
    that never executed, so a command naming more than one records every entry UNVERIFIED."""
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", "adr-filename-check.sh || taxonomy-check.sh", {"exit_code": 0})
    out = stop(r, "s")
    check("the gate is discharged — the invocation was observed",
          out.get("decision", "allow"), "allow")
    r2 = fresh_repo()
    edit(r2, "s", "adr/ADR-086-x.md")
    ran(r2, "s", "adr-filename-check.sh", {"exit_code": 0})
    ran(r2, "s", "taxonomy-check.sh", {"exit_code": 0})
    check("two separate runs are attributable and also discharge",
          stop(r2, "s").get("decision", "allow"), "allow")
    shutil.rmtree(r); shutil.rmtree(r2)


def test_a_bad_argument_value_is_a_refusal_not_argparse_exit_2():
    """argparse answers an out-of-`choices` value by exiting 2, and exit 2 from a pre-tool hook is
    a deny on every shell call whatever --on-error says. The shim cannot fix that without copying
    this vocabulary onto a second pin, so it is fixed where the vocabulary is defined."""
    r = fresh_repo()
    proc = subprocess.run(
        [sys.executable, HOOK, "--hook", "deny-irreversible", "--vendor", "bogus",
         "--event", "pre-tool", "--on-error", "allow"],
        input=json.dumps({"tool_input": {"command": "ls"}}),
        capture_output=True, text=True, cwd=r)
    check("a bad --vendor value honours --on-error allow", proc.returncode, 0)
    proc2 = subprocess.run(
        [sys.executable, HOOK, "--hook", "deny-irreversible", "--nope", "x",
         "--event", "pre-tool", "--on-error", "deny"],
        input=json.dumps({"tool_input": {"command": "ls"}}),
        capture_output=True, text=True, cwd=r)
    check("an unknown flag under --on-error deny still refuses, in the vendor's shape",
          proc2.returncode, 2)
    check("and says why rather than printing usage",
          "does not accept" in (proc2.stdout + proc2.stderr), True)
    shutil.rmtree(r)


# ── the gate ──────────────────────────────────────────────────────────────────────────────────

def test_gate_requires_every_check_not_merely_one():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", ".agents/scripts/adr-filename-check.sh x")
    check("gate: one of two checks does not discharge it", stop(r, "s").get("decision"), "block")
    ran(r, "s", ".agents/scripts/taxonomy-check.sh x")
    check("gate: both checks discharge it", stop(r, "s").get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_gate_is_scoped_to_a_session():
    r = fresh_repo()
    edit(r, "A", "adr/ADR-086-x.md")
    ran(r, "A", ".agents/scripts/adr-filename-check.sh x")
    ran(r, "A", ".agents/scripts/taxonomy-check.sh x")
    check("gate: session A discharged", stop(r, "A").get("decision", "allow"), "allow")
    edit(r, "B", "adr/ADR-087-y.md")
    check("gate: session B is not discharged by A's checks", stop(r, "B").get("decision"), "block")
    shutil.rmtree(r)


def test_a_mention_is_not_an_invocation():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", "echo adr-filename-check.sh")
    ran(r, "s", "echo taxonomy-check.sh")
    check("gate: an echoed script name does not discharge it", stop(r, "s").get("decision"), "block")
    shutil.rmtree(r)


def test_a_failed_check_does_not_count():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", ".agents/scripts/adr-filename-check.sh x", response={"is_error": True})
    ran(r, "s", ".agents/scripts/taxonomy-check.sh x", response={"is_error": True})
    check("gate: a check the runtime reported as failed", stop(r, "s").get("decision"), "block")
    shutil.rmtree(r)


def test_a_rejected_edit_is_not_recorded():
    r = fresh_repo()
    call(r, "record-doc-edit", {"session_id": "s", "tool_input": {"file_path": "adr/ADR-086-x.md"},
                                "tool_response": {"is_error": True}},
         on_error="allow", event="post-tool")
    check("gate: an edit the runtime rejected", stop(r, "s").get("decision", "allow"), "allow")
    shutil.rmtree(r)


def test_unreported_result_is_marked_not_assumed():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", ".agents/scripts/adr-filename-check.sh x")          # no tool_response
    reason = stop(r, "s").get("reason", "")
    check("gate: says it verified invocation, not success",
          "never that it passed" in reason, True)
    shutil.rmtree(r)


def test_gate_clears_state_when_it_passes():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    ran(r, "s", ".agents/scripts/adr-filename-check.sh x")
    ran(r, "s", ".agents/scripts/taxonomy-check.sh x")
    stop(r, "s")
    check("gate: state cleared on a clean stop",
          os.path.isdir(os.path.join(r, ".agents-state", "s")), False)
    shutil.rmtree(r)


# ── failing closed ────────────────────────────────────────────────────────────────────────────

def test_enforcing_hooks_fail_closed_when_config_is_unreadable():
    r = fresh_repo()
    os.remove(os.path.join(r, ".agents", "hooks", "hooks.yaml"))
    out, _ = call(r, "deny-irreversible", {"tool_input": {"command": "git push --force"}})
    check("no config: a deny hook refuses", decision_of(out), "deny")
    out = stop(r, "s")
    check("no config: the STOP GATE blocks — it enforces and is not named deny*",
          out.get("decision"), "block")
    out, _ = call(r, "record-guardrail-run", {"tool_input": {"command": "x"}},
                  on_error="allow", event="post-tool")
    check("no config: a recorder yields", decision_of(out), "allow")
    shutil.rmtree(r)


def test_unknown_hook_id_fails_closed_for_enforcers():
    r = fresh_repo()
    out, _ = call(r, "no-such-hook", {"tool_input": {"command": "ls"}}, on_error="deny")
    check("unknown hook id, enforcing", decision_of(out), "deny")
    out, _ = call(r, "no-such-hook", {"tool_input": {"command": "ls"}}, on_error="allow")
    check("unknown hook id, recorder", decision_of(out), "allow")
    shutil.rmtree(r)


def test_a_broken_config_does_not_traceback_into_allow():
    r = fresh_repo()
    with open(os.path.join(r, ".agents", "hooks", "hooks.yaml"), "w") as fh:
        fh.write("this: [is: not: valid: yaml\n")
    out, proc = call(r, "deny-irreversible", {"tool_input": {"command": "git push --force"}})
    check("unparseable config: refuses", decision_of(out), "deny")
    check("unparseable config: no traceback", "Traceback" in proc.stderr, False)
    shutil.rmtree(r)


# ── the session key ───────────────────────────────────────────────────────────────────────────

def test_session_key_cannot_escape_the_state_directory():
    spec = importlib.util.spec_from_file_location("hook", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for hostile in ("..", "../..", "../../etc", "/etc/passwd", "."):
        key = m.session_key({"session_id": hostile})
        check(f"session key {hostile!r} is contained",
              ("/" not in key and "\\" not in key and "." not in key), True)


def test_no_session_id_gives_one_shared_key_not_a_per_process_one():
    spec = importlib.util.spec_from_file_location("hook", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("no session id: key is stable, not pid-derived", m.session_key({}), "no-session")


def test_reading_state_creates_nothing():
    r = fresh_repo()
    stop(r, "quiet")
    check("a stop with nothing recorded materialises no directory",
          os.path.isdir(os.path.join(r, ".agents-state", "quiet")), False)
    shutil.rmtree(r)


# ── the deny matcher, in both directions ──────────────────────────────────────────────────────

def test_deny_matcher():
    r = fresh_repo()
    for cmd in ["git push --force origin x", "git push origin main", "git push origin HEAD:main",
                "git push origin +main", "git push --mirror origin", "npm publish",
                "git tag v1.0.0"]:
        out, _ = call(r, "deny-irreversible", {"tool_input": {"command": cmd}})
        check(f"deny: {cmd}", decision_of(out), "deny")
    for cmd in ["git push -u origin feat/x", "git push --force-with-lease origin feat/x",
                "git tag -l", "git tag --list", "git tag -n5", "npm run publish-docs",
                "grep -rn 'npm publish' README.md"]:
        out, _ = call(r, "deny-irreversible", {"tool_input": {"command": cmd}})
        check(f"allow: {cmd}", decision_of(out), "allow")
    shutil.rmtree(r)


# ── the envelope ──────────────────────────────────────────────────────────────────────────────

def test_envelope_names_the_event_it_answers():
    r = fresh_repo()
    out, _ = call(r, "deny-irreversible", {"tool_input": {"command": "git push --force o m"}})
    check("pre-tool envelope", out["hookSpecificOutput"]["hookEventName"], "PreToolUse")
    out, _ = call(r, "record-doc-edit",
                  {"session_id": "s", "tool_input": {"file_path": "adr/ADR-086-x.md"}},
                  on_error="allow", event="post-tool")
    check("post-tool carries no permission decision", "hookSpecificOutput" in out, False)
    shutil.rmtree(r)


def test_stop_degrades_where_a_runtime_cannot_block():
    r = fresh_repo()
    edit(r, "s", "adr/ADR-086-x.md")
    out = stop(r, "s", vendor="copilot")
    check("copilot: stop degrades to a warning", out.get("decision", "allow"), "allow")
    check("claude: stop blocks", stop(r, "s").get("decision"), "block")
    shutil.rmtree(r)


if __name__ == "__main__":
    main(globals())
