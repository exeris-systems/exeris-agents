#!/usr/bin/env python3
"""The rendered hook command must not carry the pinned version.

It did, and the cost was not theoretical. `.claude/settings.json` named
`.agents/vendor/exeris-agents-<pin>/hooks/bin/hook.py`, so the version lived in two files with
different lifetimes — the adapter, written when the renderer last ran, and the vendored tree,
replaced at every bump. Any checkout holding one at a version the other does not runs a command
pointing at a missing file; `python3` exits 2; exit 2 from a PreToolUse hook is a block. Every
shell call in the session is denied, whatever each hook's own `--on-error` says, because the
interpreter answers before the layer can.

The review environment for a pull request pairs the base branch's protected `.claude/` with the
branch's tree, which is exactly that state — so the first two reviews of exeris-docs #106 ran with
no shell at all and reported ten checks as `not-run`.

Run: python3 tests/test_dispatch.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, main                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(ROOT, "tools", "agents_render.py")
SHIM = os.path.join(ROOT, "bundle", "hooks", "bin", "dispatch.py")

MANIFEST = """\
version: 2
repository: fixture
imports:
  - bundle: exeris-agents
    version: {version}
    ref: deadbeef
    sha256: 'sha256:00'
agents: []
skills: []
workflows: []
policies: []
references: []
schemas: []
nested: []
adapters:
  claude:
    target: .claude
degradations: {{}}
provider-owned: []
"""

HOOKS = """\
version: 1
hooks:
  - id: deny-irreversible
    event: pre-tool
    tool: shell
    decision: deny
    reason: fixture
    match: ['*rm -rf*']
  - id: record-guardrail-run
    event: post-tool
    tool: shell
    decision: record
    reason: fixture
    match: ['*check*']
"""

# Prints a token and exits 0, so a test can tell "the shim reached hook.py" from "something ran".
STUB_HOOK = "import sys\nprint('HOOK-REACHED ' + ' '.join(sys.argv[1:]))\n"


def repo(version: str = "1.3.0", *, with_shim: bool = True) -> str:
    d = tempfile.mkdtemp(prefix="dispatch-")
    os.makedirs(os.path.join(d, ".git"))
    os.makedirs(os.path.join(d, ".agents", "hooks"))
    open(os.path.join(d, "AGENTS.md"), "w").write("# fixture\n")
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(MANIFEST.format(version=version))
    open(os.path.join(d, ".agents", "hooks", "hooks.yaml"), "w").write(HOOKS)
    vendor(d, version, with_shim=with_shim)
    return d


def vendor(d: str, version: str, *, with_shim: bool = True) -> str:
    """Write a vendored tree at `version`, as agents_bundle.py vendor would."""
    binder = os.path.join(d, ".agents", "vendor", f"exeris-agents-{version}", "hooks", "bin")
    os.makedirs(binder, exist_ok=True)
    open(os.path.join(binder, "hook.py"), "w").write(STUB_HOOK)
    if with_shim:
        shutil.copyfile(SHIM, os.path.join(binder, "dispatch.py"))
    return binder


def clean_env() -> dict:
    """The caller's environment minus the variables that name ANOTHER repository.

    `CLAUDE_PROJECT_DIR` is set in every Claude Code session and the shim reads it first, exactly
    as hook.py does. Inheriting it points `repo_root()` at the checkout the suite is being run
    from rather than at the fixture, so the suite passes in CI and fails on a developer's machine —
    the worst of the two orders to discover in.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


def render(d: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, RENDERER, "--root", d, "--vendor", "claude", *args],
                          capture_output=True, text=True, env=clean_env())


