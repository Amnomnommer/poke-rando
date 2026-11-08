"""Provenance rules for follower and character sprite catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class AssetPermission(str, Enum):
    ORIGINAL = "original"
    OPEN_LICENSE = "open_license"
    EXPRESS_PERMISSION = "express_permission"


@dataclass(frozen=True)
class SpriteAsset:
    asset_id: str
    source_url: str
    creator: str
    permission: AssetPermission
    license_or_permission: str
    art_style: str
    directions: int
    frames_per_direction: int
    species: int | None = None
    form: int = 0

    def validate(self) -> None:
        if not self.asset_id or not self.creator or not self.art_style:
            raise ValueError("sprite provenance fields cannot be empty")
        parsed = urlparse(self.source_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("sprite source must be an absolute HTTP(S) URL")
        if not self.license_or_permission.strip():
            raise ValueError("sprite needs a license or recorded permission")
        if self.directions < 1 or self.frames_per_direction < 1:
            raise ValueError("sprite dimensions must describe at least one frame")
        if self.species is not None and self.species < 1:
            raise ValueError("invalid sprite species")
        if self.form < 0:
            raise ValueError("invalid sprite form")
