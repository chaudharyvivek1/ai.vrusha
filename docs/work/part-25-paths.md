# Part 25 — multi-hop paths

**Wave 50 · `vrusha/store/paths.py`, `bench/registry.py`**

## What was built

Breadth-first path search over the entity graph, capped at `max_hops`, returning
the shortest path **with every hop's evidence**:

```
acme corporation -[acquired]-> beta limited -[supplies]-> gamma industries
  (2 hop(s), confidence 0.90)
```

Plus `expand(names, hops=1)` — everything reachable from a set of names, nearest
first — which is the call a retriever will make in Part 26.

## The two properties that matter

**Every hop carries its sentence.** A path is only as good as its weakest edge, so
a caller can print the chain and a reader can check every link. The path's
confidence is the **minimum** across its hops, not the average — an average would
let one strong edge hide a weak one.

**The hop cap is a hard limit.** Two hops over business relations is a plausible
claim; five hops is *"everything is connected to everything in six steps"* — true,
and useless. A test asserts that a 3-hop path is **not found** at `max_hops=2`.

## The benchmark

```
edges=199  ms=2.766  hops=2
```

A 20-cluster chained graph, 199 edges: a two-hop search costs **2.77 ms**. That is
the price of a graph query on top of a 9 ms retrieval, which is why Part 26's boost
will be bounded rather than free.

## Errors found and fixed

**My own example was wrong.** I expected `Meera Rao → Acme Corporation` to have a
path and it does not — they are in two disjoint components. The code was right and
the demonstration was wrong, which is worth recording: a graph that returns "no
path" is usually telling the truth about the data.

## What it will not do

| limitation | why |
|---|---|
| more than one path | returns the shortest only; a second-best is a different question |
| weighted shortest path | hops are counted, not summed by confidence |
| paths through an entity it cannot see | prose extraction misses names, and a missed name breaks a chain |

## Found while rechecking Parts 1–24

Nothing. `ruff` clean, 673 tests green, coverage accepted the new module.

## What is next

**Part 26: bounded graph ranking.** Communities and paths exist; the last graph part
is letting them influence retrieval **without dominating it** — a chunk in the
answer's community gets a bounded boost, measured on the same 40 questions, and the
bound is the point.

## Checklist for this part

- [x] one hop, two hops, and the shortest path (tested)
- [x] the hop cap is enforced (tested: 3 hops not found at `max_hops=2`)
- [x] every hop carries its sentence (tested)
- [x] path confidence is the weakest hop (tested)
- [x] disconnected entities have no path (tested)
- [x] a name reaches itself in zero hops
- [x] `expand()` returns neighbours nearest first
- [x] benchmarked on a graph whose shape is known
- [ ] graph-aware ranking (Part 26)
