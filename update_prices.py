#!/usr/bin/env python3
"""
Rebuild cards.json from cards.csv (a ManaBox export) with fresh Scryfall prices.

Run by GitHub Actions nightly, and whenever cards.csv changes.
"""

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

CSV_PATH = "cards.csv"
OUT_PATH = "cards.json"
API = "https://api.scryfall.com/cards/collection"
CHUNK = 75  # Scryfall's limit per collection request
HEADERS = {
    "User-Agent": "TheCommonsTCG-Binder/1.0 (github pages inventory)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def read_inventory(path):
    """Read the ManaBox export into a list of rows we care about."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    items = []
    for row in rows:
        scry_id = (row.get("Scryfall ID") or "").strip()
        if not scry_id:
            continue
        try:
            qty = int(row.get("Quantity") or 1)
        except ValueError:
            qty = 1
        items.append(
            {
                "id": scry_id,
                "q": qty,
                "f": (row.get("Foil") or "normal").strip() != "normal",
            }
        )
    return items


def fetch_prices(ids):
    """Ask Scryfall about every card, 75 at a time. Returns {id: card}."""
    found = {}
    for start in range(0, len(ids), CHUNK):
        batch = ids[start : start + CHUNK]
        payload = json.dumps(
            {"identifiers": [{"id": card_id} for card_id in batch]}
        ).encode("utf-8")
        request = urllib.request.Request(API, data=payload, headers=HEADERS)

        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except Exception as err:  # noqa: BLE001 - retry anything transient
                if attempt == 2:
                    print(f"  batch {start} failed: {err}", file=sys.stderr)
                    body = {"data": []}
                else:
                    time.sleep(2 * (attempt + 1))

        for card in body.get("data", []):
            found[card["id"]] = card

        print(f"  priced {min(start + CHUNK, len(ids))} of {len(ids)}")
        time.sleep(0.12)  # Scryfall asks for a short pause between calls

    return found


def build(items, cards):
    """Merge inventory quantities with Scryfall data."""
    out = []
    missing = 0

    for index, item in enumerate(items):
        card = cards.get(item["id"])
        if not card:
            missing += 1
            continue

        prices = card.get("prices") or {}
        raw = prices.get("usd_foil") if item["f"] else prices.get("usd")
        try:
            price = float(raw) if raw else None
        except (TypeError, ValueError):
            price = None

        out.append(
            {
                "i": index,
                "n": card.get("name", ""),
                "s": (card.get("set") or "").upper(),
                "sn": card.get("set_name", ""),
                "c": card.get("collector_number", ""),
                "f": item["f"],
                "r": card.get("rarity", "common"),
                "q": item["q"],
                "p": price,
                "id": item["id"],
            }
        )

    if missing:
        print(f"  {missing} cards not found on Scryfall", file=sys.stderr)
    return out


def main():
    if not os.path.exists(CSV_PATH):
        print(f"{CSV_PATH} not found", file=sys.stderr)
        return 1

    items = read_inventory(CSV_PATH)
    print(f"Read {len(items)} lines from {CSV_PATH}")

    cards = fetch_prices([item["id"] for item in items])
    built = build(items, cards)

    # Never overwrite a good file with a broken run. If Scryfall was
    # unreachable, most lookups fail at once — leave the old prices alone.
    if items and len(built) < 0.5 * len(items):
        print(
            f"Only {len(built)} of {len(items)} cards came back. "
            "Leaving cards.json as it is.",
            file=sys.stderr,
        )
        return 1

    stamp = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(
            {"updated": stamp, "cards": built},
            fh,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    total = sum((card["p"] or 0) * card["q"] for card in built)
    held = sum(card["q"] for card in built)
    print(f"Wrote {len(built)} lines, {held} cards, ${total:,.2f} at market")
    return 0


if __name__ == "__main__":
    sys.exit(main())
