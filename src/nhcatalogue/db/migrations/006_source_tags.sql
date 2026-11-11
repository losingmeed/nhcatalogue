-- 006: multi-source support (rule34 alongside nhentai)
--
-- Rebuilds `tags` so a tag name can exist once per source, and adds a
-- `source` column to `galleries`.
--
-- SQLite cannot alter a UNIQUE constraint in place, so the tables are
-- rebuilt. `gallery_tags` is rebuilt alongside `tags` because SQLite
-- rewrites a child's REFERENCES clause when the parent table is renamed:
-- dropping the old parent would cascade-delete the child's rows. Rebuilding
-- both, and dropping the old child before the old parent, keeps every row.
DROP TRIGGER IF EXISTS galleries_ai;
DROP TRIGGER IF EXISTS galleries_ad;
DROP TRIGGER IF EXISTS galleries_au;
DROP TRIGGER IF EXISTS gallery_tags_ai;
DROP TRIGGER IF EXISTS gallery_tags_ad;

ALTER TABLE gallery_tags RENAME TO gallery_tags_old;
ALTER TABLE tags RENAME TO tags_old;

CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'nhentai',
    UNIQUE(source, name_norm, type)
);

INSERT INTO tags (id, name, name_norm, type, count, url, source)
SELECT id, name, name_norm, type, count, url, 'nhentai' FROM tags_old;

CREATE TABLE gallery_tags (
    gallery_id INTEGER NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (gallery_id, tag_id)
);

INSERT INTO gallery_tags (gallery_id, tag_id) SELECT gallery_id, tag_id FROM gallery_tags_old;

DROP TABLE gallery_tags_old;
DROP TABLE tags_old;

CREATE TRIGGER galleries_ai AFTER INSERT ON galleries BEGIN
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    VALUES (new.id, new.title_en, new.title_ja, new.title_pretty, '');
END;

CREATE TRIGGER galleries_ad AFTER DELETE ON galleries BEGIN
    DELETE FROM galleries_fts WHERE rowid = old.id;
END;

CREATE TRIGGER galleries_au AFTER UPDATE OF title_en, title_ja, title_pretty ON galleries BEGIN
    DELETE FROM galleries_fts WHERE rowid = new.id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    VALUES (new.id, new.title_en, new.title_ja, new.title_pretty, '');
END;

CREATE TRIGGER gallery_tags_ai AFTER INSERT ON gallery_tags BEGIN
    DELETE FROM galleries_fts WHERE rowid = new.gallery_id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    SELECT g.id, g.title_en, g.title_ja, g.title_pretty,
           COALESCE((SELECT group_concat(t.name, ' ') FROM gallery_tags gt
                     JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.gallery_id = g.id), '')
    FROM galleries g WHERE g.id = new.gallery_id;
END;

CREATE TRIGGER gallery_tags_ad AFTER DELETE ON gallery_tags BEGIN
    DELETE FROM galleries_fts WHERE rowid = old.gallery_id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    SELECT g.id, g.title_en, g.title_ja, g.title_pretty,
           COALESCE((SELECT group_concat(t.name, ' ') FROM gallery_tags gt
                     JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.gallery_id = g.id), '')
    FROM galleries g WHERE g.id = old.gallery_id;
END;

CREATE INDEX IF NOT EXISTS idx_tags_name_norm ON tags(name_norm);
CREATE INDEX IF NOT EXISTS idx_tags_type_count ON tags(type, count DESC);
CREATE INDEX IF NOT EXISTS idx_tags_source_name ON tags(source, name_norm);
CREATE INDEX IF NOT EXISTS idx_gt_tag ON gallery_tags(tag_id, gallery_id);
CREATE INDEX IF NOT EXISTS idx_gt_gallery ON gallery_tags(gallery_id, tag_id);

ALTER TABLE galleries ADD COLUMN source TEXT NOT NULL DEFAULT 'nhentai';

CREATE INDEX IF NOT EXISTS idx_galleries_source ON galleries(source);
