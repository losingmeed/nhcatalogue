-- 003: user shelves, watchlists and the download queue
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collection_galleries (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    gallery_id INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (collection_id, gallery_id)
);

CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    auto_download INTEGER NOT NULL DEFAULT 0,
    last_seen_id INTEGER NOT NULL DEFAULT 0,
    last_sync TEXT NOT NULL DEFAULT '',
    unread_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS download_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gallery_id INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued|running|paused|done|error|canceled
    pages_done INTEGER NOT NULL DEFAULT 0,
    pages_total INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0.0,      -- 0..100
    retries INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    folder_path TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON download_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_gallery ON download_queue(gallery_id);
