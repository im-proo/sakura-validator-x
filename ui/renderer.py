"""Every screen in 𝐒𝐚𝐤𝐮𝐫𝐀 𝐕𝐚𝐥𝐢𝐝𝐀𝐭𝐨𝐫 𝐗.

One `Card` model is emitted twice: to an InputRichMessage for
send_rich_message / rich edits, and to HTML for the documented fallback.
They therefore can never drift apart.

Design law (Section 9): heading → divider → blockquoted content →
blockquoted footer. Data lives in tables inside quotations. Nothing is
ever rendered as naked text, and no screen ships without the brand line.
"""

from __future__ import annotations

from typing import Any, Sequence

from aiogram.types import (
    InputMediaDocument,
    InputMediaVideo,
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockDocument,
    InputRichBlockExpandableBlockQuotation,
    InputRichBlockFooter,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockVideo,
    InputRichMessage,
    RichBlockCaption,
    RichBlockTableCell,
    RichTextBold,
    RichTextCode,
    RichTextItalic,
)
from aiogram.types import RichTextUrl

from models.schemas import daily_limit, max_cookies, plan_limits, utcnow
from ui.states import cold_state_guard
from ui.typography import (
    BOT_NAME_DISPLAY,
    BRAND_FOOTER,
    BRAND_LINE,
    S_BULLET,
    S_CROWN,
    S_HEART,
    S_STAR,
    VERSION,
    country_flag,
    esc,
    fmt_dt,
    fmt_num,
    plan_badge,
    progress_bar,
    sc,
    truncate,
    yes_no,
)

DASH = "—"
NONE_LABEL = sc("none")
YES = sc("yes")
NO = sc("no")

# ── primitives ───────────────────────────────────────────────────────
RichTextish = str | RichTextBold | RichTextItalic | RichTextCode | RichTextUrl


def cell(text: Any, align: str = "left", valign: str = "middle", header: bool = False) -> RichBlockTableCell:
    """A table cell. align/valign are required by the type, so they are always set."""
    return RichBlockTableCell(text=text, align=align, valign=valign, is_header=header)


def code(text: Any) -> RichTextCode:
    """Monospace inline value (emails, GUIDs, keys, IDs)."""
    return RichTextCode(text=str(text))


def bold(text: Any) -> RichTextBold:
    return RichTextBold(text=str(text))


def label(text: str) -> RichBlockTableCell:
    """Left-hand column: small caps label, never translated."""
    return cell(sc(text))


def value(raw: Any, fallback: str = DASH, align: str = "left") -> RichBlockTableCell:
    """Right-hand column: warm fallback instead of None (Mistake 8)."""
    text = cold_state_guard(raw, fallback)
    return cell(text, align=align)


def _rich_to_html(text: Any) -> str:
    """Render rich text nodes into Telegram HTML."""
    if isinstance(text, RichTextCode):
        return f"<code>{esc(text.text)}</code>"
    if isinstance(text, RichTextBold):
        return f"<b>{esc(text.text)}</b>"
    if isinstance(text, RichTextItalic):
        return f"<i>{esc(text.text)}</i>"
    if isinstance(text, RichTextUrl):
        return f'<a href="{esc(text.url)}">{esc(text.text)}</a>'
    return esc(text)


class Card:
    """A screen: heading, content blocks, footer."""

    def __init__(self, heading: str | None = None) -> None:
        self._blocks: list[tuple[str, Any]] = []
        if heading:
            self.heading(heading)

    # builder ------------------------------------------------------
    def heading(self, text: str, size: int = 1) -> "Card":
        self._blocks.append(("heading", (text, size)))
        return self

    def divider(self) -> "Card":
        self._blocks.append(("divider", None))
        return self

    def video(self, file_id: str, caption: str = BOT_NAME_DISPLAY, credit: str = BRAND_FOOTER) -> "Card":
        """Video lives inside the card — never a separate message (Mistake 7)."""
        if file_id:
            self._blocks.append(("video", (file_id, caption, credit)))
        return self

    def document(self, file: Any, caption: str = "", credit: str | None = None) -> "Card":
        """A downloadable file quoted inside the card (the hit cookie .txt)."""
        if file is not None:
            self._blocks.append(("document", (file, caption, credit)))
        return self

    def para(self, text: str) -> "Card":
        self._blocks.append(("para", text))
        return self

    def quote(self, *inner: tuple[str, Any], credit: str | None = None) -> "Card":
        """Bordered content block. Never nests another quotation."""
        self._blocks.append(("quote", (list(inner), credit)))
        return self

    def expandable(self, text: str, credit: str | None = None) -> "Card":
        self._blocks.append(("expandable", (text, credit)))
        return self

    def footer(self, text: str = BRAND_FOOTER) -> "Card":
        """Always the last block on every screen."""
        self._blocks.append(("footer", text))
        return self

    def table(self, rows: Sequence[Sequence[RichBlockTableCell]], header: bool = False) -> "Card":
        self._blocks.append(("table", (list(rows), header)))
        return self

    # emitters -----------------------------------------------------
    def to_rich(self, skip_entity_detection: bool = False) -> InputRichMessage:
        blocks = []
        for kind, payload in self._blocks:
            if kind == "heading":
                text, size = payload
                blocks.append(InputRichBlockSectionHeading(text=text, size=size))
            elif kind == "divider":
                blocks.append(InputRichBlockDivider())
            elif kind == "video":
                file_id, caption, credit = payload
                blocks.append(
                    InputRichBlockBlockQuotation(
                        blocks=[
                            InputRichBlockVideo(
                                video=InputMediaVideo(media=file_id),
                                caption=RichBlockCaption(text=caption, credit=credit),
                            )
                        ]
                    )
                )
            elif kind == "document":
                file, caption, credit = payload
                # Bold plain text keeps Telegram from auto-linking the
                # filename's "@" as a mailto address.
                caption_block = (
                    RichBlockCaption(text=RichTextBold(text=caption), credit=credit)
                    if (caption or credit)
                    else None
                )
                blocks.append(
                    InputRichBlockBlockQuotation(
                        blocks=[
                            InputRichBlockDocument(
                                document=InputMediaDocument(media=file),
                                caption=caption_block,
                            )
                        ]
                    )
                )
            elif kind == "para":
                blocks.append(InputRichBlockParagraph(text=payload))
            elif kind == "quote":
                blocks.append(InputRichBlockBlockQuotation(blocks=_inner_blocks(payload[0])))
            elif kind == "expandable":
                text, credit = payload
                blocks.append(
                    InputRichBlockExpandableBlockQuotation(text=text, credit=credit)
                )
            elif kind == "footer":
                blocks.append(InputRichBlockFooter(text=payload))
            elif kind == "table":
                rows, header = payload
                blocks.append(
                    InputRichBlockTable(cells=_table_rows(rows, header), is_bordered=True, is_compact=True)
                )
        return InputRichMessage(
            blocks=blocks,
            skip_entity_detection=True if skip_entity_detection else None,
        )

    def to_html(self) -> str:
        parts: list[str] = []
        for kind, payload in self._blocks:
            if kind == "heading":
                text, _size = payload
                parts.append(f"<b>{esc(text)}</b>")
            elif kind == "divider":
                parts.append("──────────────")
            elif kind == "video":
                _file_id, caption, credit = payload
                parts.append(f"<blockquote><b>{esc(caption)}</b>\n{esc(credit)}</blockquote>")
            elif kind == "document":
                _file, caption, credit = payload
                credit_line = f"\n{esc(credit)}" if credit else ""
                parts.append(f"<blockquote>{esc(caption)}{credit_line}</blockquote>")
            elif kind == "para":
                parts.append(esc(payload))
            elif kind == "quote":
                parts.append(_inner_html(payload[0], payload[1]))
            elif kind == "expandable":
                text, credit = payload
                credit_line = f"\n{esc(credit)}" if credit else ""
                parts.append(f"<blockquote expandable>{esc(text)}{credit_line}</blockquote>")
            elif kind == "footer":
                parts.append(f"<blockquote>{esc(payload)}</blockquote>")
            elif kind == "table":
                rows, header = payload
                parts.append(_table_html(rows, header))
        return "\n\n".join(parts)


