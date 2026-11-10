-- 004: bytes downloaded, for live MB/s + ETA in the Downloads view
ALTER TABLE download_queue ADD COLUMN bytes_done INTEGER NOT NULL DEFAULT 0;
