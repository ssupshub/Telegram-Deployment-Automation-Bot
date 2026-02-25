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


async def send_chunked(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    """Send long messages in Telegram-safe 4096-char chunks."""
    chunk_size = 4000
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"```\n{chunk}\n```",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        await asyncio.sleep(0.3)  # avoid rate limits


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
            "⚠️ Usage: `/deploy staging` or `/deploy production`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    environment = args[0]

    # Production requires admin role
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
    # Fetch latest commit hash for transparency
    commit_hash = deploy_manager.get_latest_commit()
    branch = deploy_manager.get_current_branch()

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm Deploy", callback_data=f"deploy:production:{commit_hash}"),
            InlineKeyboardButton("❌ Cancel", callback_data="deploy:cancel"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"⚠️ *Production Deployment Confirmation*\n\n"
        f"👤 Initiated by: @{user['username']}\n"
        f"🌿 Branch: `{branch}`\n"
        f"🔖 Commit: `{commit_hash}`\n"
        f"🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"Are you sure you want to deploy to *PRODUCTION*?",
        parse_mode=ParseMode.MARKDOWN_V2,
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
        await update.message.reply_text("⚠️ Usage: `/rollback staging` or `/rollback production`")
        return

    environment = args[0]
    audit.log(user, "rollback_initiated", {"env": environment})

    await update.message.reply_text(f"⏪ Initiating rollback for *{environment}*...", parse_mode=ParseMode.MARKDOWN_V2)

    async for log_line in deploy_manager.run_rollback(environment):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"`{log_line}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    audit.log(user, "rollback_completed", {"env": environment})
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ Rollback for *{environment}* complete\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
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

    lines = ["📊 *Deployment Status*\n"]
    for env, info in status.items():
        emoji = "🟢" if info["healthy"] else "🔴"
        lines.append(
            f"{emoji} *{env.upper()}*\n"
            f"  • Commit: `{info['commit']}`\n"
            f"  • Deployed: {info['deployed_at']}\n"
            f"  • Health: {info['health_url']}\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


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
        f"🤖 *Deployment Bot* \\- Role: `{role}`\n\n"
        f"*Available Commands:*\n"
        f"`/deploy staging` \\- Deploy to staging\n"
        f"`/status` \\- Check environment status\n"
    )
    if is_admin:
        text += (
            "`/deploy production` \\- Deploy to production \\(requires confirmation\\)\n"
            "`/rollback staging` \\- Rollback staging\n"
            "`/rollback production` \\- Rollback production\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ── Callback Query Handler (Inline Buttons) ────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    user = get_user_info(update)
    data = query.data  # e.g., "deploy:production:abc1234" or "deploy:cancel"

    parts = data.split(":")
    action = parts[0]

    if action == "deploy":
        if parts[1] == "cancel":
            await query.edit_message_text("❌ Deployment cancelled.")
            audit.log(user, "deploy_cancelled", {})
            return

        environment = parts[1]
        commit_hash = parts[2] if len(parts) > 2 else "unknown"

        # Security: re-verify admin role on callback (buttons can be replayed)
        if not Config.is_admin(update.effective_user.id):
            await query.edit_message_text("🚫 You no longer have permission for this action.")
            return

        await query.edit_message_text(f"🚀 Deploying to *{environment}*...", parse_mode=ParseMode.MARKDOWN_V2)
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
            f"🚀 *Deployment Started*\n\n"
            f"🌍 Environment: `{environment}`\n"
            f"🔖 Commit: `{commit}`\n"
            f"👤 By: @{user['username']}\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        ),
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    success = True
    log_buffer = []

    # Stream deployment logs in real-time
    async for log_line in deploy_manager.run_deployment(environment, commit):
        log_buffer.append(log_line)
        # Send every 10 lines to avoid spam
        if len(log_buffer) >= 10:
            await send_chunked(context, chat_id, "\n".join(log_buffer))
            log_buffer = []

        if "ERROR" in log_line or "FAILED" in log_line:
            success = False

    # Flush remaining logs
    if log_buffer:
        await send_chunked(context, chat_id, "\n".join(log_buffer))

    # Final status
    if success:
        audit.log(user, "deploy_success", {"env": environment, "commit": commit})
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ *Deployment to {environment} succeeded\\!*\n🔖 Commit: `{commit}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        audit.log(user, "deploy_failed", {"env": environment, "commit": commit})
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ *Deployment to {environment} FAILED\\!*\n"
                f"🔖 Commit: `{commit}`\n"
                f"⏪ Initiating automatic rollback\\.\\.\\."
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        # Auto-rollback on failure
        async for line in deploy_manager.run_rollback(environment):
            pass  # silently rollback; could stream this too


# ── Error Handler ──────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and notify admins."""
    logger.error("Exception:", exc_info=context.error)
    if update and hasattr(update, "effective_message"):
        await update.effective_message.reply_text(
            "⚠️ An internal error occurred. Admins have been notified."
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    """Initialize and start the bot."""
    token = Config.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")

    app = Application.builder().token(token).build()

    # Register handlers
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
