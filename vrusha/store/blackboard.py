"""The blackboard: what the agents know, in the order they learned it.

`docs/AGENTS.md` §3 says agents exchange *references, not transcripts* — a worker
that reads a 12,000-token page writes forty tokens saying what it found and where
it lives. This is the store for those forty tokens.

Two decisions carry the whole design:

**Append-only, with a per-run sequence.** Nobody writes in place, so two agents can
never overwrite each other and no lock is needed to read. `seq` is dense and
monotonic *within a run*, which is what a delta read needs: "everything since 417"
is one indexed range scan, not a filter over the run's history.

**Entries are data, never instructions.** A board that 64 agents read is a board
that 64 agents can be *instructed* by, so the record is structured (kind, summary,
refs, payload) rather than a free-text message, and anything that looks like an
instruction aimed at a reader is quarantined instead of surfaced. See
`looks_like_instruction`.

The physical order is a global `row_id`; the logical order is `(run, seq)`. That
split is deliberate: one process can serve both, and a real log can replace this
table without changing a single agent.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_board_runs (
  run       TEXT PRIMARY KEY,
  next_seq  INTEGER NOT NULL DEFAULT 1,
  created   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS x_board (
  row_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run        TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  task       TEXT NOT NULL DEFAULT '',
  parent     TEXT NOT NULL DEFAULT '',
  agent      TEXT NOT NULL DEFAULT '',
  kind       TEXT NOT NULL DEFAULT 'finding',
  summary    TEXT NOT NULL DEFAULT '',
  refs       TEXT NOT NULL DEFAULT '[]',
  payload    TEXT NOT NULL DEFAULT '{}',
  provenance TEXT NOT NULL DEFAULT '{}',
  hash       TEXT NOT NULL,
  quarantined INTEGER NOT NULL DEFAULT 0,
  reason     TEXT NOT NULL DEFAULT '',
  ts         TEXT NOT NULL,
  UNIQUE (run, seq)
);

CREATE INDEX IF NOT EXISTS idx_board_run_seq ON x_board(run, seq);
CREATE INDEX IF NOT EXISTS idx_board_task    ON x_board(run, task, seq);
CREATE INDEX IF NOT EXISTS idx_board_hash    ON x_board(hash);
"""

#: Kinds an entry may declare. A closed set, because "kind" is what a reader
#: filters on and free-form kinds make filtering useless.
KINDS = frozenset({"finding", "artifact", "decision", "question", "progress",
                   "error", "report"})

#: Patterns that mean someone is talking to the reader rather than recording a
#: fact. Fetched pages are the obvious carrier: a page can contain "ignore your
#: previous instructions" and an agent that reads it as a finding will obey it.
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|forget)\b[^.]{0,40}\b(instruction|prompt|rule|above|previous)",
    re.IGNORECASE)
_ROLE_TAG = re.compile(r"^\s*(system|assistant|user)\s*:", re.IGNORECASE)
_TOOL_CALL = re.compile(r"\b(run|execute|call)\b[^.]{0,30}\b(tool|command|shell|script)",
                        re.IGNORECASE)


def looks_like_instruction(text: str) -> Optional[str]:
    """Why this text should not be shown to another agent as a finding.

    Returns the reason, or None. This is a first line, not a filter that can be
    trusted alone: the real defence is that entries are structured data and a
    prompt never treats them as instructions.
    """
    if not text:
        return None
    if _INSTRUCTION.search(text):
        return "contains an instruction aimed at the reader"
    if _ROLE_TAG.match(text):
        return "begins with a chat role tag"
    if _TOOL_CALL.search(text):
        return "asks the reader to run something"
    return None


