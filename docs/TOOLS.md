# Tools: dubforge, forge3d, cutforge

Three engines, one interface, and an honest inventory.

```bash
vrusha tools                          # what this machine can run, and why not
vrusha tools dubforge                 # one tool's actions
vrusha tools dubforge --action doctor
vrusha tools forge3d --action create --option prompt="a low-poly chair"
```

```
  tool      ready  mode           why
  cutforge  no     specification  specification only: the repository holds the engine boundary
  dubforge  yes    checkout       found at ../dubforge/src
  forge3d   yes    checkout       found at ../forge3d/src
```

## What "merged" means here

The adapter is in this repository. The code is not.

Copying dubforge's 83 files into `vrusha/tools/dubforge/` would buy two copies of
one product, one of which nobody updates, and would pull `trimesh`, `scipy` and
`pillow` into a package whose only hard dependency is numpy. What merging needs to
mean is: **one repository to clone, one command to learn, one place that says
whether a tool is usable and why not.** That is what this layer is.

A tool is found three ways, in order:

| mode | how | what it means |
|---|---|---|
| `module` | `import dubforge` works | the package is installed in this environment |
| `checkout` | `../dubforge/src/dubforge` | a sibling clone, used with `PYTHONPATH` |
| `cli` | `dubforge` on `PATH` | an installed entry point |
| `none` | — | not installed, and the reason says what to install |

A relative checkout is resolved against **the repository root**, not the current
directory: a tool must be found the same way from the repo, from `/tmp`, or from a
cron job.

## Why a subprocess and a whitelist

Every tool runs as a child process, never imported. A tool that calls `sys.exit`,
installs a signal handler or writes a global config cannot then take the caller
down with it — and its dependencies stay out of this process entirely.

Actions are a **whitelist**, not a command line. `run()` takes an action name and
options and refuses anything else, because a generic "run this argv" wrapper is a
remote-code-execution surface with a friendlier name.

## The three, honestly

| tool | licence | state |
|---|---|---|
| **dubforge** | MIT | a working package: ASR, translation, TTS, alignment, mix. Actions: `doctor capabilities providers voices demo dub batch jobs explain verify review` |
| **forge3d** | MIT | a working package: plan, create, render, repair, validate. Actions: `doctor capabilities providers backends plan create render convert repair validate card demo` |
| **cutforge** | *all rights reserved* | **a specification, not an engine.** Its own README: "the technical specification, the engine boundary, the review history and the open findings that the next version must close… there is no product surface here" |

`cutforge` is registered so that `vrusha tools` is an honest inventory rather than
a wish list. It reports `mode: specification` and never `available`. When the
engine lands it becomes an `ExternalTool` with a whitelist like the other two —
one line in `vrusha/tools/cutforge.py`.

**The licence is worth a decision.** `cutforge`'s LICENSE says "All rights
reserved", which cannot be reconciled with this repository's MIT licence if its
code is ever merged. Since both are yours, relicensing is yours to make; until
then, nothing from it is copied here.
