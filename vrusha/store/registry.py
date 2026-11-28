"""The registry: sixty-four slots, a heartbeat, and an honest live/dead count.

The board records what was learned. The claims record who is doing what. Neither
knows whether the agent that took a claim is still alive — a claim with a lease
tells you when it expires, not whether anyone is behind it. This is the third
piece: a fixed set of slots, one per concurrent agent, each with a heartbeat.

The cap is the point. `docs/AGENTS.md` §4 says 64 live agents, and a limit that
lives in a document is a limit that does not exist. Here the 65th registration
is **refused**, with the reason, because a system without a cap does not fail
gracefully — it fails by spending.

Slots are pre-created per run rather than counted on demand. That makes
allocation one atomic `UPDATE … WHERE slot = (SELECT MIN(…) …) RETURNING *`,
which either takes the lowest free slot or returns nothing at all. Counting
"how many are live" and then inserting is the read-then-write gap that lets two
agents take the same slot.

Every method holds the board's lock for its whole body — reads included. That is
not caution: `docs/BOARD.md` records two bugs where a write was serialised and
the read that followed it was not.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

#: The hard cap from `docs/AGENTS.md` §4. Not a default: a limit.
MAX_AGENTS = 64

#: A heartbeat older than this means the agent is gone. Two missed beats, so one
#: slow call does not kill a live agent's slot.
DEFAULT_STALE = 90.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_agents (
  run          TEXT NOT NULL,
  slot         INTEGER NOT NULL,
  agent        TEXT NOT NULL DEFAULT '',
  state        TEXT NOT NULL DEFAULT 'free',   -- free | live | dead
  skill        TEXT NOT NULL DEFAULT '',
  level        TEXT NOT NULL DEFAULT '',       -- L1 | L2 | L3 | L4 | L4r
  host         TEXT NOT NULL DEFAULT '',
  pid          INTEGER NOT NULL DEFAULT 0,
  started_at   REAL NOT NULL DEFAULT 0,
  heartbeat_at REAL NOT NULL DEFAULT 0,
  note         TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run, slot)
);
CREATE INDEX IF NOT EXISTS idx_agents_state ON x_agents(run, state);
CREATE INDEX IF NOT EXISTS idx_agents_agent ON x_agents(run, agent);
"""

STATES = frozenset({"free", "live", "dead"})


@dataclass
class Slot:
    run: str
    slot: int
    agent: str = ""
    state: str = "free"
    skill: str = ""
    level: str = ""
    host: str = ""
    pid: int = 0
    started_at: float = 0.0
    heartbeat_at: float = 0.0
    note: str = ""
    registered: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {"run": self.run, "slot": self.slot, "agent": self.agent,
                "state": self.state, "skill": self.skill, "level": self.level,
                "host": self.host, "pid": self.pid,
                "age": round(max(0.0, time.time() - self.heartbeat_at), 1)
                if self.heartbeat_at else None,
                "registered": self.registered, "reason": self.reason}


