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

- **A change to `bundle/` is a version change.** SemVer over the schema contract, and the three
  cases are written once — in [`CHANGELOG.md`](CHANGELOG.md)'s preamble, the file that ships to a
  consumer and the file whose own history is the evidence for them. Read them there before
  choosing a number; a copy here would be a second place to author one rule, and the copy that
  stood here was already missing a case. A `### Breaking` section is mandatory per release
  (ADR-085 §H.27); its content decides the number, never its presence. `CHANGELOG.md` moves in the
  same pull request (ADR-085 §H.27).
- **A change to `bundle/policies/` may only restrict.** A consuming repository may restrict
  further and may never relax, so a relaxation here silently relaxes every repository at once.
- **A change to `bundle/schemas/` lands in every repository's composed schema.** Adding a required
  property makes that schema reject answers its roles already produce; removing a constraint makes
  it accept what it used to refuse, silently, until the repository closes the shape itself. Either
  is work for a repository that was conforming, which is what the changelog's preamble weighs.
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

# and the evals, driven by THIS branch's runner rather than the consumer's pinned copy — otherwise
# the run only re-reports whichever version that repository is behind on. Copy into a scratch copy
# of the consumer: the runner writes its report inside the tree it evaluates.
cp bundle/evals/run.py <scratch>/.agents/vendor/exeris-agents-<pin>/evals/run.py
(cd <scratch> && python3 .agents/vendor/exeris-agents-<pin>/evals/run.py \
    --scenarios .agents/evals/scenarios.yaml --dry-run)
```

Report the counts **measured at the commit being reported**, not at whichever run happened first,
and report a check that did not run as not run
([`bundle/policies/error-handling-and-fallback.md`](bundle/policies/error-handling-and-fallback.md)
rule 1). A change to `bundle/` that a consumer's evals cover reruns them and the pull request names
the run (schema rule 14) — a change to `bundle/schemas/` or to `evals/run.py` always covers them.

## Safety

[`bundle/policies/agent-safety-and-autonomy.md`](bundle/policies/agent-safety-and-autonomy.md)
applies to work in this repository as it does everywhere else — and one item of it bites here in
particular: publishing. Cutting a tag, `npm publish` and a GitHub release are human-in-the-loop,
because a published version is what other repositories pin against and a bad one cannot be recalled
from a checkout that already vendored it.
