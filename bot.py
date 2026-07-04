"""
Mamba's Insider - Telegram auto-poster
Run on a schedule (see .github/workflows/post-updates.yml).
Reads config.json, state.json, calls.json.
Writes updated state.json / calls.json back to disk (workflow commits them).
"""

import os
import re
import json
import hashlib
import time
import requests
import feedparser

# ---------- Setup ----------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

with open(os.path.join(BASE_DIR, "config.json")) as f:
    CONFIG = json.load(f)

with open(os.path.join(BASE_DIR, "state.json")) as f:
    STATE = json.load(f)

with open(os.path.join(BASE_DIR, "calls.json")) as f:
    CALLS = json.load(f)

CHANNEL = CONFIG["telegram_channel"]


def save_json(filename, data):
    with open(os.path.join(BASE_DIR, filename), "w") as f:
        json.dump(data, f, indent=2)


# ---------- Telegram ----------

def post_to_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }, timeout=20)
    if not resp.ok:
        print(f"[telegram] failed: {resp.status_code} {resp.text}")
    else:
        print(f"[telegram] posted: {text[:60]}...")
    return resp.ok


def delete_webhook():
    """getUpdates (polling) doesn't work if a webhook is set. Safe to call every run."""
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] deleteWebhook failed (non-fatal): {e}")


# ---------- Claude caption rewriting ----------

def rewrite_caption(raw_text, context_hint=""):
    """Ask Claude to turn a raw headline/fact into a punchy channel post."""
    prompt = (
        "You write short, punchy Telegram posts for a crypto/politics/markets "
        "news channel called Mamba's Insider. Style: degen/memey but sharp — "
        "confident, a little unhinged, crypto-twitter slang is welcome (lmao, "
        "ripped, rug, ape, ngl, ate, ratio'd, ok but sparingly), 1-3 emoji max, "
        "2-4 sentences max, include the key fact/number. No hashtags. No 'NFA' "
        "or 'not financial advice' disclaimers. Do not invent facts not given below.\n\n"
        "When the raw info is news (not just a price update), add one short, clearly "
        "speculative line about how it could move a relevant asset or the market "
        "(e.g. 'could put pressure on X' or 'might send Y higher') — frame it as "
        "a take/vibe, not a certainty, using words like 'could', 'might', 'wouldn't "
        "be shocked if' rather than stating it as fact.\n\n"
        f"Context: {context_hint}\n\nRaw info:\n{raw_text}\n\n"
        "Write only the post text, nothing else."
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CONFIG.get("anthropic_model", "claude-haiku-4-5-20251001"),
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(
            block.get("text", "") for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
    except Exception as e:
        print(f"[claude] rewrite failed, falling back to raw text: {e}")
        return raw_text


# ---------- News (RSS) ----------

def run_news():
    posted_hashes = set(STATE.get("posted_news_hashes", []))
    keywords = [k.lower() for k in CONFIG["news_keywords"]]
    max_posts = CONFIG.get("max_news_posts_per_run", 5)
    posted_this_run = 0

    for feed_url in CONFIG["news_feeds"]:
        if posted_this_run >= max_posts:
            break
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[news] failed to parse {feed_url}: {e}")
            continue

        for entry in feed.entries[:15]:
            if posted_this_run >= max_posts:
                break

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            link = entry.get("link", "")
            haystack = f"{title} {summary}".lower()

            if not any(kw in haystack for kw in keywords):
                continue

            item_hash = hashlib.sha256(link.encode()).hexdigest()
            if item_hash in posted_hashes:
                continue

            caption = rewrite_caption(
                f"Headline: {title}\nSummary: {summary}\nSource link: {link}",
                context_hint="Breaking news post for the channel."
            )
            text = f"{caption}\n\n🔗 {link}"

            if post_to_telegram(text):
                posted_hashes.add(item_hash)
                posted_this_run += 1
                time.sleep(2)

    # Cap stored hash history so state.json doesn't grow forever
    STATE["posted_news_hashes"] = list(posted_hashes)[-2000:]


# ---------- Polymarket odds swings ----------

def run_polymarket():
    odds_state = STATE.get("polymarket_odds", {})
    threshold = CONFIG.get("polymarket_swing_threshold_pct", 8)
    max_posts = CONFIG.get("max_polymarket_posts_per_run", 5)
    posted_this_run = 0

    for term in CONFIG["polymarket_search_terms"]:
        if posted_this_run >= max_posts:
            break
        try:
            resp = requests.get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": "true", "closed": "false", "limit": 10, "search": term},
                timeout=20,
            )
            resp.raise_for_status()
            markets = resp.json()
        except Exception as e:
            print(f"[polymarket] search failed for '{term}': {e}")
            continue

        if not isinstance(markets, list):
            continue

        for market in markets:
            if posted_this_run >= max_posts:
                break
            market_id = str(market.get("id") or market.get("conditionId") or market.get("slug"))
            question = market.get("question") or market.get("slug", "Unknown market")

            try:
                prices = market.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                yes_price = float(prices[0]) if prices else None
            except Exception:
                yes_price = None

            if yes_price is None:
                continue

            yes_pct = round(yes_price * 100, 1)
            prev_pct = odds_state.get(market_id)

            if prev_pct is not None and abs(yes_pct - prev_pct) >= threshold:
                direction = "📈" if yes_pct > prev_pct else "📉"
                raw = (
                    f"Market: {question}\n"
                    f"Odds moved from {prev_pct}% to {yes_pct}% (Yes).\n"
                    f"Polymarket link: https://polymarket.com/event/{market.get('slug', '')}"
                )
                caption = rewrite_caption(raw, context_hint="Prediction market odds swing alert.")
                text = f"{direction} {caption}"
                if post_to_telegram(text):
                    posted_this_run += 1
                    time.sleep(2)

            odds_state[market_id] = yes_pct

    STATE["polymarket_odds"] = odds_state


# ---------- Solana CA detection + DexScreener price tracking ----------

SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
THRESHOLDS = [25, 50, 100, 200, 500, -25, -50, -75]


def get_dex_pair(address):
    """Look up a Solana token by contract address on DexScreener. Returns the
    highest-liquidity pair dict, or None if it's not a real/tracked token."""
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            timeout=20,
        )
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not pairs:
            return None
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
        return pairs[0]
    except Exception as e:
        print(f"[dexscreener] lookup failed for {address}: {e}")
        return None


