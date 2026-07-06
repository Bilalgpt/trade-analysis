"""
SOL/USDT Support & Resistance Retest Alert
===========================================
Strategy:
  1. Identify S/R levels from Weekly, Daily, 4H, 1H candles.
  2. Cluster levels within 0.5% — same level on 2+ timeframes = confluence bonus.
  3. Retest is confirmed ONLY when the last CLOSED 1H candle shows:
       LONG : wick touched support AND candle closed above it
       SHORT: wick touched resistance AND candle closed below it
  4. Score each signal with RSI, MACD, volume spike, candle pattern.
  5. Email only STRONG (>=8) or MODERATE (>=5) signals.
     Report always saved to docs/sr_report.html (GitHub Pages).
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
REPORT_PATH    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "sr_report.html")

SR_TOLERANCE    = 0.005   # 0.5% — how close price must be to "touch" a level
CLUSTER_TOL     = 0.006   # 0.6% — merge levels within this range
MIN_SCORE_EMAIL = 5       # minimum score to send email (5=MODERATE, 8=STRONG)
# ──────────────────────────────────────────────────────────


# ─── API ──────────────────────────────────────────────────

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def get_stats_binance():
    d = fetch("https://api.binance.com/api/v3/ticker/24hr?symbol=SOLUSDT")
    return {"lastPrice": d["lastPrice"], "priceChangePercent": d["priceChangePercent"],
            "highPrice": d["highPrice"], "lowPrice": d["lowPrice"], "volume": d["volume"]}

def get_candles_binance(interval, limit=300):
    url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval={interval}&limit={limit}"
    return [{"time": int(c[0])//1000, "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in fetch(url)]

def get_stats_kraken():
    data = fetch("https://api.kraken.com/0/public/Ticker?pair=SOLUSD")["result"]
    key  = [k for k in data if k != "last"][0]
    t    = data[key]
    price  = float(t["c"][0])
    open24 = float(t["o"])
    chg    = ((price - open24) / open24 * 100) if open24 else 0
    return {"lastPrice": str(round(price, 4)), "priceChangePercent": str(round(chg, 2)),
            "highPrice": str(t["h"][1]), "lowPrice": str(t["l"][1]), "volume": str(t["v"][1])}

def get_candles_kraken(interval, limit=300):
    url    = f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval={interval}"
    result = fetch(url)["result"]
    key    = [k for k in result if k != "last"][0]
    raw    = result[key][-limit:]
    return [{"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[6])} for c in raw]

def get_stats():
    return get_stats_kraken() if IS_GITHUB else get_stats_binance()

def get_candles(tf, limit=300):
    if IS_GITHUB:
        m = {"W": 10080, "D": 1440, "4H": 240, "1H": 60, "15M": 15}
        return get_candles_kraken(m[tf], limit)
    m = {"W": "1w", "D": "1d", "4H": "4h", "1H": "1h", "15M": "15m"}
    return get_candles_binance(m[tf], limit)


# ─── INDICATORS ───────────────────────────────────────────

def calculate_rsi(candles, period=14):
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 1)

def _ema_series(values, period):
    k = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema

def calculate_macd(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 40:
        return None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    diff = len(e12) - len(e26)
    macd_line = [e12[diff + i] - e26[i] for i in range(len(e26))]
    sig_line  = _ema_series(macd_line, 9)
    d2 = len(macd_line) - len(sig_line)
    hist = [macd_line[d2 + i] - sig_line[i] for i in range(len(sig_line))]
    return {
        "macd":      round(macd_line[-1], 4),
        "signal":    round(sig_line[-1],  4),
        "hist":      round(hist[-1], 4),
        "hist_prev": round(hist[-2], 4) if len(hist) >= 2 else 0,
    }

def volume_avg(candles, period=20):
    vols = [c["volume"] for c in candles[-period - 1:-1]]
    return sum(vols) / len(vols) if vols else 1


# ─── S/R LEVEL DETECTION ──────────────────────────────────

TF_WEIGHT = {"W": 4, "D": 3, "4H": 2, "1H": 1}

def find_swing_levels(candles, tf, lookback=5):
    """Return raw swing high/low levels for this timeframe."""
    levels = []
    data   = candles[-min(150, len(candles)):]
    n      = len(data)
    for i in range(lookback, n - lookback):
        window_h = [data[i - lookback + k]["high"] for k in range(lookback * 2 + 1) if k != lookback]
        window_l = [data[i - lookback + k]["low"]  for k in range(lookback * 2 + 1) if k != lookback]
        if data[i]["high"] >= max(window_h):
            levels.append({"price": round(data[i]["high"], 2), "type": "resistance",
                            "tf": tf, "weight": TF_WEIGHT.get(tf, 1)})
        if data[i]["low"] <= min(window_l):
            levels.append({"price": round(data[i]["low"],  2), "type": "support",
                            "tf": tf, "weight": TF_WEIGHT.get(tf, 1)})
    return levels

def cluster_levels(raw_levels, price, tolerance=CLUSTER_TOL):
    """
    Merge raw levels within tolerance%, count touches per cluster.
    Only keep levels within 15% of current price (relevant zone).
    """
    nearby = [lv for lv in raw_levels
              if abs(lv["price"] - price) / price <= 0.15]
    nearby.sort(key=lambda x: x["price"])

    clusters = []
    for lv in nearby:
        merged = False
        for cl in clusters:
            if abs(lv["price"] - cl["price"]) / cl["price"] <= tolerance:
                n = cl["touches"] + 1
                cl["price"]   = round((cl["price"] * cl["touches"] + lv["price"]) / n, 2)
                cl["touches"] = n
                cl["weight"]  = max(cl["weight"], lv["weight"])
                cl["tfs"].add(lv["tf"])
                cl["types"].add(lv["type"])
                merged = True
                break
        if not merged:
            clusters.append({
                "price":   lv["price"],
                "weight":  lv["weight"],
                "touches": 1,
                "tfs":     {lv["tf"]},
                "types":   {lv["type"]},
            })

    for cl in clusters:
        if "support" in cl["types"] and "resistance" in cl["types"]:
            cl["type"] = "flip"      # broken level — both S and R
        elif "resistance" in cl["types"]:
            cl["type"] = "resistance"
        else:
            cl["type"] = "support"
        cl["tfs"] = sorted(cl["tfs"])

    return sorted(clusters, key=lambda x: x["price"], reverse=True)


# ─── CANDLE PATTERN DETECTION ─────────────────────────────

def candle_pattern(c):
    """Detect single-candle patterns. Returns (name, direction) or None."""
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    body        = abs(cl - o)
    total       = h - l
    if total < 1e-9:
        return None
    upper_wick = h - max(cl, o)
    lower_wick = min(cl, o) - l
    body_pct   = body / total
    upper_pct  = upper_wick / total
    lower_pct  = lower_wick / total
    # Pin bar / hammer
    if body_pct < 0.35 and lower_pct >= 0.55 and upper_pct < 0.15:
        return ("Hammer / Pin Bar", "bullish")
    # Shooting star / bearish pin
    if body_pct < 0.35 and upper_pct >= 0.55 and lower_pct < 0.15:
        return ("Shooting Star / Pin Bar", "bearish")
    # Strong bullish close (marubozu-like)
    if body_pct >= 0.70 and cl > o:
        return ("Strong Bullish Candle", "bullish")
    # Strong bearish close
    if body_pct >= 0.70 and cl < o:
        return ("Strong Bearish Candle", "bearish")
    return None

def engulfing_pattern(candles):
    """Check last 2 candles for engulfing. Returns (name, direction) or None."""
    if len(candles) < 2:
        return None
    p, c = candles[-2], candles[-1]
    pb = abs(p["close"] - p["open"])
    cb = abs(c["close"] - c["open"])
    if (p["close"] < p["open"] and c["close"] > c["open"]
            and c["close"] >= p["open"] and c["open"] <= p["close"] and cb >= pb * 0.9):
        return ("Bullish Engulfing", "bullish")
    if (p["close"] > p["open"] and c["close"] < c["open"]
            and c["close"] <= p["open"] and c["open"] >= p["close"] and cb >= pb * 0.9):
        return ("Bearish Engulfing", "bearish")
    return None


# ─── RETEST DETECTION + SCORING ───────────────────────────

def check_retest(cluster, h1_candles, rsi, macd):
    """
    Check if the last CLOSED 1H candle confirms a retest of this S/R cluster.
    Returns a scored signal dict, or None if no retest.
    """
    last   = h1_candles[-1]   # last fully CLOSED 1H candle
    price  = last["close"]
    lv     = cluster["price"]
    ltype  = cluster["type"]
    tol    = SR_TOLERANCE

    signal_dir = None

    # Support retest: wick dipped into support zone, candle CLOSED above
    if ltype in ("support", "flip"):
        wick_hit    = last["low"]  <= lv * (1 + tol) and last["low"]  >= lv * (1 - tol * 2)
        closed_above = last["close"] > lv
        if wick_hit and closed_above:
            signal_dir = "LONG"

    # Resistance retest: wick poked into resistance zone, candle CLOSED below
    if ltype in ("resistance", "flip") and signal_dir is None:
        wick_hit    = last["high"] >= lv * (1 - tol) and last["high"] <= lv * (1 + tol * 2)
        closed_below = last["close"] < lv
        if wick_hit and closed_below:
            signal_dir = "SHORT"

    if signal_dir is None:
        return None

    # ── SCORING ───────────────────────────────────────────
    score = 0
    notes = []

    # 1. Timeframe weight (highest TF in the cluster)
    score += cluster["weight"]
    notes.append(f"Level from {'/'.join(cluster['tfs'])} (+{cluster['weight']})")

    # 2. Multi-TF confluence bonus
    if len(cluster["tfs"]) >= 2:
        bonus = 3
        score += bonus
        notes.append(f"Multi-TF confluence ({'/'.join(cluster['tfs'])}) (+{bonus})")

    # 3. Level tested multiple times = stronger
    if cluster["touches"] >= 4:
        score += 3
        notes.append(f"Very strong level — tested {cluster['touches']}x (+3)")
    elif cluster["touches"] == 3:
        score += 2
        notes.append(f"Strong level — tested {cluster['touches']}x (+2)")
    elif cluster["touches"] == 2:
        score += 1
        notes.append(f"Tested {cluster['touches']}x (+1)")

    # 4. RSI
    if rsi is not None:
        if signal_dir == "LONG":
            if rsi <= 35:
                score += 3; notes.append(f"RSI deeply oversold ({rsi}) (+3)")
            elif rsi <= 45:
                score += 2; notes.append(f"RSI oversold ({rsi}) (+2)")
            elif rsi <= 55:
                score += 1; notes.append(f"RSI neutral-low ({rsi}) (+1)")
        else:
            if rsi >= 65:
                score += 3; notes.append(f"RSI deeply overbought ({rsi}) (+3)")
            elif rsi >= 55:
                score += 2; notes.append(f"RSI overbought ({rsi}) (+2)")
            elif rsi >= 45:
                score += 1; notes.append(f"RSI neutral-high ({rsi}) (+1)")

    # 5. MACD histogram direction
    if macd:
        if signal_dir == "LONG" and macd["hist"] > macd["hist_prev"]:
            score += 2
            notes.append(f"MACD histogram turning up ({macd['hist']:+.4f}) (+2)")
        elif signal_dir == "SHORT" and macd["hist"] < macd["hist_prev"]:
            score += 2
            notes.append(f"MACD histogram turning down ({macd['hist']:+.4f}) (+2)")

    # 6. Volume spike on retest candle
    vol_a = volume_avg(h1_candles)
    if last["volume"] > vol_a * 1.3:
        score += 2
        notes.append(f"Volume spike ({last['volume']:.0f} vs avg {vol_a:.0f}) (+2)")
    elif last["volume"] > vol_a * 1.1:
        score += 1
        notes.append(f"Above-avg volume (+1)")

    # 7. Single candle pattern
    pat = candle_pattern(last)
    if pat:
        pname, pdir = pat
        if (signal_dir == "LONG" and pdir == "bullish") or \
           (signal_dir == "SHORT" and pdir == "bearish"):
            score += 2
            notes.append(f"{pname} (+2)")

    # 8. Engulfing pattern
    eng = engulfing_pattern(h1_candles[-3:])
    if eng:
        ename, edir = eng
        if (signal_dir == "LONG" and edir == "bullish") or \
           (signal_dir == "SHORT" and edir == "bearish"):
            score += 2
            notes.append(f"{ename} (+2)")

    # 9. Flip zone (extra conviction — level has been both S and R)
    if ltype == "flip":
        score += 1
        notes.append("Flip zone (was both S and R) (+1)")

    # Grade
    if score >= 8:
        grade, grade_c = "STRONG",   "#3fb950"
    elif score >= 5:
        grade, grade_c = "MODERATE", "#d29922"
    else:
        grade, grade_c = "WEAK",     "#8b949e"

    return {
        "dir":      signal_dir,
        "level":    lv,
        "ltype":    ltype,
        "tfs":      cluster["tfs"],
        "touches":  cluster["touches"],
        "score":    score,
        "grade":    grade,
        "grade_c":  grade_c,
        "notes":    notes,
        "last_c":   last,
        "rsi":      rsi,
        "macd":     macd,
        "price":    price,
    }

def calc_trade_params(sig, clusters):
    """Calculate entry, stop loss, and targets from cluster list."""
    price     = sig["price"]
    lv        = sig["level"]
    last_c    = sig["last_c"]
    wick_low  = last_c["low"]
    wick_high = last_c["high"]

    if sig["dir"] == "LONG":
        entry  = round(price, 2)
        stop   = round(wick_low - (lv * 0.003), 2)   # 0.3% below the wick low
        res    = sorted([cl["price"] for cl in clusters
                         if cl["type"] in ("resistance","flip") and cl["price"] > price])
        targets = res[:3] if res else [round(price * 1.03, 2),
                                       round(price * 1.05, 2),
                                       round(price * 1.08, 2)]
    else:
        entry  = round(price, 2)
        stop   = round(wick_high + (lv * 0.003), 2)  # 0.3% above the wick high
        sup    = sorted([cl["price"] for cl in clusters
                         if cl["type"] in ("support","flip") and cl["price"] < price],
                        reverse=True)
        targets = sup[:3] if sup else [round(price * 0.97, 2),
                                       round(price * 0.95, 2),
                                       round(price * 0.92, 2)]

    rr = None
    if targets and stop and entry != stop:
        risk   = abs(entry - stop)
        reward = abs(targets[0] - entry)
        rr     = round(reward / risk, 1) if risk > 0 else None

    return {"entry": entry, "stop": stop, "targets": targets, "rr": rr}


# ─── HTML ─────────────────────────────────────────────────

def _rsi_color(rsi, direction):
    if rsi is None: return "#8b949e"
    if direction == "LONG":
        if rsi <= 35: return "#3fb950"
        if rsi <= 50: return "#d29922"
        return "#f85149"
    else:
        if rsi >= 65: return "#f85149"
        if rsi >= 50: return "#d29922"
        return "#3fb950"

def signal_card(sig, trade):
    dir_c  = "#3fb950" if sig["dir"] == "LONG" else "#f85149"
    icon   = "&#9650;" if sig["dir"] == "LONG" else "&#9660;"
    ltype  = sig["ltype"].upper()
    rsi_c  = _rsi_color(sig["rsi"], sig["dir"])
    macd   = sig["macd"]
    macd_s = f"{macd['hist']:+.4f} ({'rising' if macd['hist']>macd['hist_prev'] else 'falling'})" if macd else "N/A"

    targets_html = "".join(
        f"<div style='background:#21262d;border-radius:5px;padding:8px 12px;text-align:center;flex:1;min-width:70px;'>"
        f"<div style='color:#8b949e;font-size:10px;'>T{i+1}</div>"
        f"<div style='color:#3fb950;font-size:14px;font-weight:bold;'>${t:.2f}</div>"
        f"<div style='color:#8b949e;font-size:10px;'>RR {round(abs(t-trade['entry'])/abs(trade['entry']-trade['stop']),1) if trade['stop']!=trade['entry'] else '-'}R</div>"
        f"</div>" for i, t in enumerate(trade["targets"])
    ) if trade["targets"] else ""

    notes_html = "".join(
        f"<div style='color:#8b949e;font-size:12px;padding:2px 0;border-bottom:1px solid #21262d;'>&#10003; {n}</div>"
        for n in sig["notes"]
    )

    score_bar_pct = min(sig["score"] * 7, 100)

    return f"""
