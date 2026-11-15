"""FSM states, callback payload registry and shared UI copy.

Every callback payload in the bot is declared here so keyboards and
handlers can never drift apart. Dynamic payloads are built by joining a
registered prefix with an identifier and parsed with `split_payload`.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CB:
    """Static callback payloads. Callback data must stay under 64 bytes."""

    # user side
    MENU_MAIN = "menu:main"
    MENU_CHECK = "menu:check"
    MENU_STATS = "menu:stats"
    MENU_LEADERBOARD = "menu:leaderboard"
    MENU_HELP = "menu:help"
    MENU_UPDATES = "menu:updates"
    MENU_PLANS = "menu:plans"
    VERIFY = "verify:now"

    CHECK_MODE_PREMIUM = "chk:mode:premium"
    CHECK_MODE_ALL = "chk:mode:all"
    CHECK_ABORT = "chk:abort"
    CHECK_CANCEL = "chk:cancel"

    LEADERBOARD_REFRESH = "lb:refresh"

    # admin side
    ADMIN_HOME = "adm:home"
    ADMIN_USERS = "adm:users"
    ADMIN_USERS_PAGE = "adm:users:p:"
    ADMIN_USERS_SEARCH = "adm:users:find"
    ADMIN_USER = "adm:user:"
    ADMIN_USER_BAN = "adm:user:ban:"
    ADMIN_USER_UNBAN = "adm:user:unban:"
    ADMIN_USER_PLAN = "adm:user:plan:"
    ADMIN_USER_PLAN_SET = "adm:user:planset:"
    ADMIN_USER_DELETE = "adm:user:del:"

    ADMIN_APIS = "adm:apis"
    ADMIN_API = "adm:api:"
    ADMIN_API_ADD = "adm:api:add"
    ADMIN_API_TOGGLE = "adm:api:toggle:"
    ADMIN_API_DELETE = "adm:api:del:"
    ADMIN_API_PROXIES = "adm:api:proxies"

    ADMIN_KEYS = "adm:keys"
    ADMIN_KEYS_PLAN = "adm:keys:p:"
    ADMIN_KEYS_CONFIRM = "adm:keys:go"

    ADMIN_CHANNELS = "adm:channels"
    ADMIN_CHANNEL_ADD = "adm:ch:add"
    ADMIN_CHANNEL_TOGGLE = "adm:ch:toggle:"
    ADMIN_CHANNEL_DELETE = "adm:ch:del:"

    ADMIN_BROADCAST = "adm:bcast"
    ADMIN_BROADCAST_SEND = "adm:bcast:send"
    ADMIN_BROADCAST_CANCEL = "adm:bcast:cancel"

    ADMIN_VIDEOS = "adm:videos"
    ADMIN_VIDEO_SET = "adm:vid:"
    ADMIN_VIDEO_CLEAR = "adm:vid:clear:"

    ADMIN_CONFIG = "adm:cfg"
    ADMIN_CONFIG_TOGGLE = "adm:cfg:toggle:"
    ADMIN_CONFIG_ROTATION = "adm:cfg:rot:"
    ADMIN_CONFIG_SET = "adm:cfg:set:"

    ADMIN_LOGS = "adm:logs"
    ADMIN_LOGS_PAGE = "adm:logs:p:"
    ADMIN_BOT_STATS = "adm:stats"
    ADMIN_REFRESH = "adm:refresh"


PREFIX_ADMIN_USER = CB.ADMIN_USER
PREFIX_ADMIN_USERS_PAGE = CB.ADMIN_USERS_PAGE
PREFIX_ADMIN_API = CB.ADMIN_API
PREFIX_ADMIN_CHANNEL = CB.ADMIN_CHANNEL_TOGGLE
PREFIX_ADMIN_VIDEO = CB.ADMIN_VIDEO_SET


def split_payload(data: str, depth: int = 1) -> list[str]:
    """Split 'adm:user:ban:123' while keeping the first `depth` segments whole.

    Returns ['adm:user:ban' -> caller's concern, '123'] style output: the
    joined prefix and everything after it, split on ':'.
    """
    parts = data.split(":")
    head = ":".join(parts[:depth])
    return [head, *parts[depth:]]


class CheckStates(StatesGroup):
    """Section 14: IDLE → waiting_for_file → waiting_for_mode → checking."""

    waiting_for_file = State()
    waiting_for_mode = State()
    checking = State()


class AdminUserStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_ban_reason = State()
    waiting_for_plan_days = State()
    waiting_for_search = State()


class AdminAPIStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_url = State()
    waiting_for_priority = State()
    waiting_for_proxies = State()


class AdminChannelStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_channel_link = State()


class AdminVideoStates(StatesGroup):
    current_slot = State()
    waiting_for_video = State()


class AdminKeyStates(StatesGroup):
    selected_plan = State()
    waiting_for_count = State()
    waiting_for_duration = State()


class AdminBroadcastStates(StatesGroup):
    waiting_for_message = State()
    confirming = State()


class AdminConfigStates(StatesGroup):
    waiting_for_value = State()


# ── Shared UI copy (Section 9.8) ─────────────────────────────────────
LOADING_TEXT = "→ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ✦"
NO_USER_TEXT = "→ sᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ ᴡɪᴛʜ /start ✦"


def error_detail(message: str) -> str:
    """Plain sentence describing what failed and how to recover."""
    return message.strip().rstrip(".") + " ✦"


def cold_state_guard(value: object, fallback: str) -> str:
    """Never surface None/empty as literal text (Mistake 8)."""
    if value is None:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "n/a", "nan", "-"):
        return fallback
    return text