def detect_new_calls_from_channel():
    """Poll getUpdates for new channel posts, pull out Solana CAs, and start
    tracking any that resolve to a real token on DexScreener."""
    channel_username = CHANNEL.lstrip("@").lower()
    last_update_id = STATE.get("last_update_id", 0)
    already_tracked = {c["address"] for c in CALLS if "address" in c}

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={
                "offset": last_update_id + 1,
                "timeout": 0,
                "allowed_updates": json.dumps(["channel_post"]),
            },
            timeout=20,
        )
        resp.raise_for_status()
        result = resp.json().get("result", [])
    except Exception as e:
        print(f"[calls] getUpdates failed: {e}")
        return

    if result:
        STATE["last_update_id"] = max(u["update_id"] for u in result)

    for update in result:
        post = update.get("channel_post")
        if not post:
            continue
        chat_username = (post.get("chat") or {}).get("username", "")
        if chat_username.lower() != channel_username:
            continue

        text = post.get("text") or post.get("caption") or ""
        candidates = set(SOLANA_ADDRESS_RE.findall(text))

        for address in candidates:
            if address in already_tracked:
                continue

            pair = get_dex_pair(address)
            if not pair:
                continue  # not a real/liquid token, skip silently

            symbol = (pair.get("baseToken") or {}).get("symbol", "???")
            price = float(pair.get("priceUsd") or 0)
            mcap = pair.get("marketCap") or pair.get("fdv")

            if price <= 0:
                continue

            CALLS.append({
                "address": address,
                "symbol": symbol,
                "call_price": price,
                "call_mcap": mcap,
                "call_date": time.strftime("%Y-%m-%d"),
                "active": True,
                "posted_thresholds": [],
            })
            already_tracked.add(address)

            raw = (
                f"New call detected: ${symbol}\n"
                f"Contract: {address}\n"
                f"Entry price: ${price}\n"
                f"Market cap at call: {mcap if mcap else 'unknown'}"
            )
            caption = rewrite_caption(raw, context_hint="Confirming a new token call just posted to the channel, tracking starts now.")
            post_to_telegram(f"📌 {caption}")
            time.sleep(2)


def run_calls():
    detect_new_calls_from_channel()

    for call in CALLS:
        if not call.get("active", True):
            continue

        address = call["address"]
        call_price = call["call_price"]
        posted = set(call.get("posted_thresholds", []))

        pair = get_dex_pair(address)
        if not pair:
            print(f"[calls] no pair data for {call['symbol']} ({address}), skipping this run")
            continue

        current_price = float(pair.get("priceUsd") or 0)
        current_mcap = pair.get("marketCap") or pair.get("fdv")
        if current_price <= 0:
            continue

        pct_change = ((current_price - call_price) / call_price) * 100

        for t in THRESHOLDS:
            already = t in posted
            hit = (t > 0 and pct_change >= t) or (t < 0 and pct_change <= t)
            if hit and not already:
                raw = (
                    f"Token: ${call['symbol']}\n"
                    f"Called on {call['call_date']} at ${call_price} "
                    f"(mcap at call: {call.get('call_mcap', 'unknown')}).\n"
                    f"Current price: ${current_price} (mcap: {current_mcap}), "
                    f"{pct_change:+.1f}% since call."
                )
                caption = rewrite_caption(raw, context_hint="Update on a token call made earlier by the channel.")
                emoji = "🚀" if t > 0 else "💀"
                text = f"{emoji} {caption}"
                if post_to_telegram(text):
                    posted.add(t)
                    time.sleep(2)

        call["posted_thresholds"] = list(posted)
        # Auto-retire calls that are down 90%+ so they don't post forever
        if pct_change <= -90:
            call["active"] = False


# ---------- Main ----------

if __name__ == "__main__":
    delete_webhook()
    run_news()
    run_polymarket()
    run_calls()

    save_json("state.json", STATE)
    save_json("calls.json", CALLS)
    print("Done.")