<div style='background:#161b22;border:1px solid {sig["grade_c"]};border-radius:8px;margin:12px 0;overflow:hidden;'>
  <!-- card header -->
  <div style='background:#21262d;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;'>
    <div>
      <span style='font-size:22px;font-weight:bold;color:{dir_c};'>{icon} {sig["dir"]}</span>
      <span style='color:#8b949e;font-size:14px;margin-left:10px;'>@ ${sig["level"]:.2f} {ltype}</span>
    </div>
    <div style='display:flex;gap:8px;align-items:center;'>
      <span style='background:{sig["grade_c"]};color:#000;padding:4px 14px;border-radius:12px;font-size:13px;font-weight:bold;'>{sig["grade"]}</span>
      <span style='background:#21262d;border:1px solid #30363d;color:#f0f6fc;padding:4px 10px;border-radius:10px;font-size:13px;'>Score: {sig["score"]}</span>
    </div>
  </div>
  <!-- score bar -->
  <div style='background:#0d1117;height:4px;'>
    <div style='height:4px;width:{score_bar_pct}%;background:{sig["grade_c"]};'></div>
  </div>
  <div style='padding:16px;'>
    <!-- price snapshot -->
    <div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;'>
      <div style='background:#21262d;border-radius:6px;padding:10px 14px;flex:1;min-width:100px;'>
        <div style='color:#8b949e;font-size:10px;'>ENTRY (now)</div>
        <div style='color:#f0f6fc;font-size:18px;font-weight:bold;'>${trade["entry"]:.2f}</div>
      </div>
      <div style='background:#21262d;border-radius:6px;padding:10px 14px;flex:1;min-width:100px;'>
        <div style='color:#8b949e;font-size:10px;'>STOP LOSS</div>
        <div style='color:#f85149;font-size:18px;font-weight:bold;'>${trade["stop"]:.2f}</div>
        <div style='color:#8b949e;font-size:11px;'>-{abs(trade["entry"]-trade["stop"]):.2f} ({abs(trade["entry"]-trade["stop"])/trade["entry"]*100:.1f}%)</div>
      </div>
      <div style='background:#21262d;border-radius:6px;padding:10px 14px;flex:1;min-width:80px;'>
        <div style='color:#8b949e;font-size:10px;'>R:R (T1)</div>
        <div style='color:{dir_c};font-size:18px;font-weight:bold;'>{trade["rr"]}R</div>
      </div>
      <div style='background:#21262d;border-radius:6px;padding:10px 14px;flex:1;min-width:80px;'>
        <div style='color:#8b949e;font-size:10px;'>RSI 1H</div>
        <div style='color:{rsi_c};font-size:18px;font-weight:bold;'>{sig["rsi"] if sig["rsi"] else "N/A"}</div>
      </div>
    </div>
    <!-- targets -->
    <div style='color:#8b949e;font-size:10px;text-transform:uppercase;margin-bottom:6px;'>Targets</div>
    <div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;'>{targets_html}</div>
    <!-- MACD -->
    <div style='background:#21262d;border-radius:6px;padding:10px;margin-bottom:12px;font-size:12px;color:#8b949e;'>
      MACD Histogram: <span style='color:{"#3fb950" if macd and macd["hist"]>macd["hist_prev"] else "#f85149"};font-weight:bold;'>{macd_s}</span>
      &nbsp;&nbsp;|&nbsp;&nbsp;
      Level TFs: <span style='color:#58a6ff;'>{" + ".join(sig["tfs"])}</span>
      &nbsp;&nbsp;|&nbsp;&nbsp;
      Tested: <span style='color:#f0f6fc;'>{sig["touches"]}x</span>
    </div>
    <!-- confirmation notes -->
    <div style='color:#8b949e;font-size:10px;text-transform:uppercase;margin-bottom:6px;'>Confirmation checklist</div>
    {notes_html}
  </div>
