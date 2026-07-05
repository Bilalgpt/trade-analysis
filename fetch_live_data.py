"""
SOL/USDT Live Data Fetcher — Binance API
==========================================
Fetches live price + candle data and saves as CSV.
No API key needed. Run anytime to get fresh data.

Usage:
    python fetch_live_data.py
"""

import urllib.request
import json
import csv
import os
from datetime import datetime, timezone

SYMBOL   = "SOLUSDT"
BASE_URL = "https://api.binance.com/api/v3"
OUT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live")

# Timeframes to fetch: (interval_name, binance_interval, candle_count)
TIMEFRAMES = [
    ("1D",  "1d",  365),   # 1 year of daily candles
    ("4H",  "4h",  500),   # ~83 days of 4H candles
    ("1H",  "1h",  500),   # ~20 days of 1H candles
    ("15M", "15m", 500),   # ~5 days of 15M candles
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())


def get_live_price():
    data = fetch(f"{BASE_URL}/ticker/price?symbol={SYMBOL}")
    return float(data["price"])


def get_24h_stats():
    data = fetch(f"{BASE_URL}/ticker/24hr?symbol={SYMBOL}")
    return {
        "price":         float(data["lastPrice"]),
        "change_pct":    float(data["priceChangePercent"]),
        "high_24h":      float(data["highPrice"]),
        "low_24h":       float(data["lowPrice"]),
        "volume_24h":    float(data["volume"]),
    }


def get_candles(interval, limit=500):
    url = f"{BASE_URL}/klines?symbol={SYMBOL}&interval={interval}&limit={limit}"
    raw = fetch(url)
    candles = []
    for c in raw:
        candles.append({
            "time":   int(c[0]) // 1000,   # convert ms to seconds
            "open":   float(c[1]),
            "high":   float(c[2]),
            "low":    float(c[3]),
            "close":  float(c[4]),
            "volume": float(c[5]),
        })
    return candles


def save_csv(candles, filepath):
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time","open","high","low","close","volume"])
        writer.writeheader()
        writer.writerows(candles)


def find_equal_highs_lows(candles, tolerance=0.002):
    """Find equal highs and equal lows (within 0.2% of each other)."""
    highs = [(c["time"], c["high"]) for c in candles]
    lows  = [(c["time"], c["low"])  for c in candles]

    equal_highs = []
    equal_lows  = []

    # Check last 50 candles for recent equal levels
    recent = candles[-50:]
    recent_highs = [c["high"] for c in recent]
    recent_lows  = [c["low"]  for c in recent]

    for i, h1 in enumerate(recent_highs):
        for j, h2 in enumerate(recent_highs):
            if i >= j:
                continue
            if abs(h1 - h2) / h1 <= tolerance:
                equal_highs.append(round((h1 + h2) / 2, 4))

    for i, l1 in enumerate(recent_lows):
        for j, l2 in enumerate(recent_lows):
            if i >= j:
                continue
            if abs(l1 - l2) / l1 <= tolerance:
                equal_lows.append(round((l1 + l2) / 2, 4))

    # Deduplicate
    equal_highs = sorted(set([round(h, 1) for h in equal_highs]), reverse=True)
    equal_lows  = sorted(set([round(l, 1) for l in equal_lows]),  reverse=True)

    return equal_highs[:5], equal_lows[:5]


def find_swing_levels(candles, lookback=5):
    """Find recent swing highs and lows."""
    swings_high = []
    swings_low  = []
    data = candles[-100:]

    for i in range(lookback, len(data) - lookback):
        window_h = [data[i-lookback+k]["high"] for k in range(lookback*2+1) if i-lookback+k != i]
        window_l = [data[i-lookback+k]["low"]  for k in range(lookback*2+1) if i-lookback+k != i]

        if data[i]["high"] > max(window_h):
            swings_high.append(data[i]["high"])
        if data[i]["low"] < min(window_l):
            swings_low.append(data[i]["low"])

    return sorted(swings_high, reverse=True)[:5], sorted(swings_low, reverse=True)[:5]


