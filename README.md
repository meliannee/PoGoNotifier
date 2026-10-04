# PoGoNotifier (events only)
Please note that this README was automatically written by AI. For any misinformation please inform me using github's Issues page.

Posts a message to Discord when a Pokémon GO event **starts** and when it **ends**.
You choose which event types you care about in `config.json`.

This branch (`feature/only_notify_events`) only deals with events. There is no
raid boss watchlist: regular raids and regular Max Battle listings are off by
default, while raid hours and Gigantamax/Dynamax days and hours still come through.

Data comes from the community-run [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck)
feed, which scrapes [LeekDuck.com](https://leekduck.com) with permission.
Please keep that attribution if you share this project.

## Files

| File | Purpose |
|------|---------|
| `watch_events.py` | The watcher. Fetches the events feed and posts to Discord. |
| `config.json` | All settings: subscriptions, routing, timezone, webhook URLs. |
| `events_state.json` | Remembers which start/end messages were already sent. Updated automatically. |
| `.github/workflows/event-check.yml` | Runs the watcher every 15 minutes on GitHub Actions. |

## Setup

1. **Create Discord webhooks.** In your Discord server: channel settings →
   Integrations → Webhooks → New Webhook → copy the URL. You can use one, two or
   three channels (see *Routing* below).
2. **Add the webhook URLs as repository secrets** (Settings → Secrets and
   variables → Actions → New repository secret):
   - `DISCORD_WEBHOOK_URL` (raid hours / raid days)
   - `DISCORD_WEBHOOK_URL_MAX` (Max Mondays / Max Battles)
   - `DISCORD_WEBHOOK_URL_EVENTS` (all other events)

   Any of them can be left out; events routed to a missing webhook are skipped.
   Don't put real webhook URLs in `config.json` if the repository is public.
3. **Optional:** add a repository variable `EVENT_TIMEZONE` (for example
   `Europe/Amsterdam`). Some events have local start/end times without a UTC
   marker; this tells the script which timezone they are in. Without it, UTC is assumed.
4. **Edit `config.json`** to pick your event types (see below).
5. **Enable Actions** for the repository. The workflow runs every 15 minutes and
   can also be started by hand from the Actions tab ("Run workflow").

> GitHub only runs scheduled workflows from the repository's **default branch**.
> On this feature branch, start the workflow manually until you merge it.
> The workflow needs write access to commit `events_state.json`
> (Settings → Actions → General → Workflow permissions, if your repo restricts it).

## Configuration (`config.json`)

| Key | Meaning |
|-----|---------|
| `event_types` | `{"<eventType>": true/false}`. `true` = notify, `false` = skip. |
| `notify_unknown_event_types` | What to do with an event type that is not listed in `event_types`. `false` = ignore it, `true` = notify. The run output lists any unlisted types it saw. |
| `keyword_override_types` | Event types that are switched off but may still let events through (default: `raid-battles`, `max-battles`). |
| `keep_name_keywords` | If a type from `keyword_override_types` is `false`, events whose name contains one of these words are still sent (default: gigantamax, dynamax, max battle day, max monday). |
| `raid_event_types` | Event types sent to the raid webhook. |
| `max_event_types` | Event types sent to the Max webhook. |
| `event_timezone` | IANA timezone name, e.g. `Europe/Amsterdam`. Empty = UTC. |
| `discord_webhook_url`, `discord_webhook_url_max`, `discord_webhook_url_events` | Webhook URLs. Environment variables / secrets take priority. |

Example: only get community days and raid hours, and ignore everything else:

```json
{
  "event_types": {
    "community-day": true,
    "raid-hour": true
  },
  "notify_unknown_event_types": false
}
```

### Routing

| eventType in… | Goes to |
|---------------|---------|
| `raid_event_types` | `DISCORD_WEBHOOK_URL` |
| `max_event_types` | `DISCORD_WEBHOOK_URL_MAX` |
| anything else | `DISCORD_WEBHOOK_URL_EVENTS` |

### Event type names

The names in `event_types` have to match the `eventType` values in the feed.
The list in `config.json` is a best guess, so check it against the live feed:

```python
import json, urllib.request
req = urllib.request.Request(
    "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json",
    headers={"User-Agent": "pogo-notifier/1.0"})
events = json.load(urllib.request.urlopen(req))
print(sorted({e["eventType"] for e in events}))
```

## Running locally

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python watch_events.py
```

Run it on a schedule with cron or Task Scheduler if you don't use GitHub Actions.
Python 3.9+ is required, and there are no third-party dependencies.

## How it works

On every run the script fetches the feed, skips event types you have not
subscribed to, and compares each remaining event with `events_state.json`.
It sends a "started" message once an event is active and an "ended" message once
it is over, then records that in the state file so nothing is sent twice.
Finished events that have dropped out of the feed are removed from the state file.

## Credit

Event data: [ScrapedDuck](https://github.com/bigfoott/ScrapedDuck) by bigfoott,
sourced from [LeekDuck.com](https://leekduck.com).
