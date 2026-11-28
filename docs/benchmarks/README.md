# Benchmark reports

Generated from `python -m bench.registry` on **2026-11-28**.

Every report names what was measured, how, and what the number means. A number
without a method is not a number — the rule this project has held since wave 20.

| # | report | benchmark | headline |
|---|---|---|---|
| 01 | [The blackboard](01-blackboard.md) | `blackboard` | append 1.29 ms, delta read 1.66 ms |
| 02 | [Claims and leases](02-claims.md) | `claims` | claim 0.45 ms |
| 03 | [The 64-slot registry](03-registry.md) | `registry` | register 1.89 ms at 64 slots |
| 04 | [The address ranking](04-address.md) | `address` | 0.54 ms over 2,000 chunks |
| 05 | [The reranker](05-rerank.md) | `rerank` | lexical 1.89 ms for 20 candidates |
| 06 | [Retrieval, end to end](06-retrieval.md) | `retrieval` | recall@5 48%, 9.5 ms/query |
| 07 | [Ingestion](07-ingest.md) | `ingest` | 163 ms/doc, 6.1 docs/s |
| 08 | [Extraction](08-extract.md) | `extract` | 41.8 ms/file |
| 09 | [The JSON API](09-serve.md) | `serve` | 8.67 ms/request |
| 10 | [Delta indexing](10-index_delta.md) | `index_delta` | append 23 ms vs rebuild 495 ms (22×) |

## The caveats that travel with these numbers

- **This pod is 2 shared vCPUs.** Every latency here is a range, not a constant;
  the same code on a quiet machine is faster.
- **`retrieval` records which embedder ran.** `model2vec` gives recall@5 0.70;
  the `hash` fallback gives 0.47. Same code, same corpus, different number —
  which is why the report prints the flag.
- **Quality numbers come from the 40-question set** (`vrusha/eval_sets/docs-40.json`).
  On a 12-question subset the same reranker looked better on every metric and
  worse on one; a small set gives a confidently wrong number, not just a noisy one.
