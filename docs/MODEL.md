# The model

`TinyLM` is a decoder-only transformer: RMSNorm, RoPE, SwiGLU, tied
embeddings, no biases. Four presets, all defined in
`vrusha/generate/model.py`.

| preset | d_model | layers | heads | params @ vocab 8,192 | params @ vocab 32,768 | what it is for |
|---|---|---|---|---|---|---|
| `tiny` | 64 | 2 | 2 | 624,960 | 2,148,672 | proving the loop runs |
| `6m` | 192 | 6 | 6 | 4,229,568 | 8,800,704 | a first real model |
| `20m` | 384 | 6 | 6 | 13,767,552 | 22,909,824 | the smallest model that writes usable prose |
| `50m` | 512 | 8 | 8 | 29,401,600 | 41,591,296 | the ceiling for a free GPU |

Those are measured by `TinyLM.params()`, not estimated. The preset **name is
nominal** — `6m` is 4.2M with the default 8k vocabulary and 8.8M with a 32k
one. `vrusha train` prints the exact count for your vocabulary before it
starts, because at this scale the embedding table is a large share of the
model: `20m` is 10.6M non-embedding parameters and 3.1M of embedding.

## The token budget is the real constraint

Chinchilla says about 20 tokens per parameter. That is not a rule of nature,
but it is a good default and it is the number the level-9 rule encodes.

| preset | params @ 8k vocab | tokens for 20× | what that is |
|---|---|---|---|
| tiny | 0.62M | 12M | a few thousand pages |
| 6m | 4.2M | 85M | a large documentation set |
| 20m | 13.8M | 275M | a serious corpus |
| 50m | 29.4M | 590M | tens of GB of clean text |

If you have 5M tokens and want a 20M model, you do not have a 20M model. You
have an undertrained one. Build a 6M model and train it properly.

## Throughput, measured

On the 2-core x86-64 sandbox this repository was developed in, with
`torch 2.15.0+cpu`, batch 4 × 128:

| preset | tokens/s | notes |
|---|---|---|
| tiny (0.16M at vocab 909) | ~2,300 | measured |
| 6m | not measured here | run 20 steps and read `train_log.jsonl` |

Do not trust a throughput number you have not measured on your own machine.
Twenty steps and one line of arithmetic beats any table:

```bash
python -m vrusha train --preset 6m --steps 20
# read tokens/s from the log line, then:
#   hours = tokens_needed / (tokens_per_second * 3600)
```

## What CPU training actually buys

Loss falls. Samples stay incoherent. That is not a bug in the trainer.

A 0.16M-parameter model shown 20,000 tokens learns the shape of the text and
nothing else. It will produce fluent repetition of a template it memorised,
which looks like progress and is not. The tell is that the training loss keeps
falling while the samples stay identical in structure.

The honest summary: **CPU training on a laptop proves the pipeline; it does not
produce a model.** The model is trained on a GPU, and nothing in the code
changes between the two.

## Judging a run

1. **Val loss, not train loss.** `train_log.jsonl` has both. A gap that widens
   is memorisation.
2. **Read the samples.** `vrusha generate --prompt "..."`. Repetition means the
   corpus was too small or too repetitive.
3. **Held-out questions.** Write ten questions you know the answer to, run
   `vrusha query` on them, and score retrieval. If retrieval fails, the model
   cannot succeed.

## The checkpoint

A `.pt` is a pickle. Restoring one with `weights_only=False` runs whatever
callable the file names, so a checkpoint you did not write is untrusted input --
and checkpoints travel: they are downloaded, copied between machines, put in a
Drive folder.

Loading is therefore safe by default and validated before a single weight is
used:

- `weights_only=True`, so no callable can run at load time;
- the stored config is checked against `ModelConfig` (unknown keys, missing
  keys, non-positive dimensions, `d_model % n_head`, an even head dimension);
- every state-dict key must exist and every tensor must have the shape the
  model expects;
- `--resume` applies the same checks and refuses a checkpoint trained with a
  different preset or vocabulary;
- generation refuses a tokenizer whose vocabulary size differs from the
  checkpoint's.

An old file that cannot be read safely raises `CheckpointError` with the fix
rather than falling back to unrestricted loading. `allow_pickle=True` exists in
the Python API for a file you wrote yourself; it is never automatic.

## What the checkpoint holds

`save_checkpoint` writes the weights, the model config and the step. It does
**not** write the optimizer state, so `--resume` restarts the schedule rather
than continuing it exactly. For runs long enough that this matters, extend the
payload — the field is already there.

`latest.pt` is always the last completed step. `step_NNNNNN.pt` files rotate,
keeping the last two (`training.checkpoint_keep`).

## Using a checkpoint in `ask`

```bash
python -m vrusha ask "..." --checkpoint data/models/20m/latest.pt
```

If the checkpoint cannot be loaded or its tokenizer is missing, `ask` says so
in the method line and composes instead. A broken checkpoint is not a dead end,
and it is never silently ignored.

## Limits, stated plainly

- **No KV cache.** Generation recomputes the prefix each step. At this scale it
  is imperceptible; at 1B parameters it would not be.
- **No flash attention.** `scaled_dot_product_attention` uses whatever the
  build provides.
- **No mixed precision in the trainer.** `--device cuda` gives you speed, not
  bf16. Add it before a serious run.
- **No evaluation harness.** `vrusha query` over your own questions is the
  harness. A benchmark you did not write measures something you do not care
  about.
