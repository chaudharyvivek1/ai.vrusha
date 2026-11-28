# Option 3b — the second shape: a dependency graph

**Wave 71 · `vrusha/orchestrator.py`, `tests/test_orchestrator.py`**

## What was built

`levels()` and `run_graph()`. A task names what it waits for, and the waves fall out
of the graph:

```
linear    [('a',[]), ('b',['a']), ('c',['b'])]     -> [['a'], ['b'], ['c']]
diamond   [('a',[]), ('b',['a']), ('c',['a']),
           ('d',['b','c'])]                        -> [['a'], ['b','c'], ['d']]
parallel  [('a',[]), ('b',[]), ('c',[])]           -> [['a','b','c']]
```

`run_stages` was one shape of dependency — linear. This is the general one, and it
is the "second shape" the last wave said should come before any Goal Execution
Engine.

## Refusals, because a bad plan should not run partially

| input | result |
|---|---|
| a cycle (`a -> b -> a`) | `CycleError`, naming the cycle |
| an unknown dependency | `ValueError`, naming the task and the dependency |
| a duplicate task name | `ValueError`, naming the duplicates |

## The policy the staged version left undefined

A task whose dependency **failed** does not run:

```
a fails -> b is marked "blocked by a" -> c is marked "blocked by b"
```

and neither `b` nor `c` executes. A test asserts that by recording every task the
worker saw — running a task on missing input and failing it afterwards is not a
barrier, it is a lie about the barrier.

## Errors found and fixed

**1. `run.failed = ...` raised `AttributeError: property has no setter`.** `Run.done`
and `Run.failed` are computed from task states, so blocking a task is enough. The
class was doing its job; my code was not.

**2. A blocked task still ran** — the first implementation marked dependents failed
*after* `run_stages` returned, so they had already executed and succeeded. The check
had to move **inside** the worker, before the claim. That is the difference between
recording a policy and enforcing one.

**3. An edit matched two functions again.** Adding the `skip_if` block hit both
`run_pool` and `run_stages` (their `work()` bodies are identical), so `run_pool`
referenced a parameter it does not have — a `NameError` in five tests. **This is the
second time in two waves**; the lesson is that these two functions should share one
implementation rather than be edited in parallel.

## What this does not do

| limitation | why |
|---|---|
| retry a failed task and continue | a failure blocks its dependents, full stop |
| conditional edges | the graph is static |
| cross-stage evidence aggregation | each stage writes findings; nothing merges them |
| a scheduler beyond wave-by-wave | enough for a DAG, not for priorities |

## The honest state

`run_graph` is **tested but not benchmarked** on a real multi-step goal: the
`multistep` benchmark still uses two linear stages. Running the company comparison
as a diamond — three researchers, then a compare that waits for all three — would be
the first real use, and it is the next step rather than a claim made here.

## Checklist

- [x] waves computed from a dependency graph (linear, diamond, parallel: tested)
- [x] a cycle is refused and named (tested)
- [x] an unknown dependency is refused (tested)
- [x] a duplicate name is refused (tested)
- [x] **a blocked task does not execute** (tested by recording what the worker saw)
- [x] the graph and the staged version agree on a linear plan (tested)
- [ ] the graph used on the real multi-step goal
