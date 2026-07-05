"""
SOL/USDT Comprehensive Hourly Email Alert
==========================================
Timeframes: Weekly, Daily, 4H, 1H, 15M
Sends full SMC analysis with trade plan every hour.
"""

import urllib.request
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
import os
EMAIL_FROM     = os.environ.get("EMAIL_FROM",     "muhammadbilalafzal1@gmail.com")
EMAIL_TO       = os.environ.get("EMAIL_TO",       "muhammadbilalafzal1@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "msdimmmadnsoraqy")
# Auto-detect environment:
# On GitHub Actions (US servers) -> Binance is blocked -> use Kraken
# Locally (Pakistan) -> Kraken is blocked -> use Binance
IS_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
# ──────────────────────────────────────────────────────────


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


# ── BINANCE (local) ──────────────────────────────────────
def get_stats_binance():
    url  = "https://api.binance.com/api/v3/ticker/24hr?symbol=SOLUSDT"
    d    = fetch(url)
    return {
        "lastPrice":          d["lastPrice"],
        "priceChangePercent": d["priceChangePercent"],
        "highPrice":          d["highPrice"],
        "lowPrice":           d["lowPrice"],
        "volume":             d["volume"],
    }

def get_candles_binance(interval, limit=200):
    # intervals: 15m, 1h, 4h, 1d, 1w
    url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval={interval}&limit={limit}"
    raw = fetch(url)
    return [{"time": int(c[0])//1000, "open": float(c[1]),
             "high": float(c[2]), "low": float(c[3]),
             "close": float(c[4]), "volume": float(c[5])} for c in raw]


# ── KRAKEN (GitHub Actions) ──────────────────────────────
def get_stats_kraken():
    url  = "https://api.kraken.com/0/public/Ticker?pair=SOLUSD"
    data = fetch(url)["result"]
    key  = [k for k in data if k != "last"][0]
    t    = data[key]
    price  = float(t["c"][0])
    open24 = float(t["o"])
    chg    = ((price - open24) / open24 * 100) if open24 else 0
    return {
        "lastPrice":          str(round(price, 4)),
        "priceChangePercent": str(round(chg, 2)),
        "highPrice":          str(t["h"][1]),
        "lowPrice":           str(t["l"][1]),
        "volume":             str(t["v"][1]),
    }

def get_candles_kraken(interval, limit=200):
    # intervals (minutes): 15, 60, 240, 1440, 10080
    url    = f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval={interval}"
    result = fetch(url)["result"]
    key    = [k for k in result if k != "last"][0]
    raw    = result[key][-limit:]
    return [{"time": int(c[0]), "open": float(c[1]),
             "high": float(c[2]), "low": float(c[3]),
             "close": float(c[4]), "volume": float(c[6])} for c in raw]


# ── UNIFIED API (auto-selects) ───────────────────────────
def get_stats():
    if IS_GITHUB:
        return get_stats_kraken()
    return get_stats_binance()

def get_candles(tf, limit=200):
    """
    tf = timeframe string used by both APIs:
      "W" -> weekly, "D" -> daily, "4H" -> 4 hour, "1H" -> 1 hour, "15M" -> 15 min
    """
    if IS_GITHUB:
        mapping = {"W": 10080, "D": 1440, "4H": 240, "1H": 60, "15M": 15}
        return get_candles_kraken(mapping[tf], limit)
    else:
        mapping = {"W": "1w", "D": "1d", "4H": "4h", "1H": "1h", "15M": "15m"}
        return get_candles_binance(mapping[tf], limit)


# ─── SMC TOOLS ────────────────────────────────────────────

def find_swings(candles, lookback=5):
    data = candles[-100:]
    sh, sl = [], []
    for i in range(lookback, len(data) - lookback):
        win_h = [data[i-lookback+k]["high"] for k in range(lookback*2+1) if k != lookback]
        win_l = [data[i-lookback+k]["low"]  for k in range(lookback*2+1) if k != lookback]
        if data[i]["high"] >= max(win_h): sh.append(round(data[i]["high"], 4))
        if data[i]["low"]  <= min(win_l): sl.append(round(data[i]["low"],  4))
    return sorted(set(sh), reverse=True)[:6], sorted(set(sl))[:6]


def find_equal_levels(candles, tolerance=0.003):
    recent = candles[-60:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    eq_h, eq_l = set(), set()
    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            if abs(highs[i]-highs[j])/highs[i] <= tolerance:
                eq_h.add(round((highs[i]+highs[j])/2, 2))
    for i in range(len(lows)):
        for j in range(i+1, len(lows)):
            if abs(lows[i]-lows[j])/lows[i] <= tolerance:
                eq_l.add(round((lows[i]+lows[j])/2, 2))
    return sorted(eq_h, reverse=True)[:5], sorted(eq_l, reverse=True)[:5]


def find_fvg(candles):
    """Find the most recent Fair Value Gaps (last 30 candles)."""
    fvgs = []
    data = candles[-30:]
    for i in range(1, len(data)-1):
        # Bullish FVG: candle[i-1] high < candle[i+1] low
        if data[i-1]["high"] < data[i+1]["low"]:
            fvgs.append(("BULLISH FVG", round(data[i-1]["high"], 2), round(data[i+1]["low"], 2)))
        # Bearish FVG: candle[i-1] low > candle[i+1] high
        if data[i-1]["low"] > data[i+1]["high"]:
            fvgs.append(("BEARISH FVG", round(data[i+1]["high"], 2), round(data[i-1]["low"], 2)))
    return fvgs[-3:] if fvgs else []


def find_order_block(candles):
    """Find the most recent bullish and bearish order blocks."""
    obs = []
    data = candles[-40:]
    for i in range(len(data)-4):
        # Bullish OB: last bearish candle before a 3+ candle bullish move
        if data[i]["close"] < data[i]["open"]:  # bearish candle
            if all(data[i+k]["close"] > data[i+k]["open"] for k in range(1,3)):
                if data[i+2]["close"] > data[i]["high"]:
                    obs.append(("BULLISH OB", round(data[i]["low"],2), round(data[i]["high"],2)))
        # Bearish OB: last bullish candle before a 3+ candle bearish move
        if data[i]["close"] > data[i]["open"]:  # bullish candle
            if all(data[i+k]["close"] < data[i+k]["open"] for k in range(1,3)):
                if data[i+2]["close"] < data[i]["low"]:
                    obs.append(("BEARISH OB", round(data[i]["low"],2), round(data[i]["high"],2)))
    return obs[-2:] if obs else []


def market_structure(candles):
    """Determine HTF market structure."""
    data = candles[-30:]
    highs  = [c["high"]  for c in data]
    lows   = [c["low"]   for c in data]
    closes = [c["close"] for c in data]
    mid    = len(data)//2

    recent_high = max(highs[mid:])
    prev_high   = max(highs[:mid])
    recent_low  = min(lows[mid:])
    prev_low    = min(lows[:mid])

    if recent_high > prev_high and recent_low > prev_low:
        return "BULLISH", "Higher Highs + Higher Lows"
    elif recent_high < prev_high and recent_low < prev_low:
        return "BEARISH", "Lower Highs + Lower Lows"
    elif recent_high > prev_high and recent_low < prev_low:
        return "EXPANSION", "Expanding range - volatile"
    else:
        return "RANGING", "Consolidation - no clear direction"


def premium_discount(candles):
    """Determine if price is in premium or discount."""
    price  = candles[-1]["close"]
    high   = max(c["high"] for c in candles[-30:])
    low    = min(c["low"]  for c in candles[-30:])
    eq     = (high + low) / 2
    rng    = high - low
    pct    = ((price - low) / rng * 100) if rng > 0 else 50

    if pct >= 75:   zone = "DEEP PREMIUM (75-100%) - Strong sell bias"
    elif pct >= 50: zone = "PREMIUM (50-75%) - Cautious longs, prefer sells"
    elif pct >= 25: zone = "DISCOUNT (25-50%) - Prefer longs"
    else:           zone = "DEEP DISCOUNT (0-25%) - Strong buy bias"

    return zone, round(eq, 2), round(pct, 1)


def sweep_proximity(price, eq_highs, eq_lows, threshold=0.8):
    """Check if price is very close to a sweep zone."""
    alerts = []
    for h in eq_highs:
        if h > price:
            dist_pct = (h - price) / price * 100
            if dist_pct <= threshold:
                alerts.append(f"!! BSL SWEEP IMMINENT: Equal Highs at ${h} only {dist_pct:.2f}% away")
    for l in eq_lows:
        if l < price:
            dist_pct = (price - l) / price * 100
            if dist_pct <= threshold:
                alerts.append(f"!! SSL SWEEP IMMINENT: Equal Lows at ${l} only {dist_pct:.2f}% away")
    return alerts


def choch_bos_check(candles):
    """Simple CHoCH/BOS detection on recent candles."""
    data   = candles[-20:]
    price  = data[-1]["close"]
    recent_highs = sorted([c["high"] for c in data[:-3]], reverse=True)
    recent_lows  = sorted([c["low"]  for c in data[:-3]])

    signals = []
    if recent_highs and price > recent_highs[0]:
        signals.append("BOS UP detected - price broke above recent swing high (bullish continuation)")
    if recent_lows and price < recent_lows[0]:
        signals.append("BOS DOWN detected - price broke below recent swing low (bearish continuation)")
    return signals


# ─── TIMEFRAME ANALYSIS ───────────────────────────────────

def analyse_tf(candles, label):
    price         = candles[-1]["close"]
    prev_close    = candles[-2]["close"]
    candle_dir    = "GREEN (bullish)" if price >= candles[-1]["open"] else "RED (bearish)"
    bias, struct  = market_structure(candles)
    zone, eq_pt, pct = premium_discount(candles)
    sw_h, sw_l    = find_swings(candles)
    eq_h, eq_l    = find_equal_levels(candles)
    fvgs          = find_fvg(candles)
    obs           = find_order_block(candles)
    sweeps        = sweep_proximity(price, eq_h, eq_l)
    bos           = choch_bos_check(candles)

    bsl = [h for h in sw_h if h > price][:3]
    ssl = [l for l in sw_l if l < price][:3]

    lines = [
        f"",
        f"{'='*50}",
        f"  {label}",
        f"{'='*50}",
        f"  Last candle:    ${price:.2f}  [{candle_dir}]",
        f"  Prev close:     ${prev_close:.2f}",
        f"  Structure:      {bias} — {struct}",
        f"  Price zone:     {zone}",
        f"  Equilibrium:    ${eq_pt:.2f}  (price at {pct}% of range)",
        f"",
        f"  BUY-SIDE LIQUIDITY (BSL) — above price:",
    ]
    if bsl:
        for b in bsl: lines.append(f"    ${b:.2f}")
    else:
        lines.append("    None identified in recent range")

    lines.append(f"")
    lines.append(f"  SELL-SIDE LIQUIDITY (SSL) — below price:")
    if ssl:
        for s in ssl: lines.append(f"    ${s:.2f}")
    else:
        lines.append("    None identified in recent range")

    if eq_h:
        lines.append(f"")
        lines.append(f"  EQUAL HIGHS (BSL pools — sweep targets):")
        for h in eq_h: lines.append(f"    ${h:.2f}")

    if eq_l:
        lines.append(f"")
        lines.append(f"  EQUAL LOWS (SSL pools — sweep targets):")
        for l in eq_l: lines.append(f"    ${l:.2f}")

    if fvgs:
        lines.append(f"")
        lines.append(f"  FAIR VALUE GAPS (price magnets):")
        for fvg in fvgs:
            lines.append(f"    {fvg[0]}: ${fvg[1]} - ${fvg[2]}")

    if obs:
        lines.append(f"")
        lines.append(f"  ORDER BLOCKS (reaction zones):")
        for ob in obs:
            lines.append(f"    {ob[0]}: ${ob[1]} - ${ob[2]}")

    if bos:
        lines.append(f"")
        lines.append(f"  STRUCTURE SIGNALS:")
        for b in bos: lines.append(f"    -> {b}")

    if sweeps:
        lines.append(f"")
        lines.append(f"  *** SWEEP ALERTS ***")
        for s in sweeps: lines.append(f"    {s}")

    return "\n".join(lines), bias, sweeps, bsl, ssl, eq_h, eq_l


# ─── TRADE PLAN ───────────────────────────────────────────

def build_trade_plan(price, w_bias, d_bias, h4_bias,
                     h1_eq_h, h1_eq_l, h1_bsl, h1_ssl,
                     h4_bsl, h4_ssl, h15_eq_h, h15_eq_l):

    lines = [
        f"",
        f"{'='*50}",
        f"  TRADE PLAN & WHAT TO DO NOW",
        f"{'='*50}",
        f"",
        f"  BIAS SUMMARY:",
        f"  Weekly:  {w_bias}",
        f"  Daily:   {d_bias}",
        f"  4H:      {h4_bias}",
        f"",
    ]

    # Determine overall bias
    biases   = [w_bias, d_bias, h4_bias]
    bull_cnt = sum(1 for b in biases if b == "BULLISH")
    bear_cnt = sum(1 for b in biases if b == "BEARISH")

    if bull_cnt >= 2:
        overall = "BULLISH"
        direction = "LONG"
    elif bear_cnt >= 2:
        overall = "BEARISH"
        direction = "SHORT"
    else:
        overall = "MIXED / NO CLEAR BIAS"
        direction = "WAIT — timeframes conflict"

    lines.append(f"  OVERALL BIAS:   {overall}")
    lines.append(f"  DIRECTION:      {direction}")
    lines.append(f"")

    if direction == "LONG":
        # Find nearest SSL for sweep
        ssl_targets = sorted([l for l in (h1_eq_l or []) + (h1_ssl or []) if l < price])
        bsl_targets = sorted([h for h in (h1_eq_h or []) + (h1_bsl or []) if h > price])
        ob_support  = sorted([h for h in (h4_ssl or []) if h < price], reverse=True)

        lines += [
            f"  LONG SETUP — STEP BY STEP:",
            f"  {'─'*40}",
            f"  STEP 1 — WAIT FOR SSL SWEEP",
        ]
        if ssl_targets:
            lines.append(f"    Watch for price to dip below: ${ssl_targets[0]:.2f}")
            lines.append(f"    You need to see a WICK below this level")
            lines.append(f"    Candle must CLOSE back above it")
        else:
            lines.append(f"    Watch for a wick sweep below recent 1H swing lows")

        lines += [
            f"",
            f"  STEP 2 — WAIT FOR CHoCH ON 1H",
            f"    After the sweep, price reverses up",
            f"    Wait for 1H candle to break above the nearest swing high",
            f"    That break = CHoCH = trend shifted bullish on 1H",
            f"",
            f"  STEP 3 — ENTER ON RETEST",
            f"    After CHoCH, price will dip slightly (into FVG or OB)",
            f"    ENTER LONG on that dip — do not enter on the CHoCH break",
        ]

        if bsl_targets:
            lines.append(f"")
            lines.append(f"  TARGETS (BSL pools above):")
            for i, t in enumerate(bsl_targets[:3], 1):
                lines.append(f"    Target {i}: ${t:.2f}")

        lines += [
            f"",
            f"  STOP LOSS:    Below the sweep wick low",
            f"  MAX HOLD:     Do not hold past $85-86 (Daily supply wall)",
            f"  RISK:         Max 1-2% of account per trade",
        ]

    elif direction == "SHORT":
        bsl_targets = sorted([h for h in (h1_eq_h or []) + (h1_bsl or []) if h > price], reverse=True)
        ssl_targets = sorted([l for l in (h1_eq_l or []) + (h1_ssl or []) if l < price], reverse=True)

        lines += [
            f"  SHORT SETUP — STEP BY STEP:",
            f"  {'─'*40}",
            f"  STEP 1 — WAIT FOR BSL SWEEP",
        ]
        if bsl_targets:
            lines.append(f"    Watch for price to spike above: ${bsl_targets[0]:.2f}")
            lines.append(f"    You need to see a WICK above this level")
            lines.append(f"    Candle must CLOSE back below it")

        lines += [
            f"",
            f"  STEP 2 — WAIT FOR CHoCH ON 1H",
            f"    After the sweep, price reverses down",
            f"    Wait for 1H candle to break below nearest swing low",
            f"    That break = CHoCH = trend shifted bearish on 1H",
            f"",
            f"  STEP 3 — ENTER ON RETEST",
            f"    After CHoCH down, price will bounce slightly",
            f"    ENTER SHORT on that bounce — do not enter on the break",
        ]

        if ssl_targets:
            lines.append(f"")
            lines.append(f"  TARGETS (SSL pools below):")
            for i, t in enumerate(ssl_targets[:3], 1):
                lines.append(f"    Target {i}: ${t:.2f}")

        lines += [
            f"",
            f"  STOP LOSS:    Above the sweep wick high",
            f"  RISK:         Max 1-2% of account per trade",
        ]

    else:
        lines += [
            f"  ACTION: DO NOT TRADE RIGHT NOW",
            f"  {'─'*40}",
            f"  Weekly, Daily and 4H timeframes are conflicting.",
            f"  Wait until at least 2 out of 3 agree on direction.",
            f"  Trading in a conflicted market = gambling, not trading.",
            f"  Sit on your hands and wait for clarity.",
        ]

    # 15M watch
    lines += [
        f"",
        f"  15M CHART — WHAT TO WATCH RIGHT NOW:",
        f"  {'─'*40}",
    ]
    if h15_eq_l:
        lines.append(f"  Equal lows on 15M at: {[f'${x}' for x in h15_eq_l[:2]]}")
        lines.append(f"  -> If swept -> look for 15M CHoCH up -> scalp long")
    if h15_eq_h:
        lines.append(f"  Equal highs on 15M at: {[f'${x}' for x in h15_eq_h[:2]]}")
        lines.append(f"  -> If swept -> look for 15M CHoCH down -> scalp short")
    if not h15_eq_l and not h15_eq_h:
        lines.append(f"  No clear equal highs/lows on 15M right now.")
        lines.append(f"  Wait for structure to form before scalping.")

    return "\n".join(lines)


# ─── SEND EMAIL ───────────────────────────────────────────

def send_email(subject, body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())


# ─── MAIN ─────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now}] Fetching all timeframes...")

    stats = get_stats()
    price = float(stats["lastPrice"])
    chg   = float(stats["priceChangePercent"])
    h24_h = float(stats["highPrice"])
    h24_l = float(stats["lowPrice"])
    vol   = float(stats["volume"])

    src = "Kraken" if IS_GITHUB else "Binance"
    print(f"  Data source: {src}")
    print("  Fetching Weekly...")
    w1  = get_candles("W",   100)
    print("  Fetching Daily...")
    d1  = get_candles("D",   200)
    print("  Fetching 4H...")
    h4  = get_candles("4H",  200)
    print("  Fetching 1H...")
    h1  = get_candles("1H",  200)
    print("  Fetching 15M...")
    m15 = get_candles("15M", 200)

    w_txt,  w_bias,  w_sw,  w_bsl,  w_ssl,  w_eqh,  w_eql  = analyse_tf(w1,  "WEEKLY (1W)")
    d_txt,  d_bias,  d_sw,  d_bsl,  d_ssl,  d_eqh,  d_eql  = analyse_tf(d1,  "DAILY (1D)")
    h4_txt, h4_bias, h4_sw, h4_bsl, h4_ssl, h4_eqh, h4_eql = analyse_tf(h4,  "4-HOUR (4H)")
    h1_txt, h1_bias, h1_sw, h1_bsl, h1_ssl, h1_eqh, h1_eql = analyse_tf(h1,  "1-HOUR (1H)")
    m_txt,  m_bias,  m_sw,  m_bsl,  m_ssl,  m_eqh,  m_eql  = analyse_tf(m15, "15-MINUTE (15M)")

    trade = build_trade_plan(
        price,
        w_bias, d_bias, h4_bias,
        h1_eqh, h1_eql, h1_bsl, h1_ssl,
        h4_bsl, h4_ssl,
        m_eqh,  m_eql
    )

    all_sweeps = w_sw + d_sw + h4_sw + h1_sw + m_sw
    has_alert  = len(all_sweeps) > 0

    if has_alert:
        subject = f"[SOL ALERT !!] ${price:.2f} ({chg:+.2f}%) — SWEEP ZONE REACHED — {now}"
    else:
        subject = f"[SOL Update] ${price:.2f} ({chg:+.2f}%) — {now}"

    body = f"""
SOL/USDT — FULL SMC ANALYSIS
==============================
{now}

LIVE SNAPSHOT
--------------
Price:       ${price:.2f}
24h Change:  {chg:+.2f}%
24h High:    ${h24_h:.2f}
24h Low:     ${h24_l:.2f}
24h Volume:  {vol:,.0f} SOL

{"!! SWEEP ALERT ACTIVE !!" if has_alert else "No active sweep alerts"}
{chr(10).join(all_sweeps) if has_alert else ""}

{w_txt}

{d_txt}

{h4_txt}

{h1_txt}

{m_txt}

{trade}

{"="*50}
QUICK RULES REMINDER
{"="*50}
1. Daily/Weekly bias = your direction. Never fight it.
2. Wait for SWEEP of equal highs/lows before entering.
3. After sweep, wait for CHoCH on 1H to confirm reversal.
4. Enter on the RETEST after CHoCH — not on the break.
5. Stop loss always below/above the sweep wick.
6. Take profit at next liquidity zone (BSL or SSL).
7. NEVER hold longs past $85-86 (major daily supply).
8. NEVER risk more than 1-2% of account per trade.
9. If in doubt — do not trade. Cash is a position.

KEY PERMANENT LEVELS TO REMEMBER:
  Major supply wall:  $86 - $92  (DO NOT hold longs here)
  Previous ATH zone:  $295
  Absolute low swept: $60.13
{"="*50}
Auto-generated every hour | Binance API
"""

    print(f"  Sending email...")
    send_email(subject, body)
    print(f"[{now}] Done. Email sent to {EMAIL_TO}")
    print(f"  Price: ${price:.2f} | {chg:+.2f}% | Sweep alerts: {len(all_sweeps)}")


if __name__ == "__main__":
    main()
