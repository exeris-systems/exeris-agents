---
title: "exeris-agents: the shared agent bundle for the Exeris ecosystem"
type: reference
visibility: public
owning-repo: exeris-agents
status: active
last-verified: 2026-09-08
---

# exeris-agents: the shared agent bundle

Guardrails for an agent working inside this repository. Human-facing description is in
[`README.md`](README.md).

## Mission and scope

This repository holds the **implementation** of the agent-file standard: the organisation-wide
policies, the base decision schemas, the L0 hook dispatcher, the eval runner, and the tooling that
renders and checks every repository's `.agents/`.

It does **not** hold the standard. `agents-md-schema.md` lives in `exeris-docs/standards/`, made
binding by ADR-085 §I, and §C.10 makes that directory the standard's single home. A rule authored
here would be a second place to author one rule — the failure the schema's own rule 2 forbids one
level down. When this repository and the standard disagree, the standard wins and this repository
is the defect.

## Operating contract

**Everything here is consumed by other repositories.** A change is never local: it reaches twenty
checkouts through a version pin. So —

- **A change to `bundle/` is a version change.** SemVer over the schema contract: a change that
  makes a conforming repository stop conforming is MAJOR — and so is one that turns a
  previously-green consumer build red, because for a bundle of gates that is the public surface.
  An ignorable addition is MINOR, wording PATCH. A `### Breaking` section is mandatory per release
  (ADR-085 §H.27); its content decides the number, not its presence. `CHANGELOG.md` moves in the same pull request (ADR-085 §H.27).
- **A change to `bundle/policies/` may only restrict.** A consuming repository may restrict
  further and may never relax, so a relaxation here silently relaxes every repository at once.
- **A change to `bundle/schemas/` that adds a required property is MAJOR**, because a repository's
  composed schema starts rejecting answers its roles already produce.
- **Never widen a base schema's role vocabulary.** Role names are per-repository by design
  (schema rule 10 keeps the repository prefix); the base leaves `agent`, `task_class` and
  `scope_class` open and the repository narrows them. A name that appears here is a leak.
- **`tools/adapters/<vendor>.yaml` is written from the runtime's documentation, never from
  memory.** A guessed tool name silently grants or withholds a capability. If the vocabulary has
  not been read, the vendor does not ship — an absent adapter is the honest state.

## Language

English everywhere — source, comments, commit messages, pull-request titles, documents.

## Entry points

| Path | What it holds |
|:--|:--|
| [`bundle/`](bundle) | What a consuming repository vendors: `policies/`, `schemas/`, `hooks/bin/`, `evals/`. Read [`bundle/BUNDLE.md`](bundle/BUNDLE.md) first. |
| [`tools/`](tools) | What CI checks out: the renderer, the agent-file checker, the bundle materialiser, the vendor mappings. |
| [`CHANGELOG.md`](CHANGELOG.md) | The contract history. A `bundle/` change without an entry is incomplete. |

## Verification and reporting

Before opening a pull request, run the tools against a real consumer rather than against nothing —
a renderer that is green on an empty tree has proved nothing:

```
python3 tools/agents_file_check.py     --root ../exeris-docs
python3 tools/agents_render.py --check --root ../exeris-docs
python3 tools/agents_bundle.py verify  --root ../exeris-docs
python3 tools/agents_bundle.py digest  --from .
```

Report the counts, and report a check that did not run as not run
([`bundle/policies/error-handling-and-fallback.md`](bundle/policies/error-handling-and-fallback.md)
rule 1). A change to `bundle/` that a consumer's evals cover reruns them and the pull request names
the run (schema rule 14).

## Safety

[`bundle/policies/agent-safety-and-autonomy.md`](bundle/policies/agent-safety-and-autonomy.md)
applies to work in this repository as it does everywhere else — and one item of it bites here in
particular: publishing. Cutting a tag, `npm publish` and a GitHub release are human-in-the-loop,
because a published version is what other repositories pin against and a bad one cannot be recalled
from a checkout that already vendored it.
