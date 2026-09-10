# exeris-agents

The shared Exeris agent bundle: the organisation-wide policies, the base decision schemas, the L0
hook dispatcher, the eval runner, and the tooling that turns a repository's `.agents/` into every
provider's adapters.

The **standard** this implements is [`agents-md-schema.md`][schema] in `exeris-docs`, made binding
by [ADR-085][adr] §I. That separation is deliberate: a standard is a document and `standards/` is
its single home (§C.10), so if the bundle also held the standard there would be two places to
author one rule — the thing the schema's own rule 2 forbids one level down. **The standard says
what is true; this repository is what makes it run.**

## Two halves, two consumption models

| | `bundle/` | `tools/` |
|:--|:--|:--|
| What | policies, base schemas, hook dispatcher, eval runner | renderer, agent-file checker, bundle materialiser, vendor mappings |
| How it reaches a repository | **vendored**, committed under `.agents/vendor/exeris-agents-<version>/` | **checked out** in CI at a pinned ref |
| Why that way | rule 8 forbids fetching policies or scripts at agent runtime; a reader on github.com, a CI runner with no network and a fresh clone must all resolve the same `$ref` | copying executable tooling into twenty repositories is the duplication this repository exists to remove |

## Consuming it

```bash
# once, when choosing a version — the network is used here, by a human, and nowhere else
python3 tools/agents_bundle.py vendor --root ../my-repo --from . --version 2.0.0 --ref <sha>
```

It prints the pin to paste into `.agents/manifest.yaml`:

```yaml
imports:
  - bundle: exeris-agents
    version: 2.0.0
    ref: <full commit sha>
    sha256: sha256:<digest over every vendored byte>
```

A role then composes a bundle policy by prefix, so a reader can tell at a glance which rules are
the organisation's and which the repository added:

```yaml
policies: [adr-registry, bundle:agent-safety-and-autonomy]
```

and a repository's schema narrows a base one instead of copying it:

```json
{ "allOf": [
    { "$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json" },
    { "properties": { "agent": { "enum": ["my-repo-reviewer"] } } } ],
  "unevaluatedProperties": false }
```

The closer is the composition's, not the base's: a base that closes itself cannot be extended, and
each base's `description` says so and says where the closer belongs. Without it the schema
accepts any property at all, so `agents_file_check.py` reports a composition that carries none —
at its root, and inside any subschema that extends a shape the bundle owns.

## Verifying it

```bash
python3 tools/agents_bundle.py verify     --root ../my-repo   # digest matches the pin
python3 tools/agents_file_check.py        --root ../my-repo   # schema rules 1-13
python3 tools/agents_render.py --check    --root ../my-repo   # adapters match their source
```

The first two run in `docs-lint`; the third is the adapter-drift gate. An adapter edited by hand is
a diff, and so is a vendored policy edited in place.

## Adding a runtime

One file: `tools/adapters/<vendor>.yaml`, mapping capabilities to that runtime's tool names, model
tiers to its model ids, canonical hook events to its own, plus target paths. Never a change to the
renderer. Only `claude.yaml` ships today — a mapping written from memory would silently grant or
withhold tools, which is worse than an absent adapter, so a runtime lands when its vocabulary has
been read.

## Versioning

SemVer over the **contract**. MAJOR is the contract moving under a repository that was following
it — a new required field, a removed or renamed manifest key, a changed vendored layout. A new
check is MINOR even when it turns a build red, because it does that only where the repository was
already not conforming. Wording is PATCH. A `### Breaking` section is mandatory in every release
(ADR-085 §H.27), so its presence never implies MAJOR — its content does. See [`CHANGELOG.md`](CHANGELOG.md).

## Licence

Apache-2.0. Contributions follow the terms in the organisation's
[`CONTRIBUTING.md`](https://github.com/exeris-systems/.github/blob/main/CONTRIBUTING.md).

[schema]: https://github.com/exeris-systems/exeris-docs/blob/main/standards/agents-md-schema.md
[adr]: https://github.com/exeris-systems/exeris-docs/blob/main/adr/ADR-085-documentation-architecture-and-repo-hygiene-standards.md
