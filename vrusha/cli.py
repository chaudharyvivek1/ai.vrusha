"""The command line: one entry point for every stage.

    vrusha doctor                 what this machine can do
    vrusha search "query"         find pages, store nothing
    vrusha harvest --query "..."  search, fetch, gate, chunk, embed, store
    vrusha ingest --dir ./notes   the same for files on disk, no network
    vrusha passes                 run the operators that reshape the store
    vrusha address 0.5            browse a region of the address tree
    vrusha query "question"       retrieval only — the honest view
    vrusha ask "question"         retrieval plus an answer with citations
    vrusha rules --explain KEY    why the system decided something
    vrusha languages              the profiles it carries
    vrusha tokenize               train a tokenizer on the store
    vrusha dataset                build binary shards
    vrusha train                  train the model
    vrusha generate               sample from a checkpoint
    vrusha extract --dir PATH     html tables and csv into typed rows
    vrusha tables                 the extracted tables, and their schemas
    vrusha derive                 a computed column, from a checked formula
    vrusha joins                  which columns across tables are one entity
    vrusha entities               spellings of one name, across tables
    vrusha board                  the agent blackboard: runs and entries
    vrusha claims                 who holds which task, and for how much longer
    vrusha agents                 the 64 slots: live, dead, free, and their skills
    vrusha math "e^{i\\pi}+1=0"   search stored formulae as formulae
    vrusha research "question"    search, fetch, extract, answer, with sources
    vrusha tools                  which engines this machine can run, and why not
    vrusha verify --refetch       is what we stored still true?
    vrusha index build            the vector index: routed scan over int8
    vrusha index export           push the same chunks into RagForge
    vrusha backends               which engines are usable, and how fast
    vrusha serve                  one JSON API, either engine behind it
    vrusha verify                 quality control over the store
    vrusha report                 one page

Every command accepts `--json`, and every command that reads the store accepts
`--db`. Nothing is required to be in a particular directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .config import ConfigError, ensure_dirs, load_config, resolve
from .languages import LanguageError, load_registry
from .rules import RuleError, load_rules


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _open_store(cfg: dict):
    from .store.db import Store
    ensure_dirs(cfg)
    return Store(resolve(cfg, "db"))


def _embedder(cfg: dict, verbose: bool = False):
    from .store.embed import get_embedder
    return get_embedder(cfg, verbose=verbose)


def _router(cfg: dict, store, embedder):
    """The router for this command, or None when the config says off."""
    if str((cfg.get("backends", {}) or {}).get("mode", "auto")).lower() == "off":
        return None
    from .store.router import Router
    return Router(store, embedder, cfg)


def _emit(args, payload, human: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(human)


def _print_table(rows: List[List[str]], headers: Optional[List[str]] = None) -> None:
    table = ([headers] if headers else []) + rows
    if not table:
        return
    widths = [max(len(str(row[i])) if i < len(row) else 0 for row in table)
              for i in range(max(len(r) for r in table))]
    for index, row in enumerate(table):
        line = "  ".join(str(cell).ljust(widths[i]) if i < len(row) else " " * widths[i]
                         for i, cell in enumerate(row))
        print("  " + line.rstrip())
        if index == 0 and headers:
            print("  " + "  ".join("-" * w for w in widths))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_doctor(args, cfg, rules, registry) -> int:
    from .doctor import format_checks, run_doctor
    checks = run_doctor(cfg)
    _emit(args, checks, format_checks(checks))
    return 0 if all(c["ok"] for c in checks) else 1


def cmd_search(args, cfg, rules, registry) -> int:
    from .harvest.search import search
    hits, notes = search(args.query, args.limit, cfg, backend=args.backend)
    if args.json:
        print(json.dumps({"query": args.query, "hits": [h.to_dict() for h in hits],
                          "notes": notes}, indent=2, ensure_ascii=False))
        return 0
    print(f"search: {args.query}")
    for note in notes:
        print(f"  · {note}")
    print()
    if not hits:
        print("  no results")
        return 0
    for index, hit in enumerate(hits, start=1):
        print(f"  {index:>2}. {hit.title or '(no title)'}")
        print(f"      {hit.url}")
        if hit.snippet:
            snippet = " ".join(hit.snippet.split())[:180]
            print(f"      {snippet}")
    return 0


def cmd_harvest(args, cfg, rules, registry) -> int:
    from .harvest.fetch import Fetcher, harvest_local, write_manifest
    from .harvest.search import search
    from .ingest import ingest_documents

    docs = []
    if args.dir:
        for path in args.dir:
            docs.extend(harvest_local(path, licence=args.licence))
    if args.url:
        fetcher = Fetcher(cfg)
        for url in args.url:
            docs.append(fetcher.get(url))
    if args.seed:
        fetcher = Fetcher(cfg)
        docs.extend(fetcher.crawl(args.seed, max_pages=args.max_pages,
                                  max_depth=args.depth, same_domain=not args.any_domain))
    if args.query:
        hits, notes = search(args.query, args.limit, cfg, backend=args.backend)
        if not args.json:
            for note in notes:
                print(f"  · {note}")
        fetcher = Fetcher(cfg)
        for hit in hits[:args.limit]:
            docs.append(fetcher.get(hit.url))
    if not docs:
        print("nothing to harvest: pass --query, --url, --seed or --dir")
        return 2

    manifest_path = write_manifest(docs, resolve(cfg, "manifests"))
    ok = [d for d in docs if d.ok]
    if not args.json:
        print(f"harvested {len(docs)} document(s), {len(ok)} with content")
        print(f"manifest {manifest_path}")
    if args.no_ingest:
        _emit(args, {"documents": len(docs), "ok": len(ok), "manifest": manifest_path}, "")
        return 0

    store = _open_store(cfg)
    report = ingest_documents(docs, store, cfg, rules, registry, _embedder(cfg, args.verbose),
                              licence=args.licence, verbose=args.verbose)

    extracted = {"tables": 0, "rows": 0, "documents": 0}
    if args.extract:
        # the page is already fetched, gated and stored: read its markup for
        # tables and JSON-LD rather than re-inferring them from flattened text
        from .store.db import url_hash
        from .store.records import extract_document
        for doc in ok:
            html = getattr(doc, "html", "")
            if not html:
                continue
            row = store.conn.execute(
                "SELECT doc_id FROM documents WHERE url_hash = ?",
                (url_hash(doc.url),)).fetchone()
            if row is None:
                continue
            summary = extract_document(store, int(row[0]), html, doc.url,
                                       dayfirst=not args.mdy)
            extracted["tables"] += int(summary["tables"])
            extracted["rows"] += int(summary["rows"])
            extracted["documents"] += 1
    store.close()
    if args.json:
        print(json.dumps({"harvest": {"documents": len(docs), "ok": len(ok)},
                          "ingest": report.to_dict(), "extract": extracted,
                          "manifest": manifest_path}, indent=2))
        return 0
    print()
    print(f"  stored    {report.stored} document(s), {report.chunks} chunk(s), "
          f"{report.embedded} embedded")
    if args.extract:
        print(f"  extracted {extracted['tables']} table(s), {extracted['rows']} row(s) "
              f"from {extracted['documents']} document(s)")
    if report.rejected:
        print(f"  rejected  {report.rejected}")
        for reason, count in list(report.by_reason.items())[:8]:
            print(f"    {count:>3}  {reason}")
    print()
    print("  next: vrusha passes")
    return 0


def cmd_ingest(args, cfg, rules, registry) -> int:
    from .ingest import ingest_paths
    paths = args.dir or []
    if not paths:
        print("pass --dir (a file or a directory)")
        return 2
    store = _open_store(cfg)
    report = ingest_paths(paths, store, cfg, rules, registry, _embedder(cfg, args.verbose),
                          licence=args.licence, manifest_dir=resolve(cfg, "manifests"),
                          verbose=args.verbose)
    store.close()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0
    print(f"  stored    {report.stored} document(s), {report.chunks} chunk(s), "
          f"{report.embedded} embedded")
    if report.rejected:
        print(f"  rejected  {report.rejected}")
        for reason, count in list(report.by_reason.items())[:8]:
            print(f"    {count:>3}  {reason}")
    return 0


def cmd_passes(args, cfg, rules, registry) -> int:
    from .store.passes import PassContext, describe, run_passes
    if args.list:
        rows = [[p["id"], str(p["level"]), p["description"]] for p in describe()]
        _print_table(rows, ["pass", "level", "job"])
        return 0
    store = _open_store(cfg)
    ctx = PassContext(store=store, cfg=cfg, rules=rules, registry=registry, verbose=not args.json)
    only = [p.strip() for p in args.only.split(",")] if args.only else None
    try:
        results = run_passes(ctx, only=only)
    except KeyError as exc:
        print(f"error: {exc}")
        return 2
    store.close()
    _emit(args, results, "")
    return 0


def cmd_address(args, cfg, rules, registry) -> int:
    from .store.address import Address, spread
    store = _open_store(cfg)
    try:
        if args.distance:
            left, right = args.distance
            a, b = Address.parse(left), Address.parse(right)
            payload = {"left": str(a), "right": str(b),
                       "distance": a.distance(b),
                       "similarity": round(a.similarity(b), 4),
                       "same_parent": a.parent() == b.parent()}
            _emit(args, payload,
                  f"{a} <-> {b}\n  distance   {a.distance(b):.2f}\n"
                  f"  similarity {a.similarity(b):.3f}\n"
                  f"  same parent {'yes' if a.parent() == b.parent() else 'no'}")
            return 0

        if args.lookup:
            region = Address.parse(args.lookup)
            rows = store.chunks_in_region(region, limit=args.limit)
            if args.json:
                print(json.dumps([{"address": r["address"], "kind": r["kind"],
                                   "symbol": r["symbol"],
                                   "preview": " ".join((r["text"] or "").split())[:160]}
                                  for r in rows], indent=2, ensure_ascii=False))
                return 0
            print(f"region {region} — {region.label()}")
            print(f"  {len(rows)} chunk(s) shown")
            for row in rows:
                preview = " ".join((row["text"] or "").split())[:100]
                print(f"  {row['address']:<18} {row['kind']:<10} {preview}")
            return 0

        addresses = [a for _id, a in store.addresses()]
        if not addresses:
            print("no addresses yet — run `vrusha passes`")
            return 1
        summary = spread(addresses)
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
            return 0
        print(f"addresses: {summary['count']}")
        print(f"  regions        {summary['clusters']}")
        print(f"  deepest        {summary['deepest']}")
        print(f"  widest cluster {summary['widest_cluster'][0]} "
              f"({summary['widest_cluster'][1]} chunks)")
        print()
        print("  domains")
        for name, count in summary["domains"].items():
            print(f"    {name:<10} {count:>6}")
        print()
        print("  families")
        for key, count in summary["families"].items():
            print(f"    {key:<10} {count:>6}")
        return 0
    finally:
        store.close()


def cmd_query(args, cfg, rules, registry) -> int:
    from .store.address import Address
    from .store.query import retrieve
    store = _open_store(cfg)
    address = Address.parse(args.address) if args.address else None
    embedder = _embedder(cfg)
    retrieval = retrieve(store, embedder, args.question, cfg,
                         address_hint=address, top_k=args.top_k,
                         router=_router(cfg, store, embedder))
    store.close()
    if args.json:
        print(json.dumps(retrieval.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"query: {args.question}")
    if getattr(args, "explain", False) and retrieval.decision:
        decision = retrieval.decision
        print(f"  backend: {decision['backend']} ({decision['gate']}: {decision['reason']})")
        if decision.get("ms"):
            print("  ms: " + "  ".join(f"{k}={v}" for k, v in decision["ms"].items()))
        for skipped in decision.get("skipped") or []:
            print(f"  skipped: {skipped}")
    for note in retrieval.notes:
        print(f"  · {note}")
    print()
    if retrieval.empty:
        print("  nothing retrieved")
        return 0
    for index, hit in enumerate(retrieval.hits, start=1):
        scores = " ".join(f"{k}={v:.3f}" for k, v in hit.scores.items())
        print(f"  [{index}] {hit.address}  {hit.kind}  {hit.symbol or ''}")
        print(f"      {hit.url}")
        print(f"      {scores}")
        print(f"      {' '.join(hit.text.split())[:200]}")
        print()
    return 0


def cmd_ask(args, cfg, rules, registry) -> int:
    from .answer import ask
    from .store.address import Address
    store = _open_store(cfg)
    embedder = _embedder(cfg)
    answer = ask(store, embedder, args.question, cfg,
                 checkpoint=args.checkpoint,
                 address=Address.parse(args.address) if args.address else None,
                 top_k=args.top_k,
                 router=_router(cfg, store, embedder))
    store.close()
    if args.json:
        print(json.dumps(answer.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"Q: {args.question}")
    print(f"   ({answer.method}, {answer.hits} retrieved)")
    print()
    print(answer.render())
    if answer.refused and answer.reason:
        print()
        print(f"  refused: {answer.reason}")
    return 0


def cmd_rules(args, cfg, rules, registry) -> int:
    if args.explain:
        decision = rules.decide(args.explain)
        if args.json:
            print(json.dumps(decision.as_dict(), indent=2, ensure_ascii=False))
            return 0
        print(f"rules --explain {args.explain}")
        if decision.rule is None:
            print("  no rule speaks to this key; the caller's default applies")
            return 0
        winner = decision.rule
        from .rules import LEVELS as _LEVELS
        print(f"  winner  level {winner.level} ({_LEVELS.get(winner.level, '')})"
              f"  {winner.id}  [{winner.action}]")
        print(f"  value   {json.dumps(winner.value, ensure_ascii=False)}")
        print(f"  why     {winner.rationale}")
        print(f"  from    {winner.source}")
        if len(decision.trace) > 1:
            print()
            print("  also considered")
            for rule in decision.trace[1:]:
                print(f"    level {rule.level:<2} {rule.id}  [{rule.action}]  (beaten on level)")
        return 0

    if args.check:
        problems = rules.check()
        conflicts = rules.conflicts()
        if args.json:
            print(json.dumps({"problems": problems,
                              "conflicts": [{"key": k, "levels": [r.level for r in v]}
                                            for k, v in conflicts]}, indent=2))
            return 0
        print(f"rules: {len(rules)} across {len(rules.level_counts())} levels")
        print("  problems  " + ("none" if not problems else ""))
        for problem in problems:
            print(f"    {problem}")
        print(f"  keys with more than one level: {len(conflicts)}")
        for key, entries in conflicts:
            print(f"    {key:<34} levels {sorted(r.level for r in entries)}")
        return 0

    counts = rules.level_counts()
    from .rules import LEVELS
    if args.json:
        print(json.dumps({"levels": {LEVELS[k]: v for k, v in counts.items()},
                          "rules": [r.to_dict() for r in rules]}, indent=2, ensure_ascii=False))
        return 0
    print(f"rules: {len(rules)}")
    for level, name in LEVELS.items():
        print(f"  level {level:>2}  {name:<12} {counts[level]:>3} rule(s)")
    print()
    if args.level:
        for rule in rules:
            if rule.level == args.level:
                print(f"  {rule.id:<34} [{rule.action}] {rule.rationale[:70]}")
    return 0


def cmd_languages(args, cfg, rules, registry) -> int:
    if args.detect:
        code, confidence = registry.detect(args.detect)
        payload = {"language": code, "confidence": confidence,
                   "profile": registry.get(code).to_dict()}
        _emit(args, payload, f"{code} ({confidence})\n"
                             f"  {registry.get(code).name}, script {registry.get(code).script}")
        return 0
    if args.json:
        print(json.dumps([p.to_dict() for p in registry], indent=2, ensure_ascii=False))
        return 0
    natural = registry.natural()
    code_langs = registry.code_languages()
    print(f"languages: {len(registry)} profiles")
    print()
    print("  natural")
    for profile in natural:
        grammar = profile.grammar.get("word_order", "")
        print(f"    {profile.code:<4} {profile.name:<10} {profile.script:<6} "
              f"family {profile.family_id:<3} {grammar}")
    print()
    print("  programming")
    for profile in code_langs:
        exts = " ".join(profile.extensions)
        print(f"    {profile.code:<12} family {profile.family_id:<3} "
              f"{profile.chunk_strategy:<10} {exts}")
    return 0


def cmd_tokenize(args, cfg, rules, registry) -> int:
    from .tokens.bpe import train_tokenizer
    from .tokens.dataset import corpus_from_store

    if args.dir:
        texts = []
        for path in args.dir:
            if os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    if os.path.isfile(full):
                        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                            texts.append(fh.read())
            elif os.path.isfile(path):
                texts.append(path)
    else:
        store = _open_store(cfg)
        items = corpus_from_store(store, min_chars=args.min_chars,
                                  address_prefix=args.address)
        store.close()
        texts = [item.text for item in items]
        if not texts:
            print("the store has no text; harvest or ingest first")
            return 1

    out = args.out or os.path.join(resolve(cfg, "tokens"), "tokenizer.json")
    tokenizer = train_tokenizer(texts, vocab_size=args.vocab, min_frequency=args.min_freq,
                                out_path=out, backend=args.backend)
    sample = texts[0][:2000]
    payload = {"path": out, "vocab_size": tokenizer.vocab_size,
               "backend": tokenizer.backend, "corpus_documents": len(texts),
               "fertility": round(tokenizer.fertility(sample), 3)}
    _emit(args, payload,
          f"tokenizer  {out}\n  vocab      {tokenizer.vocab_size:,} ({tokenizer.backend})\n"
          f"  corpus     {len(texts)} document(s)\n"
          f"  fertility  {payload['fertility']} tokens per word")
    return 0


def cmd_dataset(args, cfg, rules, registry) -> int:
    from .tokens.bpe import Tokenizer
    from .tokens.dataset import DatasetConfigError, build_dataset, corpus_from_store

    tokenizer_path = args.tokenizer or os.path.join(resolve(cfg, "tokens"), "tokenizer.json")
    if not os.path.exists(tokenizer_path):
        print(f"no tokenizer at {tokenizer_path}; run `vrusha tokenize` first")
        return 1
    tokenizer = Tokenizer.load(tokenizer_path)

    store = _open_store(cfg)
    items = corpus_from_store(store, min_chars=args.min_chars)
    store.close()
    if not items:
        print("the store has no text")
        return 1

    out = args.out or os.path.join(resolve(cfg, "tokens"), "dataset")
    try:
        manifest = build_dataset(items, tokenizer, out, shard_tokens=args.shard_tokens,
                                 val_fraction=args.val_fraction, dtype=args.dtype,
                                 tokenizer_path=tokenizer_path, verbose=not args.json)
    except DatasetConfigError as exc:
        print(f"dataset: {exc}")
        return 2
    _emit(args, manifest,
          f"dataset    {out}\n  tokens     {manifest['total_tokens']:,}\n"
          f"  shards     {len(manifest['shards'])}\n"
          f"  split      {manifest['documents']['train']} train / "
          f"{manifest['documents']['val']} val\n  dtype      {manifest['dtype']}")
    return 0


def cmd_train(args, cfg, rules, registry) -> int:
    try:
        import torch  # noqa: F401
    except ImportError:
        print("training needs torch:\n"
              "  pip install torch --index-url https://download.pytorch.org/whl/cpu")
        return 1
    from .generate.model import ModelConfig
    from .generate.train import TrainConfig, train
    from .tokens.bpe import Tokenizer

    data_dir = args.data_dir or os.path.join(resolve(cfg, "tokens"), "dataset")
    if not os.path.exists(os.path.join(data_dir, "meta.json")):
        print(f"no dataset at {data_dir}; run `vrusha dataset` first")
        return 1

    tokenizer_path = args.tokenizer or os.path.join(resolve(cfg, "tokens"), "tokenizer.json")
    if not os.path.exists(tokenizer_path):
        print(f"no tokenizer at {tokenizer_path}")
        return 1
    tokenizer = Tokenizer.load(tokenizer_path)

    model_cfg = ModelConfig.preset(args.preset, tokenizer.vocab_size)
    if args.seq_len:
        model_cfg.n_ctx = args.seq_len
    train_cfg = TrainConfig(
        steps=args.steps, batch_size=args.batch_size, seq_len=args.seq_len or model_cfg.n_ctx,
        lr=args.lr, warmup=args.warmup, eval_every=args.eval_every,
        device=args.device, seed=args.seed,
    )
    out = args.out or os.path.join(resolve(cfg, "models"), args.preset)
    summary = train(data_dir, out, model_cfg, train_cfg,
                    tokenizer_path=tokenizer_path, verbose=not args.json,
                    resume=args.resume)
    _emit(args, summary,
          f"\ntrained    {summary['checkpoint']}\n"
          f"  parameters {summary['parameters']:,}\n"
          f"  tokens     {summary['tokens_seen']:,}\n"
          f"  val loss   {summary['final_val_loss']}\n"
          f"  wall clock {summary['wall_clock_s']}s")
    return 0


def cmd_generate(args, cfg, rules, registry) -> int:
    try:
        import torch  # noqa: F401
    except ImportError:
        print("generation needs torch:\n"
              "  pip install torch --index-url https://download.pytorch.org/whl/cpu")
        return 1
    from .generate.sample import generate_text

    checkpoint = args.checkpoint
    if not checkpoint:
        models_dir = resolve(cfg, "models")
        candidates = []
        for root, _dirs, files in os.walk(models_dir):
            for name in files:
                if name == "latest.pt":
                    candidates.append(os.path.join(root, name))
        if not candidates:
            print("no checkpoint found; run `vrusha train` first")
            return 1
        checkpoint = candidates[0]

    result = generate_text(checkpoint, args.prompt,
                           tokenizer_path=args.tokenizer or "",
                           max_new_tokens=args.max_tokens,
                           temperature=args.temperature, top_k=args.top_k,
                           device=args.device, seed=args.seed)
    _emit(args, result,
          f"{result['prompt']}\n{result['completion']}\n\n"
          f"  {result['tokens']} tokens from {result['parameters']:,} parameters "
          f"on {result['device']}")
    return 0


def cmd_index(args, cfg, rules, registry) -> int:
    """Build, inspect or drop the vector index. The store is never touched."""
    from .store import ann as ann_mod

    action = getattr(args, "index_action", "stats")
    store = _open_store(cfg)
    try:
        if action == "build" and getattr(args, "engine", "native") == "sqlite-vec":
            from .store.vec0 import Vec0Backend
            backend = Vec0Backend(store, cfg)
            try:
                built = backend.build()
            except RuntimeError as exc:
                print(f"index: {exc}")
                return 1
            if args.json:
                print(json.dumps(built, indent=2))
            else:
                print(f"built {built['rows']:,} vectors into a vec0 table "
                      f"at {built['dim']} dims, {built['mb']} MB")
                print(f"  {built['path']}")
            return 0
        if action == "build":
            try:
                index = ann_mod.build_for(store, cfg, verbose=not args.json)
            except ann_mod.AnnError as exc:
                print(f"index: {exc}")
                return 1
            payload = index.stats()
            payload["directory"] = ann_mod.index_dir(cfg)
            _emit(args, payload,
                  f"built {payload['rows']:,} vectors into {payload['cells']:,} cells "
                  f"at {payload['dim']} dims, {payload['bytes'] / 1e6:.1f} MB\n"
                  f"  {payload['directory']}")
            return 0

        if action == "export":
            from .store import export as export_mod
            from .store.backends import RagForgeBackend
            if args.to != "ragforge":
                print(f"index export: unknown target {args.to!r}; only ragforge")
                return 2
            if getattr(args, "jsonl", ""):
                summary = export_mod.export_to_jsonl(store, args.jsonl)
                _emit(args, summary,
                      f"wrote {summary['chunks']:,} rows at {summary['dim']} dims "
                      f"to {summary['path']}")
                return 0
            if args.url:
                backend = RagForgeBackend(url=args.url, timeout=30.0)
                if not backend.available():
                    print(f"index export: {args.url} is not answering /ready")
                    return 1
                summary = export_mod.export_to_url(store, backend, size=args.batch)
            else:
                if not args.dir:
                    print("index export: give --url to POST, or --dir to write files")
                    return 2
                summary = export_mod.export_to_dir(store, args.dir, size=args.batch)
            _emit(args, summary,
                  f"exported {summary['chunks']:,} chunks in {summary['batches']} batches")
            return 0

        if action == "drop":
            removed = ann_mod.drop_for(cfg)
            _emit(args, {"dropped": removed, "directory": ann_mod.index_dir(cfg)},
                  "index removed; the store is untouched" if removed
                  else "no index to remove")
            return 0

        # stats: is there one, is it fresh, and how are the rows distributed
        directory = ann_mod.index_dir(cfg)
        forced = dict(cfg)
        forced["ann"] = {**cfg.get("ann", {}), "mode": "force"}
        index = ann_mod.load_for(store, forced)
        total, _max_id = store.vector_fingerprint()
        if index is None:
            on_disk = bool(directory) and os.path.exists(
                os.path.join(directory, ann_mod.META))
            _emit(args, {"index": None, "vectors_in_store": total,
                         "stale_on_disk": on_disk, "directory": directory},
                  ("index is stale: the store has moved on. run `vrusha index build`"
                   if on_disk else "no index. `vrusha index build` makes one")
                  + f"\n  vectors in store: {total:,}")
            return 0

        stats = index.stats()
        stats["stale"] = index.is_stale(store)
        stats["vectors_in_store"] = total
        stats["directory"] = directory
        cells = stats["rows_per_cell"]
        _emit(args, stats,
              f"rows {stats['rows']:,}  cells {stats['cells']:,}  "
              f"dim {stats['dim']}  {stats['bytes'] / 1e6:.1f} MB"
              + ("  (mmapped)" if stats["mmapped"] else "  (in ram)")
              + f"\n  rows/cell min {cells['min']} median {cells['median']} "
                f"max {cells['max']}"
              + f"\n  built {stats['built_at']}  stale {stats['stale']}")
        return 0
    finally:
        store.close()


def cmd_backends(args, cfg, rules, registry) -> int:
    """What engines this machine can offer, and how each has performed."""
    from .store.router import Router

    store = _open_store(cfg)
    try:
        router = Router(store, _embedder(cfg), cfg)
        report = router.report()
    finally:
        store.close()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0

    rows = [[item["name"],
             item.get("status", "yes" if item.get("available") else "no"),
             f"{item.get('rows', 0):,}" if item.get("rows") else "-",
             f"{item['ewma_ms']:.2f}" if item.get("ewma_ms") else "-",
             str(item.get("samples", 0)),
             str(item.get("errors", 0)),
             "cooling" if item.get("cooling") else "",
             ",".join(item.get("capabilities", []))]
            for item in report]
    _print_table(rows, ["backend", "status", "rows", "ewma ms", "n", "err", "", "capabilities"])
    free = report[0].get("free_ram_mb") if report else None
    if free is not None:
        print(f"\n  free RAM {free:.0f} MB, {report[0].get('vectors_in_store', 0):,} vectors in store")
    print("  set backends.ragforge.url to bring the second engine into the rotation")
    return 0


def cmd_serve(args, cfg, rules, registry) -> int:
    """The unified JSON API. Holds one router, so its memory survives requests."""
    from .serve import serve

    httpd = serve(cfg, _embedder(cfg), host=args.host, port=args.port,
                  verbose=args.verbose)
    print(f"vrusha serving on http://{args.host}:{args.port}")
    print("  POST /search   {text|vector, k, filter?}")
    print("  POST /ask      {question, k?}")
    print("  GET  /backends /health /stats")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
        httpd.store.close()
    return 0


def cmd_extract(args, cfg, rules, registry) -> int:
    """Turn tables on disk into typed rows beside the chunks."""
    from .store.records import extract_dir, extract_path

    store = _open_store(cfg)
    try:
        if not args.dir:
            print("extract: give --dir with html or csv files")
            return 2
        if args.dir and os.path.isfile(args.dir):
            single = extract_path(store, args.dir, licence=args.licence,
                                  dayfirst=not args.mdy, verbose=not args.json)
            # one file is a directory of one: give the summary the same shape
            result = {"files": 1, "tables": single.get("tables", 0),
                      "rows": single.get("rows", 0),
                      "rejected": single.get("rejected", 0),
                      "results": [single]}
        else:
            result = extract_dir(store, args.dir, licence=args.licence,
                                 dayfirst=not args.mdy, limit=args.limit,
                                 verbose=not args.json)
    finally:
        store.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"\n  {result['files']} file(s), {result['tables']} table(s), "
              f"{result['rows']} row(s), {result.get('equations', 0)} formula(s), "
              f"{result['rejected']} table(s) rejected")
    return 0


def cmd_tables(args, cfg, rules, registry) -> int:
    """The extracted tables, or the rows of one of them."""
    from .store.records import list_tables, table_rows

    store = _open_store(cfg)
    try:
        if args.id:
            rows = table_rows(store, args.id, limit=args.limit)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
                return 0
            if not rows:
                print(f"no rows in table {args.id}")
                return 0
            columns = list(rows[0])
            print("  " + "  ".join(f"{c[:18]:<18}" for c in columns))
            for row in rows:
                print("  " + "  ".join(f"{str(row.get(c, ''))[:18]:<18}" for c in columns))
            return 0

        tables = list_tables(store)
        if args.json:
            print(json.dumps(tables, indent=2, ensure_ascii=False, default=str))
            return 0
        if not tables:
            print("no tables extracted yet: `vrusha extract --dir <path>`")
            return 0
        rows = [[str(t["table_id"]), t["name"][:28], str(t["rows"]), str(t["cols"]),
                 f"{t['confidence']:.0%}",
                 ", ".join(f"{c['name']}:{c['type']}" for c in t["columns"])[:60]]
                for t in tables]
        _print_table(rows, ["id", "name", "rows", "cols", "typed", "columns"])
    finally:
        store.close()
    return 0


def cmd_derive(args, cfg, rules, registry) -> int:
    """Add computed columns to an extracted table."""
    from .store.formula import FormulaError
    from .store.records import derive_table

    if not args.table:
        print("derive: give --table <id> (see `vrusha tables`)")
        return 2

    if args.script:
        if not args.allow_scripts:
            print("derive: running a script executes code against scraped data. "
                  "Pass --allow-scripts if that is what you mean; a formula "
                  "(--expr) cannot execute anything and is the safer default.")
            return 2
        from .store.records import apply_script
        store = _open_store(cfg)
        try:
            result = apply_script(store, args.table, args.script,
                                  timeout=args.timeout,
                                  memory_mb=args.memory_mb,
                                  isolation=args.isolation)
        except (ValueError, OSError) as exc:
            print(f"derive: {exc}")
            return 1
        finally:
            store.close()
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            return 0 if result.get("ok") else 1
        if not result.get("ok"):
            print(f"derive: {result['error']}")
            run = result.get("run") or {}
            print(f"  isolation {run.get('isolation')}  "
                  f"{run.get('seconds')}s  exit {run.get('exit_code')}")
            if run.get("stderr"):
                print("  stderr: " + str(run["stderr"]).strip()[:300])
            return 1
        run = result["run"]
        print(f"  {result['origin']} over {result['rows']} row(s) "
              f"in {run['seconds']}s ({run['isolation']})")
        for column in result["columns"]:
            print(f"  {column['name']:<20} {column['nulls']} null(s)")
        return 0

    if args.spec:
        try:
            with open(args.spec, encoding="utf-8") as handle:
                spec = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"derive: cannot read {args.spec}: {exc}")
            return 2
    elif args.name and args.expr:
        spec = {"derived": [{"name": args.name, "expr": args.expr,
                             "type": args.type,
                             "guards": list(args.guard or [])}]}
    else:
        print('derive: give --spec formulas.json, or --name and --expr')
        return 2

    store = _open_store(cfg)
    try:
        result = derive_table(store, args.table, spec)
    except FormulaError as exc:
        print(f"derive: {exc}")
        return 1
    finally:
        store.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    for column in result["columns"]:
        guards = f"  when {'; '.join(column['guards'])}" if column["guards"] else ""
        print(f"  {column['name']:<20} = {column['expr']}{guards}")
        print(f"  {'':<20}   {column['nulls']} null(s) of {result['rows']}")
    return 0


def cmd_tools(args, cfg, rules, registry) -> int:
    """Which of the three engines this machine can run, and why not."""
    from .tools import describe_all, get_tool

    if not args.name:
        tools = describe_all()
        if args.json:
            print(json.dumps(tools, indent=2, ensure_ascii=False))
            return 0
        rows = [[t["name"], "yes" if t["available"] else "no", t["mode"],
                 t["reason"]] for t in tools]
        _print_table(rows, ["tool", "ready", "mode", "why"])
        return 0

    tool = get_tool(args.name)
    if tool is None:
        print(f"tools: no tool called {args.name!r}")
        return 2
    if not args.action:
        status = tool.status().to_dict()
        if args.json:
            print(json.dumps(status, indent=2, ensure_ascii=False))
        else:
            print(f"  {status['name']}: {status['summary']}")
            print(f"  ready {status['available']} ({status['mode']}) -- {status['reason']}")
            print(f"  actions: {', '.join(tool.actions) or 'none'}")
        return 0 if status["available"] else 1

    options = {}
    for pair in args.option or []:
        key, _, value = pair.partition("=")
        options[key.replace("-", "_")] = value or True
    result = tool.run(args.action, timeout=args.timeout, **options)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False,
                         default=str))
    elif result.ok:
        print(f"  {result.tool} {result.action}: ok in {result.seconds:.1f}s")
        if result.data is not None:
            print(json.dumps(result.data, indent=2, ensure_ascii=False, default=str)[:4000])
        elif result.stdout.strip():
            print(result.stdout[:4000])
    else:
        print(f"  {result.tool} {result.action}: failed -- {result.error}")
        if result.stderr.strip():
            print(result.stderr[-800:])
    return 0 if result.ok else 1


def cmd_research(args, cfg, rules, registry) -> int:
    """Search, fetch, extract, answer — and show every source."""
    from .research import render, research

    store = _open_store(cfg)
    try:
        result = research(store, _embedder(cfg), cfg, args.question, rules=rules,
                          registry=registry, limit=args.limit,
                          backend=args.backend, top_k=args.top_k,
                          verify=args.verify, licence=args.licence,
                          verbose=not args.json)
    finally:
        store.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get("answer") else 1
    print()
    print(render(result))
    return 0 if result.get("answer") else 1


def cmd_math(args, cfg, rules, registry) -> int:
    """Search the stored formulae as formulae, with sources."""
    from .store.records import list_equations, search_equations

    store = _open_store(cfg)
    try:
        if not args.query:
            equations = list_equations(store, limit=args.limit)
            if args.json:
                print(json.dumps(equations, indent=2, ensure_ascii=False))
                return 0
            if not equations:
                print("no formulae stored: `vrusha extract --dir <path>` reads them")
                return 0
            for item in equations:
                print(f"  {item['canonical'][:60]:<62} {item['url']}")
            return 0
        hits = search_equations(store, args.query, limit=args.limit,
                                min_overlap=args.min_overlap)
    finally:
        store.close()

    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
        return 0 if hits else 1
    if not hits:
        print(f"no formula matched {args.query!r}")
        return 1
    print(f"query: {args.query}")
    for index, hit in enumerate(hits, start=1):
        print(f"\n  [{index}] {hit['kind']}  score {hit['score']:.2f}")
        print(f"      {hit['latex'][:110]}")
        print(f"      {hit['url']}")
        if hit.get("licence"):
            print(f"      licence {hit['licence']}")
    return 0


def cmd_board(args, cfg, rules, registry) -> int:
    """The blackboard: what the agents have written, in order."""
    from .store.blackboard import Blackboard

    store = _open_store(cfg)
    board = Blackboard(store)
    try:
        if not args.run:
            runs = board.runs()
            if args.json:
                print(json.dumps(runs, indent=2, ensure_ascii=False))
                return 0
            if not runs:
                print("no runs yet: the blackboard is empty")
                return 0
            rows = [[item["run"], item["entries"], item["head"],
                     item["quarantined"], item["created"][:19]] for item in runs]
            _print_table(rows, ["run", "entries", "head", "quarantined", "created"])
            return 0

        entries = board.since(args.run, args.since, limit=args.limit,
                              include_quarantined=args.all)
        if args.json:
            print(json.dumps([entry.to_dict() for entry in entries],
                             indent=2, ensure_ascii=False, default=str))
            return 0
        if not entries:
            print(f"no entries after seq {args.since} in run {args.run}")
            return 0
        for entry in entries:
            flag = "!" if entry.quarantined else " "
            print(f"{flag} {entry.seq:>5} {entry.kind:<9} {entry.agent or '-':<16} "
                  f"{entry.summary[:80]}")
            if entry.refs:
                print(f"          refs: {', '.join(entry.refs[:6])}")
            if entry.quarantined:
                print(f"          quarantined: {entry.reason}")
        return 0
    finally:
        board.close()
        store.close()


def cmd_agents(args, cfg, rules, registry) -> int:
    """The 64 slots: who is live, what skill they carry, when they last beat."""
    from .store.blackboard import Blackboard
    from .store.registry import Registry

    store = _open_store(cfg)
    board = Blackboard(store)
    agents = Registry(board)
    try:
        if not args.run:
            runs = board.runs()
            if args.json:
                print(json.dumps(runs, indent=2, ensure_ascii=False))
                return 0
            if not runs:
                print("no runs yet: `vrusha agents --run <id>` after an agent registers")
                return 0
            _print_table([[item["run"], item["entries"], item["head"]]
                          for item in runs], ["run", "entries", "head"])
            return 0
        if args.sweep:
            dead = agents.sweep(args.run, stale_after=args.stale_after)
            if not args.json and dead:
                print(f"  {len(dead)} agent(s) went dead: {', '.join(dead[:6])}")
        slots = agents.slots(args.run)
        summary = agents.summary(args.run)
        if args.json:
            print(json.dumps({"summary": summary,
                              "by_skill": agents.by_skill(args.run),
                              "slots": [slot.to_dict() for slot in slots]},
                             indent=2, ensure_ascii=False, default=str))
            return 0
        live = [slot for slot in slots if slot.state != "free"]
        if not live:
            print(f"no agents registered in run {args.run} "
                  f"({summary['free']} slots free of {summary['capacity']})")
            return 0
        import time as _time
        table = []
        for slot in live:
            age = max(0.0, _time.time() - slot.heartbeat_at) if slot.heartbeat_at else 0
            table.append([slot.slot, slot.state, slot.agent, slot.level or "-",
                          slot.skill or "-", f"{age:.0f}s"])
        _print_table(table, ["slot", "state", "agent", "level", "skill", "beat"])
        print(f"\n  {summary['live']} live, {summary['dead']} dead, "
              f"{summary['free']} free of {summary['capacity']}")
        skills = agents.by_skill(args.run)
        if skills:
            print("  live by skill: " + ", ".join(
                f"{name}×{count}" for name, count in skills.items()))
        return 0
    finally:
        board.close()
        store.close()


def cmd_claims(args, cfg, rules, registry) -> int:
    """Who holds which task, and for how much longer."""
    from .store.blackboard import Blackboard
    from .store.claims import Claims

    store = _open_store(cfg)
    board = Blackboard(store)
    claims = Claims(board)
    try:
        if not args.run:
            runs = board.runs()
            if args.json:
                print(json.dumps(runs, indent=2, ensure_ascii=False))
                return 0
            if not runs:
                print("no runs yet")
                return 0
            _print_table([[item["run"], item["entries"], item["head"]]
                          for item in runs], ["run", "entries", "head"])
            return 0
        if args.reap:
            expired = claims.reap(args.run)
            if not args.json:
                print(f"  {expired} lease(s) expired and made claimable again")
        rows = claims.list(args.run, state=args.state or None)
        summary = claims.summary(args.run)
        if args.json:
            print(json.dumps({"summary": summary, "claims": rows},
                             indent=2, ensure_ascii=False, default=str))
            return 0
        if not rows:
            print(f"no claims in run {args.run}")
            return 0
        import time as _time
        table = []
        for row in rows:
            left = max(0.0, float(row["lease_until"]) - _time.time())
            table.append([row["task"], row["state"], row["agent"] or "-",
                          row["attempts"],
                          f"{left:.0f}s" if row["state"] == "claimed" else "-"])
        _print_table(table, ["task", "state", "agent", "tries", "left"])
        print(f"\n  {summary['done']} done, {summary['claimed']} claimed, "
              f"{summary['failed']} failed of {summary['total']}")
        return 0
    finally:
        board.close()
        store.close()


def cmd_entities(args, cfg, rules, registry) -> int:
    """Which spellings across tables are the same entity."""
    from .store import entities

    store = _open_store(cfg)
    try:
        if args.build:
            result = entities.build(store, threshold=args.threshold)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print(f"  {result['entities']} entity group(s), "
                      f"{result['aliases']} alias(es), threshold {result['threshold']:.0f}")
            return 0
        groups = entities.list_entities(store)
    finally:
        store.close()

    if args.json:
        print(json.dumps(groups, indent=2, ensure_ascii=False))
        return 0
    if not groups:
        print("no entity groups yet: `vrusha entities --build` resolves them")
        return 0
    for group in groups:
        print(f"\n  {group['canonical']}  ({group['size']} spellings)")
        for alias in group["aliases"]:
            print(f"    {alias['score']:>5.0f}  {alias['alias']}")
    return 0


def cmd_joins(args, cfg, rules, registry) -> int:
    """Which columns across tables are the same entity, and how sure we are."""
    from .store import relations

    store = _open_store(cfg)
    try:
        if args.build:
            result = relations.build(store, min_overlap=args.min_overlap,
                                     min_shared=args.min_shared)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            else:
                print(f"  {result['joins']} join(s) across {result['tables']} table(s)")
            return 0
        joins = relations.list_joins(store, table_id=args.table)
    finally:
        store.close()

    if args.json:
        print(json.dumps(joins, indent=2, ensure_ascii=False, default=str))
        return 0
    if not joins:
        print("no joins yet: `vrusha joins --build` infers them from the tables")
        return 0
    rows = [[str(j["left_table"]), j["left_column"], str(j["right_table"]),
             j["right_column"], str(j["shared"]), f"{j['overlap']:.0%}", j["kind"]]
            for j in joins]
    _print_table(rows, ["left", "column", "right", "column", "shared", "overlap", "kind"])
    return 0


def cmd_verify(args, cfg, rules, registry) -> int:
    from .verify import format_results, run_verify, summarise
    if getattr(args, "refetch", False):
        from .verify import format_refetch, refetch
        store = _open_store(cfg)
        try:
            report = refetch(store, cfg, limit=args.limit, apply=args.apply,
                             verbose=not args.json)
        finally:
            store.close()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        else:
            print(format_refetch(report))
        return 0 if all(entry["state"] == "unchanged" for entry in report) else 1

    store = _open_store(cfg)
    results = run_verify(store)
    store.close()
    _emit(args, results, format_results(results))
    return 0 if summarise(results) else 1


def cmd_report(args, cfg, rules, registry) -> int:
    from .report import build_report, format_report
    store = _open_store(cfg)
    report = build_report(store, cfg)
    store.close()
    _emit(args, report, format_report(report))
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # `--json` and `-v` work before or after the subcommand. SUPPRESS keeps the
    # subparser's copy from overwriting a value given globally.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="vrusha",
        description="Harvest the web, file it under numeric addresses, train a small model.",
    )
    parser.add_argument("--version", action="version", version=f"vrusha {__version__}")
    parser.add_argument("--config", help="path to a JSON config that overrides the defaults")
    parser.add_argument("--root", help="data directory (default: ./data)")
    parser.add_argument("--json", action="store_true", default=False,
                        help="machine-readable output")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="probe this machine", parents=[common]).set_defaults(func=cmd_doctor)

    p = sub.add_parser("search", parents=[common], help="search the web, store nothing")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--backend", default=None,
                   help="auto|fixtures|ddg|searxng|tavily|brave|store")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("harvest", parents=[common], help="search, fetch, gate, chunk, embed, store")
    p.add_argument("--query", help="search this and fetch the top results")
    p.add_argument("--url", action="append", help="fetch this url (repeatable)")
    p.add_argument("--seed", action="append", help="crawl from this url (repeatable)")
    p.add_argument("--dir", action="append", help="harvest a local file or directory")
    p.add_argument("--licence", help="SPDX licence for what you are harvesting")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--any-domain", action="store_true", help="follow links off the seed domain")
    p.add_argument("--backend", default=None)
    p.add_argument("--extract", action="store_true",
                   help="also extract tables and JSON-LD from the fetched markup")
    p.add_argument("--mdy", action="store_true", help="month-first dates (US corpora)")
    p.add_argument("--no-ingest", action="store_true", help="fetch and record, do not store")
    p.set_defaults(func=cmd_harvest)

    p = sub.add_parser("ingest", parents=[common], help="store local files (no network)")
    p.add_argument("--dir", action="append", required=True)
    p.add_argument("--licence")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("passes", parents=[common], help="run the operators that reshape the store")
    p.add_argument("--list", action="store_true")
    p.add_argument("--only", help="comma-separated pass ids")
    p.set_defaults(func=cmd_passes)

    p = sub.add_parser("address", parents=[common], help="browse the address tree")
    p.add_argument("lookup", nargs="?", help="a region, e.g. 2.3 or 0.5")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--distance", nargs=2, metavar=("A", "B"))
    p.set_defaults(func=cmd_address)

    sub.add_parser("backends", parents=[common],
                   help="which engines are usable, and how fast they have been"
                   ).set_defaults(func=cmd_backends)

    p = sub.add_parser("serve", parents=[common],
                       help="the unified JSON API over every backend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("query", parents=[common], help="retrieval only, no answer")
    p.add_argument("question")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--address", help="bias retrieval toward this region")
    p.add_argument("--explain", action="store_true",
                   help="print which backend served the vector signal, and why")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("ask", parents=[common], help="retrieve and answer, with citations")
    p.add_argument("question")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--address")
    p.add_argument("--checkpoint", help="a trained .pt; omit to use the composer")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("rules", parents=[common], help="inspect the ten-level rule set")
    p.add_argument("--explain", metavar="KEY")
    p.add_argument("--check", action="store_true")
    p.add_argument("--level", type=int)
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("languages", parents=[common], help="the language profiles")
    p.add_argument("--detect", metavar="TEXT")
    p.set_defaults(func=cmd_languages)

    p = sub.add_parser("tokenize", parents=[common], help="train a tokenizer")
    p.add_argument("--vocab", type=int, default=8192)
    p.add_argument("--min-freq", type=int, default=2)
    p.add_argument("--backend", default="auto", choices=["auto", "hf", "pure"])
    p.add_argument("--out")
    p.add_argument("--dir", action="append", help="train on files instead of the store")
    p.add_argument("--min-chars", type=int, default=80)
    p.add_argument("--address", help="only chunks under this region")
    p.set_defaults(func=cmd_tokenize)

    p = sub.add_parser("dataset", parents=[common], help="build binary shards")
    p.add_argument("--tokenizer")
    p.add_argument("--out")
    p.add_argument("--shard-tokens", type=int, default=2_000_000)
    p.add_argument("--val-fraction", type=float, default=0.02)
    p.add_argument("--dtype", default="auto", choices=["auto", "uint16", "uint32"],
                   help="token width; auto picks uint16 unless the vocabulary needs uint32")
    p.add_argument("--min-chars", type=int, default=80)
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("train", parents=[common], help="train the model")
    p.add_argument("--preset", default="tiny", choices=["tiny", "6m", "20m", "50m"])
    p.add_argument("--data-dir")
    p.add_argument("--tokenizer")
    p.add_argument("--out")
    p.add_argument("--steps", type=int, default=200)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--seq-len", type=int, default=256)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--eval-every", type=int, default=50)
    p.add_argument("--device", default="auto")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--resume")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("generate", parents=[common], help="sample from a checkpoint")
    p.add_argument("--checkpoint")
    p.add_argument("--tokenizer")
    p.add_argument("--prompt", default="")
    p.add_argument("--max-tokens", type=int, default=120)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--top-k", type=int, default=50)
    p.add_argument("--device", default="auto")
    p.add_argument("--seed", type=int, default=None)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("verify", parents=[common], help="quality control over the store")
    p.add_argument("--refetch", action="store_true",
                   help="re-fetch http sources and report drift, licence changes and 404s")
    p.add_argument("--apply", action="store_true",
                   help="write back: refresh retrieved timestamps and gone statuses")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("extract", parents=[common],
                       help="turn html tables and csv files into typed rows")
    p.add_argument("--dir", default="", help="a file, or a directory of html/csv")
    p.add_argument("--licence", default=None, help="SPDX id to record for these files")
    p.add_argument("--limit", type=int, default=0, help="files to process")
    p.add_argument("--mdy", action="store_true", help="month-first dates (US corpora)")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("tools", parents=[common],
                       help="dubforge, forge3d, cutforge: ready or not, and why")
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--action", default="", help="a whitelisted action for that tool")
    p.add_argument("--option", action="append", metavar="KEY=VALUE")
    p.add_argument("--timeout", type=float, default=900.0)
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("research", parents=[common],
                       help="search, fetch, extract, answer — with sources")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=5, help="pages to fetch")
    p.add_argument("--backend", default="auto")
    p.add_argument("--top-k", type=int, default=8)
    p.add_argument("--verify", action="store_true",
                   help="re-fetch each source and report whether it still says the same")
    p.add_argument("--licence", default=None,
                   help="licence to assert for the fetched pages; unknown is a refusal")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("board", parents=[common],
                       help="the agent blackboard: runs and entries")
    p.add_argument("--run", default="", help="show one run's entries")
    p.add_argument("--since", type=int, default=0, help="only entries after this seq")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--all", action="store_true", help="include quarantined entries")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("agents", parents=[common],
                       help="the 64 agent slots: live, dead, free, and their skills")
    p.add_argument("--run", default="")
    p.add_argument("--sweep", action="store_true",
                   help="mark agents dead whose heartbeat is too old")
    p.add_argument("--stale-after", type=float, default=90.0)
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("claims", parents=[common],
                       help="who holds which task, and for how much longer")
    p.add_argument("--run", default="")
    p.add_argument("--state", default="", choices=["", "claimed", "done", "failed"])
    p.add_argument("--reap", action="store_true", help="expire leases that ran out")
    p.set_defaults(func=cmd_claims)

    p = sub.add_parser("entities", parents=[common],
                       help="spellings of one name, across tables")
    p.add_argument("--build", action="store_true")
    p.add_argument("--threshold", type=float, default=90.0)
    p.set_defaults(func=cmd_entities)

    p = sub.add_parser("math", parents=[common],
                       help="search stored formulae as formulae")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-overlap", type=float, default=0.5)
    p.set_defaults(func=cmd_math)

    p = sub.add_parser("joins", parents=[common],
                       help="which columns across tables are the same entity")
    p.add_argument("--build", action="store_true", help="infer and store the edges")
    p.add_argument("--table", type=int, default=0, help="only this table's joins")
    p.add_argument("--min-overlap", type=float, default=0.5)
    p.add_argument("--min-shared", type=int, default=2)
    p.set_defaults(func=cmd_joins)

    p = sub.add_parser("derive", parents=[common],
                       help="add a computed column to an extracted table")
    p.add_argument("--table", type=int, default=0)
    p.add_argument("--spec", default="", help='JSON: {"derived": [{name, expr, guards}]}')
    p.add_argument("--name", default="")
    p.add_argument("--expr", default="")
    p.add_argument("--type", default="float")
    p.add_argument("--guard", action="append", help="expression that must be true")
    p.add_argument("--script", default="", help="a Python file defining transform(rows)")
    p.add_argument("--allow-scripts", action="store_true",
                   help="required to run --script: it executes code")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--memory-mb", type=int, default=512)
    p.add_argument("--isolation", default="auto",
                   choices=["auto", "namespace", "process"])
    p.set_defaults(func=cmd_derive)

    p = sub.add_parser("tables", parents=[common],
                       help="list extracted tables, or dump one table's rows")
    p.add_argument("--id", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("index", parents=[common],
                       help="build, inspect, export or drop the vector index")
    p.add_argument("index_action", nargs="?", default="stats",
                   choices=["build", "stats", "drop", "export"])
    p.add_argument("--engine", default="native", choices=["native", "sqlite-vec"],
                   help="which vector index to build")
    p.add_argument("--to", default="ragforge", help="export target")
    p.add_argument("--url", default="", help="RagForge base URL to POST batches to")
    p.add_argument("--dir", default="", help="write batch JSON files here instead")
    p.add_argument("--batch", type=int, default=2000)
    p.add_argument("--jsonl", default="",
                   help="write one line per chunk, for RagForge's own ingest")
    p.set_defaults(func=cmd_index)
    sub.add_parser("report", parents=[common], help="one page").set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config: {exc}")
        return 2
    if args.root:
        cfg["paths"]["root"] = args.root
    if args.verbose:
        cfg.setdefault("_verbose", True)

    try:
        rules = load_rules()
    except RuleError as exc:
        print(f"rule set is broken: {exc}")
        return 2
    try:
        registry = load_registry()
    except LanguageError as exc:
        print(f"language profiles are broken: {exc}")
        return 2

    try:
        return args.func(args, cfg, rules, registry)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