@dataclass
class Entry:
    run: str
    seq: int
    task: str = ""
    parent: str = ""
    agent: str = ""
    kind: str = "finding"
    summary: str = ""
    refs: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    hash: str = ""
    quarantined: bool = False
    reason: str = ""
    ts: str = ""

    def to_dict(self) -> dict:
        return {"run": self.run, "seq": self.seq, "task": self.task,
                "parent": self.parent, "agent": self.agent, "kind": self.kind,
                "summary": self.summary, "refs": list(self.refs),
                "payload": dict(self.payload),
                "provenance": dict(self.provenance), "hash": self.hash,
                "quarantined": self.quarantined, "reason": self.reason,
                "ts": self.ts}

    @property
    def tokens(self) -> int:
        """A rough size, so a caller can budget without tokenising."""
        return max(1, len(self.summary) // 4)


def _digest(entry: Entry) -> str:
    """A content hash: the same entry twice is the same hash."""
    body = json.dumps(
        {"run": entry.run, "seq": entry.seq, "task": entry.task,
         "parent": entry.parent, "agent": entry.agent, "kind": entry.kind,
         "summary": entry.summary, "refs": list(entry.refs),
         "payload": entry.payload, "provenance": entry.provenance},
        sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Blackboard:
    """An append-only log of what the agents in one run have learned.

    It owns **its own connection** to the store's database file, and that is not
    a detail. The first version shared the store's connection and eight threads
    appending at once produced 127 entries out of 1,000 with
    `cannot open savepoint - SQL statements in progress`: a `sqlite3.Connection`
    is not safe for concurrent statements, whatever `check_same_thread` says.
    Owning the connection also means an append can commit without committing
    somebody else's half-finished harvest.
    """

    def __init__(self, store, *, path: Optional[str] = None):
        self.store = store
        target = path or getattr(store, "path", "") or ":memory:"
        self.conn: sqlite3.Connection = sqlite3.connect(
            target, timeout=15.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL so a reader never blocks a writer; busy_timeout so a second
        # process waits for the lock instead of raising "database is locked"
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=15000")
        # SQLite has one writer at a time. Serialising inside the process is the
        # model, not a workaround: the alternative is threads racing for the
        # same savepoint name.
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- writing ----------------------------------------------------------

    def open_run(self, run: str) -> str:
        """Create the run's counter if it is new. Idempotent."""
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO x_board_runs (run, next_seq, created) "
                "VALUES (?, 1, ?)", (run, _now()))
            self.conn.commit()
        return run

    def append(self, run: str, *, task: str = "", agent: str = "", kind: str = "finding",
               summary: str = "", refs: Iterable[str] = (), payload: Optional[dict] = None,
               provenance: Optional[dict] = None, parent: str = "") -> Entry:
        """Add one entry, allocating the run's next sequence number.

        The counter and the row are written in one savepoint. A savepoint rather
        than `BEGIN IMMEDIATE` because the Python driver has usually already
        opened a transaction on this connection, and a bare `BEGIN` fails inside
        one. The atomicity that matters is `UPDATE … RETURNING`: the sequence is
        allocated and read in a single statement, so two agents appending at the
        same instant get consecutive numbers, never the same one.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; allowed: "
                             f"{', '.join(sorted(KINDS))}")
        # `refs="table:1"` would otherwise become seven single-character refs:
        # a string is iterable, and the failure is silent
        if isinstance(refs, str):
            refs = [refs]
        refs = [str(ref) for ref in refs if str(ref).strip()]
        for name, value in (("payload", payload), ("provenance", provenance)):
            if value is not None and not isinstance(value, dict):
                raise TypeError(f"{name} must be a dict, not {type(value).__name__}")
        reason = looks_like_instruction(summary)
        entry = Entry(run=run, seq=0, task=task, parent=parent, agent=agent,
                      kind=kind, summary=summary, refs=list(refs),
                      payload=dict(payload or {}),
                      provenance=dict(provenance or {}),
                      quarantined=bool(reason), reason=reason or "",
                      ts=_now())

        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO x_board_runs (run, next_seq, created) "
                "VALUES (?, 1, ?)", (run, _now()))
            try:
                row = self.conn.execute(
                    "UPDATE x_board_runs SET next_seq = next_seq + 1 "
                    "WHERE run = ? RETURNING next_seq - 1", (run,)).fetchone()
                if row is None:                 # the run vanished between calls
                    raise RuntimeError(f"run {run!r} disappeared while appending")
                entry.seq = int(row[0])
                entry.hash = _digest(entry)
                self.conn.execute(
                    "INSERT INTO x_board (run, seq, task, parent, agent, kind, summary, "
                    "refs, payload, provenance, hash, quarantined, reason, ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entry.run, entry.seq, entry.task, entry.parent, entry.agent,
                     entry.kind, entry.summary,
                     json.dumps(entry.refs, ensure_ascii=False),
                     json.dumps(entry.payload, ensure_ascii=False, default=str),
                     json.dumps(entry.provenance, ensure_ascii=False, default=str),
                     entry.hash, int(entry.quarantined), entry.reason, entry.ts))
                # committed per append: the board is shared between processes,
                # so an entry that is only in this transaction does not exist
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return entry

    # -- reading ----------------------------------------------------------

    def since(self, run: str, seq: int = 0, *, task: Optional[str] = None,
              subtree: bool = False, limit: int = 500,
              kinds: Optional[Iterable[str]] = None,
              include_quarantined: bool = False) -> List[Entry]:
        """Everything after `seq`, oldest first — the delta a reader asks for."""
        where = ["run = ?", "seq > ?"]
        params: List[Any] = [run, int(seq)]
        if task:
            where.append("(task = ? OR parent = ?)" if subtree else "task = ?")
            params.extend([task, task] if subtree else [task])
        if kinds:
            names = list(kinds)
            where.append("kind IN (" + ",".join("?" * len(names)) + ")")
            params.extend(names)
        if not include_quarantined:
            where.append("quarantined = 0")
        params.append(int(limit))
        rows = self.conn.execute(
            f"SELECT * FROM x_board WHERE {' AND '.join(where)} "
            f"ORDER BY seq LIMIT ?", params).fetchall()
        return [self._row(row) for row in rows]

    def get(self, entry_hash: str) -> Optional[Entry]:
        row = self.conn.execute(
            "SELECT * FROM x_board WHERE hash = ? LIMIT 1", (entry_hash,)).fetchone()
        return self._row(row) if row else None

    def head(self, run: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM x_board WHERE run = ?",
            (run,)).fetchone()
        return int(row[0]) if row else 0

    def runs(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT r.run, r.created, COUNT(b.row_id) AS entries, "
            "COALESCE(MAX(b.seq), 0) AS head, "
            "SUM(CASE WHEN b.quarantined = 1 THEN 1 ELSE 0 END) AS quarantined "
            "FROM x_board_runs r LEFT JOIN x_board b ON b.run = r.run "
            "GROUP BY r.run ORDER BY r.created DESC").fetchall()
        return [{"run": row["run"], "created": row["created"],
                 "entries": int(row["entries"]), "head": int(row["head"]),
                 "quarantined": int(row["quarantined"] or 0)} for row in rows]

    def replay(self, run: str) -> Dict[str, Any]:
        """Fold the log into state: the last finding per task, and every artifact.

        Replay is how the board proves it is a log and not a cache — the same
        entries must always fold to the same state.
        """
        state: Dict[str, Any] = {"findings": {}, "artifacts": [], "errors": [],
                                 "decisions": [], "questions": []}
        # paged, not one big read: a fixed limit would silently fold half a log
        # and produce a state that looks complete
        cursor = 0
        while True:
            batch = self.since(run, cursor, limit=1000)
            if not batch:
                break
            for entry in batch:
                self._fold(state, entry)
            cursor = batch[-1].seq
        return state

    @staticmethod
    def _fold(state: Dict[str, Any], entry: "Entry") -> None:
        """One entry into the running state. Separated so replay can page."""
        if entry.kind == "finding":
            state["findings"][entry.task or entry.agent or "?"] = entry.summary
        elif entry.kind == "artifact":
            state["artifacts"].extend(entry.refs)
        elif entry.kind == "error":
            state["errors"].append(entry.summary)
        elif entry.kind == "decision":
            state["decisions"].append(entry.summary)
        elif entry.kind == "question":
            state["questions"].append(entry.summary)

    # -- housekeeping -----------------------------------------------------

    @staticmethod
    def _row(row) -> Entry:
        def _json(text, fallback):
            try:
                return json.loads(text) if text else fallback
            except (TypeError, ValueError):
                return fallback

        return Entry(run=row["run"], seq=int(row["seq"]), task=row["task"],
                     parent=row["parent"], agent=row["agent"], kind=row["kind"],
                     summary=row["summary"],
                     refs=_json(row["refs"], []),
                     payload=_json(row["payload"], {}),
                     provenance=_json(row["provenance"], {}),
                     hash=row["hash"], quarantined=bool(row["quarantined"]),
                     reason=row["reason"], ts=row["ts"])
