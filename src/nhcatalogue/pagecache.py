"""Lazy page cache for galleries that belong to at least one collection.

The cache is intentionally collection-only: Browse/search results stream and
are discarded, downloaded galleries read from their library folder, and only
galleries on a shelf get a persistent copy of pages they have actually opened.
A second instance with ``ttl_seconds`` set backs the temporary reader cache:
pages opened for previews are retained briefly and promoted into permanent
storage when the gallery is saved.

Layout: ``<root>/<gallery_id>/<page:0pad>.<ext>``. Size is capped with an LRU
safety net (oldest mtimes are evicted first) so a big collection cannot fill
the disk silently.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path


class PageCache:
    def __init__(self, root: str | Path, max_bytes: int = 2 * 1024**3, ttl_seconds: float | None = None):
        self.root = Path(root)
        self.max_bytes = max(0, int(max_bytes))
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._size: int | None = None

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _pad(total_pages: int) -> int:
        return max(3, len(str(max(1, int(total_pages or 0)))))

    def _gallery_dir(self, gallery_id: int) -> Path:
        return self.root / str(int(gallery_id))

    def path_for(
        self, gallery_id: int, page: int, ext: str, total_pages: int = 0
    ) -> Path:
        pad = self._pad(total_pages)
        return self._gallery_dir(gallery_id) / f"{int(page):0{pad}d}.{ext or 'jpg'}"

    def _measure(self) -> int:
        if self._size is not None:
            return self._size
        total = 0
        if self.root.exists():
            for p in self.root.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    continue
        self._size = total
        return total

    def set_max_bytes(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        with self._lock:
            self._enforce_cap()

    def set_ttl(self, ttl_seconds: float | None) -> None:
        self.ttl_seconds = ttl_seconds

    def _expired(self, mtime: float) -> bool:
        return self.ttl_seconds is not None and (time.time() - mtime) > self.ttl_seconds

    # -------------------------------------------------------------------- reads

    def get(
        self, gallery_id: int, page: int, ext: str, total_pages: int = 0
    ) -> Path | None:
        """Return a cached page path (touching mtime for LRU), or None."""
        if self.max_bytes <= 0:
            return None
        candidate = self.path_for(gallery_id, page, ext, total_pages)
        try:
            if candidate.is_file():
                st = candidate.stat()
                if st.st_size > 0:
                    if self._expired(st.st_mtime):
                        # temp retention elapsed without promotion → drop the entry
                        candidate.unlink(missing_ok=True)
                        if self._size is not None:
                            self._size = max(0, self._size - st.st_size)
                        return None
                    os.utime(candidate, None)  # touch for LRU
                    return candidate
        except OSError:
            return None
        # extension mismatch (imports/CDN quirks): look for the same page number
        folder = self._gallery_dir(gallery_id)
        pad = self._pad(total_pages)
        if folder.is_dir():
            for alt in folder.glob(f"{int(page):0{pad}d}.*"):
                try:
                    st = alt.stat()
                    if not st.st_size:
                        continue
                    if self._expired(st.st_mtime):
                        alt.unlink(missing_ok=True)
                        if self._size is not None:
                            self._size = max(0, self._size - st.st_size)
                        continue
                    os.utime(alt, None)
                    return alt
                except OSError:
                    continue
        return None

    # ------------------------------------------------------------------- writes

    def put(
        self,
        gallery_id: int,
        page: int,
        ext: str,
        data: bytes,
        total_pages: int = 0,
    ) -> Path | None:
        """Persist a page (only call for collection galleries)."""
        if self.max_bytes <= 0 or not data:
            return None
        dest = self.path_for(gallery_id, page, ext, total_pages)
        with self._lock:
            self._measure()  # make sure the running size is known
            old_size = 0
            try:
                if dest.exists():
                    old_size = dest.stat().st_size
            except OSError:
                old_size = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, dest)
            except OSError:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            self._size = max(0, (self._size or 0) - old_size + len(data))
            self._enforce_cap()
        return dest

    def purge(self, gallery_id: int) -> int:
        """Delete every cached page for a gallery. Returns bytes reclaimed."""
        folder = self._gallery_dir(gallery_id)
        if not folder.exists():
            return 0
        reclaimed = 0
        with self._lock:
            for p in folder.rglob("*"):
                try:
                    if p.is_file():
                        reclaimed += p.stat().st_size
                except OSError:
                    continue
            shutil.rmtree(folder, ignore_errors=True)
            if self._size is not None:
                self._size = max(0, self._size - reclaimed)
        return reclaimed

    def promote_from(self, source: PageCache, gallery_id: int) -> int:
        """Move retained entries from another cache instance (e.g. the temporary
        reader cache) into this instance's permanent storage.

        Used on promotion: temporary reader entries become permanent once the
        comic enters a collection / gets downloaded. Returns bytes moved; the
        source gallery folder is cleared afterwards.
        """
        src = source._gallery_dir(int(gallery_id))
        if not src.is_dir():
            return 0
        dst = self._gallery_dir(int(gallery_id))
        moved = 0
        with self._lock:
            dst.mkdir(parents=True, exist_ok=True)
            for p in sorted(src.iterdir()):
                if not p.is_file():
                    continue
                target = dst / p.name
                shutil.move(str(p), str(target))
                moved += target.stat().st_size
            shutil.rmtree(src, ignore_errors=True)
        if self._size is not None:
            self._size += moved
        if source._size is not None:
            source._size = max(0, source._size - moved)
        self._enforce_cap()
        return moved

    # -------------------------------------------------------------------- LRU

    def _enforce_cap(self) -> None:
        if self.max_bytes <= 0:
            return
        size = self._measure()
        if size <= self.max_bytes:
            return
        files: list[tuple[float, int, Path]] = []
        for p in self.root.rglob("*"):
            try:
                if p.is_file():
                    st = p.stat()
                    files.append((st.st_mtime, st.st_size, p))
            except OSError:
                continue
        files.sort(key=lambda item: item[0])
        # trim to 80% of the cap so eviction isn't re-run on every write
        target = int(self.max_bytes * 0.8)
        for _mtime, fsize, path in files:
            if size <= target:
                break
            try:
                path.unlink()
                size -= fsize
            except OSError:
                continue
        self._size = max(0, size)
