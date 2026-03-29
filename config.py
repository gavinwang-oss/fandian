import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
    APP_ENV = os.getenv("APP_ENV", "development")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///hotel.db")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
    OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    TWILIO_SID = os.getenv("TWILIO_SID", "")
    TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "")
    TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "")

    STOP_RESPONSE = os.getenv(
        "STOP_RESPONSE",
        "You have been opted out and will no longer receive messages. Reply HELP for assistance.",
    )
    HELP_RESPONSE = os.getenv(
        "HELP_RESPONSE",
        "Fandian support: reply with your request, or reply STOP to opt out.",
    )

    RATE_LIMIT_COUNT = int(os.getenv("RATE_LIMIT_COUNT", "10"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300"))

    DISABLE_TWILIO_VALIDATION = os.getenv("DISABLE_TWILIO_VALIDATION", "false").lower() == "true"

    BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    BOOTSTRAP_HOTEL_NAME = os.getenv("BOOTSTRAP_HOTEL_NAME", "Demo Hotel")
    BOOTSTRAP_HOTEL_PHONE = os.getenv("BOOTSTRAP_HOTEL_PHONE", "")
