"""AuthCheck: report orphan / un-audited / stale-audit characters per corp."""
import logging
from datetime import timedelta, timezone as dt_timezone

import discord
from aadiscordbot.app_settings import get_all_servers
from discord import option
from discord.embeds import Embed
from discord.ext import commands

from django.conf import settings
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from allianceauth.eveonline.models import EveCorporationInfo
from allianceauth.services.modules.discord.models import DiscordUser

logger = logging.getLogger(__name__)


# ---- Channel tiers ----------------------------------------------------------

def _is_super(channel_id: int) -> bool:
    return channel_id in getattr(
        settings, "AUTHCHECK_DISCORD_BOT_SUPER_CHANNELS", []
    )


def _is_regular(channel_id: int) -> bool:
    return channel_id in getattr(
        settings, "AUTHCHECK_DISCORD_BOT_CHANNELS", []
    )


def _channel_tier(channel_id: int):
    """Return 'super', 'regular', or None for disallowed."""
    if _is_super(channel_id):
        return "super"
    if _is_regular(channel_id):
        return "regular"
    return None


# ---- Data access ------------------------------------------------------------

# Two adjacent FKs both happen to use the column name `character_id`:
#   corptools_characteraudit.character_id  -> eveonline_evecharacter.id
#   corptools_characterroles.character_id  -> corptools_characteraudit.id
# Easy to misread — the joins below thread through both.

_DIRECTOR_CORPS_SQL = """
SELECT DISTINCT
    ec.corporation_id,
    ec.corporation_name,
    ec.corporation_ticker
FROM authentication_characterownership co
JOIN eveonline_evecharacter   ec ON ec.id           = co.character_id
JOIN corptools_characteraudit ca ON ca.character_id = ec.id
JOIN corptools_characterroles cr ON cr.character_id = ca.id
WHERE co.user_id = %s
  AND cr.director = 1
ORDER BY ec.corporation_ticker
"""

# Roster source: every EveCharacter currently affiliated with the target corp.
# This catches authed characters whose ownership row has been removed
# (degraded "orphan" detection) and authed characters without a corptools
# audit row (no-audit). It cannot see characters who have never been
# registered in AA at all — the README's Caveats section explains the
# limitation and points at corputils as a richer alternative.
_CORP_ROSTER_SQL = """
SELECT
    ec.character_id        AS member_eve_id,
    ec.character_name      AS character_name,
    co.user_id             AS auth_user_id,
    au.username            AS auth_user,
    ca.id                  AS audit_id,
    ca.last_update_skills  AS last_update_skills,
    ca.last_update_assets  AS last_update_assets,
    ca.last_update_wallet  AS last_update_wallet
FROM eveonline_evecharacter ec
LEFT JOIN authentication_characterownership co ON co.character_id = ec.id
LEFT JOIN auth_user                         au ON au.id           = co.user_id
LEFT JOIN corptools_characteraudit          ca ON ca.character_id = ec.id
WHERE ec.corporation_id = %s
ORDER BY ec.character_name
"""


def _rows(sql, params):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _director_corps(auth_user_id: int):
    return _rows(_DIRECTOR_CORPS_SQL, [auth_user_id])


def _corp_roster(corporation_id: int):
    return _rows(_CORP_ROSTER_SQL, [corporation_id])


def _resolve_auth_user(discord_id: int):
    try:
        return DiscordUser.objects.get(uid=discord_id).user
    except DiscordUser.DoesNotExist:
        return None


def _resolve_corp(ticker_or_name: str):
    s = ticker_or_name.strip()
    return EveCorporationInfo.objects.filter(
        Q(corporation_ticker__iexact=s) | Q(corporation_name__iexact=s)
    ).first()


# ---- Classification & formatting -------------------------------------------

_AUDIT_FRESHNESS_FIELDS = (
    "last_update_skills",
    "last_update_assets",
    "last_update_wallet",
)


def _aware(dt):
    # Raw cursor.fetchall() returns naive datetimes from MySQL even with
    # USE_TZ=True; treat those as UTC so we can compare against an aware cutoff.
    if dt is None or timezone.is_aware(dt):
        return dt
    return dt.replace(tzinfo=dt_timezone.utc)


def _classify(row, cutoff):
    if row["auth_user_id"] is None:
        return "orphan", []
    if row["audit_id"] is None:
        return "no_audit", []
    stale = [
        f for f in _AUDIT_FRESHNESS_FIELDS
        if row[f] is None or _aware(row[f]) < cutoff
    ]
    return ("stale" if stale else "ok"), stale


def _bucket(rows):
    cutoff = timezone.now() - timedelta(
        days=getattr(settings, "AUTHCHECK_STALE_DAYS", 7)
    )
    orphans, no_audit, stale, ok = [], [], [], []
    for r in rows:
        kind, stale_fields = _classify(r, cutoff)
        if kind == "orphan":
            orphans.append(r)
        elif kind == "no_audit":
            no_audit.append(r)
        elif kind == "stale":
            r["_stale_fields"] = stale_fields
            stale.append(r)
        else:
            ok.append(r)
    return orphans, no_audit, stale, ok


