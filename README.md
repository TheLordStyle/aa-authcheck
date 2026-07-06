# aa-authcheck

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Alliance Auth](https://img.shields.io/badge/Alliance%20Auth-5.x-green.svg)](https://gitlab.com/allianceauth/allianceauth)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An [Alliance Auth](https://gitlab.com/allianceauth/allianceauth) Discord
cog with two commands:

- **`/authcheck`** — per-**character** report: which corp members are
  **orphans** (present in the corp roster but never claimed in AA),
  **un-audited** (authed but never registered a corptools token), have
  **stale audit data**, or are **OK**.
- **`/authcorpcheck`** — per-**corporation** report: is the corp loaded
  into corptools' **corporation audit** and is its data fresh, and is it
  registered as a structure **Owner** in
  [aa-structures](https://apps.allianceauth.org/apps/detail/aa-structures)
  with healthy syncs and enabled tokens.

Built on top of [aadiscordbot](https://github.com/pvyParts/allianceauth-discordbot)
and [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools).

## What it does

Two tiers of Discord channel, configured separately and shared by both
commands:

- **Regular channels** — anyone can run the commands and gets a **DM**
  with the report for every corp where one of their auth-linked
  characters holds the EVE **Director** role.
- **Super channels** — privileged users can additionally request the
  report for **any** corporation by ticker (no director check) and
  choose whether the response goes back in the channel (default) or
  via DM. When DM is chosen, a short audit line is posted in the super
  channel so there's a record of the lookup. For `/authcorpcheck`, the
  super-channel default scope (no `corp:` argument) is **every
  corporation that makes up the Member state** — discovered
  automatically from AA's state configuration (explicitly listed member
  corps plus all corps of the member alliances).

Outside both allow-lists the commands silently refuse (slash: ephemeral
*"not available in this channel"*; prefix: 👎 reaction).

### Example output — `/authcheck`

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
>
> **🟩 OK (118)** — authed and audit fresh
> • **Grace Onpoint** — authed as `grace`
> • _(... 117 more ...)_

### Example output — `/authcorpcheck`

> **14 corps checked — 3 with issues**
>
> __**HOLDR** (Holding Corp Inc)__
> 🟥 Corptools: corp not loaded into corp audit
> 🟧 Structures: not registered as structure owner
>
> __**NEWCO** (New Eden Corporation)__
> 🟨 Corptools: stale: wallet (never), assets (9.2h ago)
> 🟩 Structures: active, syncs fresh, tokens 2/2 enabled
>
> __**OLDCO** (Old Eden Corporation)__
> 🟩 Corptools: audit fresh
> 🟨 Structures: stale sync: notifications (1.2h ago); tokens 1/2 enabled
>
> **🟩 All OK (11)**
> AAA, BBBB, CCC, DDDD, EEE, FFFF, GGG, HHHH, III, JJJJ
> KKKK

## Requirements

| Component | Version |
|---|---|
| Alliance Auth | ≥ 5.0 |
| [allianceauth-discordbot](https://github.com/pvyParts/allianceauth-discordbot) | recent |
| [allianceauth-corptools](https://github.com/Solar-Helix-Independent-Transport/allianceauth-corp-tools) | ≥ 2.x |
| [aa-structures](https://apps.allianceauth.org/apps/detail/aa-structures) | optional — `/authcorpcheck`'s structure-owner checks are skipped when absent |
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
git+https://github.com/TheLordStyle/aa-authcheck.git@v0.2.0
```

Then in `local.py`:

```python
# ============================================================
#  aa-authcheck  -  per-corp auth & audit status report
#  https://github.com/TheLordStyle/aa-authcheck
# ============================================================
DISCORD_BOT_COGS += ["aa_authcheck.authcheck"]

# Channels where the commands work in director-scope DM-only mode.
AUTHCHECK_DISCORD_BOT_CHANNELS = [
    111111111111111111,   # #directors
]

# Channels where the commands additionally unlock `corp:` (any corp,
# no director check) and `dm:` (channel reply by default, DM on
# request), and where /authcorpcheck defaults to all member-state
# corps. Use sparingly — these channels can pull a report on any
# corporation in AA.
AUTHCHECK_DISCORD_BOT_SUPER_CHANNELS = [
    222222222222222222,   # #leadership
]

# Optional — defaults shown
# AUTHCHECK_STALE_DAYS = 7                     # /authcheck character staleness (days)
# AUTHCHECK_MEMBER_STATES = ["Member"]         # states scanned by /authcorpcheck
# AUTHCHECK_CORP_STALE_HOURS = 6               # corp-audit staleness (hours)
# AUTHCHECK_CORP_AUDIT_FIELDS = ["wallet", "structures", "assets"]
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
| `AUTHCHECK_DISCORD_BOT_CHANNELS` | `[]` | Regular allow-list. DM-only, director scope. Empty = commands are blocked in non-super channels. |
| `AUTHCHECK_DISCORD_BOT_SUPER_CHANNELS` | `[]` | Super allow-list. Unlocks the `corp:` and `dm:` slash-command arguments (and member-state scope for `/authcorpcheck`). If a channel appears in both lists, super wins. |
| `AUTHCHECK_STALE_DAYS` | `7` | `/authcheck`: day threshold beyond which a character's `last_update_*` timestamp counts as stale. |
| `AUTHCHECK_MEMBER_STATES` | `["Member"]` | `/authcorpcheck`: AA State names whose corporations form the super-channel default scope. |
| `AUTHCHECK_CORP_STALE_HOURS` | `6` | `/authcorpcheck`: hour threshold beyond which a corp-audit timestamp counts as stale (corptools updates corps hourly by default). |
| `AUTHCHECK_CORP_AUDIT_FIELDS` | `["wallet", "structures", "assets"]` | `/authcorpcheck`: which corp-audit `last_update_<field>` timestamps to check. Valid names: `pub_data`, `assets`, `structures`, `moons`, `observers`, `wallet`, `contracts`, `known_login`. |

Structure-owner sync freshness reuses the structures app's own grace
settings when present: `STRUCTURES_STRUCTURE_SYNC_GRACE_MINUTES`
(default 120, applies to structures + assets) and
`STRUCTURES_NOTIFICATION_SYNC_GRACE_MINUTES` (default 40, applies to
notifications + forwarding) — so a corp shows 🟨 here exactly when the
structures app itself considers its sync unhealthy.

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

### `/authcorpcheck` — corporation audit health

```text
!authcorpcheck                          # director corps, DM-only (any allowed channel)
/authcorpcheck                          # regular: director corps, DM'd
                                        # super:   ALL member-state corps, posted in channel
/authcorpcheck corp:NEWCO               # super only: one corp, posted in channel
/authcorpcheck dm:true                  # super only: DM'd + audit line in channel
```

Per corp the report shows two status lines:

- **Corptools** — 🟥 the corp isn't loaded into corptools' corporation
  audit at all; 🟨 loaded but one of the checked `last_update_*`
  timestamps is missing/older than `AUTHCHECK_CORP_STALE_HOURS`;
  🟩 fresh.
- **Structures** — 🟧 not registered as a structure owner in
  aa-structures; 🟥 owner disabled (`is_active` off), no owner
  characters, or all owner characters disabled (token failures);
  🟨 sync timestamps outside the structures app's grace windows and/or
  some tokens disabled; 🟩 active, syncs fresh, all tokens enabled.

Corps with any non-🟩 line are listed first; all-clean corps are
compacted into a ticker list at the end.

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

Corp-level report (`/authcorpcheck`) — two static IN-clause queries
merged in Python, both keyed on the `EveCorporationInfo` **pk** (in
`corptools_corporationaudit` and `structures_owner` the column named
`corporation_id` is a FK to that pk, *not* the EVE corporation id):

- `corptools_corporationaudit` — presence + the configured
  `last_update_*` timestamps vs `AUTHCHECK_CORP_STALE_HOURS`.
- `structures_owner` LEFT JOIN `structures_ownercharacter` (only when
  the structures app is installed) — `is_active`, the four
  `*_last_update_at` sync timestamps vs the structures app's grace
  windows, and enabled-vs-total owner-character counts.

Member-state discovery — the corporations of each state named in
`AUTHCHECK_MEMBER_STATES`: the state's explicitly listed
`member_corporations` plus every `EveCorporationInfo` whose alliance is
in the state's `member_alliances`. AA itself creates and maintains a
corp row for every corporation of a tracked alliance via the hourly
`update_alliance` task, so the alliance-derived list is complete.

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
- **Holding/shell corps show red forever.** The member-state scope
  includes every corp of the member alliances — 1-character holding
  corps that will never register a corptools corp audit show
  🟥 "not loaded into corp audit" on every run. There is currently no
  exclusion filter; a `member_count` threshold may be added later.
- **Forwarding sync noise.** Structure owners configured without
  notification webhooks can show a perpetually stale forwarding sync.
  This mirrors the structures app's own `are_all_syncs_ok` health view
  — if it's yellow here, it's unhealthy there too.
- **Token validity isn't stored.** aa-structures checks token validity
  live; the queryable signal is `is_enabled = False` on an owner
  character (the app disables characters after repeated ESI failures).
  A token that just went bad may briefly still show as enabled.
- **Director corps unknown to AA.** A director corp with no
  `EveCorporationInfo` row shows a red "not registered in Alliance
  Auth" block — it can't have a corp audit or structure owner until
  someone registers it in AA.

## Contributing

Bug reports and PRs welcome. Please open an issue first for anything
beyond trivial fixes so we can talk about it.

## License

MIT — see [LICENSE](LICENSE).
