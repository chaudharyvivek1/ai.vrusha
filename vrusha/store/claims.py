"""Claims: one agent per task, and a lease so a dead agent does not hold it.

Sixty-four agents, twelve tasks: the thing that must be true is that exactly
twelve of them start work and the other fifty-two do something else. Without a
claim, two agents extract the same table, both write findings, and the board ends
up with two answers to one question — which is worse than one answer, because
nobody can tell which is right.

The mechanism is deliberately small: one row per `(run, task)`, a lease deadline,
and a single `INSERT … ON CONFLICT … WHERE` that either wins the task or does not.
That statement is the whole concurrency story — there is no read-then-write gap
for two agents to slip through, because SQLite applies the conflict clause
atomically.

A lease, not a lock. An agent that dies holding a claim must not stop the run, so
every claim expires and the task becomes claimable again. `attempts` counts how
many times that has happened, which is how a caller notices a task that keeps
killing its worker.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_claims (
  run        TEXT NOT NULL,
  task       TEXT NOT NULL,
  agent      TEXT NOT NULL DEFAULT '',
  state      TEXT NOT NULL DEFAULT 'claimed',   -- claimed | done | failed
  attempts   INTEGER NOT NULL DEFAULT 1,
  claimed_at REAL NOT NULL,
  lease_until REAL NOT NULL,
  updated_at REAL NOT NULL,
  note       TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run, task)
);
CREATE INDEX IF NOT EXISTS idx_claims_lease ON x_claims(run, lease_until);
CREATE INDEX IF NOT EXISTS idx_claims_state ON x_claims(run, state);
"""

STATES = frozenset({"claimed", "done", "failed"})

#: A claim that expires in under a second cannot be renewed in time, and one
#: that never expires is a lock with extra steps.
DEFAULT_TTL = 60.0


@dataclass
class Claim:
    run: str
    task: str
    agent: str = ""
    state: str = ""
    attempts: int = 0
    lease_until: float = 0.0
    granted: bool = False
    reason: str = ""

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.lease_until - time.time())

    def to_dict(self) -> dict:
        return {"run": self.run, "task": self.task, "agent": self.agent,
                "state": self.state, "attempts": self.attempts,
                "lease_until": round(self.lease_until, 3),
                "seconds_left": round(self.seconds_left, 1),
                "granted": self.granted, "reason": self.reason}


