#!/usr/bin/env python3
"""
Pokemon GO Raid Watcher -> Discord notifier

Checks the community-run ScrapedDuck feed (scrapes LeekDuck.com, with
permission - see https://github.com/bigfoott/ScrapedDuck) for the current
raid boss list, and posts a Discord webhook message ONLY when one of the
Pokemon in your watchlist (config.json) is currently a raid boss.

Designed to be run on a schedule (cron / Task Scheduler / GitHub Actions).
It keeps a small "seen" cache (seen.json) next to the script so you don't
get re-notified every run for the same rotation - only when a watched
Pokemon newly appears (or reappears after being gone).

Credit: raid data comes from ScrapedDuck (https://github.com/bigfoott/ScrapedDuck),
which in turn credits LeekDuck.com. Please keep that attribution if you
share this script further.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SEEN_PATH = SCRIPT_DIR / "seen.json"

RAIDS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/raids.json"

TIER_EMOJI = {
    "Tier 1": "⭐",
    "Tier 3": "⭐⭐⭐",
    "Tier 5": "⭐⭐⭐⭐⭐",
    "Mega": "💠 Mega",
    "Shadow": "🌑 Shadow",
}


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


def fetch_raids():
    req = urllib.request.Request(RAIDS_URL, headers={"User-Agent": "raid-notifier/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize(name: str) -> str:
    return name.strip().lower()


def send_discord_message(webhook_url: str, content: str, embeds=None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "raid-notifier/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def build_embed(boss):
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


def main():
    # Env vars (e.g. GitHub Actions secrets) take priority over config.json,
    # so you never have to commit your webhook URL to the repo.
    config = load_json(CONFIG_PATH, {})

    webhook_url = (os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord_webhook_url") or "").strip()

    env_watchlist = os.environ.get("WATCH_POKEMON")
    if env_watchlist:
        raw_list = [n for n in env_watchlist.split(",") if n.strip()]
    else:
        raw_list = config.get("watch_pokemon", [])
    watchlist = [normalize(n) for n in raw_list]

    notify_on_leave = os.environ.get("NOTIFY_WHEN_LEAVES", "").lower() in ("1", "true", "yes") \
        or config.get("notify_when_leaves", False)

    if not config and not webhook_url:
        print(f"No config.json found and no DISCORD_WEBHOOK_URL env var set. "
              f"Copy config.example.json to config.json and edit it, or set env vars.")
        sys.exit(1)

    valid_domains = ("discord.com/api/webhooks", "discordapp.com/api/webhooks")
    if not webhook_url or not any(d in webhook_url for d in valid_domains) or "XXXXXXXXXXXX" in webhook_url:
        print("config.json is missing a valid discord_webhook_url.")
        print(f"  Got: {webhook_url!r}")
        print("  Expected something like: https://discord.com/api/webhooks/<id>/<token>")
        print("  Get one from: Discord channel -> Edit Channel -> Integrations -> Webhooks -> New Webhook")
        sys.exit(1)

    if not watchlist:
        print("Your watch_pokemon list in config.json is empty - nothing to check.")
        sys.exit(0)

    try:
        raids = fetch_raids()
    except Exception as e:
        print(f"Failed to fetch raid data: {e}")
        sys.exit(1)

    current_names = {normalize(b["name"]) for b in raids}
    seen = set(load_json(SEEN_PATH, []))

    # Newly-appeared watched bosses
    newly_active = [b for b in raids if normalize(b["name"]) in watchlist
                     and normalize(b["name"]) not in seen]

    for boss in newly_active:
        try:
            send_discord_message(
                webhook_url,
                content=f"🚨 **{boss['name']}** is in raids right now!",
                embeds=[build_embed(boss)],
            )
            print(f"Notified: {boss['name']}")
        except Exception as e:
            print(f"Failed to send Discord message for {boss['name']}: {e}")

    # Optionally notify when a watched Pokemon rotates OUT
    if notify_on_leave:
        left = [n for n in watchlist if n in seen and n not in current_names]
        for name in left:
            try:
                send_discord_message(webhook_url, content=f"👋 **{name.title()}** has left raids.")
                print(f"Notified (left): {name}")
            except Exception as e:
                print(f"Failed to send leave notice for {name}: {e}")

    # Update seen cache: keep anything currently active from the watchlist
    new_seen = {normalize(b["name"]) for b in raids if normalize(b["name"]) in watchlist}
    save_json(SEEN_PATH, sorted(new_seen))

    if not newly_active:
        print("No new watched raid bosses this run.")


if __name__ == "__main__":
    main()