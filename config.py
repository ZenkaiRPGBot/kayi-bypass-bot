import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

BOT_START_TIME = datetime.now(timezone.utc)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "986380678"))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "-1004495645007").strip()

BRAND_NAME = "Kayi Bypass"
BOT_USERNAME = "@KayiBypassBot"
CHANNEL_USERNAME = "@KayiBots"

PORT = int(os.getenv("PORT", "8080"))
PING_URL = os.getenv("PING_URL") or os.getenv("RENDER_EXTERNAL_URL")

MAX_REDIRECT_HOPS = 15
RATE_LIMIT_PER_MINUTE = 10
