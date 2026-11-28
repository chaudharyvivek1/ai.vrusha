"""dubforge: video dubbing, called as a subprocess.

MIT, and its own package with its own release. The adapter is here; the code is
where it lives, so there is one copy of dubforge and it is the maintained one.
"""
from __future__ import annotations

from . import ExternalTool, register

#: The actions this project will call. Not a command line: a whitelist, because a
#: generic "run this argv" wrapper is a remote-code-execution surface with a
#: friendlier name.
ACTIONS = ("doctor", "capabilities", "providers", "voices", "demo", "dub",
           "batch", "jobs", "explain", "validate-config", "verify", "review")

TOOL = register(ExternalTool(
    name="dubforge",
    summary="dub a video into another language: ASR, translation, TTS, alignment, mix",
    module="dubforge",
    cli="dubforge",
    actions=ACTIONS,
    checkout="../dubforge",
    requires=["dubforge (MIT)", "ffmpeg via imageio-ffmpeg", "requests"],
))
