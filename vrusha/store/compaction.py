"""Compaction: keep the decisions, drop the noise, and prove what was kept.

`docs/AGENTS.md` §9 says a summary that silently drops a fact produces confident
wrong answers — worse than no compaction, because it is compaction you trust. So
every summary here carries the **`seq` range it replaced**, and `verify()` can say
whether a fact planted in that range survived.

The summary is rule-based, not a model: keep every `decision`, every `error`, every
artifact reference, and the **last** finding per task; drop `progress` entries and
superseded findings. That is what a careful person would keep, it is deterministic,
and it costs nothing to run on every turn.

Two numbers from the plan are defaults here: compact at **85%** of the budget, and
offload a single entry past **20,000 tokens** to the blackboard as a reference.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: Compact at 85% of the budget, the threshold production harnesses use.
THRESHOLD = 0.85

#: One entry larger than this is offloaded rather than summarised.
OFFLOAD_TOKENS = 20000

#: Chars per token, the same estimate the evaluation module uses and labels.
CHARS_PER_TOKEN = 4.0

#: Entry kinds kept verbatim. Everything else is dropped or superseded.
KEEP_ALWAYS = ("decision", "error", "question", "artifact")


def tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / CHARS_PER_TOKEN))


@dataclass
class Summary:
    """What a compaction produced, and exactly what it replaced."""

    text: str
    seq_from: int
    seq_to: int
    entries_replaced: int
    tokens_before: int
    tokens_after: int
    kept: List[str] = field(default_factory=list)
    dropped: int = 0
    #: False when the summary is not smaller than what it replaced. Compaction
    #: that grows the text is not compaction, and the caller must keep the entries.
    useful: bool = True

    @property
    def ratio(self) -> float:
        return (self.tokens_after / self.tokens_before) if self.tokens_before else 0.0

    def to_dict(self) -> dict:
        return {"seq_from": self.seq_from, "seq_to": self.seq_to,
                "entries_replaced": self.entries_replaced,
                "tokens_before": self.tokens_before,
                "tokens_after": self.tokens_after,
                "ratio": round(self.ratio, 3), "kept": list(self.kept),
                "dropped": self.dropped, "useful": self.useful}


def should_compact(entries: Sequence, budget_tokens: int,
                   threshold: float = THRESHOLD) -> bool:
    used = sum(tokens(getattr(entry, "summary", "")) for entry in entries)
    return used >= budget_tokens * float(threshold)


def offload_needed(entry) -> bool:
    """One entry too big to keep inline: write it down and keep a reference."""
    return tokens(getattr(entry, "summary", "")) >= OFFLOAD_TOKENS


def compact(entries: Sequence, *, keep_recent: int = 5) -> Summary:
    """Fold the entries into a summary, recording the `seq` range it replaced."""
    entries = list(entries)
    if not entries:
        return Summary(text="", seq_from=0, seq_to=0, entries_replaced=0,
                       tokens_before=0, tokens_after=0)

    ordered = sorted(entries, key=lambda entry: int(getattr(entry, "seq", 0)))
    seq_from = int(getattr(ordered[0], "seq", 0))
    seq_to = int(getattr(ordered[-1], "seq", 0))
    before = sum(tokens(getattr(entry, "summary", "")) for entry in ordered)

    decisions, errors, questions, artifacts = [], [], [], []
    findings: Dict[str, str] = {}
    progress = 0
    for entry in ordered:
        kind = getattr(entry, "kind", "")
        summary = getattr(entry, "summary", "") or ""
        if kind == "decision":
            decisions.append(summary)
        elif kind == "error":
            errors.append(summary)
        elif kind == "question":
            questions.append(summary)
        elif kind == "artifact":
            artifacts.extend(getattr(entry, "refs", []) or [])
        elif kind == "finding":
            findings[getattr(entry, "task", "") or "?"] = summary
        else:
            progress += 1

    # the most recent few entries are kept verbatim regardless of kind: a summary
    # that loses the last thing that happened is not a summary, it is a gap
    recent = [getattr(entry, "summary", "") for entry in ordered[-keep_recent:]]

    lines = [f"[compacted seq {seq_from}-{seq_to}: {len(ordered)} entries]"]
    if decisions:
        lines.append("decisions: " + " | ".join(decisions))
    if errors:
        lines.append("errors: " + " | ".join(errors))
    if questions:
        lines.append("open questions: " + " | ".join(questions))
    if artifacts:
        lines.append("artifacts: " + ", ".join(dict.fromkeys(artifacts)))
    if findings:
        lines.append("findings: " + " | ".join(
            f"{task}: {text}" for task, text in findings.items()))
    if recent:
        lines.append("most recent: " + " | ".join(recent))
    text = "\n".join(lines)

    # explicit, because `locals()[kind]` looked up a singular name that only
    # exists in the plural -- a KeyError on the first real run
    collected = {"decision": decisions, "error": errors, "question": questions,
                 "artifact": artifacts}
    kept = [kind for kind in KEEP_ALWAYS if collected[kind]]
    if findings:
        kept.append("finding")
    after = tokens(text)
    # Measured, not assumed: with six tiny entries the first version produced a
    # "summary" 2.7x LARGER than the input. A compaction that grows the text must
    # decline, or the caller has been made slower and no better off.
    useful = after < before
    return Summary(text=text if useful else "", seq_from=seq_from,
                   seq_to=seq_to, entries_replaced=len(ordered),
                   tokens_before=before, tokens_after=after if useful else before,
                   kept=kept if useful else [], dropped=progress,
                   useful=useful)


def verify(summary: Summary, entries: Sequence) -> Dict[str, object]:
    """Did the summary keep what mattered in the range it claims to replace?

    Checks the summary against the entries it replaced: every decision, error and
    question must appear in the text, every artifact reference must be listed, and
    the `seq` range must match. A summary that fails this is not used.
    """
    inside = [entry for entry in entries
              if summary.seq_from <= int(getattr(entry, "seq", 0)) <= summary.seq_to]
    problems: List[str] = []
    for entry in inside:
        kind = getattr(entry, "kind", "")
        summary_text = getattr(entry, "summary", "") or ""
        if kind in ("decision", "error", "question") and summary_text not in summary.text:
            problems.append(f"{kind} at seq {getattr(entry, 'seq', '?')} was dropped")
        if kind == "artifact":
            for ref in getattr(entry, "refs", []) or []:
                if ref not in summary.text:
                    problems.append(f"artifact {ref} was dropped")
    if inside and int(getattr(inside[0], "seq", 0)) != summary.seq_from:
        problems.append("seq_from does not match the first entry replaced")
    return {"ok": not problems, "problems": problems,
            "checked": len(inside),
            "ratio": round(summary.ratio, 3)}


# ---------------------------------------------------------------------------
# Part 29: offload
# ---------------------------------------------------------------------------


def offload(board, entries: Sequence, run: str, *,
            limit: int = OFFLOAD_TOKENS) -> tuple:
    """Replace every oversized entry with a reference to the board entry.

    The context keeps its shape — one line where the entry was, in the same order —
    and loses only the bytes. The full text is appended to the blackboard as an
    `artifact` entry, so it is still readable by any agent and still in the log.

    A reference is better than a truncation: `board:417` can be followed, and a
    truncated 20,000-token page is just a shorter page with a hole in it.
    """
    from dataclasses import replace as _replace

    kept, offloaded = [], []
    for entry in entries:
        if tokens(getattr(entry, "summary", "")) < int(limit):
            kept.append(entry)
            continue
        original = getattr(entry, "summary", "") or ""
        written = board.append(
            run,
            task=getattr(entry, "task", "") or "",
            agent=getattr(entry, "agent", "") or "offload",
            kind="artifact",
            summary=f"offloaded from seq {getattr(entry, 'seq', '?')}: "
                    f"{original[:200]}",
            refs=[f"seq:{getattr(entry, 'seq', 0)}"],
            payload={"chars": len(original), "text": original})
        reference = (f"[offloaded seq {getattr(entry, 'seq', '?')} -> "
                     f"board:{written.seq}, {tokens(original):,} tokens]")
        kept.append(_replace(entry, summary=reference))
        offloaded.append({"seq": getattr(entry, "seq", 0),
                          "board_seq": written.seq,
                          "tokens": tokens(original)})
    return kept, {"offloaded": len(offloaded), "details": offloaded,
                  "tokens_before": sum(tokens(getattr(e, "summary", ""))
                                       for e in entries),
                  "tokens_after": sum(tokens(getattr(e, "summary", ""))
                                      for e in kept)}