class Registry:
    """The 64 slots of one run."""

    def __init__(self, board, *, capacity: int = MAX_AGENTS):
        self.board = board
        self.conn = board.conn
        self._lock = board._lock
        self.capacity = int(capacity)
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # -- the slots --------------------------------------------------------

    def open_run(self, run: str) -> int:
        """Create the run's slots if they are not there. Idempotent."""
        with self._lock:
            rows = [(run, slot) for slot in range(1, self.capacity + 1)]
            self.conn.executemany(
                "INSERT OR IGNORE INTO x_agents (run, slot, state) "
                "VALUES (?, ?, 'free')", rows)
            self.conn.commit()
            return self.capacity

    def register(self, run: str, agent: str, *, skill: str = "", level: str = "",
                 note: str = "", now: Optional[float] = None,
                 host: Optional[str] = None, pid: Optional[int] = None) -> Slot:
        """Take the lowest free slot, or refuse because all 64 are live."""
        if not agent:
            raise ValueError("an agent needs a name")
        moment = time.time() if now is None else float(now)
        self.open_run(run)

        with self._lock:
            # an agent that is already registered keeps its slot: re-registering
            # is a heartbeat, not a second identity
            existing = self.conn.execute(
                "SELECT * FROM x_agents WHERE run = ? AND agent = ? "
                "AND state = 'live'", (run, agent)).fetchone()
            if existing is not None:
                self.conn.execute(
                    "UPDATE x_agents SET heartbeat_at = ?, skill = ?, level = ?, "
                    "note = ? WHERE run = ? AND slot = ?",
                    (moment, skill or existing["skill"], level or existing["level"],
                     note or existing["note"], run, int(existing["slot"])))
                self.conn.commit()
                return self._slot(self.conn.execute(
                    "SELECT * FROM x_agents WHERE run = ? AND slot = ?",
                    (run, int(existing["slot"]))).fetchone(), registered=True,
                    reason="already registered")

            try:
                row = self.conn.execute(
                    """UPDATE x_agents
                          SET agent = ?, state = 'live', skill = ?, level = ?,
                              host = ?, pid = ?, started_at = ?, heartbeat_at = ?,
                              note = ''
                        WHERE run = ?
                          AND slot = (SELECT MIN(slot) FROM x_agents
                                       WHERE run = ? AND state IN ('free', 'dead'))
                        RETURNING *""",
                    (agent, skill, level, host if host is not None else _host(),
                     int(pid if pid is not None else os.getpid()), moment, moment,
                     run, run)).fetchone()
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

        if row is None:
            live = self.conn.execute(
                "SELECT COUNT(*) FROM x_agents WHERE run = ? AND state = 'live'",
                (run,)).fetchone()[0]
            return Slot(run=run, slot=0, agent=agent, state="refused",
                        registered=False,
                        reason=f"all {self.capacity} slots are live "
                               f"({live} live); the cap is a limit, not a default")
        return self._slot(row, registered=True, reason="registered")

    # -- keeping it -------------------------------------------------------

    def heartbeat(self, run: str, agent: str, *, now: Optional[float] = None) -> bool:
        """Say you are still here. A dead agent's slot is not revived by this."""
        moment = time.time() if now is None else float(now)
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_agents SET heartbeat_at = ? "
                "WHERE run = ? AND agent = ? AND state = 'live'",
                (moment, run, agent))
            self.conn.commit()
            return cursor.rowcount > 0

    def release(self, run: str, agent: str) -> bool:
        """Give the slot back. The agent's name is cleared, so it can be reused."""
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE x_agents SET state = 'free', agent = '', heartbeat_at = 0, "
                "started_at = 0 WHERE run = ? AND agent = ? "
                "AND state IN ('live', 'dead')",
                (run, agent))
            self.conn.commit()
            return cursor.rowcount > 0

    def sweep(self, run: str, *, stale_after: float = DEFAULT_STALE,
              now: Optional[float] = None) -> List[str]:
        """Mark agents dead whose heartbeat is older than `stale_after`."""
        moment = time.time() if now is None else float(now)
        cutoff = moment - float(stale_after)
        with self._lock:
            rows = self.conn.execute(
                "SELECT agent, slot FROM x_agents WHERE run = ? AND state = 'live' "
                "AND heartbeat_at < ?", (run, cutoff)).fetchall()
            if rows:
                self.conn.execute(
                    "UPDATE x_agents SET state = 'dead' WHERE run = ? "
                    "AND state = 'live' AND heartbeat_at < ?", (run, cutoff))
                self.conn.commit()
            return [row["agent"] for row in rows]

    # -- reading ----------------------------------------------------------

    def slots(self, run: str) -> List[Slot]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM x_agents WHERE run = ? ORDER BY slot",
                (run,)).fetchall()
            return [self._slot(row) for row in rows]

    def live(self, run: str) -> List[Slot]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM x_agents WHERE run = ? AND state = 'live' "
                "ORDER BY slot", (run,)).fetchall()
            return [self._slot(row) for row in rows]

    def summary(self, run: str) -> Dict[str, int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT state, COUNT(*) AS n FROM x_agents WHERE run = ? "
                "GROUP BY state", (run,)).fetchall()
            counts = {row["state"]: int(row["n"]) for row in rows}
        return {"capacity": self.capacity,
                "live": counts.get("live", 0),
                "dead": counts.get("dead", 0),
                "free": counts.get("free", 0),
                "total": sum(counts.values())}

    def by_skill(self, run: str) -> Dict[str, int]:
        """Which skills are live right now — the view an orchestrator routes on."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT skill, COUNT(*) AS n FROM x_agents "
                "WHERE run = ? AND state = 'live' AND skill != '' "
                "GROUP BY skill ORDER BY n DESC", (run,)).fetchall()
            return {row["skill"]: int(row["n"]) for row in rows}

    @staticmethod
    def _slot(row, registered: bool = False, reason: str = "") -> Slot:
        return Slot(run=row["run"], slot=int(row["slot"]), agent=row["agent"],
                    state=row["state"], skill=row["skill"], level=row["level"],
                    host=row["host"], pid=int(row["pid"]),
                    started_at=float(row["started_at"]),
                    heartbeat_at=float(row["heartbeat_at"]), note=row["note"],
                    registered=registered, reason=reason)


def _host() -> str:
    try:
        import socket
        return socket.gethostname()
    except Exception:                        # noqa: BLE001 - a name is optional
        return ""
