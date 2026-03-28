-- Add render_source JSONB column for retry render functionality (STAB-03)
-- Stores the full Creatomate source JSON so failed renders can be re-submitted
ALTER TABLE content_items ADD COLUMN IF NOT EXISTS render_source JSONB;
