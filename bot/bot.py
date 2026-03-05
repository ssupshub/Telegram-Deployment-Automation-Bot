"""
Telegram Deployment Automation Bot
====================================
Production-ready bot for triggering deployments via Telegram.
Supports staging/production deployments, rollbacks, and real-time logs.

Architecture:
  Telegram → Bot Handler → RBAC Check → Deployment Script → Health Check → Notify
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

from config import Config
from rbac import require_role, Role
from audit_logger import AuditLogger
from deployment import DeploymentManager

# ── Logging Setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
audit = AuditLogger()
deploy_manager = DeploymentManager()

# Fix #5: in-flight deploy lock — prevents double-deploy from a double-tap
# or a replayed callback. Maps environment → True while a deploy is running.
_deploying: set[str] = set()


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_user_info(update: Update) -> dict:
    """
    Extract user info from update for audit logging.

    Fix #3: guard against None effective_user (can occur for certain update
    types, and handle_callback is not protected by @require_role).
    """
    user = update.effective_user
    if user is None:
        return {"id": None, "username": "unknown", "full_name": "unknown"}
    return {
        "id": user.id,
        "username": user.username or "unknown",
        "full_name": user.full_name,
    }


def _escape_html(text: str) -> str:
    """Escape characters that are special in Telegram HTML mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_chunked(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """
    Send long messages in Telegram-safe 4096-char chunks using HTML mode.
    Content is HTML-escaped so raw shell output never breaks markup.
    """
    chunk_size = 4000
    for i in range(0, len(text), chunk_size):
        chunk = _escape_html(text[i: i + chunk_size])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"<pre>{chunk}</pre>",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(0.3)  # stay within Telegram rate limits


def _is_error_line(line: str) -> bool:
    """
    Return True only for sentinel error lines emitted by DeploymentManager.
    Uses a specific prefix to avoid false-positives on normal log lines that
    happen to contain the word "error".
    """
    return line.startswith("ERROR:") or line.startswith("ERROR during")


async def _stream_to_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    generator,
) -> tuple[bool, list[str]]:
    """
    Consume an async generator of log lines, send them to Telegram in batches,
    and return (success, all_lines).

    Fix #19: buffer flushes both on count (10 lines) AND on time (every 2s),
    so the user sees progress even if a deploy script goes quiet mid-run.
    """
    success = True
    log_buffer: list[str] = []
    all_lines: list[str] = []
    last_flush = asyncio.get_event_loop().time()

    async def flush():
        nonlocal log_buffer, last_flush
        if log_buffer:
            await send_chunked(context, chat_id, "\n".join(log_buffer))
            log_buffer = []
            last_flush = asyncio.get_event_loop().time()

    async for line in generator:
        all_lines.append(line)
        log_buffer.append(line)
        if _is_error_line(line):
            success = False

        now = asyncio.get_event_loop().time()
        if len(log_buffer) >= 10 or (now - last_flush) >= 2.0:
            await flush()

    await flush()  # drain any remaining lines
    return success, all_lines


# ── Command Handlers ───────────────────────────────────────────────────────────

