-- Add flowchart_json to posts table.
-- Stores the TaskNode tree (Plausible Fiction categorical structure).
-- Full schema: weve-frontend/src/lib/pf/ct/types.ts :: TaskNode

ALTER TABLE posts
  ADD COLUMN IF NOT EXISTS flowchart_json jsonb DEFAULT NULL;
