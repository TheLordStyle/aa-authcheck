# aa-authcheck

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Alliance Auth](https://img.shields.io/badge/Alliance%20Auth-5.x-green.svg)](https://gitlab.com/allianceauth/allianceauth)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An [Alliance Auth](https://gitlab.com/allianceauth/allianceauth) Discord
cog that reports, per corporation, which members are **orphans**
(present in the corp roster but never claimed in AA), which are
**un-audited** (authed but never registered a corptools token), and
which have **stale audit data** (last update older than a configurable
threshold).

Built on top of [aadiscordbot](https://github.com/pvyParts/allianceauth-discordbot)
and [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools).

## What it does

Two tiers of Discord channel, configured separately:

- **Regular channels** — anyone can run `/authcheck` and gets a **DM**
  with the report for every corp where one of their auth-linked
  characters holds the EVE **Director** role.
- **Super channels** — privileged users can additionally request the
  report for **any** corporation by ticker (no director check) and
  choose whether the response goes back in the channel (default) or
  via DM. When DM is chosen, a short audit line is posted in the super
  channel so there's a record of the lookup.

Outside both allow-lists the command silently refuses (slash: ephemeral
*"not available in this channel"*; prefix: 👎 reaction).

### Example output

> __**NEWCO** (New Eden Corporation) — 124 members__
>
> **🟥 Orphans (2)** — in corp roster, no auth ownership
> • **Bob McOrphan** — no auth ownership
> • **Alice McNobody** — no auth ownership
>
> **🟧 No corptools audit (3)** — authed but never registered the audit token
> • **Carol Hopeful** — authed as `carol`, no corptools registration
> • **Dave Newish** — authed as `dave`, no corptools registration
> • **Eve Forgetful** — authed as `eve`, no corptools registration
>
> **🟨 Stale audit (1)** — last update older than 7 days
> • **Frank Stale** — authed as `frank`, stale: skills, assets

## Requirements

| Component | Version |
|---|---|
| Alliance Auth | ≥ 5.0 |
| [allianceauth-discordbot](https://github.com/pvyParts/allianceauth-discordbot) | recent |
| [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools) | ≥ 2.x |
| Database | MySQL / MariaDB |

Corptools must have at least one character per director's account
scanned with `esi-characters.read_corporation_roles.v1` so the
director check can find them. No corp-level ESI tokens are required —
the per-corp roster is derived from each character's public ESI data
(see [Caveats](#caveats) for the trade-off).

## Install

### Production (pinned)

Add to your AA `requirements.txt`:

```text
git+https://github.com/TheLordStyle/aa-authcheck.git@v0.1.2
```

Then in `local.py`:

```python
# ============================================================
#  aa-authcheck  -  per-corp auth & audit status report
#  https://github.com/TheLordStyle/aa-authcheck
# ============================================================
DISCORD_BOT_COGS += ["aa_authcheck.authcheck"]

# Channels where the command works in director-scope DM-only mode.
AUTHCHECK_DISCORD_BOT_CHANNELS = [
    111111111111111111,   # #directors
]

# Channels where the command additionally unlocks `corp:` (any corp,
# no director check) and `dm:` (channel reply by default, DM on
# request). Use sparingly — these channels can pull a report on any
# corporation in AA.
AUTHCHECK_DISCORD_BOT_SUPER_CHANNELS = [
    222222222222222222,   # #leadership
]

# Optional — default 7
# AUTHCHECK_STALE_DAYS = 7
```

Rebuild and restart auth.

### Development

For iteration without rebuilding the whole stack, bind-mount a checkout
into the discordbot container and install it editable:

```bash
docker compose exec allianceauth_discordbot \
    pip install -e /opt/cogs/aa-authcheck

docker compose restart allianceauth_discordbot
```

Editable installs don't survive a `docker compose down` and rebuild —
production state always returns to whatever's pinned in
`requirements.txt`.

## Settings

| Setting | Default | Description |
|---|---|---|
| `AUTHCHECK_DISCORD_BOT_CHANNELS` | `[]` | Regular allow-list. DM-only, director scope. Empty = command is blocked in non-super channels. |
| `AUTHCHECK_DISCORD_BOT_SUPER_CHANNELS` | `[]` | Super allow-list. Unlocks the `corp:` and `dm:` slash-command arguments. If a channel appears in both lists, super wins. |
| `AUTHCHECK_STALE_DAYS` | `7` | Day threshold beyond which a `last_update_*` timestamp counts as stale. |

## Usage

### Regular channel (DM-only, director scope)

```text
!authcheck
/authcheck
```

Both forms DM the invoker one embed per corp where they hold director.
The slash form acknowledges in-channel with an ephemeral *"📬 Sent you
a DM"*; the prefix form reacts 👍 on success and 👎 (or replies with a
hint) on failure.

### Super channel

```text
/authcheck                              # director-scope, posted in channel
/authcheck dm:true                      # director-scope, DM'd + audit line in channel
/authcheck corp:NEWCO                   # any corp, posted in channel
/authcheck corp:NEWCO dm:true           # any corp, DM'd + audit line in channel
```

`!authcheck` in a super channel still behaves like a regular-channel
invocation (DM-only, director scope) — the `corp:` and `dm:` features
are slash-only.

When `dm:true` is used in a super channel, the bot posts a single line
in the super channel itself:

> 📬 @YourName requested authcheck for **NEWCO** — sent via DM

so there's a public record of off-channel lookups.

## How it works

Per-corp report — one raw SQL query anchored on
`eveonline_evecharacter` filtered by `corporation_id`, with left joins to:

- `authentication_characterownership` — `NULL` → classified as an
  **orphan** (AA has the character but no user owns it).
- `auth_user` — display the auth account username when one exists.
- `corptools_characteraudit` — `NULL` → classified as **no audit**;
  otherwise its `last_update_skills`, `last_update_assets` and
  `last_update_wallet` timestamps are compared against
  `AUTHCHECK_STALE_DAYS` to classify as **stale** or **ok**.

Director detection — one raw SQL query joining
`authentication_characterownership` → `eveonline_evecharacter` →
`corptools_characteraudit` → `corptools_characterroles`, returning the
distinct set of corporations where the invoker owns at least one
character with `director = 1`.

## Caveats

- **Orphan detection is partial.** The roster is derived from
  `eveonline_evecharacter`, so the cog can only see characters that
  have been registered in AA at some point — not the full corp
  membership. Characters who are in the corp in EVE but have *never*
  authed anywhere can't be detected. To get full coverage you'd need
  to anchor the roster on Alliance Auth's bundled **Corp Stats** app
  (which requires a per-corp ESI token); this cog doesn't read from
  it today, but a future version may add it as an optional richer
  source.
- **Corp freshness.** `EveCharacter.corporation_id` is updated when
  AA refreshes a character's public ESI data (runs periodically via
  `run_model_update`). A character who switched corps in the last
  hour may still be reported under their old corp.
- **Director freshness.** The director flag comes from corptools'
  roles scan, which uses ESI's `read_corporation_roles` cache. New
  directors won't be recognised until corptools' next scheduled scan
  for that character.

## Contributing

Bug reports and PRs welcome. Please open an issue first for anything
beyond trivial fixes so we can talk about it.

## License

MIT — see [LICENSE](LICENSE).