def _fmt_orphan(r):
    return f"• **{r['character_name']}** — no auth ownership"


def _fmt_no_audit(r):
    return (
        f"• **{r['character_name']}** — authed as `{r['auth_user']}`, "
        "no corptools registration"
    )


def _fmt_stale(r):
    fields = ", ".join(
        f.replace("last_update_", "") for f in r["_stale_fields"]
    )
    return (
        f"• **{r['character_name']}** — authed as `{r['auth_user']}`, "
        f"stale: {fields}"
    )


def _fmt_ok(r):
    return f"• **{r['character_name']}** — authed as `{r['auth_user']}`"


# Single embed description can hold 4096 chars; keep blocks under this so
# _build_embeds never produces an oversize page that Discord will 400 on.
_MAX_BLOCK = 3500


def _chunk_section(section_header, lines):
    """Split one bucket's lines into ≤_MAX_BLOCK-char chunks.

    Each chunk carries its own header — the first uses `section_header`
    verbatim, follow-ups append ` (cont.)`.
    """
    chunks, current, size = [], [section_header], len(section_header)
    for line in lines:
        addition = len(line) + 1  # newline
        if size + addition > _MAX_BLOCK and len(current) > 1:
            chunks.append("\n".join(current))
            cont = f"{section_header} (cont.)"
            current, size = [cont, line], len(cont) + addition
        else:
            current.append(line)
            size += addition
    chunks.append("\n".join(current))
    return chunks


def _corp_block(corp_ticker, corp_name, orphans, no_audit, stale, ok, total):
    """Return one or more text blocks for this corp's report.

    Splitting per-bucket (and within a bucket when needed) keeps each
    returned string under Discord's embed-description cap.
    """
    stale_days = getattr(settings, "AUTHCHECK_STALE_DAYS", 7)
    header = f"__**{corp_ticker}** ({corp_name}) — {total} members__"

    sections = []
    if orphans:
        sections.extend(_chunk_section(
            f"**🟥 Orphans ({len(orphans)})** — in corp roster, "
            "no auth ownership",
            [_fmt_orphan(r) for r in orphans],
        ))
    if no_audit:
        sections.extend(_chunk_section(
            f"**🟧 No corptools audit ({len(no_audit)})** — authed but "
            "never registered the audit token",
            [_fmt_no_audit(r) for r in no_audit],
        ))
    if stale:
        sections.extend(_chunk_section(
            f"**🟨 Stale audit ({len(stale)})** — last update older than "
            f"{stale_days} days",
            [_fmt_stale(r) for r in stale],
        ))
    if ok:
        sections.extend(_chunk_section(
            f"**🟩 OK ({len(ok)})** — authed and audit fresh",
            [_fmt_ok(r) for r in ok],
        ))

    if not sections:
        # Empty roster (already messaged upstream in _build_report) — keep
        # this branch for safety.
        return [f"{header}\n_No members._"]

    # Prepend the corp header to the first section block only.
    first = f"{header}\n{sections[0]}"
    if len(first) <= _MAX_BLOCK:
        return [first] + sections[1:]
    # Pathological: even the first section is at the cap. Keep the header
    # on a standalone block so we never exceed _MAX_BLOCK.
    return [header] + sections


def _build_embeds(text_blocks, title, empty_message="Nothing to report."):
    """Paginate a list of pre-formatted text blocks into Discord embeds.

    Each embed description is capped at 3900 chars (Discord's hard limit
    is 4096; we leave headroom). When more than one page is produced the
    title gets an `(i/N)` suffix.
    """
    if not text_blocks:
        return [Embed(title=title, description=empty_message, colour=0x2ECC71)]

    pages, buf, size = [], [], 0
    for block in text_blocks:
        if size + len(block) + 2 > 3900 and buf:
            pages.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(block)
        size += len(block) + 2
    if buf:
        pages.append("\n\n".join(buf))

    out = []
    for i, desc in enumerate(pages, 1):
        page_title = title + (f" ({i}/{len(pages)})" if len(pages) > 1 else "")
        out.append(Embed(title=page_title, description=desc, colour=0xE67E22))
    return out


def _build_report(corps):
    """Build embed pages covering every corp in `corps`.

    `corps` is a list of dicts with corporation_id / corporation_name /
    corporation_ticker (the shape returned by _director_corps()).
    """
    blocks = []
    for corp in corps:
        roster = _corp_roster(corp["corporation_id"])
        if not roster:
            blocks.append(
                f"__**{corp['corporation_ticker']}** "
                f"({corp['corporation_name']})__\n"
                f"⚠️ No AA-registered characters found in this corp. "
                "Members who have never authed at all can't be detected — "
                "see the README's Caveats section."
            )
            continue
        orphans, no_audit, stale, ok = _bucket(roster)
        blocks.extend(_corp_block(
            corp["corporation_ticker"],
            corp["corporation_name"],
            orphans, no_audit, stale, ok,
            len(roster),
        ))

    if len(corps) == 1:
        title = f"AuthCheck report: {corps[0]['corporation_ticker']}"
    else:
        title = f"AuthCheck report ({len(corps)} corps)"
    return _build_embeds(blocks, title)


