-- Add feedback_history JSONB column for redo feedback tracking (FEED-01/FEED-02)
ALTER TABLE content_items
ADD COLUMN IF NOT EXISTS feedback_history jsonb DEFAULT '[]'::jsonb;

-- Verify
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'content_items' AND column_name = 'feedback_history';
