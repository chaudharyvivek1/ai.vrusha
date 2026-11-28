# The program, in parts

Six parts. Each has an acceptance test that fails today, so "done" is a command
that works rather than a document that says it does.

| # | part | done means |
|---|---|---|
| 1 | ~~**Math layer**~~ — done (`vrusha/store/mathquery.py`, `vrusha math`, `docs/MATH.md`) |
| 2 | ~~**Research loop**~~ — done (`vrusha/research.py`, `vrusha research`, `docs/RESEARCH.md`) |
| 3 | ~~**dubforge** as a tool~~ — done (`vrusha/tools/`, `vrusha tools`, `docs/TOOLS.md`) |
| 4 | ~~**forge3d** as a tool~~ — done, same adapter |
| 5 | ~~**cutforge**~~ — registered, and reported as a specification: there is no engine to call |
| 6 | ~~**AI repo survey**~~ — done (`docs/SURVEY.md`; model2vec adopted as an embed backend) |

Parts 3-5 share one decision, and part 6 has its own. Both are below, because
they change what gets built.

---

## Part 1 — the math layer

A search box that cannot see `x²` is a search box for prose. Mathematical
formulae are the part of a technical page that a tokeniser destroys: `a+b` and
`b+a` are the same expression and two different strings, and `\frac{1}{2}` and
`0.5` never meet.

The published systems solve this in one of two ways. **MathWebSearch** indexes
Content MathML with substitution trees from theorem proving; **MIaS** canonicalises
MathML, reorders commutative operands and indexes every subformula; **PyA0** wraps
Approach Zero's C engine for substructure matching. All three agree on the shape:
**canonicalise, then index subformulas, then match structurally.**

What this part builds, in the project's own terms:

```
extract     <math> elements, \(...\), \[...\], $$...$$, $...$  ->  LaTeX
canonical   strip \left \right and spacing, unicode -> LaTeX, \frac{a}{b} -> (a)/(b),
            order the operands of + and *, collapse braces  -> one string per formula
index       canonical string, its symbol set, and every subformula
search      exact canonical match first, then symbol overlap, then subformula overlap
cite        every hit carries its document, url and licence
```

Deliberately not built: LaTeX→MathML conversion (a parser and a half), and
semantic equivalence (`\sin^2 + \cos^2 = 1` versus `1 = \sin^2 + \cos^2` beyond
commutative reordering). Both are where a math search engine stops being a
weekend and becomes PyA0.

**Not vendored:** PyA0 is the right *external* engine if you ever want
substructure matching at scale — it is Python, pip-installable, and has prebuilt
indexes. It joins the same pattern as RagForge: an optional backend behind the
router, not a dependency of the package.

---

## Part 2 — the research loop

Everything needed is already here, in pieces: `harvest --extract` fetches and
extracts, `ask` answers with citations, `verify --refetch` checks the sources.
What is missing is the single command that composes them and returns **sources
as a first-class output**, not a footnote:

```json
{"question": "...", "answer": "...",
 "sources": [{"url": "...", "title": "...", "licence": "MIT",
              "retrieved": "...", "chunks": 3, "equations": 2,
              "verified": "unchanged"}]}
```

Acceptance: a question about a formula returns the formula, the page it came
from, that page's licence, and whether the page still says the same thing.

---

## Parts 3-5 — dubforge, forge3d, cutforge

All three are Python, ~150 files each, and each is a *product*: an engine with
its own CLI, its own dependencies, its own tests. Copying them into
`vrusha/store/` would make `pip install vrusha` pull three products' dependency
trees and turn a 3,700-line package into a 30,000-line one.

**The decision, and my recommendation:**

| option | what it means | cost |
|---|---|---|
| **A. tools in-tree, lazy** *(recommended)* | `vrusha/tools/<name>/` — the code is in this repo, imported only when the tool is used, with a per-tool dependency extra | one repo, one clone; the package stays light; their tests come along |
| B. submodules | each repo stays separate, referenced by path | one repo to clone, three to remember; no code drift |
| C. copy everything into the package | one flat tree | `pip install` becomes heavy and fragile |

A gives what "merge in my repo" means — one repository, one history, one place
to look — without making the core package depend on ffmpeg, Blender and CadQuery.
Each tool gets the same shape: a `Tool` interface (`name`, `available()`,
`run(job)`), a `vrusha tools` listing that says why one is unavailable, and its
own tests behind a skip when its dependencies are missing.

What I need from you: **A, B or C**. I recommend A.

---

## Part 6 — the AI repo survey

The honest constraint: **merging a third-party repository wholesale is usually a
mistake** — it brings a licence (MathWebSearch is GPL-3, which would relicense
this project), a dependency tree, and code nobody here maintains. So the survey
produces a shortlist judged on four things:

1. **licence** — MIT/Apache/BSD only; GPL code cannot be merged into an MIT repo
2. **what it actually adds** — an algorithm, a format, a model; not another framework
3. **dependency weight** — does it pull 200 MB of wheels into a stdlib+numpy project
4. **testability offline** — can its behaviour be pinned by a fixture

Adoption is then one of three grades, in order of preference: **adopt the idea**
(reimplement in ~200 lines, as with the IVF index), **adopt a module** (vendor one
file, with its licence header and a note), **adopt as an optional backend**
(PyA0, RagForge).

What I will not do: `git subtree add` a framework because it looks useful. That
is how a repo becomes unbuildable in a year.

---

## The plan after this one

`docs/MASTER-PLAN.md` supersedes both documents below as the forward plan: it adds
the state of the field (which benchmarks are saturated or contaminated), a
70-skill inventory with what exists and what does not, and a six-phase plan that
puts documents and compaction *before* orchestration.

## Next programme: the agent fabric

The six parts above are done. The next plan is `docs/AGENTS.md` — four levels of
agent (super, admin, parent, sub), a shared append-only blackboard, 64 concurrent
workers, and one harvested skill per agent. It starts with a design and ten
steps, each with a test that fails today, because the first step (the blackboard)
is useful even if the hierarchy is later replaced by a scheduler.

## Order of work

1. Math layer (this part, now)
2. Research loop
3. dubforge → 4. forge3d → 5. cutforge
6. Survey and shortlist

Each part lands as its own PR with tests, merged to `main` when CI is green.
