"""Helpers for keeping Giant's registered gift IDs while adding local image variants."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def extend_gift_image_paths(
    base_dir: str | Path,
    existing: Mapping[str, Iterable[str]],
    *,
    registered_ids: Iterable[int] = range(1, 15),
) -> dict[str, list[str]]:
    """Append bundled Talkin2 images only for gift IDs Giant already registers.

    Existing Giant image paths are kept first and never overwritten. Extra source
    images (15–40) remain packaged, but are not advertised as sendable gifts
    because Giant has no corresponding registered gift records for those IDs.
    """
    root = Path(base_dir).resolve()
    result = {str(key): list(paths or ()) for key, paths in existing.items()}
    source_dir = root / "assets" / "talkin2_gifts"
    for gift_id in registered_ids:
        key = str(gift_id)
        paths = result.setdefault(key, [])
        known = set(paths)
        for path in sorted(source_dir.glob(f"gift_{gift_id:02d}_*")):
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
                relative = path.relative_to(root).as_posix()
                if relative not in known:
                    paths.append(relative)
                    known.add(relative)
    return result
