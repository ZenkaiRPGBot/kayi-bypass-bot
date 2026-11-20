import os
import sys
import time
import asyncio
from datetime import datetime, timezone
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    BOT_TOKEN, OWNER_ID, LOG_CHANNEL_ID, BOT_START_TIME,
    BRAND_NAME, BOT_USERNAME, CHANNEL_USERNAME, RATE_LIMIT_PER_MINUTE
)
from bypass_engine import extract_urls, resolve_url_chain
from keep_alive import start_web_server

dp = Dispatcher()
HTTP_SESSION: aiohttp.ClientSession | None = None
user_request_timestamps: dict[int, list[float]] = {}

def get_uptime_string() -> str:
    delta = datetime.now(timezone.utc) - BOT_START_TIME
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0 or days > 0: parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    history = user_request_timestamps.get(user_id, [])
    # keep only requests within the last 60s
    history = [t for t in history if now - t < 60]
    user_request_timestamps[user_id] = history
    if len(history) >= RATE_LIMIT_PER_MINUTE:
        return False
    user_request_timestamps[user_id].append(now)
    return True

def build_rich_restart_markdown() -> str:
    return f"""# {BRAND_NAME} Online

| Feature | Status |
| :--- | :---: |
| Bot Status | Online 🟢 |
| Bypass Engine | Ready 🟢 |
| Tracker Stripper | Active 🟢 |
| Inline Mode | Enabled 🟢 |

### What's New
- Unshortens all URL shorteners (bit.ly, tinyurl, cutt.ly, etc.)
- Strips 18+ marketing trackers (UTM, fbclid, gclid, etc.)
- Base64 destination parameter uncloaker
- Inline search query support across all chats
- Ecosystem logging to {CHANNEL_USERNAME}

*Ready to resolve links.*"""

async def send_rich_restart_notification(bot: Bot):
    rich_md = build_rich_restart_markdown()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendRichMessage"

    if OWNER_ID:
        try:
            payload = {"chat_id": OWNER_ID, "rich_message": {"markdown": rich_md}}
            if HTTP_SESSION:
                async with HTTP_SESSION.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        print(f"[restart] Restart notification sent to owner ({OWNER_ID})")
        except Exception as e:
            print(f"[restart] Notice: Could not send restart notification to owner: {e}")

    if LOG_CHANNEL_ID:
        try:
            payload = {"chat_id": LOG_CHANNEL_ID, "rich_message": {"markdown": rich_md}}
            if HTTP_SESSION:
                async with HTTP_SESSION.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    print(f"[log_channel] Restart log sent to channel: {LOG_CHANNEL_ID}")
        except Exception as e:
            print(f"[log_channel] Notice: Could not send log to channel: {e}")

async def log_bypass_event(bot: Bot, user: types.User, original_url: str, clean_url: str, hops: int):
    if not LOG_CHANNEL_ID:
        return
    text = (
        f"🔍 <b>Link Bypassed ({BRAND_NAME})</b>\n\n"
        f"• <b>User:</b> {user.full_name} (<code>{user.id}</code>)\n"
        f"• <b>Original:</b> <code>{original_url}</code>\n"
        f"• <b>Destination:</b> {clean_url}\n"
        f"• <b>Hops:</b> <code>{hops}</code>"
    )
    try:
        await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"[log] Error logging bypass event: {e}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    channel_url = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"📢 Join {CHANNEL_USERNAME}", url=channel_url))
    
    welcome_text = (
        f"<b>🦅 Welcome to {BRAND_NAME}!</b>\n\n"
        "🔗 <b>Instant Link Unshortener & Tracker Cleaner</b>\n\n"
        "<b>What I do:</b>\n"
        "• <b>Unshorten Links:</b> Expands bit.ly, tinyurl, cutt.ly, and ad-gate redirect chains.\n"
        "• <b>Strip Trackers:</b> Automatically removes <code>utm_*</code>, <code>fbclid</code>, <code>gclid</code>, and spyware query tags.\n"
        "• <b>Reveal Destinations:</b> See where suspicious links lead before clicking!\n\n"
        "<b>How to use:</b>\n"
        "Simply send or forward any link directly into this chat 👇"
    )
    await message.reply(welcome_text, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup())

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message, bot: Bot):
    t0 = time.perf_counter()
    await bot.get_me()
    latency = (time.perf_counter() - t0) * 1000
    uptime = get_uptime_string()
    response = (
        f"<b>{BRAND_NAME} — Pong!</b>\n\n"
        f"• <b>Speed:</b> <code>{latency:.1f} ms</code>\n"
        f"• <b>Uptime:</b> <code>{uptime}</code>\n"
        f"• <b>Status:</b> <code>Healthy</code>"
    )
    await message.reply(response, parse_mode=ParseMode.HTML)

