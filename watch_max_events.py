#!/usr/bin/env python3
"""
Pokemon GO Events Watcher -> Discord notifier

Checks the community-run ScrapedDuck feed (scrapes LeekDuck.com, with
permission - see https://github.com/bigfoott/ScrapedDuck) for ALL events,
and posts a Discord webhook message when one of them STARTS, and again
when it ENDS.

Designed to be run on a schedule (cron / Task Scheduler / GitHub Actions),
once a day. It keeps a small state file (max_events_state.json) next to
the script so you only get notified on the actual start/end transition,
not on every run.

Credit: event data comes from ScrapedDuck (https://github.com/bigfoott/ScrapedDuck),
which in turn credits LeekDuck.com. Please keep that attribution if you
share this script further.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - very old Python
    ZoneInfo = None

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "max_events_state.json"
EVENTS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"


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


def fetch_events():
    req = urllib.request.Request(EVENTS_URL, headers={"User-Agent": "max-events-notifier/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def send_discord_message(webhook_url, content, embeds=None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "max-events-notifier/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def boss_names(event):
    extra = event.get("extraData") or {}
    raidbattles = extra.get("raidbattles") or {}
    bosses = raidbattles.get("bosses") or []
    return [b["name"] for b in bosses if "name" in b]


def build_embed(event, kind):
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


def main():
    config = load_json(CONFIG_PATH, {})
    webhook_url = (os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord_webhook_url") or "").strip()

    # Optional IANA timezone name (e.g. "Europe/Amsterdam") for events whose
    # start/end have no "Z" suffix, i.e. are given in local game time.
    tz_name = os.environ.get("EVENT_TIMEZONE") or config.get("event_timezone")
    local_tz = None
    if tz_name and ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception as e:
            print(f"WARNING: could not load timezone {tz_name!r}: {e}. Falling back to UTC.")

    valid_domains = ("discord.com/api/webhooks", "discordapp.com/api/webhooks")
    if not webhook_url or not any(d in webhook_url for d in valid_domains) or "XXXXXXXXXXXX" in webhook_url:
        print("config.json is missing a valid discord_webhook_url.")
        print(f"  Got: {webhook_url!r}")
        print("  Expected something like: https://discord.com/api/webhooks/<id>/<token>")
        print("  Get one from: Discord channel -> Edit Channel -> Integrations -> Webhooks -> New Webhook")
        sys.exit(1)

    try:
        events = fetch_events()
    except Exception as e:
        print(f"Failed to fetch event data: {e}")
        sys.exit(1)

    # Watch every event in the feed, regardless of eventType.
    watched = events

    now = datetime.now(timezone.utc)
    state = load_json(STATE_PATH, {})
    notified_any = False
    current_ids = set()

    for event in watched:
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
                    embeds=[build_embed(event, "started")],
                )
                print(f"Notified (started): {event['name']}")
                notified_any = True
            except Exception as e:
                print(f"Failed to send start notice for {event['name']}: {e}")
            notified_start = True

        if status == "ended" and not notified_end:
            try:
                send_discord_message(
                    webhook_url,
                    content=f"🔴 **{event['name']}** has ended.",
                    embeds=[build_embed(event, "ended")],
                )
                print(f"Notified (ended): {event['name']}")
                notified_any = True
            except Exception as e:
                print(f"Failed to send end notice for {event['name']}: {e}")
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

    save_json(STATE_PATH, state)

    if not notified_any:
        print("No event start/end transitions this run.")


if __name__ == "__main__":
    main()
