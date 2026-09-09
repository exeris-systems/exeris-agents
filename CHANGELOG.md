# Changelog

All notable changes to the Exeris agent bundle. Keep a Changelog 1.1, SemVer, ADR-085 §H.27.

Versioning, stated precisely because the earlier wording licensed a wrong reading:

- **MAJOR** — the contract moves under a repository that was following it: a new required field, a
  removed or renamed manifest key, a changed vendored layout, a removed or renamed CLI flag.
- **MINOR** — a new check, policy or schema field. A new check *can* turn a green build red, but
  only where the repository was already not conforming: that is the check catching up with a rule
  that already bound, not the contract moving. This is how check tooling is versioned everywhere,
  and it is what "a conforming repository stops conforming" was always meant to say.
- **PATCH** — wording, and fixes that only make a previously-failing case pass.

A `### Breaking` section is **mandatory in every release** (ADR-085 §H.27), so "Breaking: nothing"
is an answer and the section's presence never implies MAJOR. Its content, against the three rules
above, decides the number.

## [Unreleased]

## [1.3.1] - 2026-09-09

Seven findings from an xhigh review of `exeris-kernel`'s v2 migration, all in the executable layer.
Each was reproduced before it was changed, and each has a regression test.

### Breaking

- **`.claude/settings.json` changes on the next render**, because the command it carries changes
  again — the dispatcher is now anchored to the checkout root. Re-vendor, then re-render.

### Fixed

- **The rendered hook command depended on the working directory.** It named
  `.agents/hooks/bin/dispatch.py` relative, and a hook runs with whatever working directory the
  tool call had. From a subdirectory or a worktree `python3` cannot open it and **exits 2** — and
  exit 2 from a `PreToolUse` hook is a *block*, on every shell call, whatever `--on-error` says.
  The same shape as the version-in-the-path defect 1.3.0 removed, one level up: the interpreter
  answers before the layer can. The command is now `"${VAR:-.}/…"`, where `VAR` is the vendor
  mapping's new `project-dir-var` (`CLAUDE_PROJECT_DIR` for Claude). A vendor that publishes no
  such variable falls back to the relative form and is no worse off than before.
- **`tool_result()` read `interrupted` before `exit_code`.** Claude Code sends
  `interrupted: false` on every Bash event, successful or not, so the first loop returned "the
  runtime said it succeeded" for a script that exited 1 — the exact collapse the function's own
  docstring promises not to make, and it discharged the stop gate. `exit_code` is read first as
  the only field that states an outcome; `error` and `interrupted` are read **only** as failure
  signals, because falsy there says nothing.
- **The recorder credited one gate per command.** It `break`s no longer: `a.sh && b.sh` is one
  tool event running two gates, and stopping at the first left the second armed, so a session that
  ran both was blocked on the one it had run.
- **A stop already blocked could be blocked again.** `stop_hook_active` was never read, so the
  gate re-fired on the turn the operator was using to satisfy it. It now reports once and yields.
- **`sanitised()` checked argument shape but not vocabulary.** A well-formed `--nope x` passed and
  reached argparse inside the delegated hook, which answers an unrecognised argument by exiting 2
  — a deny on every shell call, which is the class of failure the shim exists to remove. The flag
  vocabulary is checked where a refusal can still carry a reason, and a repeated flag is refused
  too.
- **`validate()`'s no-jsonschema fallback was a silent no-op** for every schema written the way
  rule 13 prescribes. An `allOf` of a `$ref` plus enums carries no top-level `required`, so the
  shallow path validated **zero fields** and returned "valid" — a grader silently weakening to
  nothing, which its own docstring calls worse than one that is missing. It now says it cannot
  validate.
- A dead recomputation of `on_error` in `run()`, assigned after the last reader.

### Not fixed, and why

- **Three `repo_root()` implementations.** Real, and deliberate: `dispatch.py`'s docstring states
  that its rule is smaller and does not have to agree with `hook.py`'s, because it only locates
  the manifest before handing off. The other half of that finding — "the stop gate re-walks the
  filesystem once per path test" — **does not reproduce**: `path_matches` reaches `repo_root()`
  only for an absolute path, and the gate's recorded entries are repository-relative, so a stop
  invocation walks once, in `state_dir`. Caching it was tried and reverted: it broke a suite that
  varies the root within one process, which is a real hazard and a worse trade than one walk.

