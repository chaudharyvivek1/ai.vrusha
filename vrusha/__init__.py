"""Vrusha: a self-hosted pipeline from web harvest to a trained small model.

The five stages, in the order you run them:

    harvest  ->  store  ->  passes  ->  tokens  ->  generate

`harvest` pulls pages and code you are permitted to use. `store` files every
unit under a numeric address (see `vrusha.store.address`). `passes` are the
deterministic operators that sort, merge, filter, analyse and re-address the
store until related material sits close together. `tokens` turns the store
into a tokenizer and binary shards. `generate` trains and samples the model.

Every stage runs offline. Nothing here needs a GPU, an API key or a network
connection to be testable; the network is used only by `harvest`.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
