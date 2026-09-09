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


def render(d: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, RENDERER, "--root", d, "--vendor", "claude", *args],
                          capture_output=True, text=True)


def commands(d: str) -> list[str]:
    settings = json.load(open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8"))
    return [h["command"] for entries in settings.get("hooks", {}).values()
            for e in entries for h in e.get("hooks", [])]


def fire(d: str, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run the rendered command the way the runtime would: from the repository root."""
    return subprocess.run([sys.executable, os.path.join(d, ".agents", "hooks", "bin", "dispatch.py"),
                           *args], capture_output=True, text=True, cwd=cwd or d)


# ── what the renderer writes ─────────────────────────────────────────────────────────────────────

def test_the_rendered_command_names_no_version():
    d = repo("1.3.0")
    render(d)
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
    check("nothing ran for it", meta.stdout, "")
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
