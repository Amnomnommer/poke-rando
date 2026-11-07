"""The stable game-adapter contract and the adapter registry.

An adapter owns everything engine-specific for one game revision family:
validation, extraction, the normalized-model mapping, applying a plan,
repacking, binary post-processing, and semantic verification. Randomization
policy never imports adapter or format code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..capabilities import Capability, Support
from ..model import GameModel


@dataclass
class BuildContext:
    """Everything an adapter may touch during one build. Inputs are read-only."""

    workspace: Path
    rom: Path
    auxiliary: dict[str, Path]
    options: dict
    settings: dict
    key: bytes
    log: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)

    def say(self, message: str) -> None:
        self.log.append(message)
        print(f"[b2er] {message}", flush=True)


class GameAdapter(ABC):
    id: str
    version: str
    display_name: str
    generation: int
    fingerprints: tuple[str, ...]
    auxiliary_inputs: dict[str, str] = {}  # name -> description (e.g. "black2": "clean Black 2 ROM")

    @abstractmethod
    def capabilities(self) -> dict[Capability, Support]:
        """Static capability declaration with evidence."""

    def validate(self, data: bytes) -> list[str]:
        """Adapter/family-specific structural validation of a whole image (read-only). Return problems."""
        return []

    def preflight(self, ctx_inputs: dict) -> list[str]:
        """Adapter-specific prerequisites (dependencies, auxiliary inputs). Return every problem."""
        return []

    @abstractmethod
    def prepare(self, ctx: BuildContext) -> dict:
        """Extract/stage into the isolated workspace."""

    @abstractmethod
    def read_model(self, ctx: BuildContext) -> GameModel:
        """Read normalized game data from the workspace."""

    def dynamic_capabilities(self, ctx: BuildContext, model: GameModel) -> dict[Capability, Support]:
        """Capabilities that depend on discovered data (e.g. script sites). Defaults to the static set."""
        return self.capabilities()

    def resolve_diagnostic_area(self, model: GameModel) -> tuple[int, dict]:
        raise NotImplementedError

    @abstractmethod
    def apply(self, ctx: BuildContext, model: GameModel, plan: dict) -> dict:
        """Write source-level changes for the planned model into the workspace."""

    @abstractmethod
    def rebuild(self, ctx: BuildContext) -> bytes:
        """Assemble the output ROM."""

    @abstractmethod
    def postprocess(self, ctx: BuildContext, rom: bytes, model: GameModel, plan: dict) -> tuple[bytes, dict]:
        """Binary-level changes and repairs on the assembled ROM."""

    @abstractmethod
    def verify(self, ctx: BuildContext, rom: bytes, model: GameModel, plan: dict, post: dict) -> dict:
        """Structural and semantic verification of the finished ROM against the plan."""

    def tool_versions(self, ctx: BuildContext) -> dict:
        return {}


class AdapterRegistry:
    def __init__(self, adapters: list[GameAdapter]) -> None:
        self._by_id: dict[str, GameAdapter] = {}
        self._by_fingerprint: dict[str, GameAdapter] = {}
        for adapter in adapters:
            if adapter.id in self._by_id:
                raise ValueError(f"duplicate adapter id {adapter.id}")
            self._by_id[adapter.id] = adapter
            for fp in adapter.fingerprints:
                if fp in self._by_fingerprint:
                    raise ValueError(f"fingerprint {fp} claimed by two adapters")
                self._by_fingerprint[fp] = adapter

    def get(self, adapter_id: str) -> GameAdapter:
        return self._by_id[adapter_id]

    def for_fingerprint(self, fingerprint_id: str) -> GameAdapter | None:
        return self._by_fingerprint.get(fingerprint_id)

    def all(self) -> list[GameAdapter]:
        return list(self._by_id.values())

    def matrix(self) -> dict:
        return {a.id: {"display_name": a.display_name, "generation": a.generation, "version": a.version,
                       "capabilities": {c.value: s.to_json() for c, s in a.capabilities().items()}}
                for a in self.all()}