</div>"""


def levels_table(clusters, price):
    rows = ""
    for cl in clusters:
        dist   = ((cl["price"] - price) / price) * 100
        tc     = "#3fb950" if cl["type"] == "support" else ("#f85149" if cl["type"] == "resistance" else "#d29922")
        tlabel = cl["type"].upper()
        tfs    = " + ".join(cl["tfs"])
        rows  += (f"<tr>"
                  f"<td style='color:{tc};font-weight:bold;padding:6px 8px;'>${cl['price']:.2f}</td>"
                  f"<td style='color:{tc};padding:6px 8px;'>{tlabel}</td>"
                  f"<td style='color:#8b949e;padding:6px 8px;'>{tfs}</td>"
                  f"<td style='color:#f0f6fc;padding:6px 8px;'>{cl['touches']}x</td>"
                  f"<td style='color:{'#3fb950' if dist>=0 else '#f85149'};padding:6px 8px;'>{dist:+.1f}%</td>"
                  f"</tr>")
    return f"""
<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0;overflow-x:auto;'>
  <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:10px;'>All Key S/R Levels (within 15% of price)</div>
  <table style='width:100%;border-collapse:collapse;font-size:13px;'>
    <tr style='color:#8b949e;font-size:11px;border-bottom:1px solid #30363d;'>
      <th style='padding:4px 8px;text-align:left;'>Level</th>
      <th style='padding:4px 8px;text-align:left;'>Type</th>
      <th style='padding:4px 8px;text-align:left;'>Timeframes</th>
      <th style='padding:4px 8px;text-align:left;'>Touches</th>
      <th style='padding:4px 8px;text-align:left;'>From Price</th>
    </tr>
    {rows}
  </table>