def _inner_blocks(inner: Sequence[tuple[str, Any]]) -> list[Any]:
    blocks: list[Any] = []
    for kind, payload in inner:
        if kind == "para":
            blocks.append(InputRichBlockParagraph(text=payload))
        elif kind == "table":
            rows, header = payload
            blocks.append(
                InputRichBlockTable(cells=_table_rows(rows, header), is_bordered=True, is_compact=True)
            )
        elif kind == "divider":
            blocks.append(InputRichBlockDivider())
        elif kind == "list":
            blocks.append(InputRichBlockParagraph(text=payload))
    return blocks


def _inner_html(inner: Sequence[tuple[str, Any]], credit: str | None) -> str:
    lines: list[str] = []
    for kind, payload in inner:
        if kind == "para":
            lines.append(_rich_to_html(payload))
        elif kind == "list":
            lines.append(_rich_to_html(payload))
        elif kind == "divider":
            lines.append("──────────")
        elif kind == "table":
            rows, header = payload
            lines.append(_table_html_rows(rows, header))
    body = "\n".join(line for line in lines if line)
    if credit:
        body = f"{body}\n{esc(credit)}" if body else esc(credit)
    return f"<blockquote>{body}</blockquote>"


def _table_rows(rows: Sequence[Sequence[RichBlockTableCell]], header: bool) -> list[list[RichBlockTableCell]]:
    """Mark the first row as a header when requested."""
    if not header or not rows:
        return [list(r) for r in rows]
    marked: list[list[RichBlockTableCell]] = []
    for index, row in enumerate(rows):
        if index == 0:
            marked.append(
                [cell(c.text, c.align, c.valign, header=True) for c in row]
            )
        else:
            marked.append(list(row))
    return marked


def _table_html_rows(rows: Sequence[Sequence[RichBlockTableCell]], header: bool) -> str:
    lines: list[str] = []
    for index, row in enumerate(rows):
        rendered = "  ".join(_rich_to_html(c.text) for c in row)
        if header and index == 0:
            lines.append(f"<b>{rendered}</b>")
        else:
            lines.append(rendered)
    return "\n".join(lines)


def _table_html(rows: Sequence[Sequence[RichBlockTableCell]], header: bool) -> str:
    return f"<blockquote>{_table_html_rows(rows, header)}</blockquote>"


def para(text: str) -> tuple[str, Any]:
    return ("para", text)


def table_block(rows: Sequence[Sequence[Any]], header: bool = False) -> tuple[str, Any]:
    """Build a table block from plain strings (converted into cells)."""
    built: list[list[RichBlockTableCell]] = []
    for row in rows:
        built.append([c if isinstance(c, RichBlockTableCell) else cell(c) for c in row])
    return ("table", (built, header))


# ── shared fragments ─────────────────────────────────────────────────
def _status_row(status: str, accent: bool = True) -> list[RichBlockTableCell]:
    suffix = f" {S_STAR}" if accent else ""
    return [label("status"), cell(f"{sc(status)}{suffix}")]


def _plan_row(plan: str) -> list[RichBlockTableCell]:
    return [label("plan"), cell(plan_badge(plan))]


def _attempts_text(user: dict[str, Any]) -> str:
    plan = str(user.get("plan") or "free")
    limit = daily_limit(plan)
    used = int(user.get("daily_attempts_used", 0) or 0)
    if limit is None:
        return f"{fmt_num(used)}/∞"
    return f"{used}/{limit}"


def _username(user: dict[str, Any]) -> str:
    handle = str(user.get("username") or "").strip()
    if handle:
        return f"@{handle}"
    name = str(user.get("full_name") or "").strip()
    return name or sc("no username")


def _video_of(config: dict[str, Any] | None, key: str) -> str:
    return str((config or {}).get(key) or "").strip()


# ── system states ────────────────────────────────────────────────────
def render_loading_card(text: str = "→ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ✦", video: str = "") -> InputRichMessage:
    card = Card(BOT_NAME_DISPLAY)
    if video:
        card.video(video)
    card.divider()
    card.quote(para(text))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_loading_card_html(text: str = "→ ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ✦") -> str:
    card = Card(BOT_NAME_DISPLAY).divider().quote(para(text)).quote(para(BRAND_FOOTER))
    return card.to_html()