@dp.message(Command("restart"))
async def cmd_restart(message: types.Message, bot: Bot):
    if message.from_user.id != OWNER_ID:
        await message.reply("Access denied. Owner only.")
        return
    await message.reply("<b>Restarting...</b>", parse_mode=ParseMode.HTML)
    if HTTP_SESSION and not HTTP_SESSION.closed:
        await HTTP_SESSION.close()
    await bot.session.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

@dp.inline_query()
async def inline_query_handler(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    urls = extract_urls(query)
    if not urls:
        return

    target_url = urls[0]
    result = await resolve_url_chain(HTTP_SESSION, target_url)
    clean_url = result["clean_url"]

    item = types.InlineQueryResultArticle(
        id="bypass_1",
        title="Clean Destination URL",
        description=clean_url,
        input_message_content=types.InputTextMessageContent(
            message_text=(
                f"🦅 <b>{BRAND_NAME} — Clean Link</b>\n\n"
                f"🎯 <b>Clean URL:</b> {clean_url}\n"
                f"<i>Original: {target_url}</i>"
            ),
            parse_mode=ParseMode.HTML
        )
    )
    await inline_query.answer([item], cache_time=10)

@dp.message(F.text)
async def handle_text_links(message: types.Message, bot: Bot):
    if message.text.startswith("/"):
        return

    urls = extract_urls(message.text)
    if not urls:
        return

    user_id = message.from_user.id
    if not check_rate_limit(user_id):
        await message.reply("⏳ <b>Rate Limit:</b> Please slow down. Max 10 link checks per minute.", parse_mode=ParseMode.HTML)
        return

    status_msg = await message.reply("<code>Resolving redirect chain...</code>", parse_mode=ParseMode.HTML)

    for url in urls[:3]:  # Process up to 3 links per message
        data = await resolve_url_chain(HTTP_SESSION, url)
        clean_url = data["clean_url"]
        hops = data["hops"]
        removed = data["removed_trackers"]
        title = data.get("page_title")

        tracker_str = ", ".join(removed) if removed else "None detected"
        title_section = f"📑 <b>Page Title:</b> {title}\n" if title else ""

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="🌐 Open Clean Link", url=clean_url))
        channel_url = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
        builder.row(types.InlineKeyboardButton(text=f"📢 Join {CHANNEL_USERNAME}", url=channel_url))

        response = (
            f"<b>🦅 {BRAND_NAME} — Link Resolved!</b>\n\n"
            f"{title_section}"
            f"🎯 <b>Original:</b>\n<code>{url}</code>\n\n"
            f"✅ <b>Clean Destination:</b>\n<code>{clean_url}</code> <i>(Tap to copy)</i>\n\n"
            f"📊 <b>Redirect Hops:</b> <code>{hops}</code>\n"
            f"🛡️ <b>Cleaned Trackers:</b> <code>{tracker_str}</code>"
        )

        await message.reply(
            response,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
            link_preview_options=types.LinkPreviewOptions(is_disabled=True)
        )

        # Log event
        asyncio.create_task(log_bypass_event(bot, message.from_user, url, clean_url, hops))

    await status_msg.delete()

async def main():
    global HTTP_SESSION
    if not BOT_TOKEN:
        print("\nERROR: BOT_TOKEN is missing in .env!\n")
        sys.exit(1)

    print(f"Launching {BRAND_NAME} Bot...")
    connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300, ssl=False, enable_cleanup_closed=True)
    HTTP_SESSION = aiohttp.ClientSession(connector=connector)

    bot = Bot(token=BOT_TOKEN)
    runner = await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)

    await send_rich_restart_notification(bot)

    print(f"{BRAND_NAME} Bot is actively polling. Ready to receive requests.")
    try:
        await dp.start_polling(bot)
    finally:
        if HTTP_SESSION and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()
        await bot.session.close()
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot stopped.")