</div>"""


def generate_html(signals, clusters, stats, indicators, now):
    price  = float(stats["lastPrice"])
    chg    = float(stats["priceChangePercent"])
    chg_s  = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
    chg_c  = "#3fb950" if chg >= 0 else "#f85149"
    src    = "Kraken" if IS_GITHUB else "Binance"

    strong   = [s for s in signals if s["grade"] == "STRONG"]
    moderate = [s for s in signals if s["grade"] == "MODERATE"]

    banner = ""
    if strong:
        banner = f"""
<div style='background:#1b2d1b;border:1px solid #3fb950;border-radius:8px;padding:14px 16px;margin:12px 0;'>
  <span style='color:#3fb950;font-weight:bold;font-size:15px;'>STRONG SIGNAL{'S' if len(strong)>1 else ''} DETECTED ({len(strong)})</span>
  <span style='color:#8b949e;font-size:13px;margin-left:12px;'>High-confidence retest confirmed.</span>
</div>"""
    elif moderate:
        banner = f"""
<div style='background:#2d2a1b;border:1px solid #d29922;border-radius:8px;padding:14px 16px;margin:12px 0;'>
  <span style='color:#d29922;font-weight:bold;font-size:15px;'>MODERATE SIGNAL{'S' if len(moderate)>1 else ''} ({len(moderate)})</span>
  <span style='color:#8b949e;font-size:13px;margin-left:12px;'>Review carefully before trading.</span>
