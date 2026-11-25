"""Verify: quality control over the store itself, not over its inputs.

The gates decide what goes in. This decides whether what is in is coherent:
every chunk addressed, no two chunks sharing an address, ordinals contiguous,
every vector the right width, the full-text index in step with the table.

A failure here is not a warning. It means a query can return a chunk whose
address points somewhere else, and every distance you compute from that
address is a lie.
"""
from __future__ import annotations

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
