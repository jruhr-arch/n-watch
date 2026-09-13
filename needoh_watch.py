#!/usr/bin/env python3
"""
NeeDoh stock watcher.

Checks Canadian Shopify storefronts for in-stock NeeDoh and pushes a
notification to your phone via ntfy.sh when something new appears.

Stores that aren't Shopify are reported in the log so you know to set those
up in Distill instead. They don't break anything - just delete their lines
from STORES once you've seen the verdict.

No pip installs needed - standard library only.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# STORES
#
# "collection": "needoh"  -> the shop has a /collections/needoh page
# "collection": None      -> scan the shop's whole catalogue instead
#
# Run this once and read the log. Any store marked NOT USABLE can be deleted
# from this list and set up in Distill instead.
# ---------------------------------------------------------------------------
STORES = [
    # --- confirmed working ---
    {"name": "Toytown Toronto",      "domain": "https://www.toytown.ca",        "collection": "needoh"},
    {"name": "Cherry Tree Lane",     "domain": "https://www.cherrytreelane.ca", "collection": "needoh"},
    {"name": "Kol Kid",              "domain": "https://kolkid.ca",             "collection": "needoh"},
    {"name": "Make Vancouver",       "domain": "https://www.makevancouver.com", "collection": "needoh"},
    {"name": "Scholar's Choice",     "domain": "https://scholarschoice.ca",     "collection": "needoh"},
    {"name": "Treasure Island Toys", "domain": "https://treasureislandtoys.ca", "collection": None},
    {"name": "Treehouse Toy Co",     "domain": "https://treehousetoyco.ca",     "collection": None},
    {"name": "Jack & Jill Montreal", "domain": "https://jackandjillmtl.com",    "collection": None},
    {"name": "Juxtapose Annex",      "domain": "https://juxtaposeannex.com",    "collection": None},

    # --- unverified: the log will tell you which of these work ---
    {"name": "Lemon & Lavender",     "domain": "https://shop.lemonlavender.com",     "collection": None},
    {"name": "Showcase",             "domain": "https://ca.shopatshowcase.com",      "collection": None},
    {"name": "Mood Toronto",         "domain": "https://moodtoronto.com",            "collection": None},
    {"name": "Caribou Gifts",        "domain": "https://caribougifts.ca",            "collection": None},
    {"name": "Avron",                "domain": "https://avron.ca",                   "collection": None},
    {"name": "Twisted Goods",        "domain": "https://twistedgoods.ca",            "collection": None},
    {"name": "Party Rock",           "domain": "https://partyrock.ca",               "collection": None},
    {"name": "Castle Toys",          "domain": "https://castletoys.ca",              "collection": None},
    {"name": "Education Station",    "domain": "https://educationstation.ca",        "collection": None},
    {"name": "Farmer's Daughter",    "domain": "https://farmerdaughtertoyshop.com",  "collection": None},
    {"name": "Bright Bean",          "domain": "https://brightbean.com",             "collection": None},
]

KEYWORDS = ("needoh", "nee doh", "nee-doh")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

TIMEOUT = 20
MAX_ALERTS_PER_RUN = 12  # so a big restock doesn't bury your phone


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def looks_like_needoh(product):
    tags = product.get("tags")
    blob = " ".join([
        product.get("title", ""),
        product.get("vendor", ""),
        product.get("product_type", ""),
        " ".join(tags) if isinstance(tags, list) else "",
    ]).lower()
    return any(k in blob for k in KEYWORDS)


def products_for_store(store):
    """Return (products, usable). usable=False means it isn't Shopify."""
    domain = store["domain"].rstrip("/")
    collection = store.get("collection")

    if collection:
        try:
            data = fetch_json(f"{domain}/collections/{collection}/products.json?limit=250")
            products = data.get("products", [])
            if products:
                return products, True
        except Exception:
            pass  # fall through to the catalogue scan

    found = []
    reached = False
    for page in range(1, 8):
        try:
            data = fetch_json(f"{domain}/products.json?limit=250&page={page}")
        except Exception as e:
            if not reached:
                return [], f"{type(e).__name__}"
            break
        if not isinstance(data, dict) or "products" not in data:
            return [], "not a Shopify store"
        reached = True
        batch = data["products"]
        if not batch:
            break
        found.extend([p for p in batch if looks_like_needoh(p)])
        time.sleep(0.4)  # be polite

    return found, True


def in_stock_items(store, max_price):
    domain = store["domain"].rstrip("/")
    products, usable = products_for_store(store)
    if usable is not True:
        return None, usable

    items = []
    for product in products:
        handle = product.get("handle", "")
        for variant in product.get("variants", []):
            if not variant.get("available"):
                continue
            try:
                price = float(variant.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if max_price is not None and price > max_price:
                continue

            vtitle = variant.get("title") or ""
            name = product.get("title", "Unknown")
            if vtitle and vtitle.lower() not in ("default title", "default"):
                name = f"{name} - {vtitle}"

            items.append({
                "key": f"{store['name']}|{product.get('id')}|{variant.get('id')}",
                "store": store["name"],
                "name": name,
                "price": price,
                "url": f"{domain}/products/{handle}?variant={variant.get('id')}",
            })
    return items, None


def ascii_safe(text):
    """ntfy headers must be latin-1 clean."""
    return text.encode("ascii", "ignore").decode("ascii").strip() or "NeeDoh"


def notify(topic, item):
    body = f"{item['name']}\n${item['price']:.2f} CAD at {item['store']}\n{item['url']}"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": ascii_safe(f"In stock: {item['store']}"),
            "Priority": "high",
            "Tags": "rotating_light",
            "Click": item["url"],
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"  notify failed: {e}")
        return False


def load_state(path):
    try:
        with open(path) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_state(path, keys):
    with open(path, "w") as f:
        json.dump(sorted(keys), f, indent=0)


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("ERROR: NTFY_TOPIC is not set. Check the repository secret.")
        return 1

    raw_cap = os.environ.get("MAX_PRICE", "").strip()
    max_price = float(raw_cap) if raw_cap else None
    state_file = os.environ.get("STATE_FILE", "state.json")

    previous = load_state(state_file)
    current = []
    unusable = []

    for store in STORES:
        try:
            found, problem = in_stock_items(store, max_price)
        except Exception as e:
            print(f"NOT USABLE  {store['name']:<22} ({type(e).__name__})")
            unusable.append(store["name"])
            continue

        if found is None:
            print(f"NOT USABLE  {store['name']:<22} ({problem})")
            unusable.append(store["name"])
            continue

        print(f"OK          {store['name']:<22} {len(found)} in stock")
        current.extend(found)

    current_keys = {i["key"] for i in current}
    new_items = [i for i in current if i["key"] not in previous]

    if new_items:
        print(f"\n{len(new_items)} newly in stock - sending alerts")
        for item in new_items[:MAX_ALERTS_PER_RUN]:
            print(f"  -> {item['store']}: {item['name']} ${item['price']:.2f}")
            notify(topic, item)
            time.sleep(0.5)
        if len(new_items) > MAX_ALERTS_PER_RUN:
            extra = len(new_items) - MAX_ALERTS_PER_RUN
            notify(topic, {
                "name": f"...and {extra} more items just went live",
                "price": 0.0,
                "store": "Multiple stores",
                "url": STORES[0]["domain"],
            })
    else:
        print("\nNothing new.")

    if unusable:
        print("\n" + "=" * 55)
        print("These stores aren't Shopify. Delete their lines from STORES")
        print("and set them up in Distill instead:")
        for name in unusable:
            print(f"  - {name}")

    save_state(state_file, current_keys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
