# Changelog

All notable changes to the Exeris agent bundle. Keep a Changelog 1.1, SemVer, ADR-085 §H.27.

The version is the **schema contract**, not the file count: a change that makes a conforming
repository stop conforming is MAJOR, a new policy or schema field a repository can ignore is MINOR,
and wording is PATCH.

## [Unreleased]

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