def render_error_card(title: str, detail: str) -> InputRichMessage:
    """Specific failure + how to recover, never a raw exception."""
    card = Card(f"◇ {sc(title)}").divider()
    card.quote(para(f"→ {sc(detail)} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def error_card(title: str, detail: str) -> str:
    """HTML fallback error card (Section 9.8)."""
    card = Card(f"◇ {sc(title)}").divider()
    card.quote(para(f"→ {sc(detail)} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_banned_card(reason: str = "") -> InputRichMessage:
    card = Card(f"◇ {sc('Access Restricted')}").divider()
    card.quote(
        para(f"✦ {sc('this account cannot use the bot')}"),
        para(f"{S_BULLET} {sc('reason')}: {cold_state_guard(reason, sc('contact support'))}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_maintenance_card() -> InputRichMessage:
    card = Card(f"◇ {sc('Under Maintenance')}").divider()
    card.quote(
        para(f"→ {sc('we are polishing the cherry blossoms')} ✦"),
        para(f"{S_BULLET} {sc('the bot returns shortly, thanks for waiting')} {S_HEART}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


# ── verification ────────────────────────────────────────────────────
def render_verify_card(channels: Sequence[dict[str, Any]], video: str = "") -> InputRichMessage:
    card = Card(f"◆ {sc('Verification Required')}")
    if video:
        card.video(video)
    card.divider()
    rows = [para(f"→ {sc('Join all channels to continue')} {S_STAR}")]
    for channel in channels:
        name = str(channel.get("channel_name") or channel.get("channel_id") or "channel")
        rows.append(para(f"{S_BULLET} {esc(name)}"))
    card.quote(*rows)
    card.quote(
        para(f"→ {sc('join all channels to unlock sakura')} {S_STAR}"),
        para(BRAND_FOOTER),
    )
    return card.to_rich()


def render_verify_card_html(channels: Sequence[dict[str, Any]]) -> str:
    rows = [para(f"→ {sc('Join all channels to continue')} {S_STAR}")]
    for channel in channels:
        rows.append(para(f"{S_BULLET} {channel.get('channel_name') or channel.get('channel_id')}"))
    card = Card(f"◆ {sc('Verification Required')}").divider().quote(*rows)
    card.quote(para(f"→ {sc('join all channels to unlock sakura')} {S_STAR}"), para(BRAND_FOOTER))
    return card.to_html()


# ── main menu ───────────────────────────────────────────────────────
def render_main_menu_card(
    user: dict[str, Any],
    rank: int,
    video: str = "",
) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    card = Card(BOT_NAME_DISPLAY)
    if video:
        card.video(video)
    card.divider()
    card.quote(
        table_block(
            [
                [label("plan"), cell(plan_badge(plan))],
                [label("checks"), value(_attempts_text(user))],
                [label("hits"), value(fmt_num(user.get("total_hits", 0)))],
                [label("rank"), value(f"#{rank}" if rank else f"#{DASH}")],
            ]
        )
    )
    card.quote(
        table_block(
            [
                [cell(f"◆ {sc('Netflix Checker')}"), cell(f"◇ {sc('Live Stats')}")],
                [cell(f"◆ {sc('Multi API')}"), cell(f"◇ {sc('Leaderboard')}")],
                [cell(f"◆ {sc('Plan System')}"), cell(f"◇ {sc('Auto Hits')}")],
            ]
        )
    )
    card.quote(
        para(f"→ /start {sc('to begin')} {S_STAR}"),
        para(BRAND_FOOTER),
    )
    return card.to_rich()


def render_main_menu_card_html(user: dict[str, Any], rank: int) -> str:
    plan = str(user.get("plan") or "free")
    card = Card(BOT_NAME_DISPLAY).divider()
    card.quote(
        table_block(
            [
                [label("plan"), plan_badge(plan)],
                [label("checks"), _attempts_text(user)],
                [label("hits"), fmt_num(user.get("total_hits", 0))],
                [label("rank"), f"#{rank}" if rank else f"#{DASH}"],
            ]
        )
    )
    card.quote(para(f"→ /start {sc('to begin')} {S_STAR}"), para(BRAND_FOOTER))
    return card.to_html()


# ── check flow ──────────────────────────────────────────────────────
def render_check_instructions_card(user: dict[str, Any]) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    limit = daily_limit(plan)
    left = "∞" if limit is None else str(max(0, limit - int(user.get("daily_attempts_used", 0) or 0)))
    limit_text = "∞" if limit is None else fmt_num(limit)

    card = Card(f"▷ {sc('Submit Cookies File')}").divider()
    card.quote(
        para(f"→ {sc('send your cookies file below')} {S_STAR}"),
        table_block(
            [
                [label("your plan"), cell(plan_badge(plan))],
                [label("max cookies"), value(limit_text)],
                [label("attempts left"), value(f"{left}/{limit_text}")],
            ]
        ),
    )
    card.quote(
        para(f"{S_BULLET} {sc('.txt, .json, .zip and .rar are accepted')}"),
        para(f"{S_BULLET} {sc('one cookie set per line inside')}"),
        para(f"{S_BULLET} {sc('max')} {fmt_num(max_cookies(plan))} {sc('cookies')}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_check_instructions_card_html(user: dict[str, Any]) -> str:
    plan = str(user.get("plan") or "free")
    limit = daily_limit(plan)
    limit_text = "∞" if limit is None else fmt_num(limit)
    left = "∞" if limit is None else str(max(0, limit - int(user.get("daily_attempts_used", 0) or 0)))
    card = Card(f"▷ {sc('Submit Cookies File')}").divider()
    card.quote(
        para(f"→ {sc('send your cookies file below')} {S_STAR}"),
        table_block(
            [
                [label("your plan"), plan_badge(plan)],
                [label("max cookies"), limit_text],
                [label("attempts left"), f"{left}/{limit_text}"],
            ]
        ),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_cookie_confirm_card(user: dict[str, Any], file_info: dict[str, Any]) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    card = Card(f"◆ {sc('File Validated')}").divider()
    card.quote(
        table_block(
            [
                [label("file name"), cell(code(truncate(str(file_info.get("file_name") or DASH), 28)))],
                [label("cookies"), value(fmt_num(file_info.get("estimated_count", 0)))],
                [label("your limit"), value(fmt_num(max_cookies(plan)))],
                [label("status"), cell(f"{sc('ready to check')} {S_STAR}")],
            ]
        )
    )
    card.quote(para(f"→ {sc('select hit mode to begin')} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_cookie_confirm_card_html(user: dict[str, Any], file_info: dict[str, Any]) -> str:
    plan = str(user.get("plan") or "free")
    card = Card(f"◆ {sc('File Validated')}").divider()
    card.quote(
        table_block(
            [
                [label("file name"), file_info.get("file_name") or DASH],
                [label("cookies"), fmt_num(file_info.get("estimated_count", 0))],
                [label("your limit"), fmt_num(max_cookies(plan))],
                [label("status"), f"{sc('ready to check')} {S_STAR}"],
            ]
        )
    )
    card.quote(para(f"→ {sc('select hit mode to begin')} {S_STAR}"), para(BRAND_FOOTER))
    return card.to_html()


# ── live dashboard ──────────────────────────────────────────────────
def render_live_card(session: Any, finished: bool = False, video: str = "") -> InputRichMessage:
    stats = session.stats
    card = Card(BOT_NAME_DISPLAY)
    if video and not finished:
        card.video(video)
    card.divider()

    bar = progress_bar(stats.total if finished else stats.checked, stats.total)
    card.quote(para(bar))

    status_text = sc("finished") if finished else sc(stats.status or "running")
    accent = finished or stats.status == "running"
    rows = [
        _status_row(status_text, accent=accent),
        [label("checked"), value(f"{fmt_num(stats.checked)}/{fmt_num(stats.total)}")],
        [label("cpm"), value(fmt_num(int(stats.cpm)))],
        [label("valid"), value(fmt_num(stats.hits))],
        [label("premium"), value(fmt_num(stats.premium))],
        [label("free"), value(fmt_num(stats.free))],
        [label("bad"), value(fmt_num(stats.bad))],
        [label("error"), value(fmt_num(stats.errors))],
        [label("hold"), value(fmt_num(stats.hold))],
    ]
    card.quote(table_block(rows))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_live_card_html(session: Any, finished: bool = False) -> str:
    stats = session.stats
    card = Card(BOT_NAME_DISPLAY).divider()
    card.quote(para(progress_bar(stats.total if finished else stats.checked, stats.total)))
    card.quote(
        table_block(
            [
                _status_row(sc("finished") if finished else sc(stats.status or "running")),
                [label("checked"), f"{fmt_num(stats.checked)}/{fmt_num(stats.total)}"],
                [label("cpm"), fmt_num(int(stats.cpm))],
                [label("valid"), fmt_num(stats.hits)],
                [label("premium"), fmt_num(stats.premium)],
                [label("bad"), fmt_num(stats.bad)],
                [label("error"), fmt_num(stats.errors)],
                [label("hold"), fmt_num(stats.hold)],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── hit card ────────────────────────────────────────────────────────
def _hit_rows(hit: dict[str, Any]) -> list[list[RichBlockTableCell]]:
    account = hit.get("account") or {}
    flag = country_flag(
        str(account.get("country") or ""), str(account.get("country_flag") or "")
    )
    profiles = account.get("profiles") or []
    profile_text = ", ".join(str(p) for p in profiles) if profiles else NONE_LABEL
    hold = bool(account.get("is_on_hold")) or str(account.get("hold_status", "")).lower() in (
        "yes",
        "true",
    )
    extra = str(account.get("extra_member", "")).lower() in ("yes", "true")

    country_text = cold_state_guard(account.get("country"), DASH)
    return [
        [label("name"), value(account.get("name"))],
        [label("email"), cell(code(cold_state_guard(account.get("email"), DASH)))],
        [label("country"), cell(f"{country_text} {flag}".strip())],
        [label("plan"), value(account.get("plan"))],
        [label("since"), value(account.get("member_since"))],
        [label("billing"), value(account.get("next_billing"))],
        [label("payment"), value(account.get("payment"))],
        [label("card"), value(account.get("card"), NONE_LABEL)],
        [label("phone"), value(account.get("phone"), NONE_LABEL)],
        [label("quality"), value(account.get("video_quality") or account.get("quality"))],
        [label("streams"), value(account.get("streams"))],
        [label("price"), value(account.get("price"))],
        [label("hold"), cell(YES if hold else NO)],
        [label("extra"), cell(YES if extra else NO)],
        [label("verified"), cell(yes_no(account.get("email_verified")))],
        [label("status"), value(account.get("membership_status"))],
        [label("profiles"), cell(truncate(profile_text, 30))],
        [label("guid"), cell(code(cold_state_guard(account.get("user_guid"), DASH)))],
        [
            label("valid till"),
            cell(
                f"{cold_state_guard(account.get('valid_till_utc') or account.get('valid_till'), DASH)}"
                f" ({cold_state_guard(account.get('remaining'), DASH)})"
            ),
        ],
    ]


def render_hit_card(
    hit: dict[str, Any],
    document: Any | None = None,
    file_name: str = "",
) -> InputRichMessage:
    """The reward card. The country flag is the only colour emoji in the bot.

    ``document`` embeds the cookie .txt as the last quoted block, so the
    details, the file and the login buttons arrive as one message.
    """
    card = Card(f"✦ {sc('Hit Identified')}").divider()
    card.quote(table_block(_hit_rows(hit)))
    if document is not None:
        card.document(document, caption=file_name or sc("cookie file"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich(skip_entity_detection=True)


def render_hit_card_html(hit: dict[str, Any], file_name: str = "") -> str:
    card = Card(f"✦ {sc('Hit Identified')}").divider()
    card.quote(table_block(_hit_rows(hit)))
    if file_name:
        card.document(file_name, caption=file_name)
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── completion ──────────────────────────────────────────────────────
def render_complete_card(session: Any) -> InputRichMessage:
    stats = session.stats
    card = Card(BOT_NAME_DISPLAY).divider()
    card.quote(para(progress_bar(stats.total or stats.checked, stats.total or stats.checked)))
    card.quote(
        table_block(
            [
                [label("status"), cell(f"{sc('finished')} {S_STAR}")],
                [label("checked"), value(fmt_num(stats.checked))],
                [label("premium"), value(fmt_num(stats.premium))],
                [label("valid"), value(fmt_num(stats.hits))],
                [label("bad"), value(fmt_num(stats.bad))],
                [label("duration"), value(stats.elapsed_label)],
            ]
        )
    )
    if not stats.hits:
        card.quote(
            para(f"◇ {sc('no hits this run')} {S_HEART}"),
            para(f"{S_BULLET} {sc('try fresher cookies next time')}"),
        )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_complete_card_html(session: Any) -> str:
    stats = session.stats
    card = Card(BOT_NAME_DISPLAY).divider()
    card.quote(para(progress_bar(stats.total or stats.checked, stats.total or stats.checked)))
    card.quote(
        table_block(
            [
                [label("status"), f"{sc('finished')} {S_STAR}"],
                [label("checked"), fmt_num(stats.checked)],
                [label("premium"), fmt_num(stats.premium)],
                [label("valid"), fmt_num(stats.hits)],
                [label("bad"), fmt_num(stats.bad)],
                [label("duration"), stats.elapsed_label],
            ]
        )
    )
    if not stats.hits:
        card.quote(para(f"◇ {sc('no hits this run')} {S_HEART}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── profile ────────────────────────────────────────────────────────
def render_stats_card(user: dict[str, Any], rank: int) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    expires = user.get("plan_expires")
    expires_label = (
        f"{S_CROWN} 𝐒𝐚𝐤𝐮𝐫𝐀" if plan == "sakura" else (fmt_dt(expires) if expires else sc("lifetime"))
    )
    card = Card(f"▤ {sc('Your Profile & Stats')}").divider()
    card.quote(
        table_block(
            [
                [label("username"), value(_username(user))],
                [label("user id"), cell(code(user.get("user_id", DASH)))],
                [label("plan"), cell(plan_badge(plan))],
                [label("expires"), value(expires_label)],
                [label("checks"), value(fmt_num(user.get("total_checks", 0)))],
                [label("hits"), value(fmt_num(user.get("total_hits", 0)))],
                [label("premium"), value(fmt_num(user.get("total_premium_hits", 0)))],
                [label("today"), value(_attempts_text(user))],
                [label("rank"), value(f"#{rank}" if rank else f"#{DASH}")],
                [label("member since"), value(fmt_dt(user.get("joined_at"), "%Y-%m-%d"))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_stats_card_html(user: dict[str, Any], rank: int) -> str:
    plan = str(user.get("plan") or "free")
    expires = user.get("plan_expires")
    expires_label = (
        f"{S_CROWN} 𝐒𝐚𝐤𝐮𝐫𝐀" if plan == "sakura" else (fmt_dt(expires) if expires else sc("lifetime"))
    )
    card = Card(f"▤ {sc('Your Profile & Stats')}").divider()
    card.quote(
        table_block(
            [
                [label("username"), _username(user)],
                [label("user id"), str(user.get("user_id", DASH))],
                [label("plan"), plan_badge(plan)],
                [label("expires"), expires_label],
                [label("checks"), fmt_num(user.get("total_checks", 0))],
                [label("hits"), fmt_num(user.get("total_hits", 0))],
                [label("premium"), fmt_num(user.get("total_premium_hits", 0))],
                [label("today"), _attempts_text(user)],
                [label("rank"), f"#{rank}" if rank else f"#{DASH}"],
                [label("member since"), fmt_dt(user.get("joined_at"))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── leaderboard ─────────────────────────────────────────────────────
def render_leaderboard_card(
    rows: Sequence[dict[str, Any]],
    viewer_id: int,
    viewer_rank: int,
) -> InputRichMessage:
    card = Card(f"♕ {sc('Top Checkers')}").divider()

    if not rows:
        card.quote(
            para(f"◇ {sc('the board is still waiting for its first hit')} {S_HEART}"),
            para(f"{S_BULLET} {sc('run a check to claim the crown')}"),
        )
    else:
        items = []
        for index, row in enumerate(rows, start=1):
            crown = f"{S_CROWN} " if index == 1 else "◆ "
            name = str(row.get("username") or "").strip()
            display = f"@{name}" if name else (row.get("full_name") or sc("anonymous"))
            items.append(para(f"{crown}{esc(str(display))} — {fmt_num(row.get('total_hits', 0))} {sc('hits')}"))
        card.quote(*items)

    in_top = any(int(row.get("user_id", 0) or 0) == int(viewer_id) for row in rows)
    if not in_top and viewer_rank:
        card.quote(para(f"→ {sc('your rank')}: #{viewer_rank}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_leaderboard_card_html(rows: Sequence[dict[str, Any]], viewer_id: int, viewer_rank: int) -> str:
    card = Card(f"♕ {sc('Top Checkers')}").divider()
    if not rows:
        card.quote(para(f"◇ {sc('the board is still waiting for its first hit')} {S_HEART}"))
    else:
        items = []
        for index, row in enumerate(rows, start=1):
            crown = f"{S_CROWN} " if index == 1 else "◆ "
            name = str(row.get("username") or "").strip()
            display = f"@{name}" if name else (row.get("full_name") or sc("anonymous"))
            items.append(para(f"{crown}{display} — {fmt_num(row.get('total_hits', 0))} {sc('hits')}"))
        card.quote(*items)
    in_top = any(int(row.get("user_id", 0) or 0) == int(viewer_id) for row in rows)
    if not in_top and viewer_rank:
        card.quote(para(f"→ {sc('your rank')}: #{viewer_rank}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── help / updates ──────────────────────────────────────────────────
def render_help_card() -> InputRichMessage:
    commands = "\n".join(
        [
            f"◆ /start — {sc('open bot')}",
            f"◆ /check — {sc('start checking')}",
            f"◆ /stats — {sc('your statistics')}",
            f"◆ /leaderboard — {sc('top checkers')}",
            f"◆ /redeem [key] — {sc('activate plan')}",
            f"◆ /help — {sc('this menu')}",
            f"◆ /updates — {sc('latest changes')}",
        ]
    )
    card = Card(f"ⓘ {sc('Commands & Documentation')}").divider()
    card.expandable(commands, credit=sc("tap to expand"))
    card.quote(
        table_block(
            [
                [cell(sc("plan"), header=True), cell(sc("checks"), header=True), cell(sc("max cookies"), header=True)],
                [cell(sc("free")), cell("5"), cell("1,000")],
                [cell(sc("basic")), cell("10"), cell("10,000")],
                [cell(sc("premium")), cell("20"), cell("100,000")],
                [cell(f"{S_CROWN} 𝐒𝐚𝐤𝐮𝐫𝐀"), cell("∞"), cell("500,000")],
            ],
            header=True,
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_help_card_html() -> str:
    card = Card(f"ⓘ {sc('Commands & Documentation')}").divider()
    card.quote(
        para("◆ /start — " + sc("open bot")),
        para("◆ /check — " + sc("start checking")),
        para("◆ /stats — " + sc("your statistics")),
        para("◆ /leaderboard — " + sc("top checkers")),
        para("◆ /redeem [key] — " + sc("activate plan")),
        para("◆ /help — " + sc("this menu")),
        para("◆ /updates — " + sc("latest changes")),
    )
    card.quote(
        table_block(
            [
                [sc("plan"), sc("checks"), sc("max cookies")],
                [sc("free"), "5", "1,000"],
                [sc("basic"), "10", "10,000"],
                [sc("premium"), "20", "100,000"],
                [f"{S_CROWN} 𝐒𝐚𝐤𝐮𝐫𝐀", "∞", "500,000"],
            ],
            header=True,
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


UPDATES: tuple[tuple[str, str], ...] = (
    ("V1.0.0", "first public build: job api, live dashboard, hit cards, plans, admin room"),
    ("V0.9.0", "zip and rar cookie bundles, premium export files"),
    ("V0.8.0", "sakura plan, key redemption, leaderboard crown"),
)


def render_updates_card() -> InputRichMessage:
    card = Card(f"↻ {sc('Latest Changes')}").divider()
    rows = []
    for version, note in UPDATES:
        rows.append([cell(code(version)), cell(sc(note))])
    card.quote(table_block(rows))
    card.quote(
        para(f"✦ {sc('current build')}: {VERSION} {S_HEART}"),
        para(BRAND_FOOTER),
    )
    return card.to_rich()


def render_updates_card_html() -> str:
    card = Card(f"↻ {sc('Latest Changes')}").divider()
    card.quote(table_block([[code(v), sc(n)] for v, n in UPDATES]))
    card.quote(para(f"✦ {sc('current build')}: {VERSION} {S_HEART}"), para(BRAND_FOOTER))
    return card.to_html()


# ── plans / receipts ────────────────────────────────────────────────
def render_plans_card(user: dict[str, Any]) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    rows = [[label("your plan"), cell(plan_badge(plan))]]
    for slug in ("free", "basic", "premium", "sakura"):
        limits = plan_limits(slug)
        attempts = limits["daily_attempts"]
        rows.append(
            [
                cell(plan_badge(slug)),
                cell("∞" if attempts is None else str(attempts)),
                cell(fmt_num(limits["max_cookies"])),
            ]
        )
    card = Card(f"◆ {sc('Plans')}").divider()
    card.quote(
        table_block(
            [[cell(sc("plan"), header=True), cell(sc("checks"), header=True), cell(sc("cookies"), header=True)], *rows[1:]],
            header=True,
        )
    )
    card.quote(
        para(f"→ {sc('redeem a key with')} /redeem SAKURA-XXXX-XXXX-XXXX"),
        para(f"{S_BULLET} {sc('sakura plan is granted by the owner only')}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_plans_card_html(user: dict[str, Any]) -> str:
    rows = [[sc("plan"), sc("checks"), sc("cookies")]]
    for slug in ("free", "basic", "premium", "sakura"):
        limits = plan_limits(slug)
        attempts = limits["daily_attempts"]
        rows.append([plan_badge(slug), "∞" if attempts is None else str(attempts), fmt_num(limits["max_cookies"])])
    card = Card(f"◆ {sc('Plans')}").divider()
    card.quote(table_block(rows, header=True))
    card.quote(para(f"→ {sc('redeem a key with')} /redeem SAKURA-XXXX-XXXX-XXXX"), para(BRAND_FOOTER))
    return card.to_html()


def render_receipt_card(result: dict[str, Any], user: dict[str, Any]) -> InputRichMessage:
    plan = str(result.get("plan") or "basic")
    days = int(result.get("days") or 0)
    expires = result.get("expires")
    limit = daily_limit(plan)
    card = Card(f"✦ {sc('Plan Activated')}").divider()
    card.quote(
        table_block(
            [
                [label("receipt id"), cell(code(result.get("receipt_id") or DASH))],
                [label("plan"), cell(plan_badge(plan))],
                [label("duration"), value(f"{days} {sc('days')}")],
                [label("activated"), value(fmt_dt(utcnow()))],
                [label("expires"), value(fmt_dt(expires) if expires else sc("lifetime"))],
                [label("daily limit"), value("∞" if limit is None else str(limit))],
            ]
        )
    )
    if plan == "sakura":
        card.quote(para(f"✦ {sc('sakura plan activated')} {S_HEART}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_receipt_card_html(result: dict[str, Any], user: dict[str, Any]) -> str:
    plan = str(result.get("plan") or "basic")
    days = int(result.get("days") or 0)
    expires = result.get("expires")
    limit = daily_limit(plan)
    card = Card(f"✦ {sc('Plan Activated')}").divider()
    card.quote(
        table_block(
            [
                [label("receipt id"), result.get("receipt_id") or DASH],
                [label("plan"), plan_badge(plan)],
                [label("duration"), f"{days} {sc('days')}"],
                [label("activated"), fmt_dt(utcnow())],
                [label("expires"), fmt_dt(expires) if expires else sc("lifetime")],
                [label("daily limit"), "∞" if limit is None else str(limit)],
            ]
        )
    )
    if plan == "sakura":
        card.quote(para(f"✦ {sc('sakura plan activated')} {S_HEART}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_plan_expired_card(old_plan: str) -> InputRichMessage:
    card = Card(f"✦ {sc('Plan Expired')}").divider()
    card.quote(
        para(f"✦ {sc('your')} {plan_badge(old_plan)} {sc('plan has expired')}"),
        para(f"{sc('you have been returned to free tier')} {S_HEART}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_plan_expired_card_html(old_plan: str) -> str:
    card = Card(f"✦ {sc('Plan Expired')}").divider()
    card.quote(
        para(f"✦ {sc('your')} {plan_badge(old_plan)} {sc('plan has expired')}"),
        para(f"{sc('you have been returned to free tier')} {S_HEART}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_daily_limit_card(user: dict[str, Any]) -> InputRichMessage:
    plan = str(user.get("plan") or "free")
    limit = daily_limit(plan)
    used = int(user.get("daily_attempts_used", 0) or 0)
    card = Card(f"◇ {sc('Daily Limit Reached')}").divider()
    card.quote(
        table_block(
            [
                [label("used"), value(f"{used}/{('∞' if limit is None else limit)}")],
                [label("resets"), cell(f"↻ {sc('midnight utc')} {S_STAR}")],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_daily_limit_card_html(user: dict[str, Any]) -> str:
    plan = str(user.get("plan") or "free")
    limit = daily_limit(plan)
    used = int(user.get("daily_attempts_used", 0) or 0)
    card = Card(f"◇ {sc('Daily Limit Reached')}").divider()
    card.quote(
        table_block(
            [
                [label("used"), f"{used}/{('∞' if limit is None else limit)}"],
                [label("resets"), f"↻ {sc('midnight utc')} {S_STAR}"],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── admin: control room ─────────────────────────────────────────────
def render_admin_dashboard_card(
    numbers: dict[str, Any],
    cluster: dict[str, Any],
    system: dict[str, Any],
    sessions: int,
) -> InputRichMessage:
    card = Card(f"◆ {sc('Admin Control Room')}").divider()
    card.quote(
        table_block(
            [
                [label("total users"), value(fmt_num(numbers.get("total_users", 0)))],
                [label("active today"), value(fmt_num(numbers.get("active_today", 0)))],
                [label("this week"), value(fmt_num(numbers.get("active_week", 0)))],
                [label("banned"), value(fmt_num(numbers.get("banned", 0)))],
            ]
        )
    )
    card.quote(
        table_block(
            [
                [label("checks today"), value(fmt_num(numbers.get("checks_today", 0)))],
                [label("hits today"), value(fmt_num(numbers.get("hits_today", 0)))],
                [label("premium today"), value(fmt_num(numbers.get("premium_today", 0)))],
            ]
        )
    )
    card.quote(
        table_block(
            [
                [label("cpu"), value(f"{system.get('cpu', 0):.0f}%")],
                [label("ram"), value(f"{system.get('ram', 0):.0f}%")],
                [label("disk"), value(f"{system.get('disk', 0):.0f}%")],
                [label("uptime"), value(str(system.get("uptime") or DASH))],
            ]
        )
    )
    cluster_rows = [
        [label("sessions"), value(fmt_num(sessions))],
        [label("apis"), value(f"{cluster.get('online', 0)}/{cluster.get('total', 0)} {sc('online')}")],
        [label("avg cpm"), value(fmt_num(int(cluster.get("avg_cpm", 0))))],
    ]
    if cluster.get("all_online"):
        cluster_rows.append([label("cluster"), cell(f"✦ {sc('all nodes online')} {S_HEART}")])
    card.quote(table_block(cluster_rows))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_dashboard_card_html(
    numbers: dict[str, Any], cluster: dict[str, Any], system: dict[str, Any], sessions: int
) -> str:
    card = Card(f"◆ {sc('Admin Control Room')}").divider()
    card.quote(
        table_block(
            [
                [label("total users"), fmt_num(numbers.get("total_users", 0))],
                [label("active today"), fmt_num(numbers.get("active_today", 0))],
                [label("banned"), fmt_num(numbers.get("banned", 0))],
                [label("sessions"), fmt_num(sessions)],
                [label("apis"), f"{cluster.get('online', 0)}/{cluster.get('total', 0)} {sc('online')}"],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_admin_users_card(page_data: dict[str, Any]) -> InputRichMessage:
    card = Card(f"◆ {sc('User Manager')}").divider()
    rows = page_data.get("rows") or []
    if not rows:
        card.quote(
            para(f"◇ {sc('no users on this page')} {S_HEART}"),
            para(f"{S_BULLET} {sc('try the previous page')}"),
        )
    else:
        table = []
        for row in rows:
            name = str(row.get("username") or "").strip()
            handle = f"@{name}" if name else sc("no username")
            banned = f" {sc('banned')}" if row.get("is_banned") else ""
            table.append(
                [
                    cell(code(row.get("user_id", DASH))),
                    cell(truncate(handle, 18) + banned),
                    cell(plan_badge(str(row.get("plan") or "free"))),
                ]
            )
        card.quote(table_block(table))
    card.quote(
        para(f"→ {sc('page')} {page_data.get('page', 0) + 1}/{page_data.get('pages', 1)}"),
        para(f"{S_BULLET} {sc('send a user id to open a profile')}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_users_card_html(page_data: dict[str, Any]) -> str:
    rows = page_data.get("rows") or []
    card = Card(f"◆ {sc('User Manager')}").divider()
    if rows:
        card.quote(
            table_block(
                [
                    [str(r.get("user_id", DASH)), str(r.get("username") or sc("no username")), str(r.get("plan") or "free")]
                    for r in rows
                ]
            )
        )
    card.quote(para(f"→ {sc('page')} {page_data.get('page', 0) + 1}/{page_data.get('pages', 1)}"), para(BRAND_FOOTER))
    return card.to_html()


def render_admin_user_card(target: dict[str, Any], rank: int) -> InputRichMessage:
    plan = str(target.get("plan") or "free")
    card = Card(f"◆ {sc('User Profile')}").divider()
    card.quote(
        table_block(
            [
                [label("user id"), cell(code(target.get("user_id", DASH)))],
                [label("username"), value(_username(target))],
                [label("plan"), cell(plan_badge(plan))],
                [label("expires"), value(fmt_dt(target.get("plan_expires")) if target.get("plan_expires") else sc("lifetime"))],
                [label("today"), value(_attempts_text(target))],
                [label("checks"), value(fmt_num(target.get("total_checks", 0)))],
                [label("hits"), value(fmt_num(target.get("total_hits", 0)))],
                [label("rank"), value(f"#{rank}" if rank else f"#{DASH}")],
                [label("status"), cell(sc("banned") if target.get("is_banned") else sc("active"))],
                [label("joined"), value(fmt_dt(target.get("joined_at")))],
            ]
        )
    )
    if target.get("is_banned") and target.get("ban_reason"):
        card.quote(para(f"{S_BULLET} {sc('ban reason')}: {target.get('ban_reason')}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_user_card_html(target: dict[str, Any], rank: int) -> str:
    card = Card(f"◆ {sc('User Profile')}").divider()
    card.quote(
        table_block(
            [
                [label("user id"), str(target.get("user_id", DASH))],
                [label("plan"), plan_badge(str(target.get("plan") or "free"))],
                [label("today"), _attempts_text(target)],
                [label("hits"), fmt_num(target.get("total_hits", 0))],
                [label("banned"), yes_no(target.get("is_banned"))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── admin: cluster ──────────────────────────────────────────────────
def render_admin_apis_card(apis: Sequence[dict[str, Any]], cluster: dict[str, Any]) -> InputRichMessage:
    card = Card(f"◆ {sc('API Manager')}").divider()
    if not apis:
        card.quote(
            para(f"◇ {sc('no checker nodes yet')} {S_HEART}"),
            para(f"{S_BULLET} {sc('add one to start checking')}"),
        )
    else:
        rows = []
        for api in apis:
            rows.append(
                [
                    cell(code(api.get("api_id", DASH))),
                    cell(truncate(str(api.get("name") or DASH), 14)),
                    cell(sc(str(api.get("status") or "offline"))),
                    cell(fmt_num(api.get("active_jobs", 0))),
                ]
            )
        card.quote(table_block(rows))
    card.quote(
        para(
            f"→ {cluster.get('online', 0)}/{cluster.get('total', 0)} {sc('online')}"
            f" · {fmt_num(int(cluster.get('avg_cpm', 0)))} {sc('cpm')}"
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_apis_card_html(apis: Sequence[dict[str, Any]], cluster: dict[str, Any]) -> str:
    card = Card(f"◆ {sc('API Manager')}").divider()
    if apis:
        card.quote(
            table_block(
                [
                    [str(a.get("api_id")), str(a.get("name")), str(a.get("status")), str(a.get("active_jobs", 0))]
                    for a in apis
                ]
            )
        )
    card.quote(para(f"→ {cluster.get('online', 0)}/{cluster.get('total', 0)} {sc('online')}"), para(BRAND_FOOTER))
    return card.to_html()


def render_admin_api_card(api: dict[str, Any], stats: dict[str, Any] | None = None) -> InputRichMessage:
    card = Card(f"◆ {sc('Node Detail')}").divider()
    card.quote(
        table_block(
            [
                [label("api id"), cell(code(api.get("api_id", DASH)))],
                [label("name"), value(api.get("name"))],
                [label("url"), cell(code(truncate(str(api.get("url") or DASH), 30)))],
                [label("status"), cell(sc(str(api.get("status") or "offline")))],
                [label("priority"), value(api.get("priority", 1))],
                [label("ping"), value(f"{api.get('last_ping_ms', 0):.0f} ms")],
                [label("uptime"), value(f"{api.get('uptime_percent', 0):.1f}%")],
                [label("jobs"), value(fmt_num(api.get("active_jobs", 0)))],
                [label("requests"), value(fmt_num(api.get("total_requests", 0)))],
                [label("success"), value(fmt_num(api.get("total_success", 0)))],
                [label("failed"), value(fmt_num(api.get("total_failed", 0)))],
                [label("avg cpm"), value(fmt_num(int(api.get("avg_cpm", 0))))],
            ]
        )
    )
    if stats:
        card.quote(
            table_block(
                [
                    [label("goroutines"), value(fmt_num(stats.get("goroutines", 0)))],
                    [label("proxies"), value(f"{stats.get('proxy_alive', 0)}/{stats.get('proxy_total', 0)}")],
                    [label("memory"), value(f"{stats.get('heap_alloc_mb', 0):.0f} mb")],
                ]
            )
        )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_api_card_html(api: dict[str, Any], stats: dict[str, Any] | None = None) -> str:
    card = Card(f"◆ {sc('Node Detail')}").divider()
    card.quote(
        table_block(
            [
                [label("api id"), str(api.get("api_id", DASH))],
                [label("status"), sc(str(api.get("status") or "offline"))],
                [label("uptime"), f"{api.get('uptime_percent', 0):.1f}%"],
                [label("jobs"), fmt_num(api.get("active_jobs", 0))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── admin: channels / videos ────────────────────────────────────────
def render_admin_channels_card(channels: Sequence[dict[str, Any]]) -> InputRichMessage:
    card = Card(f"◆ {sc('Force Join Channels')}").divider()
    if not channels:
        card.quote(
            para(f"◇ {sc('no channels yet')} {S_HEART}"),
            para(f"{S_BULLET} {sc('add one to gate the bot')}"),
        )
    else:
        rows = []
        for channel in channels:
            rows.append(
                [
                    cell(truncate(str(channel.get("channel_name") or DASH), 14)),
                    cell(code(truncate(str(channel.get("channel_id") or DASH), 16))),
                    cell(sc("on") if channel.get("is_active") else sc("off")),
                ]
            )
        card.quote(table_block(rows))
    card.quote(para(f"→ {sc('tap a channel to toggle it')} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_channels_card_html(channels: Sequence[dict[str, Any]]) -> str:
    card = Card(f"◆ {sc('Force Join Channels')}").divider()
    if channels:
        card.quote(
            table_block(
                [
                    [str(c.get("channel_name")), str(c.get("channel_id")), sc("on") if c.get("is_active") else sc("off")]
                    for c in channels
                ]
            )
        )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_admin_videos_card(slots: dict[str, str]) -> InputRichMessage:
    card = Card(f"◇ {sc('Video Slots')}").divider()
    rows = []
    labels = {
        "main_menu_video": "main menu",
        "verify_video": "verification",
        "checking_video": "checking",
    }
    for key, text in labels.items():
        current = str(slots.get(key) or "").strip()
        rows.append([label(text), cell(sc("set") if current else sc("empty"))])
    card.quote(table_block(rows))
    card.quote(para(f"→ {sc('tap a slot then send a video')} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_videos_card_html(slots: dict[str, str]) -> str:
    card = Card(f"◇ {sc('Video Slots')}").divider()
    card.quote(
        table_block(
            [
                [label("main menu"), sc("set") if slots.get("main_menu_video") else sc("empty")],
                [label("verification"), sc("set") if slots.get("verify_video") else sc("empty")],
                [label("checking"), sc("set") if slots.get("checking_video") else sc("empty")],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


# ── admin: keys / broadcast / config / logs ─────────────────────────
def render_admin_keys_card(stats: dict[str, Any], keys: Sequence[str], plan: str | None = None) -> InputRichMessage:
    card = Card(f"◆ {sc('Key Generator')}").divider()
    rows = []
    for slug in ("basic", "premium"):
        bucket = stats.get(slug, {})
        rows.append(
            [
                cell(plan_badge(slug)),
                cell(fmt_num(bucket.get("unused", 0))),
                cell(fmt_num(bucket.get("used", 0))),
            ]
        )
    card.quote(
        table_block(
            [[cell(sc("plan"), header=True), cell(sc("unused"), header=True), cell(sc("used"), header=True)], *rows],
            header=True,
        )
    )
    if keys:
        card.quote(para(f"{S_BULLET} {sc('latest unused keys')}"), *(para(code(k)) for k in keys[:6]))
    else:
        card.quote(
            para(f"◇ {sc('no unused keys yet')} {S_HEART}"),
            para(f"{S_BULLET} {sc('pick a plan below to mint some')}"),
        )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_keys_card_html(stats: dict[str, Any], keys: Sequence[str], plan: str | None = None) -> str:
    card = Card(f"◆ {sc('Key Generator')}").divider()
    card.quote(
        table_block(
            [[sc("plan"), sc("unused"), sc("used")]]
            + [[slug, str(stats.get(slug, {}).get("unused", 0)), str(stats.get(slug, {}).get("used", 0))] for slug in ("basic", "premium")],
            header=True,
        )
    )
    if keys:
        card.quote(*[para(k) for k in keys[:6]])
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_admin_keys_result_card(plan: str, days: int, keys: Sequence[str]) -> InputRichMessage:
    card = Card(f"✦ {sc('Keys Generated')}").divider()
    card.quote(
        table_block(
            [
                [label("plan"), cell(plan_badge(plan))],
                [label("duration"), value(f"{days} {sc('days')}")],
                [label("count"), value(fmt_num(len(keys)))],
            ]
        )
    )
    card.quote(*(para(code(k)) for k in keys[:20]))
    if len(keys) > 20:
        card.quote(para(f"{S_BULLET} {sc('only the first 20 keys are shown')}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_broadcast_card() -> InputRichMessage:
    card = Card(f"◆ {sc('Broadcast')}").divider()
    card.quote(
        para(f"→ {sc('send the message to broadcast')} {S_STAR}"),
        para(f"{S_BULLET} {sc('text, photo or video with caption')}"),
        para(f"{S_BULLET} {sc('you will preview before sending')}"),
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_broadcast_preview_card(preview: str, recipients: int) -> InputRichMessage:
    card = Card(f"◇ {sc('Broadcast Preview')}").divider()
    card.quote(para(truncate(preview, 400)))
    card.quote(table_block([[label("recipients"), value(fmt_num(recipients))]]))
    card.quote(para(f"→ {sc('confirm to send')} {S_STAR}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_broadcast_preview_card_html(preview: str, recipients: int) -> str:
    card = Card(f"◇ {sc('Broadcast Preview')}").divider()
    card.quote(para(preview[:400]))
    card.quote(table_block([[label("recipients"), fmt_num(recipients)]]))
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_admin_config_card(config: dict[str, Any], cluster: dict[str, Any]) -> InputRichMessage:
    card = Card(f"◇ {sc('Bot Config')}").divider()
    card.quote(
        table_block(
            [
                [label("maintenance"), cell(sc("on") if config.get("maintenance_mode") else sc("off"))],
                [label("rotation"), cell(sc(str(config.get("api_rotation_mode") or "round_robin")))],
                [label("max concurrent"), value(fmt_num(config.get("max_concurrent", 0)))],
                [label("forward channel"), value(config.get("hit_forward_channel"), sc("not set"))],
                [label("sessions live"), value(fmt_num(cluster.get("active_jobs", 0)))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_config_card_html(config: dict[str, Any], cluster: dict[str, Any]) -> str:
    card = Card(f"◇ {sc('Bot Config')}").divider()
    card.quote(
        table_block(
            [
                [label("maintenance"), sc("on") if config.get("maintenance_mode") else sc("off")],
                [label("rotation"), sc(str(config.get("api_rotation_mode") or "round_robin"))],
                [label("max concurrent"), fmt_num(config.get("max_concurrent", 0))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


def render_admin_logs_card(rows: Sequence[dict[str, Any]], page: int, pages: int) -> InputRichMessage:
    card = Card(f"◆ {sc('Admin Logs')}").divider()
    if not rows:
        card.quote(
            para(f"◇ {sc('no admin activity logged yet')} {S_HEART}"),
            para(f"{S_BULLET} {sc('actions appear here as they happen')}"),
        )
    else:
        items = []
        for entry in rows:
            stamp = fmt_dt(entry.get("performed_at"), "%m-%d %H:%M")
            items.append(
                para(
                    f"{S_BULLET} {stamp} · {sc(str(entry.get('action') or DASH))}"
                    f" · {esc(str(entry.get('target') or DASH))}"
                )
            )
        card.quote(*items)
    card.quote(para(f"→ {sc('page')} {page + 1}/{pages}"))
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_logs_card_html(rows: Sequence[dict[str, Any]], page: int, pages: int) -> str:
    card = Card(f"◆ {sc('Admin Logs')}").divider()
    if rows:
        card.quote(
            *[
                para(
                    f"{S_BULLET} {fmt_dt(e.get('performed_at'), '%m-%d %H:%M')}"
                    f" · {e.get('action')} · {e.get('target')}"
                )
                for e in rows
            ]
        )
    card.quote(para(f"→ {sc('page')} {page + 1}/{pages}"), para(BRAND_FOOTER))
    return card.to_html()


def render_admin_bot_stats_card(snapshot: dict[str, Any]) -> InputRichMessage:
    card = Card(f"✦ {sc('Bot Statistics')}").divider()
    health = snapshot.get("api_health") or {}
    card.quote(
        table_block(
            [
                [label("users"), value(fmt_num(snapshot.get("total_users", 0)))],
                [label("active today"), value(fmt_num(snapshot.get("active_today", 0)))],
                [label("sessions"), value(fmt_num(snapshot.get("active_sessions", 0)))],
                [label("cpu"), value(f"{snapshot.get('cpu_percent', 0):.0f}%")],
                [label("ram"), value(f"{snapshot.get('ram_percent', 0):.0f}%")],
                [label("disk"), value(f"{snapshot.get('disk_percent', 0):.0f}%")],
                [label("apis"), value(f"{health.get('online', 0)}/{health.get('total', 0)} {sc('online')}")],
                [label("avg cpm"), value(fmt_num(int(health.get("avg_cpm", 0))))],
                [label("recorded"), value(fmt_dt(snapshot.get("recorded_at"), "%H:%M"))],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_rich()


def render_admin_bot_stats_card_html(snapshot: dict[str, Any]) -> str:
    card = Card(f"✦ {sc('Bot Statistics')}").divider()
    card.quote(
        table_block(
            [
                [label("users"), fmt_num(snapshot.get("total_users", 0))],
                [label("sessions"), fmt_num(snapshot.get("active_sessions", 0))],
                [label("cpu"), f"{snapshot.get('cpu_percent', 0):.0f}%"],
                [label("ram"), f"{snapshot.get('ram_percent', 0):.0f}%"],
            ]
        )
    )
    card.quote(para(BRAND_FOOTER))
    return card.to_html()


BRAND_LINE_UNUSED = BRAND_LINE  # documented brand constant, kept importable