class Claims:
    """Task claims for one blackboard.

    Every method holds the board's lock for its **whole** body, reads included.
    The first version locked only the write and then read the row back outside
    the lock; sixty-four threads produced `sqlite3.InterfaceError: bad parameter
    or other API misuse`, because two threads were using one connection at once.
    A connection is not safe for concurrent statements — not even one writer and
    one reader — so the lock covers the statement *and* the read that follows it.
    """

    def __init__(self, board):
        self.board = board
        self.conn = board.conn
        self._lock = board._lock
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # -- taking work ------------------------------------------------------

    def claim(self, run: str, task: str, agent: str, *, ttl: float = DEFAULT_TTL,
              now: Optional[float] = None) -> Claim:
        """Win the task, or say who holds it.

        One statement does the whole thing: insert if absent, or take over if the
        lease has expired or the previous attempt failed. A task that is `done`
        is never re-issued, and a live lease is never broken.
        """
        if not task:
            raise ValueError("a claim needs a task")
        if ttl <= 0:
            raise ValueError("a lease with no duration is a lock nobody releases")
        moment = time.time() if now is None else float(now)
        deadline = moment + float(ttl)

        with self._lock:
            try:
                row = self.conn.execute(
                    """INSERT INTO x_claims
                         (run, task, agent, state, attempts, claimed_at,
                          lease_until, updated_at, note)
                       VALUES (?, ?, ?, 'claimed', 1, ?, ?, ?, '')
                       ON CONFLICT(run, task) DO UPDATE SET
                         agent       = excluded.agent,
                         state       = 'claimed',
                         attempts    = x_claims.attempts + 1,
                         claimed_at  = excluded.claimed_at,
                         lease_until = excluded.lease_until,
                         updated_at  = excluded.updated_at
                       WHERE x_claims.state != 'done'
                         AND (x_claims.lease_until <= ? OR x_claims.state = 'failed')
                       RETURNING *""",
                    (run, task, agent, moment, deadline, moment, moment)
                ).fetchone()
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

            if row is not None:
                return Claim(run=run, task=task, agent=agent, state="claimed",
                             attempts=int(row["attempts"]),
                             lease_until=float(row["lease_until"]), granted=True,
                             reason="claimed")

            held = self._row(run, task)
            if held is None:                # vanished between the two statements
                return Claim(run=run, task=task, agent=agent, granted=False,
                             reason="the task disappeared while claiming")
            if held["state"] == "done":
                return Claim(run=run, task=task, agent=held["agent"], state="done",
                             attempts=int(held["attempts"]),
                             lease_until=float(held["lease_until"]), granted=False,
                             reason=f"already done by {held['agent']}")
            return Claim(run=run, task=task, agent=held["agent"], state="claimed",
                         attempts=int(held["attempts"]),
                         lease_until=float(held["lease_until"]), granted=False,
                         reason=f"held by {held['agent']} for another "
                                f"{max(0.0, float(held['lease_until']) - moment):.0f}s")

    # -- keeping it, or giving it back ------------------------------------

    def renew(self, run: str, task: str, agent: str, *,
              ttl: float = DEFAULT_TTL, now: Optional[float] = None) -> Claim:
        """Extend a lease you hold. Someone else's lease is not yours to extend."""
        moment = time.time() if now is None else float(now)
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_claims SET lease_until = ?, updated_at = ? "
                "WHERE run = ? AND task = ? AND agent = ? AND state = 'claimed' "
                "AND lease_until > ?",
                (moment + float(ttl), moment, run, task, agent, moment))
            self.conn.commit()
            changed = cursor.rowcount
            held = self._row(run, task)
            if not changed:
                return Claim(run=run, task=task, agent=agent, granted=False,
                             state=(held["state"] if held else ""),
                             reason="not yours to renew" if held else "no such claim")
            return Claim(run=run, task=task, agent=agent, state="claimed",
                         attempts=int(held["attempts"]),
                         lease_until=float(held["lease_until"]), granted=True,
                         reason="renewed")

    def release(self, run: str, task: str, agent: str) -> bool:
        """Give the task back, immediately claimable by anyone."""
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_claims SET state = 'failed', lease_until = 0, "
                "updated_at = ? WHERE run = ? AND task = ? AND agent = ? "
                "AND state = 'claimed'", (time.time(), run, task, agent))
            self.conn.commit()
            return cursor.rowcount > 0

    def complete(self, run: str, task: str, agent: str, *, status: str = "done",
                 note: str = "") -> bool:
        """Mark the task finished. Only the holder can, and `done` is final."""
        if status not in ("done", "failed"):
            raise ValueError(f"status must be done or failed, not {status!r}")
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_claims SET state = ?, note = ?, updated_at = ?, "
                "lease_until = 0 WHERE run = ? AND task = ? AND agent = ? "
                "AND state = 'claimed'",
                (status, note, time.time(), run, task, agent))
            self.conn.commit()
            return cursor.rowcount > 0

    # -- housekeeping -----------------------------------------------------

    def reap(self, run: str, *, now: Optional[float] = None) -> int:
        """Expire leases that ran out, so the tasks become claimable again.

        Not required for correctness — `claim` already takes over an expired
        lease — but a reaper makes the state readable and lets a supervisor see
        which tasks keep dying.
        """
        moment = time.time() if now is None else float(now)
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_claims SET state = 'failed', updated_at = ? "
                "WHERE run = ? AND state = 'claimed' AND lease_until <= ?",
                (moment, run, moment))
            self.conn.commit()
            return cursor.rowcount

    def summary(self, run: str) -> Dict[str, int]:
        with self._lock:
            return self._summary(run)

    def _summary(self, run: str) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM x_claims WHERE run = ? "
            "GROUP BY state", (run,)).fetchall()
        counts = {row["state"]: int(row["n"]) for row in rows}
        return {"claimed": counts.get("claimed", 0),
                "done": counts.get("done", 0),
                "failed": counts.get("failed", 0),
                "total": sum(counts.values())}

    def list(self, run: str, *, state: Optional[str] = None) -> List[dict]:
        with self._lock:
            return self._list(run, state=state)

    def _list(self, run: str, *, state: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM x_claims WHERE run = ?"
        params: List = [run]
        if state:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY task"
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def _row(self, run: str, task: str):
        return self.conn.execute(
            "SELECT * FROM x_claims WHERE run = ? AND task = ?",
            (run, task)).fetchone()
