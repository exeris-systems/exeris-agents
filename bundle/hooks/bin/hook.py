#!/usr/bin/env python3
"""Exeris agent hook — one implementation, six vendor wire formats.

Reads the canonical definitions from `.agents/hooks/hooks.yaml`, so a pattern is authored once
(agents-md-schema.md rule 12). The rendered per-vendor hook files do nothing but invoke this
script with `--hook <id> --vendor <name>`; they carry no patterns of their own.

Wire shapes differ per vendor in two places and only two: what arrives on stdin, and what a
decision looks like on stdout. Everything between them is shared.

  stdin   Claude/Codex : {"tool_name": "Bash", "tool_input": {"command": …, "file_path": …}}
          Copilot      : snake_case aliases of the same
          Cursor       : {"command": …} for beforeShellExecution
          Gemini       : {"toolName": …, "args": {…}}
          Antigravity  : {"tool_name": "run_command", "tool_input": {"command": …}}

  stdout  Claude/Codex/Copilot : {"hookSpecificOutput": {"permissionDecision": "deny", …}}
                                 and {"decision": "block", "reason": …} for a stop
          Cursor               : {"permission": "deny", "userMessage": …}
          Gemini/Antigravity   : {"decision": "deny", "reason": …}

Exit codes: 0 always, except where a vendor documents exit 2 as "blocked" and gives us no other
channel. The decision travels in the JSON; an exit code is a fallback, not the contract.

State lives under `.agents-state/` (git-ignored) and is per-checkout, not per-session: a session
that ends without discharging its consequence leaves the marker for the next one, which is the
conservative direction to fail in.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Vendors whose stop event can actually block. Everywhere else `degrade: warn` applies and the
# gate prints its reason without stopping anything — recorded in manifest.yaml `degradations`.
STOP_BLOCKS = {"claude", "codex"}


def repo_root(start: str | None = None) -> str:
    d = os.path.abspath(start or HERE)
    while d != "/":
        if os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.getcwd()


def hooks_yaml() -> str:
    """The hook DEFINITIONS are the repository's; only this dispatcher is the bundle's.

    Vendored, this script sits at `.agents/vendor/<bundle>-<v>/hooks/bin/hook.py`, so a path
    relative to itself would find the bundle's own directory rather than the repository's rules.
    The repository's file wins; the sibling path is the fallback for an unvendored layout.
    """
    repo = os.path.join(repo_root(), ".agents", "hooks", "hooks.yaml")
    if os.path.exists(repo):
        return repo
    return os.path.join(HERE, "..", "hooks.yaml")


class ConfigUnavailable(RuntimeError):
    """The L0 rules could not be read. For a deny hook this is fatal, not permissive."""


def load_config() -> dict:
    try:
        import yaml
    except ImportError as exc:
        # Previously this returned {} and the dispatcher answered "allow" — so the layer ADR-085
        # calls "runtime, hard" switched itself off on any machine without pyyaml, silently.
        # A deny hook now refuses instead; a recorder still yields, because recording nothing is
        # not a safety failure.
        raise ConfigUnavailable("pyyaml is not installed, so the L0 rules cannot be read "
                                "(pip install pyyaml)") from exc
    path = hooks_yaml()
    if not os.path.exists(path):
        raise ConfigUnavailable(f"no hooks.yaml at {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        raise ConfigUnavailable(f"hooks.yaml is unreadable: {type(exc).__name__}: {exc}") from exc


def find_hook(cfg: dict, hook_id: str) -> dict | None:
    for h in cfg.get("hooks") or []:
        if h.get("id") == hook_id:
            return h
    return None


def read_event() -> dict:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _first(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def extract(event: dict) -> tuple[str, str]:
    """Return (command, file_path) from whichever vendor shape arrived."""
    args = _first(event, "tool_input", "toolInput", "args", "arguments", "input") or {}
    if not isinstance(args, dict):
        args = {}
    command = _first(event, "command") or _first(args, "command", "cmd", "script", "shellCommand") or ""
    path = _first(args, "file_path", "filePath", "path", "target_file", "notebook_path") or ""
    return str(command), str(path)


# Canonical event -> the name the Claude/Codex/Copilot envelope must carry. A hook wired to
# PostToolUse that answers "PreToolUse" is claiming to gate an action that already happened.
ENVELOPE_EVENT = {"pre-tool": "PreToolUse", "post-tool": "PostToolUse",
                  "stop": "Stop", "session-start": "SessionStart"}


def emit(vendor: str, event_kind: str, decision: str, reason: str) -> int:
    """Print the vendor's decision shape. `decision` is allow | deny | block."""
    if vendor == "cursor":
        payload = {"permission": "allow" if decision == "allow" else "deny"}
        if reason:
            payload["userMessage"] = reason
            payload["agentMessage"] = reason
    elif vendor in ("gemini", "antigravity"):
        payload = {"decision": decision, "reason": reason} if reason else {"decision": decision}
    elif event_kind == "stop":
        payload = {"decision": "block", "reason": reason} if decision == "block" else {}
    elif event_kind == "pre-tool":
        out = {"permissionDecision": "allow" if decision == "allow" else "deny"}
        if reason:
            out["permissionDecisionReason"] = reason
        payload = {"hookSpecificOutput": {"hookEventName": "PreToolUse", **out}}
    else:
        # post-tool and session-start take no permission decision: there is nothing left to permit.
        # An empty object is the documented "nothing to say" answer.
        payload = {}
    print(json.dumps(payload))
    if decision != "allow" and reason:
        print(reason, file=sys.stderr)
    # Exit 2 is the documented "blocked" fallback where the JSON channel is ignored.
    return 2 if decision in ("deny", "block") and vendor in ("claude", "codex", "copilot") else 0


