#!/usr/bin/env python3
"""
Pokemon GO Watcher -> Discord notifier (raids + events, one script)

Two independent checks against the community-run ScrapedDuck feeds
(scrapes LeekDuck.com, with permission - see
https://github.com/bigfoott/ScrapedDuck):

1. RAID BOSSES (raids.json): posts a message to DISCORD_WEBHOOK_URL only
   when a Pokemon from your watchlist (config.json / WATCH_POKEMON) is
   currently an active raid boss - any tier (Tier 1/3/5, Mega, Shadow).

2. EVENTS (events.json): posts a start/end message for events in the
   feed, EXCEPT eventType "raid-battles" and "raid-hours" (skipped
   entirely - those are covered by the raid boss check above, or are too
   frequent/local to be useful as calendar-style notices). Everything
   else is routed by eventType:
     - "max-mondays" / "max-battles"  -> DISCORD_WEBHOOK_URL_MAX
     - anything else                  -> DISCORD_WEBHOOK_URL_EVENTS

State is kept in two small files next to the script:
  - seen.json          - raid boss watchlist state
  - events_state.json  - event start/end transition state

Each section is independent: if one of the three webhook URLs isn't
configured, that section is skipped (with a warning) rather than
aborting the whole run.

Designed to run on a schedule (cron / Task Scheduler / GitHub Actions).

Credit: data comes from ScrapedDuck (https://github.com/bigfoott/ScrapedDuck),
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
SEEN_PATH = SCRIPT_DIR / "seen.json"
EVENTS_STATE_PATH = SCRIPT_DIR / "events_state.json"

RAIDS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/raids.json"
EVENTS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"

TIER_EMOJI = {
    "Tier 1": "⭐",
    "Tier 3": "⭐⭐⭐",
    "Tier 5": "⭐⭐⭐⭐⭐",
    "Mega": "💠 Mega",
    "Shadow": "🌑 Shadow",
}

# Event feed eventType values that should never be posted to any channel
# (raid-battles/raid-hours are already covered by the raid boss check).
SKIPPED_EVENT_TYPES = {"raid-battles", "raid-hours"}

# Event feed eventType values that go to the "Max" webhook instead of the
# general events one.
MAX_EVENT_TYPES = {"max-mondays", "max-battles"}


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


def normalize(name: str) -> str:
    return name.strip().lower()


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
# 1. Raid boss watchlist
# ---------------------------------------------------------------------------

def build_raid_embed(boss):
    tier = boss.get("tier", "")
    cp = boss.get("combatPower", {})
    normal = cp.get("normal", {})
    boosted = cp.get("boosted", {})
    types = ", ".join(t["name"].title() for t in boss.get("types", []))
    shiny = "✨ Can be shiny" if boss.get("canBeShiny") else "No shiny yet"

    fields = [
        {"name": "Tier", "value": TIER_EMOJI.get(tier, tier), "inline": True},
        {"name": "Type", "value": types or "Unknown", "inline": True},
        {"name": "Shiny", "value": shiny, "inline": True},
    ]
    if normal:
        fields.append({
            "name": "CP (normal / weather boosted)",
            "value": f"{normal.get('min','?')}–{normal.get('max','?')} / "
                     f"{boosted.get('min','?')}–{boosted.get('max','?')}",
            "inline": False,
        })

    return {
        "title": f"{boss['name']} is now in raids!",
        "color": 0xE74C3C,
        "fields": fields,
        "thumbnail": {"url": boss.get("image", "")},
        "footer": {"text": "Data: ScrapedDuck (LeekDuck.com)"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_raid_bosses(config, webhook_url):
    if not webhook_url:
        return

    env_watchlist = os.environ.get("WATCH_POKEMON")
    if env_watchlist:
        raw_list = [n for n in env_watchlist.split(",") if n.strip()]
    else:
        raw_list = config.get("watch_pokemon", [])
    watchlist = [normalize(n) for n in raw_list]

    if not watchlist:
        print("Raid bosses: watch_pokemon list is empty - nothing to check.")
        return

    notify_on_leave = os.environ.get("NOTIFY_WHEN_LEAVES", "").lower() in ("1", "true", "yes") \
        or config.get("notify_when_leaves", False)

    try:
        raids = fetch_json(RAIDS_URL, "raid-notifier/1.0")
    except Exception as e:
        print(f"Raid bosses: failed to fetch raid data: {e}")
        return

    current_names = {normalize(b["name"]) for b in raids}
    seen = set(load_json(SEEN_PATH, []))

    newly_active = [b for b in raids if normalize(b["name"]) in watchlist
                     and normalize(b["name"]) not in seen]

    for boss in newly_active:
        try:
            send_discord_message(
                webhook_url,
                content=f"🚨 **{boss['name']}** is in raids right now!",
                embeds=[build_raid_embed(boss)],
            )
            print(f"Raid bosses: notified {boss['name']}")
        except Exception as e:
            print(f"Raid bosses: failed to send message for {boss['name']}: {e}")

    if notify_on_leave:
        left = [n for n in watchlist if n in seen and n not in current_names]
        for name in left:
            try:
                send_discord_message(webhook_url, content=f"👋 **{name.title()}** has left raids.")
                print(f"Raid bosses: notified (left) {name}")
            except Exception as e:
                print(f"Raid bosses: failed to send leave notice for {name}: {e}")

    new_seen = {normalize(b["name"]) for b in raids if normalize(b["name"]) in watchlist}
    save_json(SEEN_PATH, sorted(new_seen))

    if not newly_active:
        print("Raid bosses: no new watched raid bosses this run.")


# ---------------------------------------------------------------------------
# 2. Events (everything except raid-battles / raid-hours)
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


def check_events(config, webhook_max, webhook_events):
    if not webhook_max and not webhook_events:
        return

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

    now = datetime.now(timezone.utc)
    state = load_json(EVENTS_STATE_PATH, {})
    notified_any = False
    current_ids = set()

    for event in events:
        event_type = event.get("eventType")
        if event_type in SKIPPED_EVENT_TYPES:
            continue

        webhook_url = webhook_max if event_type in MAX_EVENT_TYPES else webhook_events
        if not webhook_url:
            continue  # that destination isn't configured - skip quietly

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

    if not notified_any:
        print("Events: no start/end transitions this run.")


# ---------------------------------------------------------------------------

def main():
    config = load_json(CONFIG_PATH, {})

    webhook_raids = get_webhook("DISCORD_WEBHOOK_URL", "discord_webhook_url", config, "raid boss")
    webhook_max = get_webhook("DISCORD_WEBHOOK_URL_MAX", "discord_webhook_url_max", config, "Max Battles")
    webhook_events = get_webhook("DISCORD_WEBHOOK_URL_EVENTS", "discord_webhook_url_events", config, "general events")

    if not (webhook_raids or webhook_max or webhook_events):
        print("No valid webhook URLs configured at all (DISCORD_WEBHOOK_URL, "
              "DISCORD_WEBHOOK_URL_MAX, DISCORD_WEBHOOK_URL_EVENTS). Nothing to do.")
        sys.exit(1)

    check_raid_bosses(config, webhook_raids)
    check_events(config, webhook_max, webhook_events)


if __name__ == "__main__":
    main()
