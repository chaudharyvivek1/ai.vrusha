# Second audit: the whole package, before the next plan

Run before designing the next phase, on `main` at wave 30. Method: static
analysis, then every command run by hand on a fresh store, then the edge paths
that a happy-path test never touches.

The rule from earlier waves held again: **the tests were green and the bugs were
still there.** One of them had been shipping for twenty waves.

## What was checked

| check | how | result |
|---|---|---|
| undefined names, syntax, shadowed definitions | `ruff --select E9,F821,F811,F822,F823` | **1 real bug** (below) |
| mutable defaults, bare `except`, `== None` | `ruff --select B006,B008,E711,E712,E722` | clean |
| unused imports | `ruff --select F401` | 49, all removed |
| every command parses | `--help` on all 27 subcommands | clean |
| every command runs | full battery on a fresh store | clean except `doctor` (by design) |
| edge paths | empty store, missing table, no vectors, no search backend | all refuse with a sentence, none with a traceback |
| two engines agree | `sqlite-vec` vs the exact scan | identical to 1e-6 |
| filtered query routing | a filter must never reach an engine that ignores it | correct — falls back to `exact` |

## Found and fixed

### 1. `decompress_capped` was defined twice — 69 lines of dead code

`vrusha/harvest/net.py` had two definitions of the same function, 69 lines apart.
Python keeps the last, so the first had been dead since the refactor that
extracted `gunzip_members` and `_inflate_into`. Both handled the same four
encodings, so nothing was lost — but the dead one *looked* authoritative: a
reader would believe gzip is handled inline, when the live code delegates to a
multi-member decompressor with a size cap.

A duplicate definition is not a style problem. It is two functions with one name,
and the one you read is not the one that runs.

**Fixed:** the dead definition removed; `ruff --select F811` now clean.

### 2. `sqlite-vec` silently ignored a `filter`

`Vec0Backend.search()` accepted a `filter` argument and did nothing with it. The
router never sends one — `filter` is a declared *capability* and this backend does
not declare it — so no user could reach the bug through `vrusha query`. A direct
caller could, and would get unfiltered rows that look exactly like filtered ones.

**Fixed:** it raises, naming the backends that can filter. A wrong answer that
looks right is worse than an error.

### 3. `GET /tables/<id>` answered 404 for a table that exists but is empty

"Not found" and "found, no rows" are different facts, and the API conflated them.

**Fixed:** existence is checked against `x_tables`; an empty table returns 200
with `rows: []`.

### 4. 49 unused imports across 28 files

Removed. All 483 tests pass afterwards, which is the evidence that none of them
was a side-effect import — the ones that are (`vrusha/tools/__init__.py` loading
the three tool modules) carry `# noqa: F401` and were left alone.

## Investigated, and *not* a bug

### `surfaces` counts columns, not rows

`entities._surfaces()` counts how many key columns contain a spelling. It looked
wrong — the useful signal for choosing a canonical name is how *often* it occurs.
I changed it to count rows, and the change was reverted after one test:

**a column with repeated values is not a key candidate at all** (`key_columns`
requires uniqueness), so for key columns "rows" and "columns" are always the same
number. The expensive version bought nothing.

The defect was the **docstring**, which said "how many times it appears". It now
says what the number is. The test that replaced the reverted one asserts the real
behaviour: a column with repeats is not a key, so its values never reach the count.

### `doctor` exits 1

Intentional: it exits non-zero when a check fails (here, `torch` is not
installed), because a health check that always succeeds is not a health check.

### `vrusha tables --id 99` exits 0

It prints `no rows in table 99`. Arguably it should exit 1 for a table that does
not exist — noted, not changed, because the exit code is already documented as
"0 when the command ran" and changing it would break scripts that check it.

## Not covered by this audit

| not checked | why it matters |
|---|---|
| **concurrency** | no test hammers the store from two processes; the WAL path is exercised only by accident |
| **adversarial input** | fixtures are well-formed; there is no fuzzing of HTML, PDF or CSV |
| **the torch path** | `train` and `generate` are untested here because torch is not installed in this sandbox |
| **RagForge** | exercised against a stub, never against the real server |
| **long runs** | nothing checks that a 200k-chunk store stays correct after 1,000 merges |
| **prompt injection** | there is no agent layer yet; `docs/AGENTS.md` step 10 is where this becomes testable |

These are the honest gaps. A green suite says the behaviour that is tested is
correct, not that the untested behaviour is.