def session_key(event: dict) -> str:
    """Which session this state belongs to.

    Without this the state directory is per *checkout*: `guardrails-run` is append-only and
    nothing clears it, so the first check ever run in a clone discharges the stop gate for every
    session afterwards — the gate fires exactly once and then never again. Every runtime in scope
    puts a session identifier on the event; where one is absent the parent process id is a
    serviceable proxy for "this agent run", and a missing key is treated as a fresh session rather
    than as a shared one.
    """
    for k in ("session_id", "sessionId", "conversation_id", "conversationId"):
        v = event.get(k)
        if v:
            return re.sub(r"[^A-Za-z0-9_.-]", "_", str(v))[:64]
    env = os.environ.get("EXERIS_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if env:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", env)[:64]
    return f"ppid-{os.getppid()}"


def state_path(cfg: dict, name: str, session: str) -> str:
    d = os.path.join(repo_root(), cfg.get("state-dir") or ".agents-state", session)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def append_state(cfg: dict, name: str, value: str, session: str) -> None:
    if not value:
        return
    p = state_path(cfg, name, session)
    existing = set()
    if os.path.exists(p):
        existing = {l.strip() for l in open(p, encoding="utf-8") if l.strip()}
    if value not in existing:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(value + "\n")


def read_state(cfg: dict, name: str, session: str) -> list[str]:
    p = state_path(cfg, name, session)
    if not os.path.exists(p):
        return []
    return [l.strip() for l in open(p, encoding="utf-8") if l.strip()]


def tool_failed(event: dict) -> bool:
    """True when the runtime reported the tool call as failed. Absent information is not failure."""
    resp = _first(event, "tool_response", "toolResponse", "response") or {}
    if not isinstance(resp, dict):
        return False
    for key in ("is_error", "isError", "error"):
        if resp.get(key):
            return True
    code = resp.get("exit_code", resp.get("exitCode"))
    return isinstance(code, int) and code != 0


def matches_any(patterns, text: str) -> bool:
    return any(re.search(p, text) for p in patterns or [])


def path_matches(globs, path: str) -> bool:
    rel = os.path.relpath(path, repo_root()) if os.path.isabs(path) else path
    rel = rel.replace(os.sep, "/")
    for g in globs or []:
        if fnmatch.fnmatch(rel, g):
            return True
        # `**` in fnmatch does not cross the directory separator the way the layout implies.
        if g.endswith("/**") and rel.startswith(g[:-3] + "/"):
            return True
        if g.endswith("/**") and rel == g[:-3]:
            return True
    return False


# A hook id that denies must fail CLOSED when the rules are unreadable; a recorder may yield.
DENYING_HOOKS = ("deny",)


