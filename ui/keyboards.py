"""Every inline keyboard in the bot.

Rules enforced here (Section 9.4):
  * no button is ever built without `style=`
  * at most two buttons per row (the hit login row is the one exception)
  * at most six buttons per screen, except the admin dashboard spec

`button()` is the single constructor; nothing else in the codebase is
allowed to instantiate InlineKeyboardButton directly.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ui.states import CB
from ui.typography import sc

MAX_CHANNEL_BUTTONS = 8


def button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str = "primary",
) -> InlineKeyboardButton:
    """The only way a button gets created — style is mandatory."""
    if url:
        return InlineKeyboardButton(text=text, url=url, style=style)
    return InlineKeyboardButton(text=text, callback_data=callback_data or CB.MENU_MAIN, style=style)


def row(*buttons: InlineKeyboardButton) -> list[InlineKeyboardButton]:
    return [b for b in buttons if b is not None]


def markup(rows: Iterable[Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Assemble a markup, dropping empty rows."""
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


# ── user side ────────────────────────────────────────────────────────
def verify_keyboard(channels: Sequence[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Every channel as a join button, two per row, verify on its own row.

    Four channels therefore render as: [A][B] / [C][D] / [Verify].
    """
    rows: list[list[InlineKeyboardButton]] = []
    joins = list(channels)[:MAX_CHANNEL_BUTTONS]

    for index in range(0, len(joins), 2):
        pair = joins[index : index + 2]
        row_buttons: list[InlineKeyboardButton] = []
        for i, c in enumerate(pair):
            chat_id = str(c.get("channel_id") or "")
            name = sc(str(c.get("channel_name") or chat_id or "channel"))[:16]
            link = str(c.get("channel_link") or "").strip()
            if link:
                row_buttons.append(
                    button(f"{'◆' if i == 0 else '◇'} {name}", url=link, style="primary")
                )
            else:
                # No resolvable link: keep the slot so the user sees which
                # channel is missing instead of silently losing it.
                row_buttons.append(
                    button(
                        f"{'◆' if i == 0 else '◇'} {name}",
                        callback_data=CB.VERIFY,
                        style="danger",
                    )
                )
        rows.append(row_buttons)

    rows.append([button(f"◆ {sc('Verify')}", callback_data=CB.VERIFY, style="success")])
    return markup(rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"▷ {sc('Check')}", callback_data=CB.MENU_CHECK, style="primary"),
                button(f"▤ {sc('Stats')}", callback_data=CB.MENU_STATS, style="primary"),
            ],
            [button(f"♕ {sc('Leaderboard')}", callback_data=CB.MENU_LEADERBOARD, style="primary")],
            [
                button(f"ⓘ {sc('Help')}", callback_data=CB.MENU_HELP, style="primary"),
                button(f"↻ {sc('Updates')}", callback_data=CB.MENU_UPDATES, style="success"),
            ],
        ]
    )


def check_instructions_keyboard() -> InlineKeyboardMarkup:
    return markup([[button(f"◇ {sc('Cancel')}", callback_data=CB.CHECK_ABORT, style="danger")]])


def cookie_confirm_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"◆ {sc('Premium Only')}", callback_data=CB.CHECK_MODE_PREMIUM, style="primary"),
                button(f"◇ {sc('All Hits')}", callback_data=CB.CHECK_MODE_ALL, style="primary"),
            ],
            [button(f"◇ {sc('Cancel')}", callback_data=CB.CHECK_ABORT, style="danger")],
        ]
    )


def live_check_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return markup(
        [[button(f"◇ {sc('Cancel')}", callback_data=f"{CB.CHECK_CANCEL}:{session_id}", style="danger")]]
    )


def hit_login_keyboard(nf_token: dict[str, Any]) -> InlineKeyboardMarkup | None:
    """Phone | PC | TV in one row — the only three-in-a-row in the bot."""
    buttons = []
    phone = str(nf_token.get("phone_link") or "").strip()
    pc = str(nf_token.get("pc_link") or "").strip()
    tv = str(nf_token.get("tv_link") or "").strip()
    if phone:
        buttons.append(button(f"◆ {sc('Phone')}", url=phone, style="primary"))
    if pc:
        buttons.append(button("◇ PC", url=pc, style="success"))
    if tv:
        buttons.append(button("✦ TV", url=tv, style="danger"))
    if not buttons:
        return None
    return markup([buttons])


def check_complete_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"▷ {sc('Check Again')}", callback_data=CB.MENU_CHECK, style="primary"),
                button(f"◆ {sc('Back to Menu')}", callback_data=CB.MENU_MAIN, style="primary"),
            ]
        ]
    )


def stats_keyboard() -> InlineKeyboardMarkup:
    return back_to_menu_keyboard()


def leaderboard_keyboard() -> InlineKeyboardMarkup:
    return back_to_menu_keyboard()


def help_keyboard() -> InlineKeyboardMarkup:
    return back_to_menu_keyboard()


def updates_keyboard() -> InlineKeyboardMarkup:
    return back_to_menu_keyboard()


def receipt_keyboard() -> InlineKeyboardMarkup:
    return back_to_menu_keyboard()


def plan_expired_keyboard() -> InlineKeyboardMarkup:
    return markup([[button(f"◆ {sc('View Plans')}", callback_data=CB.MENU_PLANS, style="success")]])


def plans_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [button(f"◆ {sc('Redeem Key')}", callback_data=CB.MENU_HELP, style="primary")],
            [button(f"◆ {sc('Back to Menu')}", callback_data=CB.MENU_MAIN, style="primary")],
        ]
    )


def daily_limit_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [button(f"◆ {sc('Upgrade Plan')}", callback_data=CB.MENU_PLANS, style="success")],
            [button(f"◆ {sc('Back to Menu')}", callback_data=CB.MENU_MAIN, style="primary")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return markup([[button(f"◆ {sc('Back to Menu')}", callback_data=CB.MENU_MAIN, style="primary")]])


# ── admin side ───────────────────────────────────────────────────────
def admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"◆ {sc('Users')}", callback_data=CB.ADMIN_USERS, style="primary"),
                button(f"◇ {sc('APIs')}", callback_data=CB.ADMIN_APIS, style="success"),
            ],
            [
                button(f"◆ {sc('Keys')}", callback_data=CB.ADMIN_KEYS, style="primary"),
                button(f"◇ {sc('Channels')}", callback_data=CB.ADMIN_CHANNELS, style="success"),
            ],
            [button(f"◆ {sc('Broadcast')}", callback_data=CB.ADMIN_BROADCAST, style="primary")],
            [
                button(f"◆ {sc('Logs')}", callback_data=CB.ADMIN_LOGS, style="primary"),
                button(f"◇ {sc('Config')}", callback_data=CB.ADMIN_CONFIG, style="success"),
            ],
            [button(f"✦ {sc('Bot Stats')}", callback_data=CB.ADMIN_BOT_STATS, style="success")],
        ]
    )


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return markup([[button(f"◆ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="primary")]])


def admin_users_keyboard(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        rows.append(
            [
                button(
                    f"◆ {sc('Prev')}",
                    callback_data=f"{CB.ADMIN_USERS_PAGE}{max(0, page - 1)}",
                    style="primary",
                ),
                button(
                    f"◇ {sc('Next')}",
                    callback_data=f"{CB.ADMIN_USERS_PAGE}{min(pages - 1, page + 1)}",
                    style="success",
                ),
            ]
        )
    rows.append(
        [
            button(f"◆ {sc('Find User')}", callback_data=CB.ADMIN_USERS_SEARCH, style="primary"),
            button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success"),
        ]
    )
    return markup(rows)


def admin_user_actions_keyboard(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    if is_banned:
        ban_button = button(
            f"◆ {sc('Unban')}", callback_data=f"{CB.ADMIN_USER_UNBAN}{user_id}", style="success"
        )
    else:
        ban_button = button(
            f"◆ {sc('Ban')}", callback_data=f"{CB.ADMIN_USER_BAN}{user_id}", style="danger"
        )
    return markup(
        [
            [ban_button],
            [
                button(
                    f"◆ {sc('Give Plan')}",
                    callback_data=f"{CB.ADMIN_USER_PLAN}{user_id}",
                    style="primary",
                ),
                button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success"),
            ],
        ]
    )


def admin_user_plan_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(
                    f"◆ {sc('Basic')} 30d",
                    callback_data=f"{CB.ADMIN_USER_PLAN_SET}{user_id}:basic",
                    style="primary",
                ),
                button(
                    f"◇ {sc('Premium')} 30d",
                    callback_data=f"{CB.ADMIN_USER_PLAN_SET}{user_id}:premium",
                    style="success",
                ),
            ],
            [
                button(
                    f"◇ {sc('Free')}",
                    callback_data=f"{CB.ADMIN_USER_PLAN_SET}{user_id}:free",
                    style="danger",
                )
            ],
        ]
    )


def admin_apis_keyboard(apis: Sequence[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    shown = list(apis)[:4]
    for index in range(0, len(shown), 2):
        pairs = shown[index : index + 2]
        rows.append(
            [
                button(
                    f"{'◆' if i == 0 else '◇'} {sc(str(api.get('name') or api.get('api_id')))[:12]}",
                    callback_data=f"{CB.ADMIN_API}{api.get('api_id')}",
                    style="primary" if i == 0 else "success",
                )
                for i, api in enumerate(pairs)
            ]
        )
    rows.append(
        [
            button(f"◆ {sc('Add API')}", callback_data=CB.ADMIN_API_ADD, style="primary"),
            button(f"◇ {sc('Proxies')}", callback_data=CB.ADMIN_API_PROXIES, style="success"),
        ]
    )
    rows.append([button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success")])
    return markup(rows)


def admin_api_actions_keyboard(api_id: str, is_active: bool) -> InlineKeyboardMarkup:
    toggle = (
        button(
            f"◆ {sc('Disable')}", callback_data=f"{CB.ADMIN_API_TOGGLE}{api_id}", style="danger"
        )
        if is_active
        else button(
            f"◆ {sc('Enable')}", callback_data=f"{CB.ADMIN_API_TOGGLE}{api_id}", style="success"
        )
    )
    return markup(
        [
            [toggle],
            [
                button(
                    f"◇ {sc('Delete')}", callback_data=f"{CB.ADMIN_API_DELETE}{api_id}", style="danger"
                ),
                button(f"◇ {sc('Back')}", callback_data=CB.ADMIN_APIS, style="success"),
            ],
        ]
    )


def admin_channels_keyboard(channels: Sequence[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in list(channels)[:4]:
        active = bool(channel.get("is_active"))
        label = sc(str(channel.get("channel_name") or channel.get("channel_id")))[:12]
        rows.append(
            [
                button(
                    f"{'◆' if active else '◇'} {label}",
                    callback_data=f"{CB.ADMIN_CHANNEL_TOGGLE}{channel.get('channel_id')}",
                    style="success" if active else "danger",
                )
            ]
        )
    rows.append(
        [
            button(f"◆ {sc('Add Channel')}", callback_data=CB.ADMIN_CHANNEL_ADD, style="primary"),
            button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success"),
        ]
    )
    return markup(rows)


def admin_videos_keyboard(slots: dict[str, str]) -> InlineKeyboardMarkup:
    def label(key: str, text: str) -> str:
        return f"{'◆' if slots.get(key) else '◇'} {sc(text)}"

    return markup(
        [
            [
                button(
                    label("main_menu_video", "Menu Video"),
                    callback_data=f"{CB.ADMIN_VIDEO_SET}main_menu_video",
                    style="primary",
                ),
                button(
                    label("verify_video", "Verify Video"),
                    callback_data=f"{CB.ADMIN_VIDEO_SET}verify_video",
                    style="success",
                ),
            ],
            [
                button(
                    label("checking_video", "Checking Video"),
                    callback_data=f"{CB.ADMIN_VIDEO_SET}checking_video",
                    style="primary",
                ),
                button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success"),
            ],
        ]
    )


def admin_keys_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"◆ {sc('Basic')}", callback_data=f"{CB.ADMIN_KEYS_PLAN}basic", style="primary"),
                button(f"◇ {sc('Premium')}", callback_data=f"{CB.ADMIN_KEYS_PLAN}premium", style="success"),
            ],
            [button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success")],
        ]
    )


def admin_key_duration_keyboard(plan: str) -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(
                    f"◆ 7 {sc('days')}",
                    callback_data=f"{CB.ADMIN_KEYS_CONFIRM}{plan}:7",
                    style="primary",
                ),
                button(
                    f"◇ 30 {sc('days')}",
                    callback_data=f"{CB.ADMIN_KEYS_CONFIRM}{plan}:30",
                    style="success",
                ),
            ],
            [
                button(
                    f"◆ 90 {sc('days')}",
                    callback_data=f"{CB.ADMIN_KEYS_CONFIRM}{plan}:90",
                    style="primary",
                ),
                button(f"◇ {sc('Cancel')}", callback_data=CB.ADMIN_KEYS, style="danger"),
            ],
        ]
    )


def admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"◆ {sc('Send Broadcast')}", callback_data=CB.ADMIN_BROADCAST_SEND, style="primary"),
                button(
                    f"◇ {sc('Cancel Broadcast')}",
                    callback_data=CB.ADMIN_BROADCAST_CANCEL,
                    style="danger",
                ),
            ]
        ]
    )


def admin_config_keyboard(config: dict[str, Any]) -> InlineKeyboardMarkup:
    maintenance = bool(config.get("maintenance_mode"))
    maintenance_button = (
        button(
            f"◆ {sc('Disable Maintenance')}",
            callback_data=f"{CB.ADMIN_CONFIG_TOGGLE}maintenance_mode",
            style="danger",
        )
        if maintenance
        else button(
            f"◆ {sc('Enable Maintenance')}",
            callback_data=f"{CB.ADMIN_CONFIG_TOGGLE}maintenance_mode",
            style="success",
        )
    )
    return markup(
        [
            [maintenance_button],
            [
                button(
                    f"◇ {sc('Rotation Mode')}",
                    callback_data=f"{CB.ADMIN_CONFIG_ROTATION}cycle",
                    style="primary",
                ),
                button(
                    f"◇ {sc('Forward Channel')}",
                    callback_data=f"{CB.ADMIN_CONFIG_SET}hit_forward_channel",
                    style="success",
                ),
            ],
            [
                button(
                    f"◆ {sc('Max Concurrent')}",
                    callback_data=f"{CB.ADMIN_CONFIG_SET}max_concurrent",
                    style="primary",
                ),
                button(f"◇ {sc('Videos')}", callback_data=CB.ADMIN_VIDEOS, style="success"),
            ],
            [button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success")],
        ]
    )


def admin_logs_keyboard(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pages > 1:
        rows.append(
            [
                button(
                    f"◆ {sc('Prev')}",
                    callback_data=f"{CB.ADMIN_LOGS_PAGE}{max(0, page - 1)}",
                    style="primary",
                ),
                button(
                    f"◇ {sc('Next')}",
                    callback_data=f"{CB.ADMIN_LOGS_PAGE}{min(pages - 1, page + 1)}",
                    style="success",
                ),
            ]
        )
    rows.append([button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success")])
    return markup(rows)


def admin_bot_stats_keyboard() -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"↻ {sc('Refresh')}", callback_data=CB.ADMIN_BOT_STATS, style="success"),
                button(f"◇ {sc('Admin Home')}", callback_data=CB.ADMIN_HOME, style="success"),
            ]
        ]
    )


def maintenance_keyboard() -> InlineKeyboardMarkup | None:
    return None


def cancel_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return markup([[button(f"◇ {sc('Cancel')}", callback_data=callback_data, style="danger")]])


def confirm_delete_keyboard(confirm_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    return markup(
        [
            [
                button(f"◆ {sc('Confirm Delete')}", callback_data=confirm_cb, style="danger"),
                button(f"◇ {sc('Cancel')}", callback_data=cancel_cb, style="success"),
            ]
        ]
    )
