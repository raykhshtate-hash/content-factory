-- Cost tracking columns for content_items table
-- Run against Supabase SQL Editor

ALTER TABLE content_items ADD COLUMN cost_whisper NUMERIC DEFAULT 0;
ALTER TABLE content_items ADD COLUMN cost_gemini NUMERIC DEFAULT 0;
ALTER TABLE content_items ADD COLUMN cost_claude NUMERIC DEFAULT 0;
ALTER TABLE content_items ADD COLUMN cost_creatomate NUMERIC DEFAULT 0;
ALTER TABLE content_items ADD COLUMN cost_total_usd NUMERIC DEFAULT 0;
ALTER TABLE content_items ADD COLUMN gemini_prompt_version TEXT;
ALTER TABLE content_items ADD COLUMN director_prompt_version TEXT;