### Added

- Regressions for each fix: `interrupted: false` beside a non-zero exit, its paired positive, one
  command running two gates, a second stop after a block, an unknown and a repeated flag, and the
  rendered command's anchoring.

## [1.3.0] - 2026-09-09

### Breaking

- **Nothing moves the contract.** No manifest key is added, removed or renamed; no CLI flag
  changes; `bundle/schemas/` is untouched; the vendored layout gains a file and loses none. A
  repository conforming on 1.2.0 conforms on this version.
- **`.claude/settings.json` changes on the next render**, because the command it carries changes.
  That is a generated file the renderer owns, and `--check` reports it the way it reports any
  adapter whose source moved: re-render and commit. A repository that upgrades without re-rendering
  keeps working — the old command is still valid until its vendored tree is replaced, which is the
  same bump that rewrites it.
- **A new generated file appears in the canonical tree**: `.agents/hooks/bin/dispatch.py`. It is
  written by the renderer, carries the do-not-edit marker, and belongs in `.agents/` rather than
  under a provider directory because one copy serves every vendor and its path must not move when
  the pin does.

### Fixed

- **The rendered hook command no longer carries the pinned version.** It named
  `.agents/vendor/<bundle>-<version>/hooks/bin/hook.py`, putting the version in two files with
  different lifetimes: the adapter, written when the renderer last ran, and the vendored tree,
  replaced at every bump. A checkout holding one at a version the other does not ran a command
  pointing at a missing file — and the failure was not a warning. `python3` exits 2 on a file it
  cannot open, and exit 2 from a `PreToolUse` hook is a *block*, so every shell call was denied
  whatever each hook's own `--on-error` said: the interpreter answered before the layer could.

  The state is routine, not exotic. A pull request's review environment pairs the base branch's
  protected `.claude/` with the branch's own tree, so the first review of every bundle bump ran
  with no shell at all — twice on exeris-docs #106, reporting ten checks as `not-run`, before
  anyone asked why. A contributor whose editor holds the old settings while the checkout moves
  hits the same wall.

  The rendered config now names `.agents/hooks/bin/dispatch.py`, a version-free copy of a shim the
  bundle ships. It reads the pin from `.agents/manifest.yaml` when the hook fires and hands off to
  the vendored `hook.py`. `manifest.yaml` stays the single authority for which bundle runs; it is
  read at a moment when both halves are on disk together.

- **A missing dispatcher now honours the caller's `--on-error`.** It could not before: the
  interpreter's exit code arrived first, so a recorder declaring `--on-error allow` blocked the
  tool call anyway. Fail-closed is again a property of the rule, which is what `--on-error` was
  introduced to make true.

- **The shim's own directory no longer leads `sys.path`.** Running the hook in-process left the
  interpreter's `sys.path[0]` pointing at `.agents/hooks/bin/` — a generated adapter's home, and
  not part of the tree the pin's digest covers — so a file dropped beside the shim satisfied an
  import made by the shim or by the gate it starts, and L0 could be switched off by *adding* a
  file rather than editing one. Exec'ing hid this: the path then led with the vendored directory.
  That directory is put back in front for the hook, and the shim's own is removed before anything
  is imported.
- **Every escape returns a decision, not a traceback.** The manifest read caught `OSError` only,
  so a file that is not valid UTF-8 raised through `main` and exited 1 — the code every runtime
  reads as "the hook errored", which is *allow*. There is a guard at the read and one around the
  whole call, and both route to the same refusal.
- **A refusal now speaks the vendor's wire format.** It answered with an exit code alone, and exit
  2 is the block channel only on Claude, Codex and Copilot. On cursor, gemini and antigravity a
  missing dispatcher under `--on-error deny` therefore failed **open** — the opposite of what the
  flag promises and of what this file's own docstring claimed.
