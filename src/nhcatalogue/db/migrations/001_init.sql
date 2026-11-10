-- 001: core catalogue tables (plan §2)
CREATE TABLE IF NOT EXISTS galleries (
    id INTEGER PRIMARY KEY,
    media_id TEXT NOT NULL DEFAULT '',
    title_en TEXT NOT NULL DEFAULT '',
    title_ja TEXT NOT NULL DEFAULT '',
    title_pretty TEXT NOT NULL DEFAULT '',
    num_pages INTEGER NOT NULL DEFAULT 0,
    upload_date INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    scanlator TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    -- user data
    date_added TEXT NOT NULL DEFAULT (datetime('now')),
    favorite INTEGER NOT NULL DEFAULT 0,
    rating INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    read_progress_pages INTEGER NOT NULL DEFAULT 0,
    last_read_at TEXT NOT NULL DEFAULT '',
    download_status TEXT NOT NULL DEFAULT 'none',
    folder_path TEXT NOT NULL DEFAULT '',
    cover_path TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_galleries_added ON galleries(date_added DESC);
CREATE INDEX IF NOT EXISTS idx_galleries_download_status ON galleries(download_status);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    UNIQUE(name_norm, type)
);

-- fast type-ahead tag search (plan §2)
CREATE INDEX IF NOT EXISTS idx_tags_name_norm ON tags(name_norm);
CREATE INDEX IF NOT EXISTS idx_tags_type_count ON tags(type, count DESC);

CREATE TABLE IF NOT EXISTS gallery_tags (
    gallery_id INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (gallery_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_gt_tag ON gallery_tags(tag_id, gallery_id);
CREATE INDEX IF NOT EXISTS idx_gt_gallery ON gallery_tags(gallery_id, tag_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
