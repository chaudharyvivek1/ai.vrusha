"""Main generation: the model, its training loop, sampling and the composer."""

from .compose import ComposedAnswer, compose_answer, format_citations

__all__ = ["ComposedAnswer", "compose_answer", "format_citations"]


def __getattr__(name):
    """Import torch-backed pieces lazily, so the package works without torch."""
    if name in {"ModelConfig", "TinyLM", "PRESETS", "build_model"}:
        from . import model as _model
        return getattr(_model, name)
    if name in {"TrainConfig", "train", "pick_device", "lr_at"}:
        from . import train as _train
        return getattr(_train, name)
    if name in {"generate_text"}:
        from . import sample as _sample
        return getattr(_sample, name)
    raise AttributeError(name)
