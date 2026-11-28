# The documentation, indexed

Sixteen documents and a changelog. This page says what each one is for, so the
next person does not have to open all of them.

## Start here

| document | what it answers |
|---|---|
| [PLAN-V3.md](PLAN-V3.md) | **the current plan**: the review, the repo, and forty parts in nine phases |
| [MASTER-PLAN.md](MASTER-PLAN.md) | the previous plan: the benchmark landscape, a 70-skill inventory, six phases |
| [PROGRAM.md](PROGRAM.md) | the previous six-part programme, all of it done |
| [AGENTS.md](AGENTS.md) | the four-level agent design: context, limits, security, ten steps |
| [CHANGELOG.md](../CHANGELOG.md) | every wave, in order, with the bug each one found |

## How it works

| document | what it answers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | the store, the gates, the pieces and how they fit |
| [ADDRESS.md](ADDRESS.md) | the numeric tree: why a chunk has a path of five numbers |
| [PASSES.md](PASSES.md) | merge, cluster, centroid and graph passes |
| [RULES.md](RULES.md) | the ten levels of gate rules, and why a rule is a gate |

## Getting data in

| document | what it answers |
|---|---|
| [EXTRACT.md](EXTRACT.md) | pages and files to typed rows: tables, JSON-LD, PDFs, formulas, relations, entities |
| [MATH.md](MATH.md) | formulae canonicalised and searched as formulae |
| [RESEARCH.md](RESEARCH.md) | a question in, an answer out, with every source and its licence |

## Getting answers out

| document | what it answers |
|---|---|
| [ANN.md](ANN.md) | the vector index: IVF, int8, rescore |
| [BACKENDS.md](BACKENDS.md) | exact, native IVF, sqlite-vec, RagForge behind one router |
| [BENCHMARKS.md](BENCHMARKS.md) | every number, on synthetic and on real embeddings |
| [EVALUATE.md](EVALUATE.md) | retrieval and answer metrics, and the two numbers that are not numbers |
| [SERVE.md](SERVE.md) | the JSON API and its routes |
| [MODEL.md](MODEL.md) | the tokeniser and the small model |
| [TOOLS.md](TOOLS.md) | dubforge, forge3d, cutforge behind one interface |

## Many agents

| document | what it answers |
|---|---|
| [BOARD.md](BOARD.md) | the blackboard, claims and leases, and the 64-slot registry |
| [AGENTS.md](AGENTS.md) | the design those three implement |

## Keeping it honest

| document | what it answers |
|---|---|
| [AUDIT-2.md](AUDIT-2.md) | the second audit: what was found, what was not a bug, what is untested |
| [SURVEY.md](SURVEY.md) | the AI repo survey and why most candidates were refused |
| [audit/](audit/) | the first audit |

## Conventions these documents follow

- **A number without a method is not a number.** Every benchmark states the
  corpus, the hardware caveat and how to reproduce it.
- **A limit is written down with what it does not cover.** `AUDIT-2.md` has a
  "not covered" table on purpose.
- **A bug found later gets its own entry.** A wave that fixes an earlier wave's
  mistake is a change, and the changelog says so rather than quietly growing a
  correction.
- **Refusals are documented as loudly as features.** The GPL repository that was
  not adopted, the tool that is a specification rather than an engine, the
  benchmark that is contaminated — each is written down with the reason.