def commands(d: str) -> list[str]:
    settings = json.load(open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8"))
    return [h["command"] for entries in settings.get("hooks", {}).values()
            for e in entries for h in e.get("hooks", [])]


def fire(d: str, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the rendered command the way the runtime would: from the repository root."""
    return subprocess.run([sys.executable, os.path.join(d, ".agents", "hooks", "bin", "dispatch.py"),
                           *args], capture_output=True, text=True, cwd=cwd or d, env=clean_env())


# ── what the renderer writes ─────────────────────────────────────────────────────────────────────

def test_the_rendered_command_names_no_version():
    d = repo("1.3.0")
    render(d)
    check("both hooks rendered", len(commands(d)), 2)
    for c in commands(d):
        check("command names the version-free shim", ".agents/hooks/bin/dispatch.py" in c, True)
        check(f"command carries no pin: {c[:60]}", "exeris-agents-1.3.0" in c, False)
    shutil.rmtree(d)


def test_the_shim_is_written_and_carries_a_marker():
    d = repo()
    render(d)
    body = open(os.path.join(d, ".agents", "hooks", "bin", "dispatch.py"), encoding="utf-8").read()
    check("shim exists with its marker", "DO NOT EDIT" in body, True)
    check("shebang survives as line 1", body.splitlines()[0], "#!/usr/bin/env python3")
    check("the marker names no version", "1.3.0" in body.splitlines()[1], False)
    shutil.rmtree(d)


def test_a_bundle_without_the_shim_still_renders_the_old_path():
    """Upgrading the renderer must not point a repository at a file its pinned bundle lacks."""
    d = repo("1.2.0", with_shim=False)
    render(d)
    check("both hooks rendered", len(commands(d)), 2)
    for c in commands(d):
        check("falls back to the vendored hook.py",
              ".agents/vendor/exeris-agents-1.2.0/hooks/bin/hook.py" in c, True)
    check("and writes no shim",
          os.path.exists(os.path.join(d, ".agents", "hooks", "bin", "dispatch.py")), False)
    shutil.rmtree(d)


def test_an_entry_rendered_before_the_shim_is_replaced_not_duplicated():
    """The old command is still recognised as ours, or the upgrade doubles every hook.

    An entry the merge does not recognise is treated as hand-authored and kept. Recognition was one
    string; the shim changes it, and without both spellings a repository upgrading from <=1.2.0
    would keep the stale command — pointing into a vendored tree the same bump has just removed —
    alongside the new one.
    """
    d = repo("1.3.0")
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
        "type": "command", "timeout": 15,
        "command": "python3 .agents/vendor/exeris-agents-1.2.0/hooks/bin/hook.py "
                   "--hook deny-irreversible --vendor claude --event pre-tool --on-error deny"}]}]}},
        open(os.path.join(d, ".claude", "settings.json"), "w"), indent=2)
    render(d)
    got = commands(d)
    check("one command per hook, not two", len(got), 2)
    check("no stale vendored path survives",
          any("exeris-agents-1.2.0" in c for c in got), False)
    shutil.rmtree(d)


def test_a_hand_authored_hook_is_still_kept():
    """The widened recognition must not start claiming hooks the renderer never wrote."""
    d = repo()
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
        "type": "command", "command": "python3 scripts/my-own-guard.py"}]}]}},
        open(os.path.join(d, ".claude", "settings.json"), "w"), indent=2)
    render(d)
    check("the human's hook survives",
          any("my-own-guard.py" in c for c in commands(d)), True)
    shutil.rmtree(d)


def test_render_check_is_clean_on_a_second_run():
    d = repo()
    render(d)
    check("--check agrees with what was just written", render(d, "--check").returncode, 0)
    shutil.rmtree(d)


def test_a_shim_the_renderer_would_not_write_is_not_named_either():
    """One resolver, or the renderer names a file it declined to write.

    `dispatcher_path` asked `os.path.exists`, which follows a symlink out of the tree, while
    `write_dispatch` asked for containment — so a symlinked vendor directory rendered a command
    pointing at a shim that was never copied. That is the "names a file that is not there" state
    this whole change exists to remove, reintroduced by the change itself.
    """
    d = repo("1.3.0")
    outside = tempfile.mkdtemp(prefix="dispatch-outside-")
    os.makedirs(os.path.join(outside, "hooks", "bin"))
    shutil.copyfile(SHIM, os.path.join(outside, "hooks", "bin", "dispatch.py"))
    open(os.path.join(outside, "hooks", "bin", "hook.py"), "w").write(STUB_HOOK)
    pinned_dir = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0")
    shutil.rmtree(pinned_dir)
    os.symlink(outside, pinned_dir)
    render(d)
    named = commands(d)
    wrote = os.path.exists(os.path.join(d, ".agents", "hooks", "bin", "dispatch.py"))
    check("the shim was not written", wrote, False)
    check("so it is not named either",
          any(".agents/hooks/bin/dispatch.py" in c for c in named), False)
    shutil.rmtree(d, ignore_errors=True); shutil.rmtree(outside)


def test_a_stale_shim_is_removed_when_no_bundle_ships_one():
    """A repository that re-pins to a bundle without the shim keeps an orphan otherwise, and
    `--check` stays clean over it — the drift `prune` exists to catch, in a file `prune` never
    looked at."""
    d = repo("1.3.0")
    render(d)
    dest = os.path.join(d, ".agents", "hooks", "bin", "dispatch.py")
    check("the shim is there to begin with", os.path.exists(dest), True)
    shutil.rmtree(os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0"))
    vendor(d, "1.2.0", with_shim=False)
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(MANIFEST.format(version="1.2.0"))
    check("--check reports the orphan", render(d, "--check").returncode, 1)
    render(d)
    check("and the render removes it", os.path.exists(dest), False)
    shutil.rmtree(d)


def test_a_shim_symlinked_out_of_the_checkout_stops_the_render():
    """The destination is read to decide whether to delete it, and `os.path.isfile` follows a
    link. A generated adapter that leads out of the tree is tampering, and a renderer that
    quietly wrote through it would be the sink the containment checks exist to close."""
    d = repo()
    outside = tempfile.mkdtemp(prefix="dispatch-outside-")
    planted = os.path.join(outside, "dispatch.py")
    open(planted, "w").write("# DO NOT EDIT\n")
    os.makedirs(os.path.join(d, ".agents", "hooks", "bin"), exist_ok=True)
    os.symlink(planted, os.path.join(d, ".agents", "hooks", "bin", "dispatch.py"))
    out = render(d)
    check("the render stops", out.returncode, 2)
    check("and says what it found", "outside the repository" in out.stderr, True)
    check("the file outside is untouched", os.path.exists(planted), True)
    shutil.rmtree(d); shutil.rmtree(outside)


def test_a_hand_edited_shim_is_a_check_failure():
    """It sits outside the pin's digest, so `agents_bundle.py verify` cannot see it — but it is a
    generated adapter, and `--check` compares every one of those against its source byte for
    byte. The guarantee is the same; the mechanism is the one that owns generated files."""
    d = repo()
    render(d)
    dest = os.path.join(d, ".agents", "hooks", "bin", "dispatch.py")
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write("\n# a hand-edit\n")
    check("--check catches it", render(d, "--check").returncode, 1)
    shutil.rmtree(d)


def test_a_hook_id_that_is_not_a_plain_word_is_refused():
    """The renderer must not be able to emit a command the shim would then refuse to run."""
    d = repo()
    hooks = os.path.join(d, ".agents", "hooks", "hooks.yaml")
    open(hooks, "w").write(HOOKS.replace("id: deny-irreversible", "id: 'deny irreversible; x'"))
    out = render(d)
    check("the render fails", out.returncode, 2)
    check("and says why", "not a plain word" in out.stderr, True)
    shutil.rmtree(d)


# ── what the shim does when it fires ─────────────────────────────────────────────────────────────

def test_the_shim_reaches_the_pinned_hook():
    d = repo()
    render(d)
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("hook.py ran", out.stdout.startswith("HOOK-REACHED"), True)
    check("its arguments arrived intact", "--hook deny-irreversible" in out.stdout, True)
    shutil.rmtree(d)


def test_a_bumped_pin_reaches_the_new_tree_without_re_rendering():
    """The defect this whole change exists for, in the state that produced it.

    Render at one version, then move the tree and the pin on without re-rendering — a pull request
    whose `.claude/` is the base branch's and whose `.agents/` is the branch's. Before, the command
    named a directory that no longer existed and every tool call was denied.
    """
    d = repo("1.2.0")
    render(d)
    shutil.rmtree(os.path.join(d, ".agents", "vendor", "exeris-agents-1.2.0"))
    vendor(d, "1.3.0")
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(MANIFEST.format(version="1.3.0"))
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("the stale adapter still finds the hook", out.stdout.startswith("HOOK-REACHED"), True)
    check("and exits without blocking", out.returncode, 0)
    shutil.rmtree(d)


def test_a_missing_hook_honours_the_callers_on_error():
    """Fail closed where the rule says so, open where it does not — which `python3` could not do."""
    d = repo()
    render(d)
    shutil.rmtree(os.path.join(d, ".agents", "vendor"))
    denied = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    allowed = fire(d, "--hook", "record-guardrail-run", "--vendor", "claude", "--on-error", "allow")
    check("a deny rule still blocks", denied.returncode, 2)
    check("and says why", "no hook.py" in denied.stderr, True)
    check("a recorder does not block", allowed.returncode, 0)
    shutil.rmtree(d)


def test_a_repository_owned_hook_is_the_fallback():
    d = repo()
    render(d)
    shutil.rmtree(os.path.join(d, ".agents", "vendor"))
    open(os.path.join(d, ".agents", "hooks", "bin", "hook.py"), "w").write(STUB_HOOK)
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("the repository's own copy runs", out.stdout.startswith("HOOK-REACHED"), True)
    shutil.rmtree(d)


def test_the_working_directory_wins_over_the_shims_own_location():
    """Two checkouts on one machine. The shim must read the manifest of the repository being
    worked in, not of the one it happens to sit in — the same rule hook.py applies to hooks.yaml."""
    a, b = repo("1.3.0"), repo("1.3.0")
    render(a)
    open(os.path.join(b, ".agents", "vendor", "exeris-agents-1.3.0", "hooks", "bin", "hook.py"),
         "w").write("import sys\nprint('B-HOOK')\n")
    out = fire(a, "--hook", "deny-irreversible", "--vendor", "claude", cwd=b)
    check("it ran the working directory's hook", out.stdout.strip(), "B-HOOK")
    shutil.rmtree(a); shutil.rmtree(b)


def test_the_hooks_own_exit_code_survives():
    """The exit code IS the decision on the runtimes that document one, so the shim must not
    replace it with its own. Running the hook in this process rather than exec'ing a second
    interpreter moves that code through a `SystemExit`, which is the part worth a test."""
    d = repo()
    render(d)
    hook = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0", "hooks", "bin", "hook.py")
    open(hook, "w").write("import sys\nprint('BLOCKING')\nsys.exit(2)\n")
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("a blocking hook still blocks", out.returncode, 2)
    check("and its output still arrives", out.stdout.strip(), "BLOCKING")
    shutil.rmtree(d)


def test_a_non_integer_exit_keeps_its_message():
    """`sys.exit("...")` is exit 1 with the string on stderr. Catching SystemExit to return
    `.code` turned that into a bare 1 and dropped the message; letting it reach the interpreter
    is both shorter and the only version that keeps what the hook was trying to say."""
    d = repo()
    render(d)
    hook = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0", "hooks", "bin", "hook.py")
    open(hook, "w").write("import sys\nsys.exit('hooks.yaml is unreadable')\n")
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("the exit code is 1", out.returncode, 1)
    check("and the message survives", "hooks.yaml is unreadable" in out.stderr, True)
    shutil.rmtree(d)


def test_a_hook_that_cannot_start_falls_back_to_on_error():
    """A hook.py that raises on import is not a hook that allows."""
    d = repo()
    render(d)
    hook = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0", "hooks", "bin", "hook.py")
    open(hook, "w").write("raise RuntimeError('broken')\n")
    denied = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    allowed = fire(d, "--hook", "record-guardrail-run", "--vendor", "claude", "--on-error", "allow")
    check("a deny rule blocks", denied.returncode, 2)
    check("a recorder does not", allowed.returncode, 0)
    check("and the reason names the file", "cannot run" in denied.stderr, True)
    shutil.rmtree(d)


def test_a_command_that_is_not_flag_value_pairs_is_not_forwarded():
    """What reaches `execv` is rebuilt from strings that each matched a pattern.

    The vector comes from a provider config, and this file is the boundary between that config and
    the dispatcher. It does not own the flag vocabulary — hook.py does — so it checks the shape:
    `--flag` then a plain value, nothing positional. A hand-edited command is refused here with a
    reason, rather than reaching an argument parser the reader never associated with the config.
    """
    d = repo()
    render(d)
    odd = fire(d, "--hook", "deny-irreversible", "--on-error")
    meta = fire(d, "--hook", "deny-irreversible; rm -rf /", "--on-error", "deny")
    positional = fire(d, "deny-irreversible", "claude")
    check("an unpaired vector is refused", odd.returncode, 2)
    check("a value carrying shell punctuation is refused", meta.returncode, 2)
    check("the hook did not run", "HOOK-REACHED" in meta.stdout, False)
    check("and the refusal is in the vendor's shape",
          json.loads(meta.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
    check("a positional vector is refused", positional.returncode, 2)
    check("and each says why", "--flag value pairs" in meta.stderr, True)
    shutil.rmtree(d)


def test_a_pin_that_leaves_the_vendored_tree_is_not_run():
    """The pin is repository content and its value becomes an argument to `execv`.

    The vendored path is `.agents/vendor/{bundle}-{version}/hooks/bin/hook.py`, built from two
    manifest values — and an absolute `bundle` swallows the base it was joined to, so the pin can
    name any directory on the machine. That is code outside the tree rule 8's digest vouches for,
    running with the session's permissions. It is refused, and the hook's own `--on-error` decides
    what the refusal means.
    """
    d = repo()
    render(d)
    outside = tempfile.mkdtemp(prefix="dispatch-outside-")
    os.makedirs(os.path.join(outside + "-v", "hooks", "bin"))
    open(os.path.join(outside + "-v", "hooks", "bin", "hook.py"), "w").write(
        "import sys\nprint('ESCAPED')\n")
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(
        MANIFEST.format(version="v").replace("bundle: exeris-agents", f"bundle: {outside}"))
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("nothing outside the tree ran", "ESCAPED" in out.stdout, False)
    check("and the deny rule still blocks", out.returncode, 2)
    shutil.rmtree(d); shutil.rmtree(outside); shutil.rmtree(outside + "-v")


def test_the_renderer_refuses_a_pin_that_is_not_a_plain_name():
    """The same value, refused earlier and louder. The shim declines to run such a pin; the
    renderer will not build a path from one at all, so the repository is told rather than left
    with adapters whose behaviour depends on what happens to exist outside the tree."""
    d = repo()
    open(os.path.join(d, ".agents", "manifest.yaml"), "w").write(
        MANIFEST.format(version="v").replace("bundle: exeris-agents", "bundle: /etc"))
    out = render(d)
    check("the render fails", out.returncode, 2)
    check("and names the offending key", "not a plain name" in out.stderr, True)
    shutil.rmtree(d)


def test_a_symlinked_vendor_entry_is_not_run():
    """`..` is not the only way out of a directory."""
    d = repo("1.3.0")
    render(d)
    outside = tempfile.mkdtemp(prefix="dispatch-outside-")
    os.makedirs(os.path.join(outside, "hooks", "bin"))
    open(os.path.join(outside, "hooks", "bin", "hook.py"), "w").write(
        "import sys\nprint('ESCAPED')\n")
    pinned_dir = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0")
    shutil.rmtree(pinned_dir)
    os.symlink(outside, pinned_dir)
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("a symlinked bundle is not vouched for", "ESCAPED" in out.stdout, False)
    check("and the deny rule still blocks", out.returncode, 2)
    shutil.rmtree(d, ignore_errors=True); shutil.rmtree(outside)


# ── what the shim refuses to let happen ─────────────────────────────────────────────────────────

def test_a_module_dropped_beside_the_shim_cannot_hijack_it():
    """`.agents/hooks/bin/` is a generated adapter's home, not part of the digest-covered tree.

    The interpreter puts the running script's directory first on `sys.path`, so before this was
    handled a file dropped there satisfied an import made by the shim or by the gate it starts —
    and the L0 layer could be switched off by adding a file next to it rather than by editing it.
    Exec'ing a second interpreter used to hide this: the path then led with the VENDORED
    directory, which the pin's digest does cover.
    """
    d = repo()
    render(d)
    beside = os.path.join(d, ".agents", "hooks", "bin")
    for name in ("json.py", "re.py", "runpy.py"):
        open(os.path.join(beside, name), "w").write("print('HIJACKED')\n")
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("no planted module was imported", "HIJACKED" in (out.stdout + out.stderr), False)
    check("and the real hook still ran", out.stdout.startswith("HOOK-REACHED"), True)
    shutil.rmtree(d)


def test_the_hook_still_sees_its_own_directory_first():
    """What exec'ing hook.py did implicitly, kept: a hook may import from beside itself."""
    d = repo()
    render(d)
    binder = os.path.join(d, ".agents", "vendor", "exeris-agents-1.3.0", "hooks", "bin")
    open(os.path.join(binder, "sidecar.py"), "w").write("TOKEN = 'SIDECAR'\n")
    open(os.path.join(binder, "hook.py"), "w").write(
        "import sidecar\nprint('HOOK-REACHED ' + sidecar.TOKEN)\n")
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude")
    check("the vendored directory leads the path", "SIDECAR" in out.stdout, True)
    shutil.rmtree(d)


def test_a_manifest_that_is_not_utf8_denies_rather_than_erroring():
    """A traceback exits 1, and 1 is the code every runtime reads as a hook that errored — allow.

    The read caught OSError only, and a file that is not valid UTF-8 raises ValueError.
    """
    d = repo()
    render(d)
    with open(os.path.join(d, ".agents", "manifest.yaml"), "wb") as fh:
        fh.write(b"imports:\n  - bundle: exeris-agents\n    version: \xff\xfe\n")
    shutil.rmtree(os.path.join(d, ".agents", "vendor"))
    out = fire(d, "--hook", "deny-irreversible", "--vendor", "claude", "--on-error", "deny")
    check("it refuses", out.returncode, 2)
    check("without a traceback", "Traceback" in out.stderr, False)
    shutil.rmtree(d)


def test_a_refusal_speaks_the_vendors_own_shape():
    """Exit 2 is the block channel on Claude, Codex and Copilot and is ignored on the rest.

    Without the JSON, `--on-error deny` failed OPEN on cursor, gemini and antigravity — the
    opposite of what the flag promises, on half the runtimes in scope.
    """
    d = repo()
    render(d)
    shutil.rmtree(os.path.join(d, ".agents", "vendor"))
    cursor = fire(d, "--hook", "deny-irreversible", "--vendor", "cursor", "--on-error", "deny")
    gemini = fire(d, "--hook", "deny-irreversible", "--vendor", "gemini", "--on-error", "deny")
    stop = fire(d, "--hook", "guardrails-gate", "--vendor", "claude", "--event", "stop",
                "--on-error", "deny")
    check("cursor is told in its own shape", json.loads(cursor.stdout)["permission"], "deny")
    check("gemini too", json.loads(gemini.stdout)["decision"], "deny")
    check("a stop refusal blocks the stop", json.loads(stop.stdout)["decision"], "block")
    yields = fire(d, "--hook", "record-guardrail-run", "--vendor", "gemini",
                  "--event", "post-tool", "--on-error", "allow")
    check("and a recorder still yields, in that shape too",
          json.loads(yields.stdout), {"decision": "allow"})
    check("without blocking", yields.returncode, 0)
    shutil.rmtree(d)


# ── the pin parser ───────────────────────────────────────────────────────────────────────────────

def shim_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dispatch_shim", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_manifests_own_version_key_is_not_the_pin():
    m = shim_module()
    check("reads the import, not `version: 2`",
          m.pinned("version: 2\nimports:\n  - bundle: exeris-agents\n    version: 1.3.0\n"),
          ("exeris-agents", "1.3.0"))
    check("and not a `version:` after the block",
          m.pinned("imports:\n  - bundle: a\n    version: 1.0.0\nversion: 2\n"), ("a", "1.0.0"))


def test_an_import_naming_no_version_is_not_a_pin():
    m = shim_module()
    check("an unpinned import yields nothing",
          m.pinned("imports:\n  - bundle: exeris-agents\nagents: []\n"), None)
    check("no imports at all yields nothing", m.pinned("version: 2\nimports: []\n"), None)


def test_a_flow_style_imports_block_is_read():
    """Valid YAML the renderer accepts. A line reader cannot see it, and the None it returned put
    the shim straight into the deny path — a disagreement about which bundle runs, decided by
    which parser happened to be in use."""
    m = shim_module()
    check("flow style resolves",
          m.pinned("version: 2\nimports: [{bundle: exeris-agents, version: 1.3.0}]\n"),
          ("exeris-agents", "1.3.0"))


def test_the_pin_comes_only_from_imports():
    """A `- bundle:` under any other key must not resolve. The renderer and the checker read
    `imports:`; a parser that reads the first one anywhere can pick a different bundle than they
    do, silently, and this file decides which code runs."""
    text = ("version: 2\n"
            "provider-owned:\n"
            "  - bundle: not-this-one\n"
            "    version: 6.6.6\n"
            "imports:\n"
            "  - bundle: exeris-agents\n"
            "    version: 1.3.0\n")
    m = shim_module()
    check("through pyyaml", m.pinned(text), ("exeris-agents", "1.3.0"))
    check("and through the line reader", m.pinned_by_line(text), ("exeris-agents", "1.3.0"))


def test_the_line_reader_agrees_with_pyyaml_on_the_real_manifest():
    """The fallback is only worth having if it answers the same question the same way."""
    m = shim_module()
    text = ("version: 2\nimports:\n  - bundle: exeris-agents\n    version: 1.3.0\n"
            "    ref: deadbeef\n    sha256: 'sha256:00'\nagents: []\n")
    check("both readers agree", m.pinned_by_line(text), m.pinned(text))
    check("and on an item whose version comes first",
          m.pinned_by_line("imports:\n  - version: 2.0.0\n    bundle: b\n"), ("b", "2.0.0"))


def test_the_pin_survives_key_order_and_quoting():
    m = shim_module()
    check("bundle after another key",
          m.pinned("imports:\n  - ref: x\n    bundle: exeris-agents\n    version: 1.3.0\n"),
          ("exeris-agents", "1.3.0"))
    check("quoted values",
          m.pinned("imports:\n  - bundle: 'exeris-agents'\n    version: \"1.3.0\"\n"),
          ("exeris-agents", "1.3.0"))
    check("the first import that names both",
          m.pinned("imports:\n  - bundle: other\n  - bundle: exeris-agents\n    version: 2.0.0\n"),
          ("exeris-agents", "2.0.0"))


if __name__ == "__main__":
    main(globals())
