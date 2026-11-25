# The ten levels

Rules conflict. One says store everything useful; another says store nothing
whose licence is unknown. A flat list cannot resolve that, because the rule
that runs last wins and nobody can say why.

A level resolves it. Every rule has a number from 1 to 10. **Lower wins.**
There is no override file and no exception list: to change level-1 behaviour
you edit level 1, in the open, in a diff.

| level | name | holds | may be overridden by |
|---|---|---|---|
| 1 | inviolable | licence, robots.txt, secrets, personal data, safety | nothing |
| 2 | legal | attribution, copyright, terms of use | level 1 |
| 3 | integrity | provenance, no fabrication, UTC timestamps | levels 1–2 |
| 4 | language | encoding, normalisation, detection confidence | levels 1–3 |
| 5 | structure | address scheme, chunk boundaries, schema version | levels 1–4 |
| 6 | quality | minimum length, text ratio, link density | levels 1–5 |
| 7 | dedup | similarity thresholds, merge policy | levels 1–6 |
| 8 | retrieval | top-k, fusion weights, refusal threshold | levels 1–7 |
| 9 | training | split, token budget, sequence length | levels 1–8 |
| 10 | preference | defaults and style | levels 1–9 |

## Where the rules live

```
vrusha/rules/levels/
  level-01/01-licence.json      3 rules
  level-01/02-robots.json       2
  level-01/03-secrets.json      2
  level-01/04-policy.json       4
  level-02/01-attribution.json  3
  ...
  level-10/01-defaults.json     5
```

One folder per level, one JSON file per concern, 55 rules in total. The folder
number and the file's `level` field must agree, and the loader refuses to start
if they do not.

## A rule

```json
{
  "id": "licence.allowed",
  "key": "licence.allowed",
  "level": 1,
  "action": "allow",
  "value": ["MIT", "Apache-2.0", "BSD-3-Clause"],
  "scope": ["harvest", "code"],
  "rationale": "Only these licences permit storing the material and training on it."
}
```

- `id` is unique across the whole set; the loader refuses duplicates.
- `key` is the question being answered. Several rules may share a key at
  different levels; that is the point.
- `action` is one of `allow`, `block`, `warn`, `set`, `prefer`.
- `scope` limits where a rule applies; empty means everywhere.
- `rationale` is required. A rule that cannot explain itself is not a rule,
  it is a mystery.

## Asking why

```
$ vrusha rules --explain licence.allowed
rules --explain licence.allowed
  winner  level 1  licence.allowed  [allow]
  value   ["MIT", "Apache-2.0", "BSD-3-Clause", ...]
  why     Only these licences permit storing the material and training on it.
  from    levels/level-01/01-licence.json
```

When more than one level speaks to the same key, the winner is printed first
and the rules it beat follow under `also considered`, each with its level:

```
  also considered
    level 6   quality.min_words   [set]   (beaten on level)
```

`vrusha rules --check` lists every key that has more than one level, so a
conflict is something you chose rather than something you inherited.

A decision without a trace is indistinguishable from a bug. `decide()` returns
the winner **and** the rules it beat, and the CLI prints both.

## Where the code asks

Every gate and every pass asks the rule set instead of hard-coding a number:

```python
rules.get("licence.allowed")                  # ["MIT", "Apache-2.0", ...]
rules.get("quality.min_words")                # 120
rules.get("retrieval.top_k")                  # 8
rules.get("chunk.max_lines")                  # 60
rules.decide("policy.refused_domains").action # "block"
```

`vrusha doctor` loads the rules and runs `check()`: every level populated, every
rule has a rationale, no level-1 rule is a soft preference. A broken rule set
stops the CLI before any work starts.

## Adding a rule

1. Pick the level. If you are unsure, it is probably 6 or 10.
2. Add an object to the right file, or a new file in the right folder.
3. Give it a rationale that would satisfy someone reading the diff in a year.
4. Run `vrusha rules --check`.

If you find yourself adding a level-1 rule, ask whether it is genuinely
inviolable. Level 1 is where the rules live that you will one day have to
defend.

## What the levels are not

- They are not a policy engine. There is no expression language; a rule is a
  key, an action and a value, and the code interprets it.
- They are not a permission system. They decide what enters *this* store on
  *this* machine. They are not legal advice, and they do not know your
  jurisdiction.
- Level 1 stops the pipeline. If you want something to be stored and a level-1
  rule refuses it, the answer is not to add an exception. It is to not store
  it.
