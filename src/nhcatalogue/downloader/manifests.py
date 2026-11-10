"""Manifest + Houdoku-compatible metadata for downloaded folders (plan §4).

Two files per gallery folder:

* ``metadata.json`` — Houdoku-compatible, so the folder tree is portable to
  other readers: ``{id, media_id, title{english,japanese,pretty},
  images{pages,cover,thumbnail}, tags[], num_pages, upload_date}``.
* ``.nhc.json`` — internal resume manifest: expected page list with sizes,
  downloaded set → resume support + integrity verification.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_NAME = ".nhc.json"
METADATA_NAME = "metadata.json"

# bump when manifest semantics change
MANIFEST_VERSION = 1


def build_metadata(gallery) -> dict:
    """Houdoku-compatible metadata.json payload."""
    return {
        "id": gallery.id,
        "media_id": gallery.media_id,
        "title": {
            "english": gallery.title_en,
            "japanese": gallery.title_ja,
            "pretty": gallery.title_pretty,
        },
        "images": {
            "pages": [
                {
                    "t": page.ext[0] if page.ext[0] in "jpggw" else "j",
                    "w": page.width,
                    "h": page.height,
                }
                for page in gallery.pages
            ],
            "cover": {"t": gallery.cover_ext[0]},
            "thumbnail": {"t": gallery.thumb_ext[0]},
        },
        "tags": [
            {"id": t.id, "name": t.name, "type": t.type, "count": t.count, "url": t.url}
            for t in gallery.tags
        ],
        "num_pages": gallery.num_pages,
        "upload_date": gallery.upload_date,
        "scanlator": getattr(gallery, "scanlator", "") or "",
    }


def build_manifest(gallery) -> dict:
    """Internal .nhc.json resume manifest."""
    return {
        "version": MANIFEST_VERSION,
        "gallery_id": gallery.id,
        "media_id": gallery.media_id,
        "title": gallery.title,
        "num_pages": len(gallery.pages),
        "expected": [
            {"page": p.number, "ext": p.ext, "size": 0, "hash": ""} for p in gallery.pages
        ],
        "downloaded": {},  # page number (str) -> {size, hash}
        "cover_ext": gallery.cover_ext,
        "complete": False,
    }


def read_manifest(folder: Path) -> dict | None:
    p = folder / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "gallery_id" not in data:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(folder: Path, manifest: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def read_metadata(folder: Path) -> dict | None:
    """Read Houdoku metadata.json — used by import to adopt existing folders."""
    p = folder / METADATA_NAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_metadata(folder: Path, gallery) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / METADATA_NAME).write_text(
        json.dumps(build_metadata(gallery), ensure_ascii=False, indent=1), encoding="utf-8"
    )


def mark_page_downloaded(manifest: dict, page: int, size: int, hash_: str = "") -> None:
    manifest.setdefault("downloaded", {})[str(int(page))] = {
        "size": int(size),
        "hash": hash_ or "",
    }
    expected = manifest.get("expected", [])
    if len(manifest["downloaded"]) >= max(1, len(expected)):
        # all pages accounted for → complete
        manifest["complete"] = True


def verify_page(manifest: dict, page: int, folder: Path, ext: str) -> bool:
    """Integrity check: page exists on disk, size > 0 and matches manifest."""
    entry = manifest.get("downloaded", {}).get(str(int(page)))
    if not entry:
        return False
    pad = max(3, len(str(manifest.get("num_pages", 0))))
    path = folder / f"{page:0{pad}d}.{ext}"
    if not path.exists():
        return False
    size = path.stat().st_size
    if size <= 0:
        return False
    if entry.get("size") and entry["size"] != size:
        return False
    return True
