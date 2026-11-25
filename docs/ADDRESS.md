# The address

An address is where a unit of data lives. It is five numbers, coarse to fine.
Distance between two addresses is distance between two meanings.

```
    d1 . d2 . d3 . d4 . d5
    |    |    |    |    +-- ordinal   1..N    position inside the cluster
    |    |    |    +-------- cluster   1..N    what it is about (k-means bucket)
    |    |    +------------- kind      1..4    the structural unit
    |    +------------------ family    1..N    the language
    +----------------------- domain    1..5    prose / code / data / rules / talk
```

`2.3.1.4.17` is: code, TypeScript, module, cluster 4, item 17.

## Domains

| id | name | what lands here |
|---|---|---|
| 1 | prose | natural-language knowledge: articles, notes, docs |
| 2 | code | source code, chunked on function and class boundaries |
| 3 | data | structured records: tables, JSON, CSV |
| 4 | rules | the rule set itself, addressable like anything else |
| 5 | talk | conversation, questions, answers |

Domains are fixed in code (`vrusha/store/address.py`). A new one is a schema
change, not a config change.

## Kinds

Prose: `document`, `section`, `paragraph`, `claim`.
Code: `module`, `class`, `function`, `block`.

The kind is decided by the chunker from the language profile, not by the
model.

## Distance

```
d(a, b) = Σ  w_i · |a_i − b_i|        w = (8, 4, 2, 1, 0.5)
```

The weights halve at each step. A difference in the domain costs sixteen times
a difference in the ordinal:

| comparison | distance | similarity `1/(1+d)` |
|---|---|---|
| same address | 0.00 | 1.00 |
| neighbours in one cluster | 0.50 | 0.67 |
| same cluster, two apart | 1.00 | 0.50 |
| same family, different kind | 2.00 | 0.33 |
| same domain, different family | 4.00 | 0.20 |
| different domain | 8.00 | 0.11 |

Similarity is `1/(1+d)`, not `1 − d/d_max`. The maximum is unbounded — a family
id can be any integer — so dividing by it would call two unrelated domains
99% similar.

## Prefixes are folders

A prefix of an address is a real region, and everything starting with it lives
under it.

```
2            all code
2.1          all Python
2.1.3        all Python functions
2.1.3.4      Python functions in cluster 4
2.1.3.4.17   one function
```

`vrusha address 2.1.3` lists the region. `vrusha query "..." --address 2.1`
biases retrieval toward Python. `vrusha tokenize --address 2` trains the
tokenizer on code only.

Addresses print at their real depth, so a region reads as a region:

```
>>> str(Address.parse("2.1.3.4.17").prefix(3))
'2.1.3'
>>> Address.parse("2.1.3").segments
(2, 1, 3, 0, 0)
```

## The float form

For a spreadsheet, an address renders as a decimal: the domain is the integer
part, each later segment is two digits.

| address | float |
|---|---|
| `2.3.1.4.17` | `2.03010417` |
| `0.5` (a depth-1 region) | `0.05000000` |
| `0.5.1` | `0.05010000` |

Sorting the float column sorts the tree in order: a parent comes before all of
its children. `parse` accepts all three forms — `"0.5"`, `"0.05"` and
`"0.05000000"` are the same address.

### Past 99

Two digits cannot express 100, so when any segment exceeds 99 the whole tail
widens to three digits per segment:

| address | float |
|---|---|
| `2.3.1.4.100` | `2.003001004100` |
| `2.3.1.4.999` | `2.003001004999` |

`parse` tells the forms apart by tail length alone — 8 digits is four 2-digit
groups, 12 digits is four 3-digit groups, anything shorter is literal
dot-separated segments. Every segment is capped at `MAX_SEGMENT` (999), so a
literal segment is never more than three digits and the packed form can never
collide with a dotted one. A tail of 9, 10, 11 or 13+ digits is a malformed
packed address and is refused rather than reinterpreted.

This matters in practice: `assign.address` writes one ordinal per chunk inside
a cluster, and a cluster with more than 99 chunks is ordinary. Before this
rule the parser refused every such address and the pass pipeline raised.

## Distance, and distance-for-retrieval

`Address.distance` is arithmetic: it subtracts segment values with halving
weights. That is the right thing for **sorting, browsing and rendering**, and
it is what makes a prefix a folder.

It is the wrong thing for **retrieval**, because the segments are not all
numbers of the same kind. Cluster ids are assigned by cluster *size*
(`sort.stability`), so cluster 1 and cluster 2 are the two biggest topics, not
two neighbouring ones. `|1 - 2|` carries no meaning.

`semantic_similarity(a, b, centroids)` is what retrieval uses:

| relationship | score |
|---|---|
| same address | 1.00 |
| different domain | 0.00 |
| same domain, different family | 0.25 |
| same family, different kind | 0.50 |
| same kind, different cluster | cosine of the two cluster centroids |
| same cluster, different ordinal | 1.0 scaled down by the ordinal gap |

Domain, family and kind are **labels**: family 2 is exactly as far from family 3
as it is from family 20. The cluster term is measured from the stored centroids,
so two clusters that are actually about the same thing score high however they
happen to be numbered. With no centroids available the score is a neutral 0.6,
never a number invented from the ids.

The address signal also enters fusion as a **tiebreaker**, not as evidence:
hits are ordered by `(has lexical or semantic support, primary score, total)`,
so a region hint can reorder hits with equal evidence and can never displace a
hit the text actually matched.

## How an address is assigned

Only the `assign.address` pass writes an address, and it writes one from four
parts it did not invent:

1. **domain** from the document kind (`code` → 2, prose → 1)
2. **family** from the language profile's `family_id`
3. **kind** from the chunker's `kind`
4. **cluster** from `analyse.cluster`, a deterministic k-means over the
   embeddings of each `(domain, family, kind)` group

The ordinal is then the position inside the cluster, ordered by size then id,
so the same store always produces the same addresses.

`sort.stability` renumbers clusters by size (biggest is 1) so the number is
stable as the store grows. `reflow.address` compacts ordinals after merges so
no number is skipped. Every change is written to `address_log` with the pass
that made it.

## Why this and not a flat id

A flat id is a name. It tells you nothing. Two flat ids can be adjacent and
unrelated.

An address carries three things a flat id cannot:

- **proximity** — a cheap third retrieval signal, and a tiebreaker when BM25
  and vectors disagree;
- **browsing** — `vrusha address 2.1.3` is a file browser over the corpus;
- **selection** — "train the tokenizer on code only" is a prefix, not a query.

The cost is that addresses move when the store changes. That is why the log
exists: an address is a position, not an identity. `chunk_id` is the identity.

## Limits, stated plainly

- **Clusters are k-means, not a topic model.** They are deterministic and
  cheap, and they will put two things together that a human would separate.
  The number is a coarse bucket, not a taxonomy.
- **The family segment is the language, not the topic.** Two unrelated Python
  functions share a family. Topic lives in the cluster segment.
- **Distance is a heuristic.** It is useful for ranking and for browsing. It
  is not a metric with a proof behind it, and `d(a,b) = d(b,a)` is the only
  axiom it satisfies.
- **A reflow changes addresses.** Anything you cached by address is stale
  after a merge pass. Cache by `chunk_id`.