- **The pin is read only from `imports:`, and through pyyaml when it is importable.** The line
  reader took the first `- bundle:` in any block sequence, so a manifest could resolve a different
  bundle here than in the renderer and the checker; and it could not see a flow-style `imports:`
  that the renderer accepts, returning None and denying instead. It is scoped to the key and is
  now the fallback rather than the mechanism.
- **The renderer cannot emit a command the shim would refuse.** A hook id, a vendor and an event
  become words in a command string, and nothing constrained them to the alphabet the shim checks.
  The renderer validates them where the string is built, and both patterns name each other.
- **The shim's destination is confined too.** It is read to decide whether a stale copy should be
  deleted, and `os.path.isfile` follows a link — so a generated adapter symlinked out of the
  checkout would have had the renderer read a file elsewhere to decide about deleting a link to
  it. A link leading out of the tree stops the render; it is tampering, not a state to write
  through.
- **One resolver for the shim source.** `dispatcher_path` used `os.path.exists`, which follows a
  symlink out of the tree, while `write_dispatch` required containment — so a symlinked vendor
  directory rendered a command naming a shim that was never written, which is the failure this
  release exists to remove. A stale shim is also removed when a re-pinned bundle ships none;
  nothing did that before, and `--check` stayed clean over the orphan.
- **The shim runs the hook in its own process.** It exec'd a second interpreter, which made an
  OS-command sink out of a call that never needed one and paid a Python startup on every tool
  event — the hook fires on all of them. `runpy` runs the confined path here instead; the hook's
  own exit code travels back through `SystemExit`, which is the decision channel on the runtimes
  that document one, and a hook that cannot start now falls to the caller's `--on-error` rather
  than to whatever the child process happened to return.
- **The rendered command is validated before it becomes an argument vector.** The shim is the
  boundary between a provider's config and the dispatcher, and it forwarded whatever the config
  contained. It now requires `--flag value` pairs and rebuilds the vector from strings that each
  matched a pattern, so a hand-edited command is refused there, with a reason, instead of reaching
  an argument parser the reader never associated with the config. The flag *vocabulary* stays
  hook.py's — this checks shape, not names, so a new flag needs no change here.
- **The pin is validated before it becomes a path.** `bundle` and `version` are joined into a
  path under `.agents/vendor/` which is read, written and — through the shim — executed, and
  neither was checked. An absolute `bundle` is the sharp case: `os.path.join` swallows the base it
  was joined to, so the pin could name any directory on the machine, and the shim would have run
  code the digest in rule 8 does not cover. Components must now be plain names, the resolved path
  must stay inside the vendored tree (realpath, so a symlink out is caught too), and the renderer
  refuses such a manifest outright rather than building adapters from it. Raised by SonarCloud on
  the pull request that introduced the shim; the same shape was reachable through the renderer
  before it.

### Changed

- The shim's line reader for the pin uses string operations rather than regular expressions, one
  of which had super-linear backtracking on a line it would never match; `emit` is split so the
  vendor shape table and the exit-code rule are separate functions; and `SystemExit` from the hook
  is no longer caught. Catching it to read `.code` and return the same number was a longer way of
  writing what the interpreter already does, and it dropped the message a non-integer exit
  carries — `sys.exit("hooks.yaml is unreadable")` became a bare 1. `.agents` is a constant in the
  renderer. All four raised by the quality analysis, all four real.

### Added

- `tests/test_dispatch.py` — 77 assertions over the renderer's output and the shim's behaviour,
  including the bump-without-re-render state that produced this, the case where recognising only
  the new command shape would leave a repository with every hook rendered twice, two escapes from
  the vendored tree, a hand-edited command vector, a module planted beside the shim, a manifest
  that is not UTF-8, and a refusal read back in each vendor's own shape. The suite scrubs
  `CLAUDE_PROJECT_DIR` from the environment it runs fixtures in: inheriting it pointed the shim at
  the checkout the suite was being run from, so it passed in CI and failed on a developer's
  machine.


## [1.2.0] - 2026-09-09

### Breaking

