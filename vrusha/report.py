"""Report: one page you can read in the morning.

Numbers first, then the shape of the address tree, then what the passes did,
then whether a tokenizer and a model exist. No adjectives.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .config import resolve
from .store.address import DOMAINS, Address, spread
from .store.db import Store


def _bar(count: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = max(1, round(width * count / total))
    return "#" * filled + "." * (width - filled)


def build_report(store: Store, cfg: dict) -> Dict:
    stats = store.stats()
    addresses = [address for _chunk_id, address in store.addresses()]
    address_summary = spread(addresses)

    domains = []
    total_domain = sum((stats.get("domains") or {}).values())
    for key, count in (stats.get("domains") or {}).items():
        name = DOMAINS.get(int(key), f"domain-{key}") if str(key).isdigit() else str(key)
        domains.append({"domain": name, "chunks": count,
                        "bar": _bar(count, total_domain)})

    top_domains = [
        {"domain": row[0], "documents": row[1]}
        for row in store.conn.execute(
            "SELECT domain, COUNT(*) FROM documents GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 8"
        ).fetchall()
    ]

    passes = store.pass_history(limit=20)
    tokenizer_path = os.path.join(resolve(cfg, "tokens"), "tokenizer.json")
    tokenizer = None
    if os.path.exists(tokenizer_path):
        meta_path = tokenizer_path + ".meta.json"
        tokenizer = {"path": tokenizer_path}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                tokenizer.update(json.load(fh))

    models_dir = resolve(cfg, "models")
    checkpoints = []
    if os.path.isdir(models_dir):
        for root, _dirs, files in os.walk(models_dir):
            for name in files:
                if name.endswith(".pt"):
                    path = os.path.join(root, name)
                    checkpoints.append({"path": path,
                                        "mb": round(os.path.getsize(path) / 1024 ** 2, 1)})

    dataset_meta = None
    dataset_path = os.path.join(resolve(cfg, "tokens"), "dataset", "meta.json")
    if os.path.exists(dataset_path):
        with open(dataset_path, "r", encoding="utf-8") as fh:
            dataset_meta = json.load(fh)

    return {
        "store": stats,
        "addresses": address_summary,
        "domains": domains,
        "top_domains": top_domains,
        "passes": passes,
        "tokenizer": tokenizer,
        "dataset": dataset_meta,
        "checkpoints": sorted(checkpoints, key=lambda c: c["path"])[:5],
    }


def format_report(report: Dict) -> str:
    store = report["store"]
    lines: List[str] = []
    lines.append("vrusha report")
    lines.append("=" * 60)
    lines.append(f"  documents        {store['documents']:,}")
    lines.append(f"  chunks           {store['chunks']:,} active, "
                 f"{store['chunks_total']:,} stored")
    lines.append(f"  addressed        {store['addressed']:,}")
    lines.append(f"  edges            {store['edges']:,}")
    lines.append(f"  address changes  {store['address_log']:,}")
    if store.get("vector_dim"):
        lines.append(f"  vector width     {store['vector_dim']}")

    if report["domains"]:
        lines.append("")
        lines.append("  by domain")
        for row in report["domains"]:
            lines.append(f"    {row['domain']:<10} {row['chunks']:>6,}  {row['bar']}")

    summary = report["addresses"]
    if summary.get("count"):
        lines.append("")
        lines.append("  address tree")
        lines.append(f"    regions          {summary['clusters']}")
        lines.append(f"    deepest          {summary['deepest']}")
        widest = summary["widest_cluster"]
        lines.append(f"    widest cluster   {widest[0]} ({widest[1]} chunks)")

    if report["top_domains"]:
        lines.append("")
        lines.append("  sources")
        for row in report["top_domains"]:
            lines.append(f"    {row['domain']:<34} {row['documents']:>5}")

    if report["passes"]:
        lines.append("")
        lines.append("  last passes")
        for entry in reversed(report["passes"][:8]):
            stats = ", ".join(f"{k}={v}" for k, v in entry["stats"].items()
                              if k not in ("pass", "level"))
            lines.append(f"    {entry['ts']}  {entry['pass_id']:<22} {stats}")

    lines.append("")
    lines.append("  pipeline")
    tokenizer = report["tokenizer"]
    if tokenizer:
        lines.append(f"    tokenizer        {tokenizer.get('vocab_size', '?'):,} tokens "
                     f"({tokenizer.get('backend', '?')})")
    else:
        lines.append("    tokenizer        not trained — run `vrusha tokenize`")
    dataset = report["dataset"]
    if dataset:
        lines.append(f"    dataset          {dataset.get('total_tokens', 0):,} tokens, "
                     f"{len(dataset.get('shards', []))} shard(s), {dataset.get('dtype')}")
    else:
        lines.append("    dataset          not built — run `vrusha dataset`")
    if report["checkpoints"]:
        for checkpoint in report["checkpoints"]:
            lines.append(f"    checkpoint       {checkpoint['path']} ({checkpoint['mb']} MB)")
    else:
        lines.append("    checkpoint       none — run `vrusha train`")
    return "\n".join(lines)
