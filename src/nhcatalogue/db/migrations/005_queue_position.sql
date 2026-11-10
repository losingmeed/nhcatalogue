-- Big Pickle Phase 4: queue reordering support.
-- Add a ``position`` column so the download queue can be reordered (drag and
-- drop / up/down) independently of insertion order. Existing rows keep their
-- display order (newest first, matching the previous ``id DESC`` sort) via
-- the backfill below.
ALTER TABLE download_queue ADD COLUMN position INTEGER NOT NULL DEFAULT 0;

-- Backfill position so newest (highest id) sorts first, preserving the prior
-- ``ORDER BY id DESC`` display order.
UPDATE download_queue
   SET position = (
       SELECT count(*) FROM download_queue d2 WHERE d2.id > download_queue.id
   );

CREATE INDEX IF NOT EXISTS idx_queue_position ON download_queue(position);