</div>"""
    else:
        banner = """
<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;margin:12px 0;'>
  <span style='color:#8b949e;font-size:15px;'>No confirmed retests this hour.</span>
  <span style='color:#8b949e;font-size:13px;margin-left:12px;'>Levels table updated below — monitor for next hourly check.</span>
</div>"""

    signal_cards = "".join(signal_card(s, calc_trade_params(s, clusters)) for s in signals) \
                   if signals else ""

    # Indicator summary row
    ind_html = ""
    for tf_label, ind in indicators.items():
        rsi = ind["rsi"]
        macd = ind["macd"]
        rc = "#3fb950" if rsi and rsi < 40 else ("#f85149" if rsi and rsi > 60 else "#d29922")
        mc_c = "#3fb950" if macd and macd["hist"] > macd["hist_prev"] else "#f85149"
        ind_html += (f"<div style='background:#21262d;border-radius:6px;padding:10px;flex:1;min-width:100px;text-align:center;'>"
                     f"<div style='color:#8b949e;font-size:10px;'>{tf_label}</div>"
                     f"<div style='color:{rc};font-weight:bold;font-size:14px;'>RSI {rsi if rsi else 'N/A'}</div>"
                     f"<div style='color:{mc_c};font-size:11px;'>MACD {'&#9650;' if macd and macd['hist']>macd['hist_prev'] else '&#9660;'}</div>"
                     f"</div>")

    return f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>SOL S/R Retest Alert — {now}</title>
</head>
<body style='margin:0;padding:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'>
<div style='max-width:800px;margin:0 auto;padding:16px;'>

  <!-- HEADER -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:12px;'>
    <div style='color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>SOL/USDT — S/R Retest Scanner</div>
    <div style='display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px;margin-top:6px;'>
      <div>
        <span style='font-size:38px;font-weight:bold;color:#f0f6fc;'>${price:.2f}</span>
        <span style='font-size:16px;color:{chg_c};margin-left:10px;'>{chg_s} (24h)</span>
      </div>
      <div style='text-align:right;color:#8b949e;font-size:12px;'>{now}<br>{src} API</div>
    </div>
  </div>

  {banner}

  <!-- SIGNAL CARDS -->
  {signal_cards if signal_cards else ""}

  <!-- INDICATOR SUMMARY -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0;'>
    <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:10px;'>RSI + MACD Snapshot</div>
    <div style='display:flex;gap:8px;flex-wrap:wrap;'>{ind_html}</div>
  </div>

  <!-- LEVELS TABLE -->
  {levels_table(clusters, price)}

  <!-- RULES -->
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-top:12px;'>
    <div style='color:#8b949e;font-size:12px;text-transform:uppercase;margin-bottom:8px;'>S/R Retest Rules</div>
    {"".join(f"<div style='color:#e6edf3;font-size:13px;padding:3px 0;border-bottom:1px solid #21262d;'>&#10003; {r}</div>" for r in [
        "Wait for the 1H candle to fully CLOSE before acting — never jump in mid-candle.",
        "LONG: wick touched support zone, candle closed above it = confirmed retest.",
        "SHORT: wick touched resistance zone, candle closed below it = confirmed retest.",
        "Only trade STRONG (8+) signals. MODERATE (5-7) = smaller size or skip.",
        "Stop loss always goes below the retest wick (not below the S/R level itself).",
        "Multi-TF confluence (level on 2+ timeframes) = strongest setups. Prioritise these.",
        "RSI oversold at support + MACD turning up = maximum conviction for LONG.",
        "RSI overbought at resistance + MACD turning down = maximum conviction for SHORT.",
        "Never risk more than 1-2% of account per trade regardless of signal strength.",
    ])}
  </div>

  <div style='text-align:center;color:#8b949e;font-size:11px;margin-top:16px;padding:16px;'>
    S/R Retest Scanner &nbsp;|&nbsp; {src} API &nbsp;|&nbsp; {now}
  </div>
</div>
</body>
</html>"""


