"""forge3d: 3D model creation, called as a subprocess.

MIT. Heavier dependencies than dubforge (trimesh, scipy, networkx, pillow), which
is exactly why it is not a dependency of this package: `import vrusha` must not
install a geometry stack.
"""
from __future__ import annotations

from . import ExternalTool, register

ACTIONS = ("doctor", "capabilities", "providers", "backends", "plan", "create",
           "render", "convert", "repair", "validate", "card", "demo")

TOOL = register(ExternalTool(
    name="forge3d",
    summary="3D model creation for agents: plan, create, render, repair, validate",
    module="forge3d",
    cli="forge3d",
    actions=ACTIONS,
    checkout="../forge3d",
    requires=["forge3d (MIT)", "trimesh", "scipy", "networkx", "pillow"],
))