# ---- The cog ----------------------------------------------------------------

class AuthCheck(commands.Cog):
    """Report orphan / un-audited / stale-audit characters per corp."""

    def __init__(self, bot):
        self.bot = bot

    # ---- prefix --------------------------------------------------------

    @commands.command(pass_context=True)
    async def authcheck(self, ctx):
        """!authcheck — DM the invoker the report for their director corps.

        Super-channel features (specifying a corp, channel reply, audit
        line) are slash-only by design.
        """
        if _channel_tier(ctx.message.channel.id) is None:
            return await ctx.message.add_reaction(chr(0x1F44E))  # 👎
        auth_user = _resolve_auth_user(ctx.author.id)
        if auth_user is None:
            return await ctx.message.reply(
                "Your Discord account isn't linked to Alliance Auth."
            )
        corps = _director_corps(auth_user.id)
        if not corps:
            return await ctx.message.reply(
                "You're not a director on any registered character."
            )
        embeds = _build_report(corps)
        try:
            for e in embeds:
                await ctx.author.send(embed=e)
        except discord.Forbidden:
            return await ctx.message.reply(
                "I can't DM you — check **Server Settings → Privacy → "
                "Allow direct messages from server members**."
            )
        await ctx.message.add_reaction(chr(0x1F44D))  # 👍

    # ---- slash ---------------------------------------------------------

    @commands.slash_command(name="authcheck", guild_ids=get_all_servers())
    @option(
        "corp",
        description="Corp ticker or name (super channel only; "
                    "defaults to your director corps)",
        required=False,
    )
    @option(
        "dm",
        description="Send the report via DM instead of in this channel "
                    "(super channel only; default false)",
        required=False,
        default=False,
    )
    async def slash_authcheck(self, ctx, corp: str = None, dm: bool = False):
        try:
            await self._slash_impl(ctx, corp, dm)
        except Exception as e:
            # aadiscordbot's generic error handler swallows the traceback
            # and shows the user "Something Went Wrong" — log it here so
            # the discordbot log captures the real failure.
            logger.exception("authcheck slash command failed")
            msg = (
                f"⚠️ AuthCheck hit `{type(e).__name__}`. Ask an admin to "
                "check the discordbot log for the traceback."
            )
            try:
                if ctx.response.is_done():
                    await ctx.followup.send(msg, ephemeral=True)
                else:
                    await ctx.respond(msg, ephemeral=True)
            except Exception:
                logger.exception("authcheck error reply also failed")

    async def _slash_impl(self, ctx, corp, dm):
        tier = _channel_tier(ctx.channel.id)
        if tier is None:
            return await ctx.respond(
                "This command isn't available in this channel.",
                ephemeral=True,
            )

        # Regular channels: lock to DM-only, director-scope. Reject the
        # corp argument so callers don't get a silent override.
        if tier == "regular":
            if corp:
                return await ctx.respond(
                    "Requesting a specific corp is only allowed in a "
                    "super channel. Try again without `corp:`.",
                    ephemeral=True,
                )
            dm = True

        await ctx.defer(ephemeral=dm)

        auth_user = _resolve_auth_user(ctx.user.id)
        if auth_user is None:
            return await ctx.respond(
                "Your Discord account isn't linked to Alliance Auth.",
                ephemeral=True,
            )

        if corp:
            target = _resolve_corp(corp)
            if target is None:
                return await ctx.respond(
                    f"Unknown corporation `{corp}`.", ephemeral=True,
                )
            corps = [{
                "corporation_id":     target.corporation_id,
                "corporation_name":   target.corporation_name,
                "corporation_ticker": target.corporation_ticker,
            }]
            scope_label = target.corporation_ticker
        else:
            corps = _director_corps(auth_user.id)
            if not corps:
                return await ctx.respond(
                    "You're not a director on any registered character.",
                    ephemeral=True,
                )
            scope_label = "your director corps"

        embeds = _build_report(corps)

        if dm:
            try:
                for e in embeds:
                    await ctx.user.send(embed=e)
            except discord.Forbidden:
                return await ctx.respond(
                    "I can't DM you — check **Server Settings → "
                    "Privacy → Allow direct messages from server "
                    "members**.",
                    ephemeral=True,
                )
            if tier == "super":
                await ctx.channel.send(
                    f"📬 {ctx.user.mention} requested authcheck for "
                    f"**{scope_label}** — sent via DM"
                )
            await ctx.respond("📬 Sent you a DM.", ephemeral=True)
        else:
            # tier == 'super' here (regular forced dm=True above).
            await ctx.respond(embed=embeds[0])
            for e in embeds[1:]:
                await ctx.followup.send(embed=e)


def setup(bot):
    bot.add_cog(AuthCheck(bot))