@require_role(Role.STAGING)
async def cmd_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deploy <environment> — triggers a deployment."""
    user = get_user_info(update)
    args = context.args

    if not args or args[0] not in ("staging", "production"):
        await update.message.reply_text(
            "⚠️ Usage: <code>/deploy staging</code> or <code>/deploy production</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    environment = args[0]

    if environment == "production":
        if not Config.is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Production deployments require admin role.")
            audit.log(user, "deploy_production_denied", {"env": environment})
            return
        await _confirm_production_deploy(update, context, user)
    else:
        await _run_deployment(update, context, environment, user)


async def _confirm_production_deploy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict,
) -> None:
    """Show inline confirmation buttons before production deploy."""
    # Fix #13: pass the explicit production branch from Config instead of
    # calling get_current_branch(), which returns the bot container's local
    # HEAD — not the branch being deployed.
    branch = Config.github_branch_production()
    commit_hash = deploy_manager.get_latest_commit(branch=branch)

    keyboard = [[
        InlineKeyboardButton("✅ Confirm Deploy", callback_data=f"deploy:production:{commit_hash}"),
        InlineKeyboardButton("❌ Cancel", callback_data="deploy:cancel"),
    ]]

    await update.message.reply_text(
        f"⚠️ <b>Production Deployment Confirmation</b>\n\n"
        f"👤 Initiated by: @{_escape_html(user['username'])}\n"
        f"🌿 Branch: <code>{_escape_html(branch)}</code>\n"
        f"🔖 Commit: <code>{_escape_html(commit_hash)}</code>\n"
        f"🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"Are you sure you want to deploy to <b>PRODUCTION</b>?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


@require_role(Role.ADMIN)
async def cmd_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rollback <environment> — rolls back to the previous stable deployment."""
    user = get_user_info(update)
    args = context.args

    if not args or args[0] not in ("staging", "production"):
        await update.message.reply_text(
            "⚠️ Usage: <code>/rollback staging</code> or <code>/rollback production</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    environment = args[0]
    audit.log(user, "rollback_initiated", {"env": environment})

    await update.message.reply_text(
        f"⏪ Initiating rollback for <b>{_escape_html(environment)}</b>...",
        parse_mode=ParseMode.HTML,
    )

    rollback_success, _ = await _stream_to_chat(
        context,
        update.effective_chat.id,
        deploy_manager.run_rollback(environment),
    )

    if rollback_success:
        audit.log(user, "rollback_completed", {"env": environment})
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Rollback for <b>{_escape_html(environment)}</b> complete.",
            parse_mode=ParseMode.HTML,
        )
    else:
        audit.log(user, "rollback_failed", {"env": environment})
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Rollback for <b>{_escape_html(environment)}</b> FAILED. Check logs above.",
            parse_mode=ParseMode.HTML,
        )


