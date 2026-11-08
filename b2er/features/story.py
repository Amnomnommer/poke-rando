"""Declarative story-skip graph used by per-game adapters after 1.2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class StoryCheckpoint:
    checkpoint_id: str
    requires: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()
    clears: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.checkpoint_id:
            raise ValueError("checkpoint_id cannot be empty")
        if set(self.grants) & set(self.clears):
            raise ValueError("a checkpoint cannot grant and clear the same flag")


def validate_story_path(checkpoints: Iterable[StoryCheckpoint], *, initial_flags: Iterable[str] = ()) -> tuple[str, ...]:
    known = set(initial_flags)
    seen: set[str] = set()
    for checkpoint in checkpoints:
        if checkpoint.checkpoint_id in seen:
            raise ValueError(f"duplicate checkpoint: {checkpoint.checkpoint_id}")
        missing = set(checkpoint.requires) - known
        if missing:
            raise ValueError(f"{checkpoint.checkpoint_id} requires unavailable flags: {sorted(missing)}")
        known.difference_update(checkpoint.clears)
        known.update(checkpoint.grants)
        seen.add(checkpoint.checkpoint_id)
    return tuple(sorted(known))
