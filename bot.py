#!/usr/bin/env python3
"""
Telegram Auto-Post Bot for HDhub4u Content
Admin-only bot that automatically posts content to Telegram channels
"""

import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import Database
from scraper import HDhub4uScraper
from cache_manager import CacheManager
from bypass import resolve_links_batch

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.getenv('BOT_TOKEN')
ADMIN_IDS  = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]

db        = Database()
scraper   = HDhub4uScraper()
cache     = CacheManager()
scheduler = AsyncIOScheduler(timezone="UTC")

PLOT_PREVIEW_LIMIT = 200

# ── helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def _escape_md(value) -> str:
    if value is None:
        return ''
    return escape_markdown(str(value), version=1)

async def _check_admin(update: Update) -> bool:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ Access denied. This bot is for admins only."
        )
        return False
    return True

def _get_post_mode() -> str:
    """Returns 'text' or 'button' (default: button)"""
    return db.get_setting('post_mode') or 'button'

# ── command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return

    channel    = _escape_md(db.get_setting('channel') or 'Not set')
    timer      = _escape_md(db.get_setting('timer') or '5')
    auto_on    = db.get_setting('auto_post_enabled') == 'true'
    post_mode  = _get_post_mode()

    text = (
        "🤖 *HDhub4u Auto-Post Bot*\n\n"
        "*Commands:*\n"
        "/setchannel — Set target channel\n"
        "/settimer — Set interval (minutes)\n"
        "/postmode — Toggle text/button link mode\n"
        "/status — Bot status\n"
        "/posted — Post history\n"
        "/start\\_autopost — Start auto-posting\n"
        "/stop\\_autopost — Stop auto-posting\n"
        "/force\\_post — Manual post now\n"
        "/stats — Statistics\n\n"
        f"*Channel:* `{channel}`\n"
        f"*Timer:* {timer} min\n"
        f"*Auto-post:* {'✅ Active' if auto_on else '❌ Inactive'}\n"
        f"*Link mode:* {'📝 Text (inline)' if post_mode == 'text' else '🔘 Button'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "📢 Usage: `/setchannel @mychannel` or `/setchannel -1001234567890`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    channel = context.args[0]
    db.set_setting('channel', channel)
    await update.message.reply_text(
        f"✅ Channel set to: `{channel}`\nMake sure the bot is an admin there!",
        parse_mode=ParseMode.MARKDOWN
    )


async def set_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "⏱️ Usage: `/settimer 5` (minutes)", parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        minutes = int(context.args[0])
        if minutes < 1:
            await update.message.reply_text("⚠️ Must be at least 1 minute")
            return
        db.set_setting('timer', str(minutes))
        if db.get_setting('auto_post_enabled') == 'true':
            _restart_scheduler(context.application)
        await update.message.reply_text(f"✅ Interval set to {minutes} minutes")
    except ValueError:
        await update.message.reply_text("⚠️ Please provide a valid number")


async def postmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between text-link mode and button mode."""
    if not await _check_admin(update):
        return

    current = _get_post_mode()
    new_mode = 'text' if current == 'button' else 'button'
    db.set_setting('post_mode', new_mode)

    if new_mode == 'text':
        await update.message.reply_text(
            "📝 *Text mode enabled*\n\n"
            "Links will appear inline in the post like:\n"
            "┠ ➥ FSLv2 Server : https://...\n"
            "┠ ➥ Pixeldrain : https://...\n\n"
            "HubDrive links will be auto-bypassed to direct links.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "🔘 *Button mode enabled*\n\n"
            "Links will appear as inline keyboard buttons below the post.",
            parse_mode=ParseMode.MARKDOWN
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return

    channel   = _escape_md(db.get_setting('channel') or 'Not set')
    timer     = _escape_md(db.get_setting('timer') or '5')
    auto_on   = db.get_setting('auto_post_enabled') == 'true'
    post_mode = _get_post_mode()

    text = (
        "📊 *Bot Status*\n\n"
        f"• Channel: `{channel}`\n"
        f"• Timer: {timer} min\n"
        f"• Auto-posting: {'✅ Active' if auto_on else '❌ Inactive'}\n"
        f"• Link mode: {'📝 Text' if post_mode == 'text' else '🔘 Button'}\n\n"
        f"• Total posts: {db.get_total_posts()}\n"
        f"• Last post: {_escape_md(db.get_last_post_time() or 'Never')}\n"
        f"• Cache entries: {cache.size()}\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def posted_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    posts = db.get_recent_posts(limit=10)
    if not posts:
        await update.message.reply_text("📝 No posts yet!")
        return
    lines = ["*Recent Posts:*\n"]
    for p in posts:
        lines.append(f"• {_escape_md(p['title'])}\n  _{_escape_md(p['posted_at'])}_\n")
    await update.message.reply_text('\n'.join(lines), parse_mode=ParseMode.MARKDOWN)


async def start_autopost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    if not db.get_setting('channel'):
        await update.message.reply_text("⚠️ Set a channel first: /setchannel")
        return
    db.set_setting('auto_post_enabled', 'true')
    _restart_scheduler(context.application)
    timer = db.get_setting('timer') or '5'
    await update.message.reply_text(
        f"✅ Auto-posting started! Every {timer} minutes."
    )


async def stop_autopost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    db.set_setting('auto_post_enabled', 'false')
    scheduler.remove_all_jobs()
    await update.message.reply_text("⏸️ Auto-posting stopped!")


async def force_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    channel = db.get_setting('channel')
    if not channel:
        await update.message.reply_text("⚠️ Set a channel first: /setchannel")
        return
    msg = await update.message.reply_text("🔄 Fetching & posting...")
    try:
        await post_to_channel(context.application, channel, force=True)
        await msg.edit_text("✅ Done!")
    except Exception as e:
        logger.error(f"force_post error: {e}", exc_info=True)
        await msg.edit_text(f"❌ Error: {str(e)}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    text = (
        "📈 *Statistics*\n\n"
        f"• Total posts: {db.get_total_posts()}\n"
        f"• Today: {db.get_posts_count_today()}\n"
        f"• Cache entries: {cache.size()}\n"
        f"• Cache hit rate: {cache.get_hit_rate():.1f}%\n"
        f"• DB size: {db.get_size_mb():.2f} MB\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── posting core ──────────────────────────────────────────────────────────────

async def post_to_channel(application, channel: str, force: bool = False):
    try:
        content = await scraper.get_latest_content(cache)
    except Exception as e:
        logger.error(f"Scrape error: {e}", exc_info=True)
        return

    if not content:
        logger.warning("No content from scraper")
        return

    post_mode   = _get_post_mode()
    posted_count = 0

    for item in content:
        if db.is_posted(item['url']):
            logger.info(f"Skip duplicate: {item['title']}")
            continue

        # Fetch raw links
        try:
            raw_links = await scraper.get_download_links(item['url'], cache)
        except Exception as e:
            logger.error(f"Link fetch error: {e}")
            raw_links = []

        # Resolve HubDrive → direct links when in text mode
        if post_mode == 'text' and raw_links:
            try:
                resolved = await resolve_links_batch(raw_links)
            except Exception as e:
                logger.error(f"Bypass error: {e}", exc_info=True)
                resolved = [{"label": l.get("server", "Download"), "url": l["url"],
                             "quality": l.get("quality", "")} for l in raw_links]
        else:
            resolved = [{"label": l.get("server", "Download"), "url": l["url"],
                         "quality": l.get("quality", "")} for l in raw_links]

        item['resolved_links'] = resolved

        # Build message + markup depending on mode
        if post_mode == 'text':
            message  = format_post_text_mode(item)
            keyboard = None
        else:
            message  = format_post_button_mode(item)
            keyboard = create_download_keyboard(item)

        # Send
        try:
            send_kwargs = dict(
                chat_id=channel,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )

            if item.get('poster_url'):
                await application.bot.send_photo(
                    photo=item['poster_url'],
                    caption=message,
                    **send_kwargs,
                )
            else:
                await application.bot.send_message(
                    text=message,
                    **send_kwargs,
                )

            db.add_post(item['title'], item['url'])
            logger.info(f"Posted: {item['title']}")
            posted_count += 1

            if posted_count >= 3:
                break

            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Send error for {item['title']}: {e}", exc_info=True)
            continue

    if posted_count == 0:
        logger.info("Nothing new to post")


# ── message formatters ────────────────────────────────────────────────────────

def _header(item: dict) -> str:
    """Common header lines shared by both modes."""
    title   = _escape_md(item.get('title', 'Unknown'))
    quality = _escape_md(item.get('quality', ''))

    genre_raw = item.get('genre', [])
    genre = ', '.join(_escape_md(g) for g in genre_raw) if isinstance(genre_raw, (list, tuple)) \
            else _escape_md(genre_raw)

    year   = _escape_md(item.get('year', ''))
    rating = _escape_md(item.get('rating', ''))

    plot_raw = item.get('plot', '')
    plot = ''
    if plot_raw:
        shortened = plot_raw[:PLOT_PREVIEW_LIMIT]
        plot = _escape_md(shortened) + ('...' if len(plot_raw) > PLOT_PREVIEW_LIMIT else '')

    msg = f"🎬 *{title}*"
    if quality: msg += f"\n\n📊 Quality: {quality}"
    if year:    msg += f"\n📅 Year: {year}"
    if rating:  msg += f"\n⭐ Rating: {rating}"
    if genre:   msg += f"\n🎭 Genre: {genre}"
    if plot:    msg += f"\n\n📝 {plot}"
    return msg


def format_post_text_mode(item: dict) -> str:
    """
    Format post with links shown inline as plain text (no buttons).

    Example:
    ┠ ➥ FSLv2 Server : https://...
    ┠ ➥ Pixeldrain   : https://...
    ┖ ➥ Google Drive : https://...
    """
    msg = _header(item)
    links = item.get('resolved_links', [])

    if links:
        msg += "\n\n📥 *Download Links:*"
        for i, link in enumerate(links[:10]):
            label = _escape_md(link.get('label', 'Download'))
            url   = link.get('url', '')
            prefix = "┠" if i < len(links) - 1 else "┖"
            msg += f"\n{prefix} ➥ {label} : {url}"

    return msg


def format_post_button_mode(item: dict) -> str:
    """Format post for button mode (no links in text)."""
    msg = _header(item)
    count = len(item.get('resolved_links', []))
    if count:
        word = 'Link' if count == 1 else 'Links'
        msg += f"\n\n💾 {count} Download {word} Available"
        msg += "\n👇 _Click the buttons below to download_"
    return msg


def create_download_keyboard(item: dict) -> InlineKeyboardMarkup:
    buttons = []
    quality_map = {
        '4K': '🎥 4K UHD', '2160p': '🎥 4K UHD',
        '1080p': '📺 1080p FHD', '720p': '📱 720p HD',
        '480p': '📱 480p SD',
    }
    for i, link in enumerate(item.get('resolved_links', [])[:8]):
        label   = link.get('label', f'Link {i+1}')
        quality = link.get('quality', '')
        text    = quality_map.get(quality, f'📥 {label}')
        buttons.append([InlineKeyboardButton(text, url=link['url'])])
    if item.get('url'):
        buttons.append([InlineKeyboardButton('ℹ️ More Info', url=item['url'])])
    return InlineKeyboardMarkup(buttons) if buttons else None


# ── scheduler ─────────────────────────────────────────────────────────────────

def _restart_scheduler(application):
    scheduler.remove_all_jobs()
    if db.get_setting('auto_post_enabled') != 'true':
        return
    timer   = int(db.get_setting('timer') or '5')
    channel = db.get_setting('channel')
    if not channel:
        return
    scheduler.add_job(
        post_to_channel,
        'interval',
        minutes=timer,
        args=[application, channel],
        id='auto_post',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info(f"Scheduler: every {timer}m → {channel}")


# ── error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An internal error occurred. Please try again."
            )
        except Exception:
            pass


# ── startup ───────────────────────────────────────────────────────────────────

async def post_init(application):
    logger.info("Bot initialised")
    if db.get_setting('auto_post_enabled') == 'true':
        _restart_scheduler(application)


def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN not set")
        return
    if not ADMIN_IDS:
        logger.critical("ADMIN_IDS not set")
        return

    scheduler.start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",          start))
    app.add_handler(CommandHandler("setchannel",     set_channel))
    app.add_handler(CommandHandler("settimer",       set_timer))
    app.add_handler(CommandHandler("postmode",       postmode))
    app.add_handler(CommandHandler("status",         status))
    app.add_handler(CommandHandler("posted",         posted_history))
    app.add_handler(CommandHandler("start_autopost", start_autopost))
    app.add_handler(CommandHandler("stop_autopost",  stop_autopost))
    app.add_handler(CommandHandler("force_post",     force_post))
    app.add_handler(CommandHandler("stats",          stats))

    app.add_error_handler(error_handler)

    logger.info("Polling started")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == '__main__':
    main()
