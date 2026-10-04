#!/usr/bin/env python3
"""
Pokemon GO Watcher -> Discord notifier (events only)

Checks the community-run ScrapedDuck events feed (scrapes LeekDuck.com, with
permission - see https://github.com/bigfoott/ScrapedDuck) and posts a
start/end message to Discord for every event type you are subscribed to.

Everything configurable lives in config.json next to this script:

    event_types                  {"<eventType>": true/false, ...} subscriptions
    notify_unknown_event_types   what to do with types not listed above
    keyword_override_types       disabled types that may still let events through...
    keep_name_keywords           ...when the event name contains one of these
    event_timezone               IANA tz for events without a "Z" timestamp
    discord_webhook_url_events   the single Discord webhook all events are sent to

The webhook URL and the timezone can also be set via the environment variables
DISCORD_WEBHOOK_URL_EVENTS and EVENT_TIMEZONE (these take priority over
config.json). All subscribed events, whatever their type, go to that one webhook.

State is kept next to the script in events_state.json (start/end transitions).

Designed to run on a schedule (cron / Task Scheduler / GitHub Actions).

Credit: data comes from ScrapedDuck (https://github.com/bigfoott/ScrapedDuck),
which in turn credits LeekDuck.com. Please keep that attribution if you
share this script further.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - very old Python
    ZoneInfo = None

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
EVENTS_STATE_PATH = SCRIPT_DIR / "events_state.json"

EVENTS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"WARNING: {path} has a JSON syntax error and could not be read: {e}")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fetch_json(url: str, user_agent: str):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_discord_message(webhook_url: str, content: str, embeds=None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "pogo-notifier/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def get_webhook(env_name, config_key, config, label):
    """Read a webhook URL from env (priority) or config.json, and validate it.

    Returns "" (and prints a warning) if missing or malformed, so callers
    can skip that section instead of crashing the whole run.
    """
    url = (os.environ.get(env_name) or config.get(config_key) or "").strip()
    valid_domains = ("discord.com/api/webhooks", "discordapp.com/api/webhooks")
    if not url or not any(d in url for d in valid_domains) or "XXXXXXXXXXXX" in url:
        print(f"NOTE: no valid {label} webhook configured "
              f"(set {env_name} or {config_key!r} in config.json) - skipping {label}.")
        return ""
    return url


# ---------------------------------------------------------------------------
# Settings (everything comes from config.json)
# ---------------------------------------------------------------------------

def load_settings(config):
    event_types = {str(k): bool(v) for k, v in (config.get("event_types") or {}).items()}
    if not event_types:
        print("WARNING: config.json has no 'event_types' - only "
              "'notify_unknown_event_types' will decide what gets sent.")

    return {
        "event_types": event_types,
        "notify_unknown": bool(config.get("notify_unknown_event_types", True)),
        "override_types": set(config.get("keyword_override_types") or []),
        "keywords": tuple(str(k).lower() for k in (config.get("keep_name_keywords") or [])),
    }


def should_notify(event, settings, unknown_seen):
    etype = event.get("eventType")
    subs = settings["event_types"]

    if etype not in subs:
        unknown_seen.add(etype)
        return settings["notify_unknown"]

    if subs[etype]:
        return True

    # Type is switched off - still allow events whose name matches a keyword
    # (e.g. Gigantamax/Dynamax days and hours), for the configured types only.
    if etype in settings["override_types"]:
        name = (event.get("name") or "").lower()
        return any(k in name for k in settings["keywords"])

    return False


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def parse_dt(value, local_tz):
    """Parse an ISO 8601 timestamp from ScrapedDuck.

    Per the ScrapedDuck docs: if the string ends with "Z" it's UTC (global
    event). Otherwise it's a naive local timestamp - we attach local_tz to
    it if one was configured, else we assume UTC.
    """
    if not value:
        return None
    if value.endswith("Z"):
        return datetime.fromisoformat(value[:-1] + "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz or timezone.utc)
    return dt


def get_status(event, now, local_tz):
    start = parse_dt(event.get("start"), local_tz)
    end = parse_dt(event.get("end"), local_tz)
    if end is not None and now >= end:
        return "ended"
    if start is not None and now >= start:
        return "active"
    return "upcoming"


def boss_names(event):
    extra = event.get("extraData") or {}
    raidbattles = extra.get("raidbattles") or {}
    bosses = raidbattles.get("bosses") or []
    return [b["name"] for b in bosses if "name" in b]


def build_event_embed(event, kind):
    bosses = boss_names(event)
    fields = [{"name": "Type", "value": event.get("heading") or event.get("eventType", ""), "inline": True}]
    if bosses:
        fields.append({"name": "Bosses", "value": ", ".join(bosses), "inline": False})
    started = kind == "started"
    return {
        "title": f"{event['name']} has {'started' if started else 'ended'}!",
        "url": event.get("link", ""),
        "color": 0x2ECC71 if started else 0x95A5A6,
        "fields": fields,
        "thumbnail": {"url": event.get("image", "")},
        "footer": {"text": "Data: ScrapedDuck (LeekDuck.com)"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_events(config, webhook_url):
    if not webhook_url:
        return

    settings = load_settings(config)

    tz_name = os.environ.get("EVENT_TIMEZONE") or config.get("event_timezone")
    local_tz = None
    if tz_name and ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception as e:
            print(f"Events: could not load timezone {tz_name!r}: {e}. Falling back to UTC.")

    try:
        events = fetch_json(EVENTS_URL, "pogo-notifier/1.0")
    except Exception as e:
        print(f"Events: failed to fetch event data: {e}")
        return

    unknown_seen = set()
    now = datetime.now(timezone.utc)
    state = load_json(EVENTS_STATE_PATH, {})
    notified_any = False
    current_ids = set()

    for event in events:
        if not should_notify(event, settings, unknown_seen):
            continue

        event_id = event.get("eventID")
        if not event_id:
            continue
        current_ids.add(event_id)

        status = get_status(event, now, local_tz)
        prev = state.get(event_id, {})
        notified_start = prev.get("notified_start", False)
        notified_end = prev.get("notified_end", False)

        if status in ("active", "ended") and not notified_start:
            try:
                send_discord_message(
                    webhook_url,
                    content=f"🟢 **{event['name']}** has started!",
                    embeds=[build_event_embed(event, "started")],
                )
                print(f"Events: notified (started) {event['name']}")
                notified_any = True
            except Exception as e:
                print(f"Events: failed to send start notice for {event['name']}: {e}")
            notified_start = True

        if status == "ended" and not notified_end:
            try:
                send_discord_message(
                    webhook_url,
                    content=f"🔴 **{event['name']}** has ended.",
                    embeds=[build_event_embed(event, "ended")],
                )
                print(f"Events: notified (ended) {event['name']}")
                notified_any = True
            except Exception as e:
                print(f"Events: failed to send end notice for {event['name']}: {e}")
            notified_end = True

        state[event_id] = {
            "name": event.get("name"),
            "notified_start": notified_start,
            "notified_end": notified_end,
        }

    # Drop events that are both finished and no longer present in the feed,
    # so the state file doesn't grow forever.
    for eid in [eid for eid, s in state.items() if eid not in current_ids and s.get("notified_end")]:
        del state[eid]

    save_json(EVENTS_STATE_PATH, state)

    if unknown_seen:
        print(f"Events: unlisted event types seen: {sorted(t for t in unknown_seen if t)}. "
              f"Add them to 'event_types' in config.json to control them.")

    if not notified_any:
        print("Events: no start/end transitions this run.")


# ---------------------------------------------------------------------------

def main():
    config = load_json(CONFIG_PATH, {})

    webhook_url = get_webhook("DISCORD_WEBHOOK_URL_EVENTS", "discord_webhook_url_events", config, "events")

    if not webhook_url:
        print("No valid webhook URL configured (set DISCORD_WEBHOOK_URL_EVENTS or "
              "'discord_webhook_url_events' in config.json). Nothing to do.")
        sys.exit(1)

    check_events(config, webhook_url)


if __name__ == "__main__":
    main()