- **Nothing moves the contract.** `bundle/schemas/` is unchanged, no manifest key is added,
  removed or renamed, no CLI flag is removed, and the vendored layout is the same. A repository
  that conforms to the schema on 1.1.1 conforms on this version.
- **A profile whose composition does not resolve now fails the check** — the one change that can
  turn a green build red. Rule 5 has required a reference to resolve since schema v2; nothing
  verified it, so a repository could carry a broken one and report `0 errors`. That is the check
  catching up with a rule that already bound, which is MINOR by the rule above and by how check
  tooling is versioned generally. **A repository carrying such a reference must fix it.**
- A case with no `expect.schema` is an error rather than `ok`, and `--report` outside the checkout
  is refused. Both were never correct; the second is a path-safety fix.
- *Not new here, recorded for accuracy:* refusing a `schemas` or `fixtures` directory symlinked
  out of the checkout shipped in **1.1.1**, filed there under *Changed*. It is a behaviour
  restriction released as a patch. Left as published — renumbering breaks the pin that names it —
  and noted so the history is not read as if it happened in this release. Rule 5 always required a
  reference to point at something; nothing verified it, so a repository could carry a policy, a
  skill or a handoff target that does not exist and report `0 errors`. Repositories with such a
  reference go from green to red on this version — correctly, and visibly for the first time.

### Fixed

- **`policies:` and `references:` on a profile were never validated.** Neither that a bare name
  resolves under `.agents/`, nor that `bundle:<name>` resolves into the vendored tree, nor that
  `bundle:` is used at all in a repository that pins no bundle. `skills:` and `handoffs:` were
  unchecked on profiles too — a workflow's `steps.skill` was checked, a profile's was not. An
  empty composition, a typo and a correct profile were indistinguishable at `0/0`. Reported from
  the SDK side.
- Profile checks run in two passes, so a handoff naming a role listed later resolves rather than
  depending on directory order for its verdict.
- **The renderer answered a missing `name` or `description` with a `KeyError` traceback**, which
  says which key but not which file — while every other missing thing in it is reported by name.

### Fixed — the eval runner's path guards, which guarded the wrong things

- **`--scenarios` was read before it was guarded.** `load_yaml()` ran seven lines above
  `within_repo()`, so the one CLI-controlled read the guard exists for still happened: a path
  outside the checkout produced a YAML parse error or a raw `FileNotFoundError` rather than a
  refusal. A guard that runs after its sink is a comment.
- **`--report` was never guarded at all** — the only *write* sink, while three read sinks were
  tightened. Confirmed creating a directory tree and a file outside the repository.
- **`$ref` resolution was a fourth unguarded `open()`**, taking a path fragment out of schema JSON,
  which is the same class of input as the three that were guarded.
- **A case naming no `expect.schema` resolved to the schema *directory* and was reported `ok`** —
  the "resolves to something rather than to the right thing" failure, one level below the one
  1.1.1 fixed.
- **A missing fixture aborted the whole run with a traceback** while a missing schema four lines
  later was recorded and skipped, so one typo hid every later result.

### Fixed — the checker

- `provider_owned_paths()` re-read and re-parsed `manifest.yaml` a third time inside its own
  `except: pass`, so an unparseable manifest produced an empty list and the same false findings
  the entry was added to remove. It takes the parsed manifest and an explicit reporter now.
- `generated-region` was read by nothing: the entry contributed only its `path`, exempting a
  partially generated file as if it were wholly provider-owned. The named region must now be
  present in the file.

### Added

- `tests/test_composition.py` — 12 assertions over composition resolution, the `bundle:` prefix in
  its three states, forward handoffs and the renderer's missing-field message. Eight of them fail
  against 1.1.1.
- Four more assertions in `tests/test_consumer_paths.py`, one per sink: `--scenarios` refused
  *before* the read, `--report` refused with nothing created, a case with no schema reported as an
  error rather than `ok`, and a missing fixture recorded without aborting the run.
