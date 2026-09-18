---
title: "Review rules for exeris-agents"
type: reference
visibility: public
owning-repo: exeris-agents
status: active
last-verified: 2026-09-18
---

# Review rules for `exeris-agents`

The `repo-routine` extension of `exeris-systems/.github`'s `docs-guardrails-review.md`, applied
**after** its steps and under its severity tags, output format and verdict schema. It adds checks
and raises severities; it lowers nothing and skips nothing. One review, one verdict, one publisher —
the extension is what makes having rules of one's own stop being a reason to keep a review of one's
own.

## What this repository is answerable for

Every other repository's agent layer. The policies twenty repositories are judged against, the base
schemas their own schemas compose, the L0 dispatcher that runs on every hook event, the eval runner,
and the renderer that writes their provider adapters. None of that is prose the shared routine can
read, and a change here is never local: it reaches a checkout through a version pin, and a bad
version cannot be recalled from a repository that already vendored it.

`AGENTS.md` states the operating contract these rules apply. It is the source; this file is what a
reviewer does with it, and where the two disagree `AGENTS.md` wins and this file is the defect.

Three of the rules below are a program. `ci/contract_check.py gate` decides them, its cases are in
`tests/test_contract_check.py`, and they are named here anyway so that a review **reports** them
rather than assuming CI did — which is the state R1 of `exeris-systems/.github`'s own rules exists
because of. The rest need a reader.

## Step R — rules of this repository

R1. **A change to `bundle/` is a version change, and the number is an argument.** The changelog
    moving with it is `contract_check` G1 and is mechanical. The number is not: `CHANGELOG.md`'s
    preamble states three cases, and MAJOR is *the work a conforming consumer must do*, whichever
    direction the text moved — a base that stops constraining is MAJOR by relaxation. A pull request
    changing `bundle/` whose body does not say which case it falls under → `[STYLE]`; one arguing a
    number the preamble's cases contradict → `[HARD BLOCK]`. 2.0.0 was MAJOR for relaxing, and it is
    the reading a reviewer is most likely to get backwards.

R2. **A base refuses nothing and names no role.** `contract_check` G2 fails on a closer
    (`additionalProperties`, `unevaluatedProperties`, `unevaluatedItems` set to `false`) anywhere in
    `bundle/schemas/*.base.schema.json`, and on `enum` or `const` under `agent`, `task_class` or
    `scope_class`. A pull request that reintroduces either → `[HARD BLOCK]`, reported rather than
    left to the gate: a closer in a base cannot be extended by the schema that composes it, and a
    role name in a base leaks into every composed schema at once (agents-md-schema.md rule 10).

R3. **A release with a non-empty `### Breaking` section says what to type.** The section's presence
    is `release.yml`'s gate at the tag and `contract_check` G4's at the pull request; a
    `MIGRATION.md` section naming the release is G3's. What neither can judge is whether the section
    is a migration: steps a repository can follow, in the order it must follow them. A section that
    describes the change again instead → `[DOC DEBT]` naming what a consumer still cannot do.

R4. **`bundle/policies/` may only restrict.** A consuming repository may restrict further and may
    never relax, so a relaxation here relaxes twenty repositories at once, silently, with nothing
    going red anywhere. Any removed or weakened line — `REPOSITORY CHECK OUTPUT` lists them — →
    `[HARD BLOCK]` unless the pull request names what permits it. Adding a constraint is free and is
    not this rule.

R5. **A rule arrives with a case that can fail it.** A check, branch or refusal added to or changed
    in `tools/*.py`, `bundle/hooks/bin/*.py`, `bundle/evals/run.py` or `ci/*.py` without a case in
    `tests/` → `[HARD BLOCK]`. A rule nothing can fail on is not enforced, it is described. This
    repository's own history is the argument: three review rounds each found the enforcement layer
    reporting enforcement it did not perform, and each was answered with a hand-run check nobody
    kept, so the next change re-broke what the last one fixed.

R6. **An adapter is written from the runtime's documentation, never from memory.** A change to
    `tools/adapters/<vendor>.yaml` whose pull request does not cite what the vocabulary was read
    from → `[HARD BLOCK]`. A guessed tool name silently grants or withholds a capability, and an
    absent adapter is the honest state.

R7. **Publishing is human-in-the-loop.** A change widening `release.yml` beyond a tag a person cut,
    granting a write scope to a job that runs on merge, or relaxing
    `bundle/policies/agent-safety-and-autonomy.md` §1 → `[HARD BLOCK]`. A published version is what
    twenty repositories pin against.

R8. **Nothing here interprets events, aggregates them, or names a model as fit for a workload**
    (ADR-086 §A.2 and §H.36, and `docs/adr/ADR-086.link.md` in this repository's own words).
    `exeris-agents` owns event semantics; `exeris-ai-execution` owns their interpretation. Any
    artefact that crosses that seam — hook code, policy line, schema description, README sentence →
    `[HARD BLOCK]`.

R9. **A change a consumer's evals cover reruns them, and the pull request names the run**
    (agents-md-schema.md rule 14, `AGENTS.md`). A change to `bundle/schemas/` or to
    `bundle/evals/run.py` always covers them. Absent → `[CONTRACT]`; a run named without the
    consumer, the branch and the counts it produced → `[STYLE]`.

R10. **The counts are measured at the commit being reported, and a check that did not run says so**
    (`bundle/policies/error-handling-and-fallback.md` rule 1). A *Verification* section whose
    numbers disagree with `REPOSITORY CHECK OUTPUT` → `[STYLE]`; one claiming a check or a case
    that does not exist → `[HARD BLOCK]`. `contract_check` reports a diff-scoped rule it could not
    run as not-run; a body that reads that as a pass is this finding.

R11. **The bundle cannot import itself.** `imports: []` in `.agents/manifest.yaml` is structural
    here, not provisional, and `agent-check: false` in `guardrails.yml` follows from it. A pull
    request adding an import, vendoring this bundle into this repository, or turning the shared
    agent check back on → `[HARD BLOCK]`: the fetched tree would come from `main`, so a change to
    `agents_file_check.py` would be judged by the version it replaces.

## Where this does not apply, and what it costs

Not to the shared routine's own steps — pull request body, records, commits, hygiene — which
`docs-guardrails-review.md` judges and which are not restated here: a rule in two places drifts in
one of them. Not to what a consuming repository does with the bundle; that belongs to the caller's
own `repo-routine`.

Not to the standing debt `contract_check locate` reports. Three releases cannot satisfy R3 and never
will: `1.1.1` carries no `### Breaking` section at all, and `1.1.0` and `1.4.0` carry non-empty ones
with no `MIGRATION.md` section naming them. That is why G3 and G4 are forward-only, and it is
reported every run so it is not forgotten — but it is history, not the pull request in front of you,
and raising it as a finding against a change that did not cause it is noise. Raise it once, as
`[DOC DEBT]`, against a pull request that touches those files for another reason.

The cost is that R1's number, R3's substance, and R4, R6, R7, R8 and R9 entire are judgement rather
than a program. R4 has a locator that finds candidates and decides nothing; R6, R7 and R8 have
nothing at all, and a reviewer that reads them loosely enforces them loosely. "Checkable, not
checked" is the state to say out loud, and R2 is here because it was exactly that until this file
and its gate existed together.
