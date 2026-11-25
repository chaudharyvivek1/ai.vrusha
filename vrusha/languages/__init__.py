"""Language profiles: natural languages and programming languages."""

from .registry import (
    PROFILE_DIR,
    LanguageError,
    LanguageProfile,
    Registry,
    load_registry,
    normalize,
)

__all__ = [
    "PROFILE_DIR",
    "LanguageError",
    "LanguageProfile",
    "Registry",
    "load_registry",
    "normalize",
]