- `against-consumer` runs **this branch's** eval runner over the real consumer tree, placed where
  vendoring would place it. Its comment claimed that job covered these cases; it never invoked the
  runner at all, so the eval half had no consumer-level gate. The first attempt at the step ran the
  *consumer's own vendored* runner, which only re-reports whichever bundle that repository is
  behind on — it went red against a consumer still pinning 1.1.0, correctly for the consumer and
  uselessly for the bundle. The two sibling steps had it right: the job exists to test the tools in
  the pull request.
- `_harness.reset()`, because extracting the shared harness replaced a duplicated counter with a
  single un-resettable one: two suites in one process reported each other's numbers.

## [1.1.1] - 2026-09-09

### Fixed

Two defects a consuming repository hits and this one does not, both found by running the 1.1.0
tools against a second consumer rather than by reading them. Each has a regression test that fails
on 1.1.0.

- **A `provider-owned` entry in the mapping spelling rule 7 requires discarded the whole list.**
  `{path: …, generated-region: …}` is how a file with a generated region is declared — a JSON
  settings file has no comment to carry a marker — and the checker read the list with `set(...)`,
  which raises `TypeError` on an unhashable dict inside a bare `except: pass`. One mapping entry
  therefore voided every plain string beside it, and the adapter check reported provider-owned
  operational files as unmarked semantics. `exeris-docs`, the pilot, has such an entry today.
- **The eval runner resolved `schema_dir` and `fixture_dir` against itself.** That is correct only
  while the runner sits at `.agents/evals/`. Vendored — and `evals/` is in the vendored set — it
  sits at `.agents/vendor/<bundle>-<v>/evals/`, so the defaults this repository documents pointed
  at the bundle's own base schemas and at a fixtures directory the vendored tree does not have,
  and **not one scenario resolved in any consumer**. Both are now relative to the scenarios file,
  and `--scenarios` defaults to the repository's `.agents/evals/scenarios.yaml` when there is one.

### Changed

- **The eval runner refuses a path that leaves the checkout.** `--scenarios` is a CLI argument and
  `defaults.schema_dir` / `fixture_dir` are values in a YAML file; all three reach `open()`. The
  runner has no business reading outside the repository it is evaluating, and a `schema_dir` that
  silently resolves somewhere else is the same failure the fix above addresses one level up — a
  path that resolves to *something* rather than to the right thing. A scenarios file that relied on
  escaping the tree now exits with the resolved path named.

Consumers on 1.1.0: re-vendor. The tooling half of the first fix reaches you without one, because
`tools/` is checked out rather than vendored; the eval half does not.

## [1.1.0] - 2026-09-08

### Breaking

- **The L0 failure mode flips from allow to deny.** A dispatcher that could not read its rules
  used to answer `allow`; a hook that *enforces* one now refuses. On a machine without `pyyaml`,
  or with a `hooks.yaml` this dispatcher cannot parse, shell commands that were permitted before
  are now denied with the cause in the reason. That is the point — a layer ADR-085 calls "runtime,
  hard" that switches itself off on a missing dependency is worse than no layer, because the
  operator still believes it is there — but it will stop a checkout that worked yesterday.
- **Every rendered adapter must be re-rendered.** The do-not-edit marker's attribution changed
  (`exeris-systems/.github` → `exeris-systems/exeris-agents`), so `agents_render.py --check` now
  reports drift against adapters generated by 1.0.0 until they are regenerated. Consumers on
  1.0.0: re-vendor, then `agents_render.py --root .`.
- **Rendered hook commands gain `--event` and `--on-error`.** A command rendered by 1.0.0 still
  runs, and defaults to `--on-error deny`, so it fails in the safe direction — but a *recorder*
  invoked by a stale command will refuse instead of yielding when its config is unreadable.
  Re-rendering resolves it.

### Fixed

Findings from a review of the 1.0.0 dispatcher, each with a regression test that fails on 1.0.0:

- **The stop gate — the layer's actual enforcement — still failed open.** Failing closed was
  decided by a hook-id name prefix (`deny*`), and the gate is not called that. It is now decided
  by the hook's own `decision` in `hooks.yaml`, rendered onto the command, so the guarantee
  follows the rule instead of the name.