def run(hook_id: str, vendor: str) -> int:
    event = read_event()
    session = session_key(event)
    try:
        cfg = load_config()
    except ConfigUnavailable as exc:
        if hook_id.startswith(DENYING_HOOKS):
            return emit(vendor, "pre-tool", "deny",
                        f"L0 unavailable and this is a deny hook, so it refuses rather than "
                        f"waving the action through: {exc}")
        print(f"exeris-hook: {exc}; recorder inactive", file=sys.stderr)
        return emit(vendor, "post-tool", "allow", "")

    spec = find_hook(cfg, hook_id)
    if not spec:
        if hook_id.startswith(DENYING_HOOKS):
            return emit(vendor, "pre-tool", "deny",
                        f"L0: no hook '{hook_id}' in hooks.yaml, and a deny hook that cannot find "
                        f"its own rule refuses rather than allowing")
        return emit(vendor, "pre-tool", "allow", "")

    kind = spec.get("event", "pre-tool")
    command, path = extract(event)

    if spec.get("decision") == "deny":
        if command and matches_any(spec.get("match"), command):
            return emit(vendor, kind, "deny", " ".join((spec.get("reason") or "").split()))
        return emit(vendor, kind, "allow", "")

    if spec.get("record"):
        # A check that ran and FAILED has not discharged anything. Where the runtime reports the
        # result (Claude and Codex put it on the PostToolUse event) a failure is not recorded;
        # where it does not, the recorder says so by recording anyway rather than pretending.
        if spec.get("tool") == "shell" and tool_failed(event):
            return emit(vendor, kind, "allow", "")
        subject = command if spec.get("tool") == "shell" else path
        if spec.get("tool") == "shell":
            if matches_any(spec.get("match"), subject):
                # Record the script name, not the whole command line: the gate asks whether a
                # check ran, and the flags it ran with are the reviewer's question, not the hook's.
                for pat in spec.get("match") or []:
                    m = re.search(pat, subject)
                    if m:
                        append_state(cfg, spec["record"],
                                     m.group(0).replace("\\", ""), session)
        elif subject and path_matches(spec.get("paths"), subject):
            rel = os.path.relpath(subject, repo_root()) if os.path.isabs(subject) else subject
            append_state(cfg, spec["record"], rel.replace(os.sep, "/"), session)
        return emit(vendor, kind, "allow", "")

    if kind == "stop":
        edited = read_state(cfg, "docs-edited", session)
        ran = read_state(cfg, "guardrails-run", session)
        blocked = []
        for rule in spec.get("rules") or []:
            hits = [f for f in edited if path_matches(rule.get("when-edited"), f)]
            if not hits:
                continue
            required = rule.get("requires") or []
            # ALL of them. `any` here would mean a rule listing two checks is discharged by
            # running either — the gate softens silently the moment a second requirement is added,
            # which is the failure this whole layer exists to prevent.
            missing = [req for req in required
                       if not any(req.rstrip("$").replace("\\", "") in r for r in ran)]
            if not missing:
                continue
            reason = " ".join((rule.get("reason") or "").split())
            blocked.append(f"{reason} (edited: {', '.join(sorted(hits)[:4])}; "
                           f"not run: {', '.join(missing)})")
        if not blocked:
            return emit(vendor, kind, "allow", "")
        text = "L0 gate: " + " | ".join(blocked)
        if vendor in STOP_BLOCKS:
            return emit(vendor, kind, "block", text)
        # Degraded: the runtime documents no way to block a stop, so the gate reports and yields.
        print(f"exeris-hook (warning, {vendor} cannot block a stop): {text}", file=sys.stderr)
        return emit(vendor, kind, "allow", "")

    return emit(vendor, kind, "allow", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Exeris agent hook dispatcher")
    ap.add_argument("--hook", required=True, help="hook id from .agents/hooks/hooks.yaml")
    ap.add_argument("--vendor", default=os.environ.get("EXERIS_HOOK_VENDOR", "claude"),
                    choices=["claude", "copilot", "codex", "gemini", "antigravity", "cursor"])
    a = ap.parse_args()
    try:
        return run(a.hook, a.vendor)
    except ConfigUnavailable:
        raise
    except Exception as exc:
        # A recorder that crashes should not brick a session; a DENY hook that crashes must not
        # become an allow, which is what returning {} silently did.
        print(f"exeris-hook: {type(exc).__name__}: {exc}", file=sys.stderr)
        if a.hook.startswith(DENYING_HOOKS):
            return emit(a.vendor, "pre-tool", "deny",
                        f"L0 deny hook failed ({type(exc).__name__}), refusing rather than "
                        f"allowing: {exc}")
        print(json.dumps({}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
