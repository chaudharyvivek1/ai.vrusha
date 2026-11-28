"""cutforge: a specification, not an engine — and the listing says so.

`cutforge` is a design record: its own README states that it holds "the technical
specification, the engine boundary, the review history and the open findings that
the next version must close", and that "there is no product surface here". There is
no runnable engine to call, so this tool reports itself as unavailable with that
reason rather than pretending, and `vrusha tools` shows the truth instead of a
capability that does not exist.

It is registered at all for two reasons: the listing is the honest inventory of
what the platform can do, and when the engine lands it becomes an `ExternalTool`
with a whitelist like the other two.
"""
from __future__ import annotations

from typing import Optional

from . import ExternalTool, ToolResult, ToolStatus, register


class SpecificationOnly(ExternalTool):
    """A tool that exists as a document. Never available, always explained."""

    def __init__(self, *args, note: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.note = note

    def status(self) -> ToolStatus:
        return ToolStatus(self.name, False, "specification",
                          self.note or "specification only: no runnable engine",
                          self.requires, self.summary)

    def run(self, action: str, *, timeout: Optional[float] = None,
            **options) -> ToolResult:
        return ToolResult(self.name, action, False, error=self.note)


TOOL = register(SpecificationOnly(
    name="cutforge",
    summary="prompt-driven video editing: declared intent plus assets to a frame-accurate cut",
    module="cutforge",
    cli="cutforge",
    actions=("doctor", "plan", "render"),
    checkout="../cutforge",
    requires=["an engine (the repository is a specification today)"],
    note=("specification only: the repository holds the engine boundary and the "
          "open findings, not a runnable engine"),
))
