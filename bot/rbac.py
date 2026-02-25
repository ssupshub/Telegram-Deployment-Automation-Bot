"""
rbac.py - Role-Based Access Control
=====================================
Provides a decorator to gate command handlers by role.

Roles (hierarchical):
  ADMIN   → can do everything (production deploy, rollback, staging)
  STAGING → can deploy staging, check status

Usage:
  @require_role(Role.ADMIN)
  async def cmd_deploy_production(update, context): ...

  @require_role(Role.STAGING)
  async def cmd_deploy_staging(update, context): ...
"""

import logging
from enum import Enum
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import Config

logger = logging.getLogger(__name__)


class Role(Enum):
    STAGING = "staging"  # minimum privilege
    ADMIN = "admin"      # full access


def require_role(role: Role):
    """
    Decorator factory for Telegram command handlers.
    Checks the user's Telegram ID against the allow-list before executing the command.
    Unauthorized calls are silently denied and logged.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            if user is None:
                return  # ignore non-user messages

            user_id = user.id

            # Determine if user passes the role check
            authorized = False
            if role == Role.STAGING:
                authorized = Config.is_authorized(user_id)
            elif role == Role.ADMIN:
                authorized = Config.is_admin(user_id)

            if not authorized:
                logger.warning(
                    "UNAUTHORIZED ACCESS ATTEMPT: user_id=%s username=%s command=%s required_role=%s",
                    user_id, user.username, func.__name__, role.value,
                )
                await update.effective_message.reply_text(
                    f"🚫 Access denied. This command requires `{role.value}` role.\n"
                    f"Contact an admin if you believe this is an error.",
                )
                return

            return await func(update, context, *args, **kwargs)

        return wrapper
    return decorator