# ─── EMAIL + REPORT ───────────────────────────────────────

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
    print(f"[{now}] S/R Retest Scanner — {src}")

    stats = get_stats()
    price = float(stats["lastPrice"])
    print(f"  Price: ${price:.2f}")

    # Fetch candles
    print("  Fetching candles...")
    w_c   = get_candles("W",  100)
    d_c   = get_candles("D",  200)
    h4_c  = get_candles("4H", 200)
    h1_c  = get_candles("1H", 300)

    # Build S/R levels from all timeframes
    print("  Building S/R levels...")
    raw_levels = (find_swing_levels(w_c,  "W",  lookback=3) +
                  find_swing_levels(d_c,  "D",  lookback=5) +
                  find_swing_levels(h4_c, "4H", lookback=5) +
                  find_swing_levels(h1_c, "1H", lookback=5))
    clusters = cluster_levels(raw_levels, price)
    print(f"  Found {len(clusters)} S/R clusters near price")

    # Indicators on 1H (primary confirmation timeframe)
    rsi_1h  = calculate_rsi(h1_c)
    macd_1h = calculate_macd(h1_c)

    # Indicator snapshot for all timeframes (report display)
    indicators = {
        "Weekly": {"rsi": calculate_rsi(w_c),  "macd": calculate_macd(w_c)},
        "Daily":  {"rsi": calculate_rsi(d_c),  "macd": calculate_macd(d_c)},
        "4H":     {"rsi": calculate_rsi(h4_c), "macd": calculate_macd(h4_c)},
        "1H":     {"rsi": rsi_1h,              "macd": macd_1h},
    }

    # Scan every cluster for a confirmed retest
    print("  Scanning for retests on last closed 1H candle...")
    all_signals = []
    for cl in clusters:
        sig = check_retest(cl, h1_c, rsi_1h, macd_1h)
        if sig and sig["grade"] != "WEAK":
            all_signals.append(sig)

    # Sort: strongest first
    all_signals.sort(key=lambda x: x["score"], reverse=True)
    print(f"  Signals found: {len(all_signals)} (STRONG: {sum(1 for s in all_signals if s['grade']=='STRONG')}, MODERATE: {sum(1 for s in all_signals if s['grade']=='MODERATE')})")

    # Generate HTML
    html = generate_html(all_signals, clusters, stats, indicators, now)

    # Always save report
    print("  Saving report -> docs/sr_report.html")
    save_report(html)

    # Only email if signals exist
    if all_signals:
        strongest = all_signals[0]
        grade_tag = f"[{strongest['grade']}]" if strongest else ""
        subject = (f"SOL S/R {grade_tag} {strongest['dir']} @ ${strongest['level']:.2f} "
                   f"| Score {strongest['score']} | ${price:.2f} | {now}")
        print(f"  Sending email: {subject}")
        send_email(subject, html)
    else:
        print("  No signals — report saved, no email sent.")

    print(f"[{now}] Done.")


if __name__ == "__main__":
    main()
