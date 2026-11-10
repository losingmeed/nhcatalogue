-- 002: FTS5 over titles + tag names (plan §2) with triggers to keep rows in sync.
-- A regular (non-external-content) FTS5 table keyed by rowid = galleries.id so
-- DELETE works directly when a row or its tags change.
CREATE VIRTUAL TABLE IF NOT EXISTS galleries_fts USING fts5(
    title_en, title_ja, title_pretty, tags
);

CREATE TRIGGER IF NOT EXISTS galleries_ai AFTER INSERT ON galleries BEGIN
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    VALUES (new.id, new.title_en, new.title_ja, new.title_pretty, '');
END;

CREATE TRIGGER IF NOT EXISTS galleries_ad AFTER DELETE ON galleries BEGIN
    DELETE FROM galleries_fts WHERE rowid = old.id;
END;

-- full refresh of one gallery's FTS row (titles + all tag names)
CREATE TRIGGER IF NOT EXISTS galleries_au AFTER UPDATE OF title_en, title_ja, title_pretty ON galleries BEGIN
    DELETE FROM galleries_fts WHERE rowid = new.id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    VALUES (new.id, new.title_en, new.title_ja, new.title_pretty, '');
END;

CREATE TRIGGER IF NOT EXISTS gallery_tags_ai AFTER INSERT ON gallery_tags BEGIN
    DELETE FROM galleries_fts WHERE rowid = new.gallery_id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    SELECT g.id, g.title_en, g.title_ja, g.title_pretty,
           COALESCE((SELECT group_concat(t.name, ' ') FROM gallery_tags gt
                     JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.gallery_id = g.id), '')
    FROM galleries g WHERE g.id = new.gallery_id;
END;

CREATE TRIGGER IF NOT EXISTS gallery_tags_ad AFTER DELETE ON gallery_tags BEGIN
    DELETE FROM galleries_fts WHERE rowid = old.gallery_id;
    INSERT INTO galleries_fts(rowid, title_en, title_ja, title_pretty, tags)
    SELECT g.id, g.title_en, g.title_ja, g.title_pretty,
           COALESCE((SELECT group_concat(t.name, ' ') FROM gallery_tags gt
                     JOIN tags t ON t.id = gt.tag_id
                     WHERE gt.gallery_id = g.id), '')
    FROM galleries g WHERE g.id = old.gallery_id;
END;
