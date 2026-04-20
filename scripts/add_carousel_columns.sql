-- Phase 7: Instagram Carousel Mode
-- Adds carousel_slides JSONB (per-slide structured data from Claude)
-- and stage_detail TEXT (sub-stage progress like 'assembling_slide_3/10').
-- Apply via Supabase SQL Editor. Idempotent.

ALTER TABLE content_items
    ADD COLUMN IF NOT EXISTS carousel_slides JSONB DEFAULT NULL;

ALTER TABLE content_items
    ADD COLUMN IF NOT EXISTS stage_detail TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_content_items_stage_detail
    ON content_items (stage_detail)
    WHERE stage_detail IS NOT NULL;
