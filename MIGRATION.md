# Migration

What a conforming repository has to do to move between two versions of this bundle. One section
per upgrade that asks for work: [`CHANGELOG.md`](CHANGELOG.md) says what changed and why, and this
file says what to type. A release with a non-empty `### Breaking` section gains a section here
before its tag (`changelog-conventions.md` rule 8).

## 1.4 → 2.0

**What moved.** The three base schemas stop closing themselves. No object in
`verdict.base.schema.json`, `handoff.base.schema.json` or `triage-result.base.schema.json` declares
`additionalProperties` or `unevaluatedProperties` any more, because a composing schema cannot add a
property to a base that does — `unevaluatedProperties` in the base reads like the fix and is not:
it sees the annotations of its own schema object and its in-place applicators, never a sibling
`allOf` branch in your schema.

**What that costs you.** Nothing here adds a required field or moves the vendored layout. The bases
became *more permissive*, and until you close your compositions yourself, a schema that was
refusing a foreign property accepts one — silently, with nothing going red. That is the whole of
the work below, and it is why this is a MAJOR.

### 1. Re-vendor, and move the pin

Bumping the version means re-vendoring, never editing the vendored copy in place: `sha256` in
`.agents/manifest.yaml` is a digest over every byte of the tree.

From a checkout of this repository, with `$AGENTS` its path and `$SHA` the commit you are
vendoring (it is recorded in the pin, so `verify` can say which tree the digest belongs to):

```
python3 "$AGENTS/tools/agents_bundle.py" vendor --root . --from "$AGENTS" \
    --version 2.0.0 --ref "$SHA"
python3 "$AGENTS/tools/agents_bundle.py" verify --root .    # pin, tree and digest agree
```

Every relative `$ref` in your schemas carries the pin (`../vendor/exeris-agents-1.4.0/…`), so the
version appears in each composed schema too.

### 2. Add one closer per object — not one per schema

`"unevaluatedProperties": false` at the root of every schema that `$ref`s a base, **and again in a
subschema over every object the base leaves open**. The keyword stops at the object it sits in: a
root closer leaves every array item taking any property. Measured on a composition closing only its
root — a foreign property at the root is refused and one in a check entry walks in.

Eight objects across the three compositions:

| Composition | Objects to close |
|:--|:--|
| verdict | the root, a `findings` item, a `checks_run` item, a `handoffs` item |
| triage-result | the root, a `validation_gates` item, a `secondary_handoffs` item |
| handoff | the root |

The `handoffs` and `secondary_handoffs` items are declared one file over, in
`handoff.base.schema.json`, and are open all the same.

An object you do not extend still needs a subschema of its own, carrying **both** the `$ref` and
the closer:

```json
"checks_run": {
  "items": {
    "allOf": [{ "$ref": "../vendor/exeris-agents-2.0.0/schemas/verdict.base.schema.json#/properties/checks_run/items" }],
    "unevaluatedProperties": false
  }
}
```

The `$ref` is not optional. A closer without it closes an object whose properties nothing declared
at that location, so every field the base defines — `check`, `result` — comes back as unevaluated.
Measured, on this exact pair: with the `$ref`, no error; without it, a line naming the base's own
fields on a decision that was well-formed.

`python3 tools/agents_file_check.py --root .` names every object left open, so this is enumerated
rather than discovered.

### 3. Point every eval case at the composed schema

`expect.schema` naming a bundle base is now refused rather than advised against. A base fixes the
shape and leaves the vocabulary and every closer to you, so from 2.0.0 it accepts a foreign
property anywhere and any value your own enums exclude — a case graded against one passes on
answers your repository refuses. Name the composed schema in `.agents/schemas/` instead.

### 4. What a raw validation failure looks like now

Beside the real error, a raw Draft 2020-12 validator will carry a line naming your own fields:

```
<root>: Unevaluated properties are not allowed ('agent', 'checks_run', 'decision', … were unexpected)
```

Those are not the problem. A composition validates through a `$ref` into the base; when anything
inside that branch fails, the branch fails, and a failing subschema contributes no annotations, so
the closer above it reports everything present. The eval runner filters these secondary artefacts —
read the other errors first, and the unevaluated line goes when they do.

### Order, and what you can do before the bump

The **root** closer can be added while you are still on 1.4.0: over that closed base it changes no
outcome, so it is a safe, separate commit. The per-object closers and the `$ref` subschemas cannot
go in early, because 1.4.0's base refuses the added field — which is the reason this release
exists.

### When you are done

```
python3 "$AGENTS/tools/agents_file_check.py" --root .   # no object left open
python3 "$AGENTS/tools/agents_bundle.py" verify --root .

# the runner is vendored, so it runs from under the pin — not from `.agents/evals/`, which
# holds your scenarios and no runner at all
python3 .agents/vendor/exeris-agents-2.0.0/evals/run.py \
    --scenarios .agents/evals/scenarios.yaml --dry-run
```
