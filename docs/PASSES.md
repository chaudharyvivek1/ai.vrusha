# The passes

A pass is a deterministic operator over the store: it reads rows, decides
something, writes rows, and logs what it did. The log entry is the contract —
if a pass changed something it did not log, it is a bug.

They run in a fixed order (`passes.order` in the config). Twelve ship today.

| pass | level | reads | writes |
|---|---|---|---|
| `filter.policy` | 1 | documents.licence, documents.domain | deactivates chunks, `filtered` edges |
| `filter.quality` | 6 | chunks.chars, chunks.text | deactivates chunks |
| `filter.stale` | 3 | documents.retrieved | `stale` edges |
| `merge.exact` | 7 | chunks.sha | deactivates chunks, `merged_into` edges |
| `merge.near_duplicate` | 7 | vectors | deactivates chunks, `merged_into` edges |
| `analyse.cluster` | 5 | vectors, lang | chunks.addr_cluster |
| `analyse.centroid` | 5 | vectors, clusters | centroids |
| `sort.stability` | 5 | cluster sizes | `sort.stability` in meta |
| `assign.address` | 5 | all of the above | chunks.address and its five columns |
| `reflow.address` | 5 | addresses | compacts ordinals, address_log |
| `analyse.graph` | 8 | vectors | `near` edges |
| `link.parent` | 5 | addresses | `child_of` edges |

## The order is the argument

```
filter   ->  merge  ->  cluster  ->  address  ->  graph  ->  link
```

- **Filter before merge.** No point computing a near-duplicate against a chunk
  you are about to deactivate.
- **Merge before cluster.** A duplicate pair drags a centroid toward itself
  and shrinks the distance between two clusters that are actually distinct.
- **Cluster before address.** The cluster is the fourth segment; you cannot
  write an address without it.
- **Address before graph.** The graph is built over the surviving chunks, so it
  costs less and is not polluted by merged copies.
- **Reflow last among the address passes.** Compaction must run after every
  pass that deactivates a chunk, or it compacts a set that is about to change.

## Nothing is deleted

A filtered or merged chunk is set `active = 0` and keeps its row, its text, its
vector and its address history. The pass log plus `address_log` means you can
always answer "what was removed, by which pass, on what grounds".

Deleting would make the log a story you cannot check.

## Determinism

Every pass is deterministic given the store and the config:

- k-means is seeded (`passes.seed`) and initialised k-means++ from the
  lowest chunk id, so two runs on the same data give the same clusters;
- `sort.stability` breaks ties by cluster id, not by dictionary order;
- `merge.*` keeps the lowest chunk id as canonical;
- `assign.address` orders by `(chars DESC, chunk_id)`, so the ordinal is a
  function of the data, not of insertion time.

Run `vrusha passes` twice on an unchanged store: every count should be zero or
identical the second time.

## Running a subset

```bash
vrusha passes --list                    # ids, levels, one-line jobs
vrusha passes --only assign.address     # one pass, in its place in the order
vrusha passes --only merge.near_duplicate,reflow.address
```

`--only` filters the configured order; it does not let you reorder. Reordering
is a config change, on purpose.

## Writing a new pass

```python
@pass_("analyse.language_mix", 4, "count the language mix per cluster")
def analyse_language_mix(ctx: PassContext) -> dict:
    rows = ctx.store.conn.execute(
        "SELECT addr_cluster, lang, COUNT(*) FROM chunks WHERE active=1 "
        "GROUP BY addr_cluster, lang").fetchall()
    ...
    return {"clusters": len(rows)}      # this dict is the log entry
```

1. Decorate with `pass_(id, level, description)`. The level is the rule level
   that governs the pass, and it is what the log records.
2. Take a `PassContext` (store, cfg, rules, registry, verbose).
3. Return a dict of counts. That dict is written to `passes.stats` and is what
   `vrusha report` prints.
4. Add the id to `passes.order` in the config.

If the pass changes an address, write it through `store.set_address(...)` with
the pass id, so the change lands in `address_log`.

## Ideas that belong here

The registry is the extension point. Passes that would earn their place:

- `analyse.duplicate_clusters` — two clusters whose centroids are closer than
  a threshold are one topic with two names; merge them and re-address.
- `filter.near_miss` — chunks whose nearest neighbour is below a floor are
  orphans; they retrieve for nothing.
- `assign.subcluster` — a sixth segment for the largest clusters, when one
  cluster holds a third of the store.
- `analyse.coverage` — which regions of the tree have no material at all, which
  is the map of what the model will not know.
