"""Verify: quality control over the store itself, not over its inputs.

The gates decide what goes in. This decides whether what is in is coherent:
every chunk addressed, no two chunks sharing an address, ordinals contiguous,
every vector the right width, the full-text index in step with the table.

A failure here is not a warning. It means a query can return a chunk whose
address points somewhere else, and every distance you compute from that
address is a lie.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .store.address import Address
from .store.db import Store


def _result(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run_verify(store: Store) -> List[dict]:
    results: List[dict] = []
    conn = store.conn

    total_active = store.chunk_count(active_only=True)
    results.append(_result("chunks", total_active > 0,
                           f"{total_active} active chunks"))

    unaddressed = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE active=1 AND address IS NULL").fetchone()[0]
    results.append(_result("every active chunk has an address", unaddressed == 0,
                           f"{unaddressed} without an address"))

    duplicates = conn.execute(
        "SELECT address, COUNT(*) AS n FROM chunks WHERE active=1 AND address IS NOT NULL "
        "GROUP BY address HAVING n > 1").fetchall()
    results.append(_result("addresses are unique", not duplicates,
                           f"{len(duplicates)} address(es) shared by more than one chunk"))

    mismatched = 0
    for row in conn.execute(
            "SELECT address, addr_domain, addr_family, addr_kind, addr_cluster, addr_ordinal "
            "FROM chunks WHERE active=1 AND address IS NOT NULL").fetchall():
        address = Address.parse(row[0])
        if address.segments != (row[1], row[2], row[3], row[4], row[5]):
            mismatched += 1
    results.append(_result("address string matches its columns", mismatched == 0,
                           f"{mismatched} row(s) disagree"))

    gaps = 0
    regions: Dict[tuple, List[int]] = {}
    for row in conn.execute(
            "SELECT addr_domain, addr_family, addr_kind, addr_cluster, addr_ordinal "
            "FROM chunks WHERE active=1 AND address IS NOT NULL").fetchall():
        regions.setdefault((row[0], row[1], row[2], row[3]), []).append(row[4])
    for key, ordinals in regions.items():
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            gaps += 1
    results.append(_result("ordinals are contiguous per cluster", gaps == 0,
                           f"{gaps} region(s) with a gap or a duplicate"))

    without_vector = conn.execute(
        "SELECT COUNT(*) FROM chunks c LEFT JOIN vectors v ON v.chunk_id = c.chunk_id "
        "WHERE c.active=1 AND v.chunk_id IS NULL").fetchone()[0]
    results.append(_result("every active chunk has a vector", without_vector == 0,
                           f"{without_vector} without"))

    dims = {row[0] for row in conn.execute("SELECT DISTINCT dim FROM vectors").fetchall()}
    results.append(_result("all vectors share one width", len(dims) <= 1,
                           f"widths present: {sorted(dims) or 'none'}"))

    recorded = store.embed_dim()
    consistent = recorded is None or dims <= {recorded}
    results.append(_result(
        "vectors match the recorded embedding width", consistent,
        f"recorded {recorded}, present {sorted(dims) or 'none'}"
        + ("" if consistent else "; re-embed with `store.clear_vectors()` then re-ingest")))

    live_clusters = {
        (r[0], r[1], r[2], r[3]) for r in conn.execute(
            "SELECT DISTINCT addr_domain, addr_family, addr_kind, addr_cluster "
            "FROM chunks WHERE active=1 AND addr_cluster IS NOT NULL").fetchall()
    }
    stored_clusters = {
        (r[0], r[1], r[2], r[3]) for r in conn.execute(
            "SELECT domain, family, kind, cluster FROM centroids").fetchall()
    }
    stale_centroids = stored_clusters - live_clusters
    results.append(_result(
        "centroids describe the live clustering", not stale_centroids,
        f"{len(stale_centroids)} centroid(s) for clusters that no longer exist"
        + ("" if not stale_centroids else "; run `vrusha passes --only analyse.centroid`")))

    merged_ok = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE active=0 AND merged_into IS NOT NULL "
        "AND merged_into != 0 AND merged_into NOT IN (SELECT chunk_id FROM chunks)"
    ).fetchone()[0]
    results.append(_result("merged_into points at a real chunk", merged_ok == 0,
                           f"{merged_ok} dangling merge reference(s)"))

    orphans = conn.execute(
        "SELECT COUNT(*) FROM chunks c LEFT JOIN documents d ON d.doc_id = c.doc_id "
        "WHERE d.doc_id IS NULL").fetchone()[0]
    results.append(_result("no chunk without a document", orphans == 0, f"{orphans} orphan(s)"))

    fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    results.append(_result("full-text index is in step", fts_count == store.chunk_count(active_only=False),
                           f"{fts_count} indexed, {store.chunk_count(active_only=False)} in the table"))

    blocked_licences = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE lower(coalesce(licence,'unknown')) IN "
        "('agpl-3.0','gpl-3.0','gpl-2.0','lgpl-3.0','lgpl-2.1','sspl-1.0','busl-1.1','proprietary')"
    ).fetchone()[0]
    results.append(_result("no blocked licence in the store", blocked_licences == 0,
                           f"{blocked_licences} document(s)"))

    return results


def _shingle_set(text: str, size: int = 5) -> set:
    words = text.split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def refetch(store, cfg: dict, *, limit: int = 0, apply: bool = False,
            verbose: bool = True) -> List[dict]:
    """Is what we stored still true?

    `run_verify` checks the store against itself. This checks it against the
    web: the URL still resolves, the licence still says what it said, and the
    text has not drifted away from the chunks we embedded.

    Drift is measured on word shingles, not on length, because a page that
    gained a cookie banner is not a page that changed its prices. A dry run by
    default: `apply=True` updates `retrieved` and records a gone page's status.
    """
    from .harvest.fetch import Fetcher
    from .harvest.gates import detect_licence

    query = ("SELECT doc_id, url, title, text, licence, retrieved FROM documents "
             "WHERE url LIKE 'http%' ORDER BY doc_id")
    if limit:
        query += f" LIMIT {int(limit)}"
    documents = store.conn.execute(query).fetchall()
    allow_private = bool((cfg.get("http", {}) or {}).get("allow_private", False))
    fetcher = Fetcher(cfg, allow_private=allow_private)
    report: List[dict] = []

    for document in documents:
        url = document["url"]
        try:
            fetched = fetcher.get(url)
        except Exception as exc:                       # noqa: BLE001
            report.append({"doc_id": int(document["doc_id"]), "url": url,
                           "state": "error", "detail": str(exc)[:200]})
            continue

        if not fetched.ok:
            # a 404 or 410 arrives as an error string, not a status: read the
            # code back out of it so the operator sees 410, not 0
            code = int(fetched.status or 0)
            if not code:
                found = re.search(r"HTTP (\d{3})", getattr(fetched, "error", "") or "")
                code = int(found.group(1)) if found else 0
            report.append({"doc_id": int(document["doc_id"]), "url": url,
                           "state": "gone", "status": code,
                           "action": "stop citing it"})
            if apply:
                store.conn.execute("UPDATE documents SET status = ? WHERE doc_id = ?",
                                   (code, int(document["doc_id"])))
            continue

        before = _shingle_set(document["text"] or "")
        after = _shingle_set(fetched.text or "")
        overlap = len(before & after) / max(1, len(before | after))
        licence_now = detect_licence(fetched.text or "")
        licence_before = document["licence"]

        if overlap >= 0.95:
            state, action = "unchanged", "nothing"
        elif overlap >= 0.70:
            state, action = "drifted", "refresh retrieved, keep chunks"
        else:
            state, action = "rewritten", "re-chunk and re-embed"

        if licence_before and licence_now and licence_before != licence_now:
            state, action = "licence-changed", "re-run filter.policy"

        report.append({"doc_id": int(document["doc_id"]), "url": url, "state": state,
                       "status": fetched.status, "overlap": round(overlap, 3),
                       "licence_before": licence_before, "licence_now": licence_now,
                       "action": action})
        if verbose:
            print(f"  {state:<16} {overlap:>5.1%}  {url[:80]}")
        if apply and state == "unchanged":
            store.conn.execute(
                "UPDATE documents SET retrieved = ? WHERE doc_id = ?",
                (fetched.retrieved, int(document["doc_id"])))

    if apply:
        store.commit()
    return report


def format_refetch(report: List[dict]) -> str:
    if not report:
        return "no http documents to re-fetch"
    counts: Dict[str, int] = {}
    for entry in report:
        counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    lines = [f"re-fetched {len(report)} document(s)"]
    for state, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {state:<16} {count}")
    for entry in report:
        if entry["state"] != "unchanged":
            lines.append(f"  ! {entry['state']:<14} {entry.get('overlap', '-')}  "
                         f"{entry['url'][:90]}")
    return "\n".join(lines)


def summarise(results: List[dict]) -> bool:
    return all(r["ok"] for r in results)


def format_results(results: List[dict]) -> str:
    lines = ["verify", ""]
    for result in results:
        mark = "ok  " if result["ok"] else "FAIL"
        lines.append(f"  [{mark}] {result['name']:<38} {result['detail']}")
    failed = [r for r in results if not r["ok"]]
    lines.append("")
    lines.append("all checks passed" if not failed else f"{len(failed)} check(s) failed")
    return "\n".join(lines)
