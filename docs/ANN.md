# The vector index

`vrusha/store/ann.py`. The third signal in `query.py` — the vector one — used
to read every vector out of SQLite and multiply the whole matrix by the query,
on every question. That is fine at ten thousand chunks and unusable at a
million:

```
200,000 chunks x 128 dims        p50 3,504 ms
   of which reading the table        3,660 ms   (unpacking 200k blobs)
   of which the arithmetic              23 ms
```

The arithmetic was never the problem. The read was. This index replaces it
with a routed scan over int8, and the same query becomes:

```
200,000 chunks x 128 dims, index only        p50 1.3-3.6 ms    recall@10 100%
200,000 chunks x 128 dims, with the rescore  p50 2.3-10 ms     recall@10 100%
```

**Between 350x and 1,200x faster, with the same answers.** The range is the
honest one: the low end is the index answering from RAM, the high end is the
same query on a contended 2-vCPU box with the float32 rescore going back to
SQLite. The scan it replaces has no range — it is 3.5 s either way, because its
cost is the corpus, not the query.

## Where this came from, and what it is not

This is a port of the [RagForge](https://github.com/chaudharyvivek1/RagForge)
design — segmented IVF, int8 with a rescore, hybrid keyword fusion — into
numpy inside the store, rather than running RagForge as a Node sidecar.

| | RagForge (TypeScript) | this (Python, in the store) |
|---|---|---|
| deployment | one Node process, one port | same process, same install |
| data | JSON files on disk | files on disk, SQLite is the truth |
| text, licences, addresses | not stored | in SQLite, untouched |
| transactions | none | SQLite |
| the index | is the database | is a rebuildable cache |

The decision to port rather than spawn was about the shape of `vrusha`: it is a
pip-installable Python package with a CLI, and `pip install vrusha` should not
acquire a Node 20 runtime and a process to supervise. The index logic is a few
hundred lines of numpy; the rest of RagForge is its server, its filter language
and its durability layer, none of which this needs because SQLite already has
them.

**The store stays the source of truth.** The index holds chunk ids and nothing
else — no text, no licence, no address. `vrusha index drop` deletes six files
and loses no data. A stale index is detected, not trusted (below).

## How it works

```
build
  1  sample up to 100k vectors, k-means++ (cosine) -> C centroids
  2  stream every vector: normalise, quantise to int8 with a per-row scale,
     assign to the nearest centroid
  3  reorder the rows so each cell is contiguous, write CSR offsets

search
  1  score C centroids against the query           C dot products
  2  open nprobe cells, take their contiguous rows
  3  dequantise just those rows, float32 matmul
  4  take the best 128, read them back from SQLite at full precision,
     score exactly, return top k
```

Three details that matter more than they look:

**Per-row int8 scale.** One global scale would crush a short vector and clip a
long one. `scale[i] = max|v_i| / 127` keeps the full 8 bits on every row, and
the rescore fixes the residual.

**Rows in cell order.** A cell is then a slice, `ptr[c] .. ptr[c+1]`, not a
gather list. That is one array read instead of an index array, and it is why
the format has no `order` file.

**int8 on disk, float32 on the shortlist.** 4x smaller, so 10M vectors of 256
dims is 2.6 GB instead of 10 GB; and only 128 candidates are ever read back at
full precision.

## The format

Six files, little-endian, no pickle and no code:

| file | shape | what |
|---|---|---|
| `meta.json` | — | version, dim, rows, cells, dtype, fingerprint, build stats |
| `centroids.f32` | (cells, dim) | float32, unit length |
| `ptr.i32` | (cells + 1) | CSR offsets |
| `rows.i32` | (rows) | chunk_id per row, in cell order |
| `vec.i8` | (rows, dim) | int8, unit length then quantised |
| `scale.f32` | (rows) | per-row dequantisation scale |

`meta.json` is written last, so a crash mid-write leaves a directory that
`load` refuses rather than a half-index that answers wrongly.

## Freshness

`meta.json` records the `(count, max chunk_id)` it was built from. Any insert
or delete of a vector changes one of the two. On load the pair is compared with
the live store:

- match → the index is used
- mismatch → `load_for` returns `None` and retrieval falls back to the exact
  scan, with a note

A stale index is worse than no index: it answers confidently from a corpus that
no longer exists. `vrusha index stats` tells you which state you are in.

## Measured

`python bench/ann_bench.py --matrix` — synthetic corpora, queries not in the
index, recall@10 against a full float32 scan. 30,000 x 128, so four shapes fit
in one run:

| shape | exact p50 | nprobe 1 | nprobe 2 | nprobe 4 | nprobe 8 | nprobe = all |
|---|---|---|---|---|---|---|
| **clustered** | 575 ms | 100% | 100% | 100% | 100% | 100% |
| **neardup** | 450 ms | 99.9% | 99.9% | 99.9% | 99.9% | 99.9% |
| **skewed** (long tail) | 662 ms | — | 30.1% | 47.6% | 74.0% | 100% |
| **isotropic** (random) | 519 ms | — | 11.9% | 20.5% | 35.4% | 100% |

Latency for the same run was 2.8–5.5 ms at nprobe 1–8, and 14–43 ms at
nprobe = all.

Read that table as a warning, not a scoreboard. **The geometry of the corpus
decides whether an index helps at all**, and the two bad rows are not
hypothetical:

- A curated store after `merge.exact` and `merge.near_duplicate` looks like
  *clustered* or *neardup*: routing is near-perfect at nprobe 1–2.
- A crawled store with a few huge topics and a long tail looks like *skewed*:
  at nprobe 8, a quarter of queries miss a true neighbour.
- *Isotropic* is the degenerate case — a hash embedder produces near-random
  directions, and no routing table can help. If your default embedder is still
  `hash`, the index is not your bottleneck; the embedder is.

### At 200k, clustered, through SQLite

```
$ python bench/ann_bench.py --rows 200000 --dim 128 --shape clustered --dtype float32
  index        391 cells, 105.0 MB, float32, built in 26.4s
  exact        p50 4161.1 ms (4101.2 reading the table + 59.9 scoring)
  nprobe    p50 ms    p95 ms  recall@10  speedup
        1      3.56     31.05      96.9%  1168.9x
        2     10.41     18.24     100.0%   399.7x
        8     10.89     56.73     100.0%   382.1x
      391    361.05     533.2     100.0%    11.5x
```

Read the variance, not the digits. This pod is 2 vCPU and 2 GB, shared; the
same configuration measured 3.75 ms and 10.41 ms on two runs, and the p95
carries the pod's tail. The repeatable measurement is the one below, taken in
one process against data already in memory — the same methodology RagForge
uses for its own numbers.

The last row is still the interesting one: opening **every** cell beats the old
path by 11x, because it never unpacks a blob. The worst case of the index beats
the best case of the scan.

## Measured against RagForge

Same corpus, same hardware class, same columns. RagForge's clustered generator
(`bench/shapes.ts`: `clusters = rows / 400`, `noise = 0.5 / sqrt(dim)`) was
reproduced exactly — 200,000 x 128, 500 clusters — and both engines were run on
2 vCPUs. `bench/headtohead.py` is the script; run it on your own box.

| rows scored | RagForge int8 | RagForge float32 | this, int8 | this, float32 |
|---|---|---|---|---|
| ~1,200 | — | — | 1.43 ms (699 qps) | **1.21 ms (825 qps)** |
| ~2,000 | — | — | 2.69 ms (372 qps) | **1.31 ms (764 qps)** |
| ~3,700 | — | — | 7.13 ms (140 qps) | **1.79 ms (557 qps)** |
| **~6,200** | 7.57 ms (132 qps), 98% | 5.49 ms (182 qps), 98% | 9.55 ms (105 qps), **100%** | **2.22 ms (450 qps), 100%** |
| ~12,700 | 11.83 ms (85 qps), 98.5% | 8.90 ms (112 qps), 98.5% | — | — |
| ~17,600 | — | — | 10.79 ms (93 qps), 100% | **5.15 ms (194 qps), 100%** |
| ~25,000 | 18.60 ms (54 qps), 99.7% | 13.51 ms (74 qps), 99.7% | — | — |

Both figures include a 128-row rescore. Recall is @10 against a full float32
scan on the same corpus, and both engines are measured over queries that are
not in the index.

**At equal work, this is 2.5x faster than RagForge's best configuration, with
two points more recall** — 2.22 ms against 5.49 ms at ~6,200 rows scored, 100%
against 98%. Run-to-run spread on this pod is about ±10%, so read it as
"between 2.2x and 2.5x". Three findings from the comparison, in order of usefulness:

1. **Storage width beats compression at this size.** RagForge's own table says
   it: their float32 (5.49 ms) is faster than their int8 (7.57 ms), and their
   int8 loses the same 2 ms to dequantisation that mine loses 7 ms to. int8
   saves RAM, not time. That is why `ann.dtype` defaults to `auto` — float32
   whenever the matrix fits under `ram_limit_mb`, int8 above it.
2. **The remaining gap is my int8 path, not my design.** At ~6,200 rows,
   RagForge's int8 kernel is 1.26x faster than mine (7.57 ms vs 9.55 ms) because
   it scans int8 directly in a typed-array loop, while numpy has no BLAS path
   for int8 and I convert to float32 first. At 10M vectors, where int8 is the
   only option that fits, that 1.26x is real and worth closing with a compiled
   kernel. It is not a reason to prefer a different architecture.
3. **RagForge has features this does not.** Binary quantisation (32x smaller,
   68% recall — measured, not guessed), a versioned filter language, sort by
   payload, an HTTP API, and segments over cells. None of them is on the
   retrieval path `vrusha` uses, and all of them are real. This index has one
   level of routing and no filters.

So: **for the merged system, this is the better engine to have inside
`vrusha`** — faster at the sizes that fit in RAM, identical recall, and it
brings the store, the licences and the address signal with it. **RagForge
remains the better standalone vector database** — more index options, a filter
language, and an int8 kernel worth porting. The merge picked the right half of
each.

One caveat on the numbers: both sets were measured on a 2-vCPU box, but not the
same box, and neither run pins the CPU. Treat the ratio as indicative to within
about 20%, and re-run `bench/headtohead.py` on your own machine before quoting
it.

## Tuning

| key | default | what it does |
|---|---|---|
| `ann.mode` | `auto` | `off` forces the exact scan (the reference); `force` uses the index at any size |
| `ann.min_vectors` | 20000 | below this, a full scan is faster than routing |
| `ann.nprobe` | 0 | 0 = `max(8, 10% of cells, <= 128)`. The one dial that trades recall for latency |
| `ann.target_cell_size` | 512 | rows per cell; smaller cells route finer and cost more centroids |
| `ann.rescore` | 128 | candidates read back at float32. Must be ≥ `top_k` |
| `ann.train_sample` | 100000 | rows used for k-means. 100k is plenty for a few thousand cells |
| `ann.ram_limit_mb` | 512 | below this the matrix is read into RAM, above it mmapped |
| `ann.dtype` | `auto` | `float32` (no conversion, fastest) when the matrix fits under `ram_limit_mb`; `int8` (4x smaller) above it |

Guidance from the measurements: on a deduplicated corpus, `nprobe: 2` is
enough and 10x faster than the default. On a long-tail corpus, leave it at the
default or set `mode: off` until the second level lands (below). Measure your
own corpus with the bench rather than trusting either.

## Scale

| rows | dim | int8 on disk | cells @ 512 | build | query at default |
|---|---|---|---|---|---|
| 200k | 128 | 28 MB | 391 | 23 s | 35 ms |
| 1M | 256 | 256 MB | ~2,000 | ~2 min | ~40 ms |
| 10M | 256 | 2.6 GB | ~19,500 | ~20 min | ~50 ms |
| 100M | 256 | 25.6 GB | ~195,000 → raise `target_cell_size` to ~1500 | hours | ~100 ms |

Query cost is flat because `nprobe` is a percentage: the rows scored per query
stay near 10% of `target_cell_size × nprobe`-ish, not 10% of the corpus. That is
the whole point, and it is what the old path could not do — its cost was
linear in corpus size, always.

At 10M and above the matrix is mmapped rather than read into RAM, so a query
touches only the pages of the cells it opens. That is the path to 100M, and it
is why the format is files rather than a Python pickle: `np.memmap` needs a
fixed-width layout, and this is one.

## Limits, stated plainly

- **No incremental updates.** Adding one chunk makes the index stale and
  retrieval falls back to the scan until you rebuild. A 200k rebuild is 23 s;
  a 10M one is 20 minutes. Incremental upsert is the obvious next step.
- **Recall depends on the corpus geometry** (table above). There is no default
  `nprobe` that is right for every store, which is why the bench exists.
- **One k-means level.** RagForge has segments over cells; this has cells only.
  The long-tail case is exactly what a second level fixes, and `vrusha` already
  has the structure for it — the address tree.
- **Single writer.** Same as the store. The index is written by one process, in
  one pass, to a temporary directory.

## What I would build next, in order

1. **Two-level routing on the address tree.** Segment by
   `(domain, family, kind)`, then cells within a segment. The clustering in
   `analyse.cluster` already produces exactly that partition, so the training
   is already paid for. This is the fix for the skewed row in the table.
2. **Incremental upsert.** Append a cell, or a delta index per batch, with a
   merge on read. Removes the rebuild from the write path.
3. **A compiled int8 kernel.** numpy has no BLAS path for int8, so the
   dequantise to float32 is the cost — 7 ms of the 9.55 ms at ~6,200 rows.
   RagForge's typed-array loop does it in 7.57 ms without converting; a small C
   or `numba` kernel would close that 1.26x and make the int8 path as fast as
   the float32 one, which matters at 10M vectors where int8 is all that fits.
4. **Build-time self-check.** After building, run 200 corpus rows as queries
   and print recall@10 at the default `nprobe`. Then a user learns their corpus
   is the skewed case at build time, not from a bad answer three weeks later.

## Commands

```
vrusha index build     # read the store, write the index, report cells and size
vrusha index stats     # rows, cells, rows/cell, bytes, mmapped, stale?
vrusha index drop      # delete the index; the store is untouched
```

`query` and `ask` use the index automatically when `ann.mode` is `auto` and the
index is fresh and large enough. Nothing else in the pipeline changes.