- **`main()` re-raised the one exception the layer made fatal**, turning a refusal into an
  uncaught traceback and exit 1 — which every runtime reads as a non-blocking hook error, that is,
  allow.
- **The no-session fallback was per process.** Each hook is spawned by its own shell, so a
  pid-derived key gave the recorder and the gate different directories and the gate never fired.
  One shared key now, cleared when the gate passes, so bleed between sessions is bounded.
- **A session id of `..` escaped the state directory** and wrote into the repository root, outside
  the gitignore entry. The key is sanitised to `[A-Za-z0-9_-]` with no dots.
- **`tool_failed` collapsed "no information" into "succeeded".** It is now tri-state: a check
  whose result the runtime did not report is recorded as *invoked*, and the gate's reason says it
  verifies invocation and never success rather than implying the stronger claim.
- **The failed-call guard covered shell recorders only**, so an edit the runtime rejected still
  recorded the file as edited.
- **`repo_root()` resolved from the script's own location**, so a dispatcher not vendored inside
  the target read another repository's rules entirely. Found by the new suite, not by review.
  The working directory is the authority now; the script's location is the fallback.
- Reading state created a directory as a side effect; the shell recorder scanned every pattern
  twice; the module docstring still described the per-checkout behaviour that 1.0.0 removed.

### Added

- `agents_bundle.py vendor` retargets **every reference into the vendored tree** — a schema
  `$ref`, a Markdown link in `AGENTS.md`, a backtick span in a policy — to the version it just
  wrote, and prints each file it moved. The vendored path carries the version, so all of them go
  stale on a bump. Both halves of this broke a build once: the schema `$ref`s failed the
  agent-file check, and a link in `AGENTS.md` failed the link check afterwards, because the first
  fix covered only the schemas. Leaving it to be caught means every consuming repository
  hand-edits every referring file on every bump, forever.
- `tests/test_hook.py` — 41 assertions over the gate, the failure modes, the session key, the
  envelope and the deny matcher in both directions, run in CI. Its absence is why the same layer
  needed three rounds of fixing.

## [1.0.0] - 2026-09-08

### Breaking

- Nothing. This is the first release line.

### Added

- `policies/agent-safety-and-autonomy.md` and `policies/error-handling-and-fallback.md`, the two
  organisation-wide policies, generalised out of `exeris-docs` where they were first written.
- `schemas/{handoff,verdict,triage-result}.base.schema.json` — the decision handoffs with the role
  vocabulary lifted out, so a repository narrows them by `allOf` instead of copying them.
- `hooks/bin/hook.py`, the L0 dispatcher: one implementation, six vendor wire formats, no patterns
  of its own.
- `evals/run.py` and `evals/eval-rubric.md`.
- `tools/agents_render.py`, `tools/agents_file_check.py`, `tools/adapters/claude.yaml` — moved from
  `exeris-systems/.github`, where they had lived for a day.
- `tools/agents_bundle.py` — vendor, digest and verify. `agents-md-schema.md` rule 8's
  `[L1: pinned-import and checksum check]` names a real check from here on.

### Fixed before first release

Fifteen findings from an adversarial review of this branch, all confirmed in the code before
being fixed. The ones that mattered:

- the L0 stop gate discharged a rule when *any* of its required checks had run, not all of them —
  latent while every rule lists one check, and a silent softening the moment a second is added;
- `render_hooks` replaced the whole `hooks` key of `.claude/settings.json`, deleting every
  hand-authored hook. It now merges, recognising its own entries by the dispatcher they invoke;
- adapter frontmatter was built by f-string, so a description containing `': '` produced invalid
  YAML and the renderer reported success. It is emitted through a YAML dumper;
- `_common.py` was a 216-line verbatim copy of another repository's helper, of which two names
  were used, shipping in the npm tarball. Replaced by a 90-line local module;
- `prune` never removed a stale per-skill symlink, `--skills-copy` compared one directory level,
  `staged()` digested a partial bundle as complete, the vendored eval runner resolved the
  repository root two levels short, and an unmapped hook event was dropped silently.
