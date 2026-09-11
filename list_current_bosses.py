#!/usr/bin/env python3
"""
Quick helper: prints every current raid boss name exactly as ScrapedDuck
returns it, grouped by tier. Run this once to see the exact spelling to
copy into config.json's watch_pokemon list (e.g. "Mega Venusaur" vs
"Venusaur").

Usage: python3 list_current_bosses.py
"""
import json
import urllib.request

RAIDS_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/raids.json"


def main():
    req = urllib.request.Request(RAIDS_URL, headers={"User-Agent": "raid-notifier/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raids = json.loads(resp.read().decode("utf-8"))

    by_tier = {}
    for boss in raids:
        by_tier.setdefault(boss.get("tier", "Unknown"), []).append(boss["name"])

    for tier, names in by_tier.items():
        print(f"\n=== {tier} ===")
        for name in names:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