@require_role(Role.STAGING)
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — shows current deployment status for all environments."""
    user = get_user_info(update)
    audit.log(user, "status_checked", {})

    status = await deploy_manager.get_status()

    lines = ["📊 <b>Deployment Status</b>\n"]
    for env, info in status.items():
        emoji = "🟢" if info["healthy"] else "🔴"
        lines.append(
            f"{emoji} <b>{_escape_html(env.upper())}</b>\n"
            f"  • Commit: <code>{_escape_html(info['commit'])}</code>\n"
            f"  • Deployed: {_escape_html(info['deployed_at'])}\n"
            f"  • Health: {_escape_html(info['health_url'])}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available commands based on user role."""
    user_id = update.effective_user.id
    is_admin = Config.is_admin(user_id)
    is_authorized = Config.is_authorized(user_id)

    if not is_authorized:
        await update.message.reply_text("🚫 You are not authorized to use this bot.")
        return

    role = "Admin" if is_admin else "Staging User"
    text = (
        f"🤖 <b>Deployment Bot</b> — Role: <code>{role}</code>\n\n"
        f"<b>Available Commands:</b>\n"
        f"<code>/deploy staging</code> — Deploy to staging\n"
        f"<code>/status</code> — Check environment status\n"
    )
    if is_admin:
        text += (
            "<code>/deploy production</code> — Deploy to production (requires confirmation)\n"
            "<code>/rollback staging</code> — Rollback staging\n"
            "<code>/rollback production</code> — Rollback production\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── Callback Query Handler ─────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    # Fix #3: guard against None user before calling get_user_info
    if update.effective_user is None:
        return
    user = get_user_info(update)

    data = query.data
    parts = data.split(":", maxsplit=2)
    action = parts[0]

    if action == "deploy":
        if len(parts) < 2 or parts[1] == "cancel":
            await query.edit_message_text("❌ Deployment cancelled.")
            audit.log(user, "deploy_cancelled", {})
            return

        environment = parts[1]
        commit_hash = parts[2] if len(parts) > 2 else "unknown"

        # Security: re-verify admin role on callback (buttons can be replayed).
        if not Config.is_admin(update.effective_user.id):
            await query.edit_message_text("🚫 You no longer have permission for this action.")
            return

        # Fix #5: reject if a deploy for this environment is already in flight.
        if environment in _deploying:
            await query.edit_message_text(
                f"⏳ A deployment to <b>{_escape_html(environment)}</b> is already in progress. "
                f"Please wait for it to finish.",
                parse_mode=ParseMode.HTML,
            )
            return

        await query.edit_message_text(
            f"🚀 Deploying to <b>{_escape_html(environment)}</b>...",
            parse_mode=ParseMode.HTML,
        )
        await _run_deployment(update, context, environment, user, confirmed_commit=commit_hash)


async def _run_deployment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    environment: str,
    user: dict,
    confirmed_commit: Optional[str] = None,  # Fix #16: correct Optional type
) -> None:
    """Core deployment execution with real-time log streaming."""
    chat_id = update.effective_chat.id

    # Fix #13: resolve commit from the correct branch, not local HEAD.
    if confirmed_commit:
        commit = confirmed_commit
    else:
        branch = (
            Config.github_branch_production()
            if environment == "production"
            else Config.github_branch_staging()
        )
        commit = deploy_manager.get_latest_commit(branch=branch)

    # Fix #5: acquire the deploy lock before starting.
    if environment in _deploying:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ A deployment to <b>{_escape_html(environment)}</b> is already running.",
            parse_mode=ParseMode.HTML,
        )
        return

    _deploying.add(environment)
    try:
        audit.log(user, "deploy_started", {"env": environment, "commit": commit})

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🚀 <b>Deployment Started</b>\n\n"
                f"🌍 Environment: <code>{_escape_html(environment)}</code>\n"
                f"🔖 Commit: <code>{_escape_html(commit)}</code>\n"
                f"👤 By: @{_escape_html(user['username'])}\n"
                f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
            ),
            parse_mode=ParseMode.HTML,
        )

        success, _ = await _stream_to_chat(
            context,
            chat_id,
            deploy_manager.run_deployment(environment, commit),
        )

        if success:
            audit.log(user, "deploy_success", {"env": environment, "commit": commit})
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ <b>Deployment to {_escape_html(environment)} succeeded!</b>\n"
                    f"🔖 Commit: <code>{_escape_html(commit)}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
        else:
            audit.log(user, "deploy_failed", {"env": environment, "commit": commit})
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ <b>Deployment to {_escape_html(environment)} FAILED!</b>\n"
                    f"🔖 Commit: <code>{_escape_html(commit)}</code>\n"
                    f"⏪ Initiating automatic rollback..."
                ),
                parse_mode=ParseMode.HTML,
            )

            rollback_success, _ = await _stream_to_chat(
                context,
                chat_id,
                deploy_manager.run_rollback(environment),
            )

            rollback_status = (
                "✅ Auto-rollback completed."
                if rollback_success
                else "❌ Auto-rollback also FAILED — manual intervention required!"
            )
            audit.log(
                user,
                "auto_rollback_completed" if rollback_success else "auto_rollback_failed",
                {"env": environment, "commit": commit},
            )
            await context.bot.send_message(chat_id=chat_id, text=rollback_status)

    finally:
        # Fix #5: always release the lock, even if an exception occurs.
        _deploying.discard(environment)


# ── Error Handler ──────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify the user."""
    logger.error("Exception:", exc_info=context.error)
    if update and hasattr(update, "effective_message") and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ An internal error occurred. Admins have been notified."
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Initialize and start the bot."""
    Config.validate()

    token = Config.get_telegram_bot_token()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("deploy", cmd_deploy))
    app.add_handler(CommandHandler("rollback", cmd_rollback))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    logger.info("🤖 Deployment bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
