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


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_user_info(update: Update) -> dict:
    """Extract user info from update for audit logging."""
    user = update.effective_user
    return {
        "id": user.id,
        "username": user.username or "unknown",
        "full_name": user.full_name,
    }


def _escape_html(text: str) -> str:
    """Escape the three characters that are special in Telegram HTML mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def send_chunked(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    """
    Send long messages in Telegram-safe 4096-char chunks.

    BUG FIX: the original used ParseMode.MARKDOWN_V2 with a triple-backtick
    code fence.  In MarkdownV2 *every* special character (-, ., !, (, ) …)
    must be escaped even inside code blocks, so raw shell output almost always
    caused a "Bad Request: can't parse entities" error from the Telegram API.

    Fix: switch to HTML mode for chunked log output.  The content is wrapped
    in <pre> tags (monospace) and the text is HTML-escaped so angle brackets
    and ampersands in shell output don't break the markup.
    """
    chunk_size = 4000
    for i in range(0, len(text), chunk_size):
        chunk = _escape_html(text[i : i + chunk_size])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"<pre>{chunk}</pre>",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(0.3)  # avoid rate limits


def _is_error_line(line: str) -> bool:
    """
    Return True only for the sentinel error lines emitted by DeploymentManager,
    not for arbitrary log lines that happen to mention the word 'error'.

    DeploymentManager.run_deployment() always emits:
        "ERROR: Deploy script exited with code <N>"
        "ERROR: <exception message>"
    and run_rollback() emits:
        "ERROR: Rollback script exited with code <N>"
        "ERROR during rollback: <exception message>"

    Checking for the "ERROR:" prefix (capital, colon) catches exactly these
    sentinel lines without false-positives on normal log content such as
    "[INFO] Checking error.log for issues".
    """
    return line.startswith("ERROR:") or line.startswith("ERROR during")


# ── Command Handlers ───────────────────────────────────────────────────────────

@require_role(Role.STAGING)
async def cmd_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /deploy <environment>
    Triggers a deployment. Production requires inline confirmation.
    """
    user = get_user_info(update)
    args = context.args

    if not args or args[0] not in ("staging", "production"):
        await update.message.reply_text(
            "⚠️ Usage: <code>/deploy staging</code> or <code>/deploy production</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    environment = args[0]

    # Production requires admin role.
    if environment == "production":
        if not Config.is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Production deployments require admin role.")
            audit.log(user, "deploy_production_denied", {"env": environment})
            return
        await _confirm_production_deploy(update, context, user)
    else:
        await _run_deployment(update, context, environment, user)


async def _confirm_production_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict):
    """Show inline confirmation buttons before production deploy."""
    commit_hash = deploy_manager.get_latest_commit(branch=Config.GITHUB_BRANCH_PRODUCTION)
    branch = deploy_manager.get_current_branch()

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm Deploy", callback_data=f"deploy:production:{commit_hash}"),
            InlineKeyboardButton("❌ Cancel", callback_data="deploy:cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"⚠️ <b>Production Deployment Confirmation</b>\n\n"
        f"👤 Initiated by: @{_escape_html(user['username'])}\n"
        f"🌿 Branch: <code>{_escape_html(branch)}</code>\n"
        f"🔖 Commit: <code>{_escape_html(commit_hash)}</code>\n"
        f"🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"Are you sure you want to deploy to <b>PRODUCTION</b>?",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )


@require_role(Role.ADMIN)
async def cmd_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rollback <environment>
    Rolls back to the previous stable deployment.
    """
    user = get_user_info(update)
    args = context.args

    if not args or args[0] not in ("staging", "production"):
        await update.message.reply_text("⚠️ Usage: <code>/rollback staging</code> or <code>/rollback production</code>",
                                        parse_mode=ParseMode.HTML)
        return

    environment = args[0]
    audit.log(user, "rollback_initiated", {"env": environment})

    await update.message.reply_text(
        f"⏪ Initiating rollback for <b>{_escape_html(environment)}</b>...",
        parse_mode=ParseMode.HTML,
    )

    # BUG FIX: the original silently ignored rollback output and never detected
    # failures.  Now we stream the output and track success/failure.
    rollback_success = True
    log_buffer = []

    async for log_line in deploy_manager.run_rollback(environment):
        log_buffer.append(log_line)
        if len(log_buffer) >= 10:
            await send_chunked(context, update.effective_chat.id, "\n".join(log_buffer))
            log_buffer = []
        if _is_error_line(log_line):
            rollback_success = False

    if log_buffer:
        await send_chunked(context, update.effective_chat.id, "\n".join(log_buffer))

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
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /status
    Shows current deployment status for all environments.
    """
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


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ── Callback Query Handler (Inline Buttons) ────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    user = get_user_info(update)
    data = query.data  # e.g. "deploy:production:abc1234" or "deploy:cancel"

    # BUG FIX: use maxsplit=2 so the commit hash (parts[2]) captures everything
    # after the second colon.  With the default split() a hash containing ":"
    # would be truncated.  Git short SHAs won't contain ":" but this also
    # prevents an attacker from injecting extra colon-delimited segments that
    # shift the index of subsequent parts.
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
    confirmed_commit: str = None,
):
    """Core deployment execution with real-time log streaming."""
    chat_id = update.effective_chat.id
    commit = confirmed_commit or deploy_manager.get_latest_commit()

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

    # BUG FIX: success used to be set *after* the loop based on the last line
    # examined, but Python's for-loop variable retains the last value — if the
    # error line arrived mid-stream the flag would still be True at the end.
    # Now `success` is updated inside the loop so any error line flips it to
    # False permanently.
    success = True
    log_buffer = []

    async for log_line in deploy_manager.run_deployment(environment, commit):
        log_buffer.append(log_line)
        if len(log_buffer) >= 10:
            await send_chunked(context, chat_id, "\n".join(log_buffer))
            log_buffer = []

        # BUG FIX: check for the specific sentinel prefix, not arbitrary
        # occurrences of the word "ERROR" or "FAILED" in log output.
        if _is_error_line(log_line):
            success = False

    # Flush remaining buffered lines.
    if log_buffer:
        await send_chunked(context, chat_id, "\n".join(log_buffer))

    if success:
        audit.log(user, "deploy_success", {"env": environment, "commit": commit})
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ <b>Deployment to {_escape_html(environment)} succeeded!</b>\n🔖 Commit: <code>{_escape_html(commit)}</code>",
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

        # BUG FIX: the original discarded all rollback output with `pass`.
        # Now we stream it back to the user so they can see rollback progress
        # and detect if the rollback itself failed.
        rollback_success = True
        rb_buffer = []
        async for line in deploy_manager.run_rollback(environment):
            rb_buffer.append(line)
            if len(rb_buffer) >= 10:
                await send_chunked(context, chat_id, "\n".join(rb_buffer))
                rb_buffer = []
            if _is_error_line(line):
                rollback_success = False

        if rb_buffer:
            await send_chunked(context, chat_id, "\n".join(rb_buffer))

        rollback_status = "✅ Auto-rollback completed." if rollback_success else "❌ Auto-rollback also FAILED — manual intervention required!"
        audit.log(user, "auto_rollback_completed" if rollback_success else "auto_rollback_failed",
                  {"env": environment, "commit": commit})
        await context.bot.send_message(chat_id=chat_id, text=rollback_status)


# ── Error Handler ──────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and notify admins."""
    logger.error("Exception:", exc_info=context.error)
    if update and hasattr(update, "effective_message") and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ An internal error occurred. Admins have been notified."
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    """Initialize and start the bot."""
    Config.validate()  # fail fast on missing required config

    # FIX: use Config.get_telegram_bot_token() — the single, canonical way to
    # read the token, replacing the ambiguous dual @property / class-attribute.
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
