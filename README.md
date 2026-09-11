# Pokémon GO Raid Watcher → Discord

Pings a Discord channel only when a Pokémon **you choose** shows up as a raid boss
(any tier: 1★, 3★, 5★, Mega, or Shadow — whatever's in the feed).

Data source: [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck), a community
project that scrapes LeekDuck.com *with permission* and republishes it as JSON.
It's free to use as long as you don't put it behind a paywall or monetize it with
ads, and you credit ScrapedDuck + LeekDuck.com (this script's Discord embeds
already do that in the footer).

## 1. Create a Discord webhook

1. In Discord, go to the channel you want alerts in → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**.
2. Name it (e.g. "Raid Alerts"), copy the **Webhook URL**.

## 2. Configure your watchlist

1. Copy `config.example.json` to `config.json`.
2. Paste your webhook URL into `discord_webhook_url`.
3. Add the Pokémon you want alerts for into `watch_pokemon`.
   - Run `python3 list_current_bosses.py` once to see the *exact* names the
     feed uses right now (e.g. it's `"Mega Venusaur"`, not `"Venusaur"` with a
     separate "mega" flag — the tier and name are combined for Megas).
   - Names not currently in raids won't show in that list — for those, just
     use the Pokémon's normal name (e.g. `"Zacian"`); it'll match once it
     rotates in.
4. Optional: set `"notify_when_leaves": true` if you also want a message when
   a watched Pokémon rotates *out* of raids.

## 3. Test it once

```bash
pip install --break-system-packages -r requirements.txt   # (stdlib only, but harmless if you keep this)
python3 watch_raids.py
```

You should see either "Notified: X" for anything on your list that's
currently active, or "No new watched raid bosses this run."

## 4. Schedule it

The script is stateless between runs except for `seen.json` (so you only get
notified once per rotation, not every 10 minutes). Pick whichever scheduler
fits how you already work:

### Option A — GitHub Actions (recommended: free, nothing to keep running)

1. Push this folder to a GitHub repo (public is fine now — no secrets are
   committed). **Don't commit `config.json`** for this option; the workflow
   reads settings from repo secrets/variables instead.
2. In the repo, go to **Settings → Secrets and variables → Actions**:
   - Add a **secret** named `DISCORD_WEBHOOK_URL` with your webhook URL.
   - Add a **variable** named `WATCH_POKEMON` with a comma-separated list,
     e.g. `Zacian,Mega Venusaur,Xerneas`.
3. The included `.github/workflows/raid-check.yml` runs every 15 minutes
   automatically and commits `seen.json` back so state persists between runs.
4. You can trigger it manually anytime from the repo's **Actions** tab
   (workflow_dispatch) to test it.

### Option B — Your own computer (cron / Task Scheduler)

- **Mac/Linux (cron)**: `crontab -e`, add:
  ```
  */15 * * * * cd /path/to/raid-notifier && /usr/bin/python3 watch_raids.py >> run.log 2>&1
  ```
- **Windows (Task Scheduler)**: create a task that runs
  `python watch_raids.py` every 15 minutes, with "Start in" set to this
  folder.

Your computer needs to be on for Option B to fire; Option A runs in the cloud
regardless.

## Files

| File | Purpose |
|---|---|
| `watch_raids.py` | Main script — checks feed, sends Discord alerts |
| `list_current_bosses.py` | One-off helper to see exact current boss names |
| `config.example.json` | Template — copy to `config.json` and fill in |
| `seen.json` | Auto-created; tracks what's already been notified |
| `.github/workflows/raid-check.yml` | GitHub Actions schedule (Option A) |

## Notes

- The feed only lists what's **currently** live in raids, not future
  rotations — so you'll get pinged when your Pokémon *actually* enters raids,
  not in advance. If you want advance warning too, I can also wire up a check
  against LeekDuck's events calendar (upcoming rotations) — just ask.
- This is about the **rotation schedule** (which Pokémon are raid bosses
  right now), not live per-gym raid alerts near you — that requires
  real-time scanner data, which isn't something I can build against, since it
  generally violates Niantic's terms of service.
