"""SQLite catalogue: schema bootstrap, sequential migrations, all queries.

Uses stdlib ``sqlite3`` in a lock-guarded executor (aiosqlite-shaped API
without the extra dependency). WAL mode + ``foreign_keys`` on.

Everything downstream (server, downloader, sync) talks to the DB through this
class so the rest of the app stays synchronous-friendly and testable.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..client.models import Gallery, Tag, normalize_tag_name, normalize_type

MIGRATIONS_PACKAGE = "nhcatalogue.db.migrations"


class Database:
    def __init__(self, path: str | Path = ":memory:", *, executor_workers: int = 2):
        self.path = str(path)
        self._lock = asyncio.Lock()
        self._executor = asyncio.get_event_loop().run_in_executor if False else None
        self._conn: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._open()
        self._migrate()

    # ------------------------------------------------------------------ setup

    def _open(self) -> None:
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def _migration_files(self) -> list[tuple[str, str]]:
        files: list[tuple[str, str]] = []
        try:
            root = importlib.resources.files(MIGRATIONS_PACKAGE)
            for entry in sorted(root.iterdir(), key=lambda p: p.name):
                if entry.name.endswith(".sql"):
                    files.append((entry.name, entry.read_text(encoding="utf-8")))
        except (ModuleNotFoundError, FileNotFoundError):
            # fall back to filesystem layout (source tree / PyInstaller)
            here = Path(__file__).parent / "migrations"
            for p in sorted(here.glob("*.sql")):
                files.append((p.name, p.read_text(encoding="utf-8")))
        return files

    def _migrate(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        applied = {r[0] for r in self._conn.execute("SELECT name FROM schema_migrations")}
        for name, sql in self._migration_files():
            if name in applied:
                continue
            with self._conn:  # transaction per migration
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(name) VALUES (?)", (name,)
                )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------- async wrapper plumbing

    async def _run(self, fn, *args, **kwargs):
        """Run a sync sqlite call off the loop thread under the write lock."""
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    @property
    def conn(self) -> sqlite3.Connection:
        assert self._conn is not None, "database is closed"
        return self._conn

    # ------------------------------------------------------------- settings KV

    def _settings_get_all(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                out[r["key"]] = r["value"]
        return out

    async def settings_get_all(self) -> dict[str, Any]:
        return await self._run(self._settings_get_all)

    def settings_get_all_sync(self) -> dict[str, Any]:
        return self._settings_get_all()

    def _settings_set(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    async def settings_set(self, key: str, value: Any) -> None:
        await self._run(self._settings_set, key, value)

    def settings_set_sync(self, key: str, value: Any) -> None:
        self._settings_set(key, value)

    # ------------------------------------------------------------- upsert tags

    def _upsert_tag(self, tag: Tag) -> int:
        row = self.conn.execute(
            "SELECT id FROM tags WHERE name_norm=? AND type=?", (tag.name_norm, tag.type)
        ).fetchone()
        if row:
            # keep the existing row id (gallery_tags references it); refresh
            # the freshest name/count/url from the API
            self.conn.execute(
                "UPDATE tags SET count=MAX(count, ?), name=?, url=? WHERE id=?",
                (tag.count, tag.name, tag.url or "", row["id"]),
            )
            return int(row["id"])
        self.conn.execute(
            "INSERT OR REPLACE INTO tags(id, name, name_norm, type, count, url) "
            "VALUES (?,?,?,?,?,?)",
            (tag.id, tag.name, tag.name_norm, tag.type, tag.count, tag.url or ""),
        )
        return int(tag.id)

    def _upsert_gallery(self, gallery: Gallery) -> None:
        # tag rows first (need ids for the join + FTS triggers)
        tag_ids: list[int] = []
        for tag in gallery.tags:
            tag_ids.append(self._upsert_tag(tag))
        conn = self.conn
        exists = conn.execute("SELECT 1 FROM galleries WHERE id=?", (gallery.id,)).fetchone()
        raw_json = self._gallery_raw_json(gallery)
        if exists:
            # never clobber user data on re-add
            conn.execute(
                "UPDATE galleries SET media_id=?, title_en=?, title_ja=?, title_pretty=?, "
                "num_pages=?, upload_date=?, language=?, category=?, scanlator=?, raw_json=? "
                "WHERE id=?",
                (
                    gallery.media_id,
                    gallery.title_en,
                    gallery.title_ja,
                    gallery.title_pretty,
                    gallery.num_pages,
                    gallery.upload_date,
                    gallery.language,
                    gallery.category,
                    gallery.scanlator,
                    raw_json,
                    gallery.id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO galleries(id, media_id, title_en, title_ja, title_pretty, "
                "num_pages, upload_date, language, category, scanlator, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    gallery.id,
                    gallery.media_id,
                    gallery.title_en,
                    gallery.title_ja,
                    gallery.title_pretty,
                    gallery.num_pages,
                    gallery.upload_date,
                    gallery.language,
                    gallery.category,
                    gallery.scanlator,
                    raw_json,
                ),
            )
        # refresh gallery_tags: delete-then-insert keeps FTS triggers correct
        conn.execute("DELETE FROM gallery_tags WHERE gallery_id=?", (gallery.id,))
        for tag_id in tag_ids:
            conn.execute(
                "INSERT OR IGNORE INTO gallery_tags(gallery_id, tag_id) VALUES (?, ?)",
                (gallery.id, tag_id),
            )

    @staticmethod
    def _gallery_raw_json(gallery: Gallery) -> str:
        """Store the full normalized gallery as raw JSON for Houdoku export."""
        return json.dumps(
            {
                "id": gallery.id,
                "media_id": gallery.media_id,
                "title": {
                    "english": gallery.title_en,
                    "japanese": gallery.title_ja,
                    "pretty": gallery.title_pretty,
                },
                "images": {
                    "pages": [
                        {"t": p.ext[0] if p.ext[0] in "jpgg" else "j", "w": p.width, "h": p.height}
                        for p in gallery.pages
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
                "scanlator": gallery.scanlator,
            }
        )

    async def upsert_gallery(self, gallery: Gallery) -> None:
        await self._run(self._upsert_gallery, gallery)

    # -------------------------------------------------------------- catalogue

    def _gallery_from_row(self, row: sqlite3.Row, with_tags: bool = True) -> dict[str, Any]:
        g = dict(row)
        if with_tags:
            tag_rows = self.conn.execute(
                "SELECT t.id, t.name, t.name_norm, t.type, t.count FROM gallery_tags gt "
                "JOIN tags t ON t.id = gt.tag_id WHERE gt.gallery_id=? ORDER BY t.type, t.name",
                (row["id"],),
            ).fetchall()
            g["tags"] = [dict(t) for t in tag_rows]
        else:
            g["tags"] = []
        g["favorite"] = bool(g["favorite"])
        g["has_folder"] = bool(g["folder_path"])
        return g

    def _fetch_gallery(self, gallery_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM galleries WHERE id=?", (gallery_id,)
        ).fetchone()
        if row is None:
            return None
        return self._gallery_from_row(row)

    async def get_gallery(self, gallery_id: int) -> dict[str, Any] | None:
        return await self._run(self._fetch_gallery, int(gallery_id))

    def _delete_gallery(self, gallery_id: int, delete_files: bool = False) -> None:
        import shutil

        row = self.conn.execute(
            "SELECT folder_path FROM galleries WHERE id=?", (gallery_id,)
        ).fetchone()
        if row and delete_files and row["folder_path"]:
            shutil.rmtree(row["folder_path"], ignore_errors=True)
        self.conn.execute("DELETE FROM galleries WHERE id=?", (gallery_id,))
        self.conn.execute("DELETE FROM download_queue WHERE gallery_id=?", (gallery_id,))

    async def delete_gallery(self, gallery_id: int, *, delete_files: bool = False) -> None:
        await self._run(self._delete_gallery, int(gallery_id), delete_files)

    def _update_user_data(self, gallery_id: int, patch: dict[str, Any]) -> dict | None:
        allowed = (
            "favorite",
            "rating",
            "notes",
            "read_progress_pages",
            "download_status",
            "folder_path",
            "cover_path",
        )
        sets, vals = [], []
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "favorite":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            vals.append(v)
        if k := "last_read_at" if "read_progress_pages" in patch else None:
            sets.append("last_read_at=datetime('now')")
        if sets:
            vals.append(int(gallery_id))
            self.conn.execute(
                f"UPDATE galleries SET {', '.join(sets)} WHERE id=?", vals
            )
        return self._fetch_gallery(gallery_id)

    async def update_user_data(self, gallery_id: int, patch: dict[str, Any]) -> dict | None:
        return await self._run(self._update_user_data, int(gallery_id), patch)

    async def set_progress(self, gallery_id: int, page: int) -> dict | None:
        """PATCH /api/catalogue/{id}/progress — resume-where-you-left-off."""
        return await self.update_user_data(
            gallery_id, {"read_progress_pages": max(0, int(page))}
        )

    # ------------------------------------------------------- catalogue queries

    def _query_catalogue(
        self,
        *,
        q: str = "",
        include_tags: Sequence[dict[str, str]] = (),
        exclude_tags: Sequence[dict[str, str]] = (),
        favorite: bool | None = None,
        rating_min: int | None = None,
        downloaded: bool | None = None,
        collection: int | None = None,
        tag_id: int | None = None,
        sort: str = "added",
        limit: int = 60,
        offset: int = 0,
    ) -> dict[str, Any]:
        conn = self.conn
        where: list[str] = ["1=1"]
        params: list[Any] = []
        joins: list[str] = []
        fts_rank = None

        if q:
            # FTS over titles + tag names; fall back to LIKE for robustness
            fts_q = self._fts_query(q)
            fts_match = False
            try:
                row = conn.execute(
                    "SELECT count(*) FROM galleries_fts WHERE galleries_fts MATCH ?",
                    (fts_q,),
                ).fetchone()
                fts_match = row[0] > 0
            except sqlite3.OperationalError:
                fts_match = False
            if fts_match:
                where.append("g.id IN (SELECT rowid FROM galleries_fts WHERE galleries_fts MATCH ?)")
                params.append(fts_q)
                fts_rank = True
            else:
                like = f"%{q}%"
                where.append(
                    "(g.title_en LIKE ? OR g.title_ja LIKE ? OR g.title_pretty LIKE ? "
                    "OR EXISTS (SELECT 1 FROM gallery_tags gt JOIN tags t ON t.id=gt.tag_id "
                    "WHERE gt.gallery_id=g.id AND t.name_norm LIKE ?))"
                )
                params += [like, like, like, like]

        if include_tags:
            for t in include_tags:
                joins.append(
                    "JOIN gallery_tags gt_{n} ON gt_{n}.gallery_id=g.id "
                    "JOIN tags t_{n} ON t_{n}.id=gt_{n}.tag_id AND t_{n}.name_norm=? "
                    "AND t_{n}.type=?".format(n=len(joins))
                )
                params += [normalize_tag_name(t["name"]), normalize_type(t.get("type", "general"))]
        if exclude_tags:
            for t in exclude_tags:
                where.append(
                    "NOT EXISTS (SELECT 1 FROM gallery_tags gte JOIN tags te ON te.id=gte.tag_id "
                    "WHERE gte.gallery_id=g.id AND te.name_norm=? AND te.type=?)"
                )
                params += [normalize_tag_name(t["name"]), normalize_type(t.get("type", "general"))]

        if favorite is not None:
            where.append("g.favorite=?")
            params.append(1 if favorite else 0)
        if rating_min is not None:
            where.append("g.rating>=?")
            params.append(int(rating_min))
        if downloaded is not None:
            where.append("g.download_status=?")
            params.append("done" if downloaded else "none")
        if tag_id is not None:
            where.append(
                "g.id IN (SELECT gallery_id FROM gallery_tags WHERE tag_id=?)"
            )
            params.append(int(tag_id))
        if collection is not None:
            where.append(
                "g.id IN (SELECT gallery_id FROM collection_galleries WHERE collection_id=?)"
            )
            params.append(int(collection))

        order = {
            "added": "g.date_added DESC, g.id DESC",
            "title": "g.title_pretty COLLATE NOCASE ASC",
            "rating": "g.rating DESC, g.date_added DESC",
            "pages": "g.num_pages DESC",
            "released": "g.upload_date DESC",
            "progress": "g.last_read_at DESC",
        }.get(sort, "g.date_added DESC, g.id DESC")
        if fts_rank:
            order = (
                f"bm25(galleries_fts) ASC, {order}"
                if "IN (SELECT rowid" in where and "galleries_fts MATCH" in where[-1]
                else order
            )

        # count first (without the fts rank join complexity)
        count_sql = (
            f"SELECT count(*) FROM galleries g {' '.join(joins)} WHERE {' AND '.join(where)}"
        )
        total = conn.execute(count_sql, params).fetchone()[0]

        sql = (
            f"SELECT g.* FROM galleries g {' '.join(joins)} WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, params + [int(limit), int(offset)]).fetchall()
        items = [self._gallery_from_row(r, with_tags=True) for r in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def query_catalogue(self, **kwargs) -> dict[str, Any]:
        return await self._run(self._query_catalogue, **kwargs)

    @staticmethod
    def _fts_query(q: str) -> str:
        """Make an FTS5 MATCH expression from free user text (AND of tokens)."""
        tokens = [t for t in re.findall(r"[\w\-]+", q) if t]
        if not tokens:
            return '""'
        return " ".join(f'"{t}"*' for t in tokens)

    # ------------------------------------------------------------ tag queries

    def _tag_search(
        self, q: str = "", type_: str = "", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        conn = self.conn
        where = ["1=1"]
        params: list[Any] = []
        if q:
            norm = normalize_tag_name(q)
            where.append("(t.name_norm LIKE ? OR t.name LIKE ?)")
            params += [f"%{norm}%", f"%{norm}%"]
        if type_:
            where.append("t.type=?")
            params.append(normalize_type(type_))
        # gallery counts come from the local catalogue (plan: /api/tags?q=&type=)
        base = (
            "FROM tags t LEFT JOIN gallery_tags gt ON gt.tag_id=t.id "
            f"WHERE {' AND '.join(where)} GROUP BY t.id, t.name_norm, t.type, t.name, t.count"
        )
        total = conn.execute(f"SELECT count(*) FROM (SELECT t.id {base})", params).fetchone()[0]
        rows = conn.execute(
            "SELECT t.id, t.name, t.name_norm, t.type, t.count AS api_count, "
            f"count(gt.gallery_id) AS gallery_count {base} "
            "ORDER BY gallery_count DESC, t.count DESC, t.name_norm LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def tag_search(self, q: str = "", type_: str = "", limit: int = 100, offset: int = 0):
        return await self._run(self._tag_search, q, type_, limit, offset)

    def _tag_facets(
        self,
        *,
        include_tags: Sequence[dict[str, str]] = (),
        exclude_tags: Sequence[dict[str, str]] = (),
    ) -> list[dict[str, Any]]:
        """Live per-type counts for the filter sidebar — tags present in the
        *currently filtered* set (plan: /api/tags/facets)."""
        conn = self.conn
        where = ["1=1"]
        params: list[Any] = []
        if include_tags:
            for t in include_tags:
                where.append(
                    "g.id IN (SELECT gallery_id FROM gallery_tags gt JOIN tags t ON "
                    "t.id=gt.tag_id AND t.name_norm=? AND t.type=?)"
                )
                params += [normalize_tag_name(t["name"]), normalize_type(t.get("type", "general"))]
        if exclude_tags:
            for t in exclude_tags:
                where.append(
                    "g.id NOT IN (SELECT gallery_id FROM gallery_tags gt JOIN tags t ON "
                    "t.id=gt.tag_id AND t.name_norm=? AND t.type=?)"
                )
                params += [normalize_tag_name(t["name"]), normalize_type(t.get("type", "general"))]
        sql = (
            "SELECT t.type, count(DISTINCT t.id) AS tag_count, count(DISTINCT g.id) AS gallery_count "
            "FROM tags t JOIN gallery_tags gt ON gt.tag_id=t.id "
            f"JOIN galleries g ON g.id=gt.gallery_id WHERE {' AND '.join(where)} "
            "GROUP BY t.type ORDER BY t.type"
        )
        rows = conn.execute(sql, params).fetchall()
        return {
            "types": [dict(r) for r in rows],
        }

    async def tag_facets(self, **kwargs) -> dict[str, Any]:
        return await self._run(self._tag_facets, **kwargs)

    def _tags_by_group(self) -> dict[str, list[dict]]:
        """Tags browser: all tags in the catalogue grouped by type, with counts."""
        conn = self.conn
        rows = conn.execute(
            "SELECT t.id, t.name, t.name_norm, t.type, t.count, "
            "count(gt.gallery_id) AS gallery_count "
            "FROM tags t JOIN gallery_tags gt ON gt.tag_id=t.id "
            "GROUP BY t.id ORDER BY t.type, gallery_count DESC, t.name_norm"
        ).fetchall()
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["type"], []).append(dict(r))
        return groups

    async def tags_by_group(self) -> dict[str, list[dict]]:
        return await self._run(self._tags_by_group)

    # --------------------------------------------------------------- collections

    def _collections(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.*, (SELECT count(*) FROM collection_galleries cg "
            "WHERE cg.collection_id=c.id) AS gallery_count FROM collections c ORDER BY c.name"
        ).fetchall()
        return [dict(r) for r in rows]

    async def collections(self) -> list[dict]:
        return await self._run(self._collections)

    def _collection_create(self, name: str, description: str = "") -> dict:
        cur = self.conn.execute(
            "INSERT INTO collections(name, description) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description",
            (name.strip(), description),
        )
        cid = cur.lastrowid
        row = self.conn.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
        out = dict(row)
        out["gallery_count"] = 0
        return out

    async def collection_create(self, name: str, description: str = "") -> dict:
        return await self._run(self._collection_create, name, description)

    def _collection_delete(self, collection_id: int) -> None:
        self.conn.execute("DELETE FROM collections WHERE id=?", (int(collection_id),))

    async def collection_delete(self, collection_id: int) -> None:
        await self._run(self._collection_delete, int(collection_id))

    def _collection_add(self, collection_id: int, gallery_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO collection_galleries(collection_id, gallery_id) "
            "VALUES (?, ?)",
            (int(collection_id), int(gallery_id)),
        )

    async def collection_add(self, collection_id: int, gallery_id: int) -> None:
        await self._run(self._collection_add, int(collection_id), int(gallery_id))

    def _collection_remove(self, collection_id: int, gallery_id: int) -> None:
        self.conn.execute(
            "DELETE FROM collection_galleries WHERE collection_id=? AND gallery_id=?",
            (int(collection_id), int(gallery_id)),
        )

    async def collection_remove(self, collection_id: int, gallery_id: int) -> None:
        await self._run(self._collection_remove, int(collection_id), int(gallery_id))

    # ---------------------------------------------------------------- watchlists

    def _watchlists(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM watchlists ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    async def watchlists(self) -> list[dict]:
        return await self._run(self._watchlists)

    def _watchlist_create(self, name: str, query: str, auto_download: bool) -> dict:
        cur = self.conn.execute(
            "INSERT INTO watchlists(name, query, auto_download) VALUES (?,?,?)",
            (name.strip(), query.strip(), 1 if auto_download else 0),
        )
        row = self.conn.execute("SELECT * FROM watchlists WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    async def watchlist_create(self, name: str, query: str, auto_download: bool = False) -> dict:
        return await self._run(self._watchlist_create, name, query, auto_download)

    def _watchlist_update(self, watchlist_id: int, patch: dict[str, Any]) -> dict | None:
        allowed = {"name", "query", "auto_download", "enabled", "last_seen_id", "unread_count", "last_sync"}
        sets, vals = [], []
        for k, v in patch.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(1 if (k in ("auto_download", "enabled") and v) else (0 if k in ("auto_download", "enabled") else v))
        if sets:
            vals.append(int(watchlist_id))
            self.conn.execute(f"UPDATE watchlists SET {', '.join(sets)} WHERE id=?", vals)
        row = self.conn.execute("SELECT * FROM watchlists WHERE id=?", (int(watchlist_id),)).fetchone()
        return dict(row) if row else None

    async def watchlist_update(self, watchlist_id: int, patch: dict[str, Any]) -> dict | None:
        return await self._run(self._watchlist_update, int(watchlist_id), patch)

    def _watchlist_delete(self, watchlist_id: int) -> None:
        self.conn.execute("DELETE FROM watchlists WHERE id=?", (int(watchlist_id),))

    async def watchlist_delete(self, watchlist_id: int) -> None:
        await self._run(self._watchlist_delete, int(watchlist_id))

    # ---------------------------------------------------------------- downloads

    def _queue_add(self, gallery_id: int, pages_total: int) -> int | None:
        # dedup: one active queue row per gallery
        row = self.conn.execute(
            "SELECT id FROM download_queue WHERE gallery_id=? AND status IN "
            "('queued','running','paused')",
            (int(gallery_id),),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO download_queue(gallery_id, pages_total) VALUES (?,?)",
            (int(gallery_id), int(pages_total)),
        )
        return int(cur.lastrowid)

    async def queue_add(self, gallery_id: int, pages_total: int) -> int | None:
        return await self._run(self._queue_add, int(gallery_id), int(pages_total))

    def _queue_update(self, queue_id: int, patch: dict[str, Any]) -> dict | None:
        allowed = {"status", "pages_done", "pages_total", "progress", "retries", "error", "folder_path"}
        sets, vals = [], []
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "progress" and isinstance(v, float):
                v = round(v, 2)
            sets.append(f"{k}=?")
            vals.append(v)
        if "status" in patch:
            if patch["status"] == "running":
                sets.append("started_at=datetime('now')")
            if patch["status"] in ("done", "error", "canceled"):
                sets.append("finished_at=datetime('now')")
        if sets:
            vals.append(int(queue_id))
            self.conn.execute(f"UPDATE download_queue SET {', '.join(sets)} WHERE id=?", vals)
        row = self.conn.execute("SELECT * FROM download_queue WHERE id=?", (int(queue_id),)).fetchone()
        return dict(row) if row else None

    async def queue_update(self, queue_id: int, patch: dict[str, Any]) -> dict | None:
        return await self._run(self._queue_update, int(queue_id), patch)

    def _queue_list(self, status: str = "") -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT q.*, g.title_pretty, g.title_en FROM download_queue q "
                "JOIN galleries g ON g.id=q.gallery_id WHERE q.status=? "
                "ORDER BY q.id DESC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT q.*, g.title_pretty, g.title_en FROM download_queue q "
                "JOIN galleries g ON g.id=q.gallery_id ORDER BY q.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    async def queue_list(self, status: str = "") -> list[dict]:
        return await self._run(self._queue_list, status)

    def _queue_by_gallery(self, gallery_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM download_queue WHERE gallery_id=? ORDER BY id DESC LIMIT 1",
            (int(gallery_id),),
        ).fetchone()
        return dict(row) if row else None

    async def queue_by_gallery(self, gallery_id: int) -> dict | None:
        return await self._run(self._queue_by_gallery, int(gallery_id))

    # ------------------------------------------------------------------ helpers

    def _all_galleries(self, limit: int = 0) -> list[dict]:
        sql = "SELECT * FROM galleries"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    async def all_galleries(self, limit: int = 0) -> list[dict]:
        return await self._run(self._all_galleries, limit)

    def _gallery_ids(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT id FROM galleries").fetchall()]

    async def gallery_ids(self) -> list[int]:
        return await self._run(self._gallery_ids)

    def _count_galleries(self) -> int:
        return self.conn.execute("SELECT count(*) FROM galleries").fetchone()[0]

    async def count_galleries(self) -> int:
        return await self._run(self._count_galleries)
