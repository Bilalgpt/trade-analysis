"""
SOL/USDT Hourly Alert — HTML Email + GitHub Pages Report
=========================================================
Timeframes: Weekly, Daily, 4H, 1H, 15M
- Sends beautifully formatted HTML email every hour
- Saves HTML report to report/index.html (served via GitHub Pages)
"""

import urllib.request
import json
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
EMAIL_FROM     = os.environ.get("EMAIL_FROM",     "muhammadbilalafzal1@gmail.com")
EMAIL_TO       = os.environ.get("EMAIL_TO",       "muhammadbilalafzal1@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "msdimmmadnsoraqy")
IS_GITHUB      = os.environ.get("GITHUB_ACTIONS") == "true"
REPORT_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
# ──────────────────────────────────────────────────────────


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


# ── BINANCE (local) ───────────────────────────────────────
def get_stats_binance():
    d = fetch("https://api.binance.com/api/v3/ticker/24hr?symbol=SOLUSDT")
    return {"lastPrice": d["lastPrice"], "priceChangePercent": d["priceChangePercent"],
            "highPrice": d["highPrice"], "lowPrice": d["lowPrice"], "volume": d["volume"]}

def get_candles_binance(interval, limit=200):
    url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval={interval}&limit={limit}"
    raw = fetch(url)
    return [{"time": int(c[0])//1000, "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in raw]

# ── KRAKEN (GitHub Actions) ───────────────────────────────
def get_stats_kraken():
    data = fetch("https://api.kraken.com/0/public/Ticker?pair=SOLUSD")["result"]
    key  = [k for k in data if k != "last"][0]
    t    = data[key]
    price  = float(t["c"][0])
    open24 = float(t["o"])
    chg    = ((price - open24) / open24 * 100) if open24 else 0
    return {"lastPrice": str(round(price, 4)), "priceChangePercent": str(round(chg, 2)),
            "highPrice": str(t["h"][1]), "lowPrice": str(t["l"][1]), "volume": str(t["v"][1])}

def get_candles_kraken(interval, limit=200):
    url    = f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval={interval}"
    result = fetch(url)["result"]
    key    = [k for k in result if k != "last"][0]
    raw    = result[key][-limit:]
    return [{"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[6])} for c in raw]

# ── UNIFIED ───────────────────────────────────────────────
def get_stats():
    return get_stats_kraken() if IS_GITHUB else get_stats_binance()

def get_candles(tf, limit=200):
    if IS_GITHUB:
        m = {"W": 10080, "D": 1440, "4H": 240, "1H": 60, "15M": 15}
        return get_candles_kraken(m[tf], limit)
    m = {"W": "1w", "D": "1d", "4H": "4h", "1H": "1h", "15M": "15m"}
    return get_candles_binance(m[tf], limit)


# ─── SMC TOOLS ────────────────────────────────────────────

def find_swings(candles, lookback=5):
    data = candles[-100:]
    sh, sl = [], []
    for i in range(lookback, len(data) - lookback):
        wh = [data[i-lookback+k]["high"] for k in range(lookback*2+1) if k != lookback]
        wl = [data[i-lookback+k]["low"]  for k in range(lookback*2+1) if k != lookback]
        if data[i]["high"] >= max(wh): sh.append(round(data[i]["high"], 4))
        if data[i]["low"]  <= min(wl): sl.append(round(data[i]["low"],  4))
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
    fvgs = []
    data = candles[-30:]
    for i in range(1, len(data)-1):
        if data[i-1]["high"] < data[i+1]["low"]:
            fvgs.append(("Bullish FVG", round(data[i-1]["high"],2), round(data[i+1]["low"],2)))
        if data[i-1]["low"] > data[i+1]["high"]:
            fvgs.append(("Bearish FVG", round(data[i+1]["high"],2), round(data[i-1]["low"],2)))
    return fvgs[-3:] if fvgs else []

def find_order_blocks(candles):
    obs = []
    data = candles[-40:]
    for i in range(len(data)-4):
        if data[i]["close"] < data[i]["open"]:
            if all(data[i+k]["close"] > data[i+k]["open"] for k in range(1,3)):
                if data[i+2]["close"] > data[i]["high"]:
                    obs.append(("Bullish OB", round(data[i]["low"],2), round(data[i]["high"],2)))
        if data[i]["close"] > data[i]["open"]:
            if all(data[i+k]["close"] < data[i+k]["open"] for k in range(1,3)):
                if data[i+2]["close"] < data[i]["low"]:
                    obs.append(("Bearish OB", round(data[i]["low"],2), round(data[i]["high"],2)))
    return obs[-2:] if obs else []

def market_structure(candles):
    data  = candles[-30:]
    mid   = len(data)//2
    rh    = max(c["high"] for c in data[mid:])
    ph    = max(c["high"] for c in data[:mid])
    rl    = min(c["low"]  for c in data[mid:])
    pl    = min(c["low"]  for c in data[:mid])
    if rh > ph and rl > pl: return "BULLISH"
    if rh < ph and rl < pl: return "BEARISH"
    return "RANGING"

def premium_discount(candles):
    price = candles[-1]["close"]
    high  = max(c["high"] for c in candles[-30:])
    low   = min(c["low"]  for c in candles[-30:])
    eq    = (high + low) / 2
    rng   = high - low
    pct   = ((price - low) / rng * 100) if rng > 0 else 50
    if pct >= 75:   zone = "DEEP PREMIUM"
    elif pct >= 50: zone = "PREMIUM"
    elif pct >= 25: zone = "DISCOUNT"
    else:           zone = "DEEP DISCOUNT"
    return zone, round(eq, 2), round(pct, 1)

def sweep_proximity(price, eq_h, eq_l, threshold=0.8):
    alerts = []
    for h in eq_h:
        if h > price:
            d = (h - price) / price * 100
            if d <= threshold:
                alerts.append(f"BSL SWEEP IMMINENT: Equal Highs ${h} ({d:.2f}% away)")
    for l in eq_l:
        if l < price:
            d = (price - l) / price * 100
            if d <= threshold:
                alerts.append(f"SSL SWEEP IMMINENT: Equal Lows ${l} ({d:.2f}% away)")
    return alerts

def analyse_tf(candles, label):
    price         = candles[-1]["close"]
    bias          = market_structure(candles)
    zone, eq_pt, pct = premium_discount(candles)
    sw_h, sw_l    = find_swings(candles)
    eq_h, eq_l    = find_equal_levels(candles)
    fvgs          = find_fvg(candles)
    obs           = find_order_blocks(candles)
    sweeps        = sweep_proximity(price, eq_h, eq_l)
    bsl           = [h for h in sw_h if h > price][:3]
    ssl           = [l for l in sw_l if l < price][:3]
    last_candle   = "GREEN" if candles[-1]["close"] >= candles[-1]["open"] else "RED"
    return {
        "label": label, "price": price, "bias": bias,
        "zone": zone, "eq_pt": eq_pt, "pct": pct,
        "bsl": bsl, "ssl": ssl, "eq_h": eq_h, "eq_l": eq_l,
        "fvgs": fvgs, "obs": obs, "sweeps": sweeps, "last_candle": last_candle
    }

def build_trade_plan(price, w, d, h4, h1, m15):
    biases   = [w["bias"], d["bias"], h4["bias"]]
    bull_cnt = biases.count("BULLISH")
    bear_cnt = biases.count("BEARISH")
    if bull_cnt >= 2:   direction, overall = "LONG",  "BULLISH"
    elif bear_cnt >= 2: direction, overall = "SHORT", "BEARISH"
    else:               direction, overall = "WAIT",  "MIXED"

    ssl_targets = sorted([l for l in (h1["eq_l"] or []) + (h1["ssl"] or []) if l < price])
    bsl_targets = sorted([h for h in (h1["eq_h"] or []) + (h1["bsl"] or []) if h > price])

    sweep_level = ssl_targets[0] if ssl_targets and direction == "LONG" else \
                  bsl_targets[0] if bsl_targets and direction == "SHORT" else None

    targets = bsl_targets[:3] if direction == "LONG" else ssl_targets[:3]
    stop    = round(ssl_targets[0] - 1.0, 2) if direction == "LONG" and ssl_targets else \
              round(bsl_targets[0] + 1.0, 2) if direction == "SHORT" and bsl_targets else None

    return {
        "direction": direction, "overall": overall,
        "sweep_level": sweep_level, "targets": targets,
        "stop": stop, "biases": biases,
        "w_bias": w["bias"], "d_bias": d["bias"],
        "h4_bias": h4["bias"], "h1_bias": h1["bias"],
        "h1_eq_l": h1["eq_l"], "h1_eq_h": h1["eq_h"],
        "m15_eq_l": m15["eq_l"], "m15_eq_h": m15["eq_h"],
        "all_sweeps": w["sweeps"] + d["sweeps"] + h4["sweeps"] + h1["sweeps"] + m15["sweeps"]
    }


# ─── HTML GENERATION ──────────────────────────────────────

def bias_color(bias):
    if bias == "BULLISH": return "#3fb950"
    if bias == "BEARISH": return "#f85149"
    return "#d29922"

def zone_color(zone):
    if "DEEP PREMIUM" in zone: return "#f85149"
    if "PREMIUM" in zone:      return "#d29922"
    if "DEEP DISCOUNT" in zone:return "#3fb950"
    return "#58a6ff"

def candle_color(c):
    return "#3fb950" if c == "GREEN" else "#f85149"

def levels_rows(items, color):
    if not items: return "<tr><td colspan='2' style='color:#8b949e;'>None identified</td></tr>"
    return "".join(f"<tr><td style='color:{color};font-weight:bold;'>${x:.2f}</td>"
                   f"<td style='color:#8b949e;font-size:12px;'>{'BSL — buy stops above' if color=='#3fb950' else 'SSL — sell stops below'}</td></tr>"
                   for x in items)

def tf_card(tf):
    bc = bias_color(tf["bias"])
    zc = zone_color(tf["zone"])
    lc = candle_color(tf["last_candle"])
    fvg_html = "".join(f"<span style='background:#21262d;border-radius:4px;padding:2px 8px;"
                       f"margin:2px;font-size:12px;color:#58a6ff;'>{f[0]}: ${f[1]}-${f[2]}</span>"
                       for f in tf["fvgs"]) or "<span style='color:#8b949e;font-size:12px;'>None recent</span>"
    ob_html  = "".join(f"<span style='background:#21262d;border-radius:4px;padding:2px 8px;"
                       f"margin:2px;font-size:12px;color:#d29922;'>{o[0]}: ${o[1]}-${o[2]}</span>"
                       for o in tf["obs"]) or "<span style='color:#8b949e;font-size:12px;'>None recent</span>"
    sweep_html = "".join(f"<div style='background:#2d1b1b;border-left:3px solid #f85149;"
                         f"padding:8px;margin:4px 0;border-radius:0 4px 4px 0;font-size:13px;color:#f85149;'>"
                         f"!! {s}</div>" for s in tf["sweeps"])

    return f"""
<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;margin:12px 0;overflow:hidden;'>
  <div style='background:#21262d;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;'>
    <span style='font-size:16px;font-weight:bold;color:#f0f6fc;'>{tf["label"]}</span>
    <span style='background:{bc};color:#000;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:bold;'>{tf["bias"]}</span>
  </div>
  <div style='padding:16px;'>
    <div style='display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;'>
      <div><span style='color:#8b949e;font-size:11px;display:block;'>LAST CLOSE</span>
           <span style='color:{lc};font-size:20px;font-weight:bold;'>${tf["price"]:.2f}</span>
           <span style='color:{lc};font-size:11px;'> {tf["last_candle"]}</span></div>
      <div><span style='color:#8b949e;font-size:11px;display:block;'>ZONE</span>
           <span style='color:{zc};font-size:14px;font-weight:bold;'>{tf["zone"]}</span>
           <span style='color:#8b949e;font-size:11px;'> ({tf["pct"]}% of range)</span></div>
      <div><span style='color:#8b949e;font-size:11px;display:block;'>EQUILIBRIUM</span>
           <span style='color:#f0f6fc;font-size:14px;font-weight:bold;'>${tf["eq_pt"]:.2f}</span></div>
    </div>
    <div style='display:flex;gap:12px;flex-wrap:wrap;'>
      <div style='flex:1;min-width:140px;'>
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:6px;'>BSL Above Price</div>
        {"".join(f"<div style='color:#3fb950;font-size:13px;padding:2px 0;'>&#9650; ${x:.2f}</div>" for x in tf["bsl"]) or "<div style='color:#8b949e;font-size:12px;'>None</div>"}
        {"".join(f"<div style='color:#58a6ff;font-size:12px;padding:2px 0;'>= ${x:.2f} (equal highs)</div>" for x in tf["eq_h"][:2]) }
      </div>
      <div style='flex:1;min-width:140px;'>
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:6px;'>SSL Below Price</div>
        {"".join(f"<div style='color:#f85149;font-size:13px;padding:2px 0;'>&#9660; ${x:.2f}</div>" for x in tf["ssl"]) or "<div style='color:#8b949e;font-size:12px;'>None</div>"}
        {"".join(f"<div style='color:#58a6ff;font-size:12px;padding:2px 0;'>= ${x:.2f} (equal lows)</div>" for x in tf["eq_l"][:2]) }
      </div>
      <div style='flex:1;min-width:140px;'>
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:6px;'>FVGs</div>
        {fvg_html}
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin:8px 0 4px;'>Order Blocks</div>
        {ob_html}
      </div>
    </div>
    {sweep_html}
  </div>
</div>"""

def generate_html(stats, w, d, h4, h1, m15, plan, now):
    price  = float(stats["lastPrice"])
    chg    = float(stats["priceChangePercent"])
    h24h   = float(stats["highPrice"])
    h24l   = float(stats["lowPrice"])
    vol    = float(stats["volume"])
    chg_c  = "#3fb950" if chg >= 0 else "#f85149"
    chg_s  = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
    dir_c  = bias_color(plan["overall"])
    has_alerts = len(plan["all_sweeps"]) > 0

    alert_banner = ""
    if has_alerts:
        alert_banner = f"""
<div style='background:#2d1b1b;border:1px solid #f85149;border-radius:8px;padding:16px;margin:12px 0;'>
  <div style='color:#f85149;font-size:16px;font-weight:bold;margin-bottom:8px;'>!! SWEEP ALERT ACTIVE</div>
  {"".join(f"<div style='color:#ffa0a0;font-size:13px;padding:2px 0;'>{s}</div>" for s in plan["all_sweeps"])}
</div>"""

    targets_html = "".join(
        f"<div style='background:#21262d;border-radius:6px;padding:10px;text-align:center;flex:1;min-width:80px;'>"
        f"<div style='color:#8b949e;font-size:11px;'>T{i+1}</div>"
        f"<div style='color:#3fb950;font-size:16px;font-weight:bold;'>${t:.2f}</div></div>"
        for i, t in enumerate(plan["targets"])
    ) if plan["targets"] else "<div style='color:#8b949e;'>Wait for bias to align</div>"

    step1_level = f"${plan['sweep_level']:.2f}" if plan["sweep_level"] else "nearest swing low"
    step1_dir   = "below" if plan["direction"] == "LONG" else "above"
    choch_dir   = "above nearest 1H swing high" if plan["direction"] == "LONG" else "below nearest 1H swing low"
    stop_html   = f"<span style='color:#f85149;font-weight:bold;'>${plan['stop']:.2f}</span>" if plan["stop"] else "<span style='color:#f85149;'>Below sweep wick</span>"

    m15_watch = ""
    if m15["eq_l"]:
        m15_watch += f"<div style='color:#58a6ff;font-size:13px;padding:3px 0;'>Equal Lows at {', '.join(f'${x}' for x in m15['eq_l'][:2])} — watch for SSL sweep + 15M CHoCH up</div>"
    if m15["eq_h"]:
        m15_watch += f"<div style='color:#58a6ff;font-size:13px;padding:3px 0;'>Equal Highs at {', '.join(f'${x}' for x in m15['eq_h'][:2])} — watch for BSL sweep + 15M CHoCH down</div>"
    if not m15_watch:
        m15_watch = "<div style='color:#8b949e;font-size:13px;'>No clear equal levels on 15M right now</div>"

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>SOL/USDT Analysis — {now}</title>
</head>
<body style='margin:0;padding:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'>
<div style='max-width:800px;margin:0 auto;padding:16px;'>

  <!-- HEADER -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:12px;'>
    <div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;'>
      <div>
        <div style='color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px;'>SOL / USDT</div>
        <div style='font-size:42px;font-weight:bold;color:#f0f6fc;line-height:1.1;'>${price:.2f}</div>
        <div style='font-size:18px;color:{chg_c};'>{chg_s} (24h)</div>
      </div>
      <div style='text-align:right;'>
        <div style='color:#8b949e;font-size:12px;margin-bottom:4px;'>Last updated</div>
        <div style='color:#f0f6fc;font-size:13px;'>{now}</div>
        <div style='margin-top:8px;'>
          <span style='background:{dir_c};color:#000;padding:5px 14px;border-radius:16px;font-weight:bold;font-size:14px;'>{plan["direction"]}</span>
        </div>
      </div>
    </div>
    <div style='display:flex;gap:16px;margin-top:16px;flex-wrap:wrap;border-top:1px solid #30363d;padding-top:16px;'>
      <div><span style='color:#8b949e;font-size:11px;'>24H HIGH</span><br><span style='color:#3fb950;font-weight:bold;'>${h24h:.2f}</span></div>
      <div><span style='color:#8b949e;font-size:11px;'>24H LOW</span><br><span style='color:#f85149;font-weight:bold;'>${h24l:.2f}</span></div>
      <div><span style='color:#8b949e;font-size:11px;'>24H VOLUME</span><br><span style='color:#f0f6fc;font-weight:bold;'>{vol:,.0f} SOL</span></div>
      <div><span style='color:#8b949e;font-size:11px;'>DATA SOURCE</span><br><span style='color:#f0f6fc;font-size:12px;'>{"Kraken" if IS_GITHUB else "Binance"}</span></div>
    </div>
  </div>

  {alert_banner}

  <!-- BIAS SUMMARY -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px;'>
    <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:12px;'>Timeframe Bias Summary</div>
    <div style='display:flex;gap:8px;flex-wrap:wrap;'>
      {"".join(f"<div style='background:#21262d;border-radius:6px;padding:10px 14px;text-align:center;flex:1;min-width:80px;'><div style='color:#8b949e;font-size:11px;'>{lbl}</div><div style='color:{bias_color(b)};font-weight:bold;font-size:13px;margin-top:4px;'>{b}</div></div>" for lbl, b in [("Weekly",plan["w_bias"]),("Daily",plan["d_bias"]),("4H",plan["h4_bias"]),("1H",plan["h1_bias"])])}
    </div>
  </div>

  <!-- TRADE PLAN -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:12px;overflow:hidden;'>
    <div style='background:#21262d;padding:12px 16px;'>
      <span style='font-size:16px;font-weight:bold;color:#f0f6fc;'>Trade Plan</span>
      <span style='float:right;background:{dir_c};color:#000;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:bold;'>{plan["direction"]}</span>
    </div>
    <div style='padding:16px;'>
      {"<div style='color:#d29922;background:#2d2a1b;border-radius:6px;padding:12px;margin-bottom:12px;font-size:13px;'>Timeframes are conflicting. Do NOT trade until at least 2 out of 3 align. Sit on hands.</div>" if plan["direction"] == "WAIT" else f"""
      <div style='margin-bottom:16px;'>
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:8px;'>Step-by-Step Entry</div>
        <div style='display:flex;flex-direction:column;gap:8px;'>
          <div style='background:#21262d;border-radius:6px;padding:10px 14px;border-left:3px solid #58a6ff;'>
            <span style='color:#58a6ff;font-weight:bold;font-size:13px;'>STEP 1 — Wait for sweep</span><br>
            <span style='color:#e6edf3;font-size:13px;'>Watch for price to go {step1_dir} {step1_level} on 1H with a wick. Candle must close back {"above" if plan["direction"]=="LONG" else "below"} the level.</span>
          </div>
          <div style='background:#21262d;border-radius:6px;padding:10px 14px;border-left:3px solid #58a6ff;'>
            <span style='color:#58a6ff;font-weight:bold;font-size:13px;'>STEP 2 — Wait for CHoCH on 1H</span><br>
            <span style='color:#e6edf3;font-size:13px;'>After sweep, wait for price to break {choch_dir}. That break = CHoCH confirmed.</span>
          </div>
          <div style='background:#21262d;border-radius:6px;padding:10px 14px;border-left:3px solid #58a6ff;'>
            <span style='color:#58a6ff;font-weight:bold;font-size:13px;'>STEP 3 — Enter on retest</span><br>
            <span style='color:#e6edf3;font-size:13px;'>After CHoCH, price dips back into FVG/OB. Enter {"long" if plan["direction"]=="LONG" else "short"} on that dip. Do NOT enter on the CHoCH break itself.</span>
          </div>
        </div>
      </div>
      <div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;'>
        <div style='flex:1;'>
          <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:8px;'>Targets</div>
          <div style='display:flex;gap:8px;flex-wrap:wrap;'>{targets_html}</div>
        </div>
        <div style='background:#21262d;border-radius:6px;padding:12px;min-width:140px;'>
          <div style='color:#8b949e;font-size:11px;'>STOP LOSS</div>
          <div style='margin-top:4px;'>{stop_html}</div>
          <div style='color:#8b949e;font-size:11px;margin-top:8px;'>MAX HOLD</div>
          <div style='color:#d29922;font-size:13px;'>Do not hold past $85-86</div>
        </div>
      </div>"""}
      <div>
        <div style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-bottom:6px;'>15M Scalp Watch</div>
        {m15_watch}
      </div>
    </div>
  </div>

  <!-- TIMEFRAME CARDS -->
  <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin:16px 0 8px;'>Timeframe Breakdown</div>
  {tf_card(w)}
  {tf_card(d)}
  {tf_card(h4)}
  {tf_card(h1)}
  {tf_card(m15)}

  <!-- PERMANENT LEVELS -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-top:12px;'>
    <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:12px;'>Key Permanent Levels to Remember</div>
    <div style='display:flex;gap:12px;flex-wrap:wrap;'>
      <div style='flex:1;min-width:160px;'>
        <div style='color:#8b949e;font-size:11px;margin-bottom:6px;'>MAJOR RESISTANCE (BSL)</div>
        <div style='color:#f85149;font-size:13px;'>$86-92 — Daily supply wall (DO NOT hold longs here)</div>
        <div style='color:#f85149;font-size:13px;margin-top:4px;'>$120-125 — Major resistance</div>
        <div style='color:#f85149;font-size:13px;margin-top:4px;'>$295 — ATH (ultimate BSL)</div>
      </div>
      <div style='flex:1;min-width:160px;'>
        <div style='color:#8b949e;font-size:11px;margin-bottom:6px;'>MAJOR SUPPORT (SSL)</div>
        <div style='color:#3fb950;font-size:13px;'>$72-73 — 4H strong support</div>
        <div style='color:#3fb950;font-size:13px;margin-top:4px;'>$64-65 — Daily strong support</div>
        <div style='color:#3fb950;font-size:13px;margin-top:4px;'>$60.13 — Absolute low (swept)</div>
      </div>
    </div>
  </div>

  <!-- RULES -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-top:12px;'>
    <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:10px;'>Rules Reminder</div>
    {"".join(f"<div style='color:#e6edf3;font-size:13px;padding:3px 0;border-bottom:1px solid #21262d;'>{''.join(['&#10003; ',r])}</div>" for r in ["Daily/Weekly bias = your direction. Never fight it.","Wait for SWEEP before entering. No sweep = no trade.","After sweep: wait for CHoCH on 1H to confirm reversal.","Enter on the RETEST after CHoCH. Not on the break.","Stop loss always below/above the sweep wick.","NEVER hold longs past $85-86 (major daily supply wall).","NEVER risk more than 1-2% of your account per trade.","When in doubt — do not trade. Cash is a position."])}
  </div>

  <div style='text-align:center;color:#8b949e;font-size:11px;margin-top:16px;padding:16px;'>
    Auto-generated every hour &nbsp;|&nbsp; {"Kraken" if IS_GITHUB else "Binance"} API &nbsp;|&nbsp; {now}
  </div>

</div>
</body>
</html>"""


# ─── EMAIL + REPORT SAVE ──────────────────────────────────

def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls()
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

def save_report(html):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


# ─── MAIN ─────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    src = "Kraken" if IS_GITHUB else "Binance"
    print(f"[{now}] Fetching data from {src}...")

    stats = get_stats()
    price = float(stats["lastPrice"])
    chg   = float(stats["priceChangePercent"])

    print("  Weekly..."); w1  = get_candles("W",   100)
    print("  Daily...");  d1  = get_candles("D",   200)
    print("  4H...");     h4  = get_candles("4H",  200)
    print("  1H...");     h1  = get_candles("1H",  200)
    print("  15M...");    m15 = get_candles("15M", 200)

    w  = analyse_tf(w1,  "Weekly (1W)")
    d  = analyse_tf(d1,  "Daily (1D)")
    h4 = analyse_tf(h4,  "4-Hour (4H)")
    h1 = analyse_tf(h1,  "1-Hour (1H)")
    m  = analyse_tf(m15, "15-Minute (15M)")

    plan = build_trade_plan(price, w, d, h4, h1, m)
    html = generate_html(stats, w, d, h4, h1, m, plan, now)

    has_alerts = len(plan["all_sweeps"]) > 0
    subject = f"[SOL ALERT !!] ${price:.2f} ({chg:+.2f}%) — SWEEP ZONE REACHED — {now}" \
              if has_alerts else f"[SOL] ${price:.2f} ({chg:+.2f}%) — {plan['direction']} — {now}"

    print("  Saving report...")
    save_report(html)

    print("  Sending email...")
    send_email(subject, html)

    print(f"[{now}] Done.")
    print(f"  Price: ${price:.2f} | {chg:+.2f}% | Direction: {plan['direction']} | Alerts: {len(plan['all_sweeps'])}")


if __name__ == "__main__":
    main()
