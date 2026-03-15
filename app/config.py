import os
from dotenv import load_dotenv

load_dotenv(override=True)

class Settings:
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("BOT_TOKEN", ""))
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_KEY", ""))
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    CREATOMATE_API_KEY: str = os.getenv("CREATOMATE_API_KEY", "")
    GCS_BUCKET_NAME: str = os.getenv("GCS_BUCKET", "")
    DRIVE_TALKING_HEAD_FOLDER_ID: str = os.getenv("DRIVE_TALKING_HEAD_FOLDER_ID", "")
    DRIVE_STORYBOARD_FOLDER_ID: str = os.getenv("DRIVE_STORYBOARD_FOLDER_ID", "")
    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
    BASE_URL: str = os.getenv("BASE_URL", "")  # public URL for webhooks (e.g. Cloud Run URL)

settings = Settings()