def smc_analysis(candles, label):
    """Basic SMC analysis on candle data."""
    price     = candles[-1]["close"]
    last_high = max(c["high"] for c in candles[-20:])
    last_low  = min(c["low"]  for c in candles[-20:])
    eq_highs, eq_lows = find_equal_highs_lows(candles)
    sw_highs, sw_lows = find_swing_levels(candles)

    # Premium / Discount
    full_range = last_high - last_low
    eq_point   = last_low + full_range / 2
    zone       = "PREMIUM (look for sells)" if price > eq_point else "DISCOUNT (look for buys)"

    # Nearest levels
    bsl = [h for h in sw_highs if h > price]
    ssl = [l for l in sw_lows  if l < price]

    lines = [
        f"\n{'='*50}",
        f"  {label} ANALYSIS",
        f"{'='*50}",
        f"  Current close:    ${price:.2f}",
        f"  20-candle high:   ${last_high:.2f}",
        f"  20-candle low:    ${last_low:.2f}",
        f"  Equilibrium:      ${eq_point:.2f}",
        f"  Zone:             {zone}",
        f"\n  BUY-SIDE LIQUIDITY (BSL) above price:",
    ]
    for b in bsl[:3]:
        lines.append(f"    ${b:.2f}")
    lines.append(f"\n  SELL-SIDE LIQUIDITY (SSL) below price:")
    for s in ssl[:3]:
        lines.append(f"    ${s:.2f}")
    if eq_highs:
        lines.append(f"\n  EQUAL HIGHS (BSL pools): {[f'${x}' for x in eq_highs[:3]]}")
    if eq_lows:
        lines.append(f"  EQUAL LOWS  (SSL pools): {[f'${x}' for x in eq_lows[:3]]}")

    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*50}")
    print(f"  SOL/USDT LIVE DATA FETCHER")
    print(f"  {now}")
    print(f"{'='*50}")

    # Live price + 24h stats
    stats = get_24h_stats()
    print(f"\n  LIVE PRICE:   ${stats['price']:.2f}")
    print(f"  24h Change:   {stats['change_pct']:+.2f}%")
    print(f"  24h High:     ${stats['high_24h']:.2f}")
    print(f"  24h Low:      ${stats['low_24h']:.2f}")
    print(f"  24h Volume:   {stats['volume_24h']:,.0f} SOL")

    # Fetch + save each timeframe
    all_candles = {}
    for (label, interval, limit) in TIMEFRAMES:
        print(f"\n  Fetching {label} candles...", end=" ")
        candles = get_candles(interval, limit)
        all_candles[label] = candles

        filename = f"LIVE_SOLUSDT_{label}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        filepath = os.path.join(OUT_DIR, filename)
        save_csv(candles, filepath)
        print(f"saved {len(candles)} candles -> {filename}")

    # SMC Analysis
    print("\n" + "="*50)
    print("  SMC ANALYSIS — LIVE DATA")
    print("="*50)
    for label in ["1D", "4H", "1H"]:
        print(smc_analysis(all_candles[label], label))

    # Trade watch
    price = stats["price"]
    print(f"\n{'='*50}")
    print(f"  CURRENT TRADE WATCH")
    print(f"{'='*50}")

    h1 = all_candles["1H"]
    recent_lows = sorted([c["low"] for c in h1[-30:]])
    recent_highs = sorted([c["high"] for c in h1[-30:]], reverse=True)
    nearest_ssl = recent_lows[0]
    nearest_bsl = recent_highs[0]

    print(f"  Price now:       ${price:.2f}")
    print(f"  Nearest SSL:     ${nearest_ssl:.2f}  <- watch for sweep here")
    print(f"  Nearest BSL:     ${nearest_bsl:.2f}  <- watch for sweep here")

    if price > nearest_ssl + 1:
        print(f"\n  SETUP: Price is ${price - nearest_ssl:.2f} above SSL at ${nearest_ssl:.2f}")
        print(f"  Wait for sweep below ${nearest_ssl:.2f} -> CHoCH on 1H -> LONG")
    if price < nearest_bsl - 1:
        print(f"\n  SETUP: Price is ${nearest_bsl - price:.2f} below BSL at ${nearest_bsl:.2f}")
        print(f"  If price sweeps ${nearest_bsl:.2f} -> CHoCH bearish on 1H -> SHORT")

    print(f"\n  Run this script again anytime for fresh data.")
    print(f"  CSVs saved to: {OUT_DIR}\n")


if __name__ == "__main__":
    main()
