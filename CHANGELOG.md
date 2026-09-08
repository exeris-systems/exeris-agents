# Changelog

All notable changes to the Exeris agent bundle. Keep a Changelog 1.1, SemVer, ADR-085 §H.27.

The version is the **schema contract**, not the file count: a change that makes a conforming
repository stop conforming is MAJOR, a new policy or schema field a repository can ignore is MINOR,
and wording is PATCH.

## [Unreleased]

Nothing yet.

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
