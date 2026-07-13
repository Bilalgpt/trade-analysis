"""
SOL/USDT Sweep -> CHoCH -> Retrace Detector (Phase 1 — dry run)
================================================================
Implements the strategy from analysis/sweep_choch_strategy_analysis.txt

Sequence detected on 4H (bearish example, longs mirrored):
  G1  Equal highs pool (2+ swing highs within tolerance, untaken)
  G2  Sweep: wick beyond pool, candle CLOSES back inside
  G3  CHoCH: full BODY close below protected swing low, within 12
      candles of the sweep + displacement check
  G4  Direction agrees with Daily bias (or Daily is neutral)
  G5  Price retracing into the 50-79% zone of the sweep->CHoCH leg

Alerts fire at exactly two moments per setup:
  ARMED : CHoCH confirmed -> "setup arming, zone X-Y"
  ENTRY : price inside zone + 15M trigger -> "ENTRY signal, score N"

Dry-run mode: emails OFF by default (set SEND_EMAIL=1 to enable).
State persists in state/sweep_state.json so each setup alerts once.
Report saved to docs/sweep_report.html.
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
SEND_EMAIL     = os.environ.get("SEND_EMAIL", "0") == "1"   # dry-run default

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(BASE_DIR, "docs",  "sweep_report.html")
STATE_PATH  = os.path.join(BASE_DIR, "state", "sweep_state.json")

# Strategy parameters (see analysis doc sections 4 & 6)
POOL_TOL         = 0.003    # 0.3% — equal highs/lows clustering tolerance
SWEEP_MAX_DEPTH  = 0.015    # >1.5% beyond pool = probably real breakout
CHOCH_MAX_DELAY  = 12       # CHoCH must occur within N 4H candles of sweep
DISPLACEMENT_X   = 1.5      # CHoCH body vs 20-candle avg body
ZONE_LO, ZONE_HI = 0.50, 0.79   # retrace entry window
OTE_LO           = 0.62     # deeper part of zone (bonus point)
STOP_BUFFER      = 0.004    # 0.4% beyond sweep wick
SCAN_WINDOW      = 80       # how many recent 4H candles to scan for setups
SWING_LOOKBACK   = 3        # fractal swing lookback on 4H
# ──────────────────────────────────────────────────────────


# ─── API (dual: Binance local / Kraken on GitHub) ─────────

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def get_candles_binance(interval, limit=300):
    url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval={interval}&limit={limit}"
    return [{"time": int(c[0])//1000, "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in fetch(url)]

def get_candles_kraken(interval, limit=300):
    url    = f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval={interval}"
    result = fetch(url)["result"]
    key    = [k for k in result if k != "last"][0]
    raw    = result[key][-limit:]
    return [{"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[6])} for c in raw]

def get_candles(tf, limit=300):
    if IS_GITHUB:
        m = {"D": 1440, "4H": 240, "1H": 60, "15M": 15}
        return get_candles_kraken(m[tf], limit)
    m = {"D": "1d", "4H": "4h", "1H": "1h", "15M": "15m"}
    return get_candles_binance(m[tf], limit)


# ─── INDICATORS / HELPERS ─────────────────────────────────

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

def market_structure(candles):
    data = candles[-30:]
    mid  = len(data) // 2
    rh, ph = max(c["high"] for c in data[mid:]), max(c["high"] for c in data[:mid])
    rl, pl = min(c["low"]  for c in data[mid:]), min(c["low"]  for c in data[:mid])
    if rh > ph and rl > pl: return "BULLISH"
    if rh < ph and rl < pl: return "BEARISH"
    return "RANGING"

def find_swings(candles, lookback=SWING_LOOKBACK):
    """Return (swing_highs, swing_lows) as lists of (index, price)."""
    highs, lows = [], []
    n = len(candles)
    for i in range(lookback, n - lookback):
        wh = [candles[i-lookback+k]["high"] for k in range(lookback*2+1) if k != lookback]
        wl = [candles[i-lookback+k]["low"]  for k in range(lookback*2+1) if k != lookback]
        if candles[i]["high"] >= max(wh): highs.append((i, candles[i]["high"]))
        if candles[i]["low"]  <= min(wl): lows.append((i, candles[i]["low"]))
    return highs, lows

def avg_body(candles, upto_idx, period=20):
    lo = max(0, upto_idx - period)
    bodies = [abs(c["close"] - c["open"]) for c in candles[lo:upto_idx]]
    return (sum(bodies) / len(bodies)) if bodies else 0

def avg_volume(candles, upto_idx, period=20):
    lo = max(0, upto_idx - period)
    vols = [c["volume"] for c in candles[lo:upto_idx]]
    return (sum(vols) / len(vols)) if vols else 1

def left_fvg(candles, i):
    """Did candle i leave an FVG? Returns (lo, hi) of the gap or None."""
    if i < 1 or i + 1 >= len(candles):
        return None
    if candles[i-1]["high"] < candles[i+1]["low"]:            # bullish FVG
        return (candles[i-1]["high"], candles[i+1]["low"])
    if candles[i-1]["low"] > candles[i+1]["high"]:            # bearish FVG
        return (candles[i+1]["high"], candles[i-1]["low"])
    return None

def candle_pattern(c):
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
    total = h - l
    if total < 1e-9:
        return None
    body  = abs(cl - o) / total
    upper = (h - max(cl, o)) / total
    lower = (min(cl, o) - l) / total
    if body < 0.35 and lower >= 0.55 and upper < 0.15:
        return ("Hammer/Pin", "bullish")
    if body < 0.35 and upper >= 0.55 and lower < 0.15:
        return ("Shooting Star/Pin", "bearish")
    if body >= 0.70:
        return ("Strong Candle", "bullish" if cl > o else "bearish")
    return None

def engulfing(prev, curr):
    pb, cb = abs(prev["close"] - prev["open"]), abs(curr["close"] - curr["open"])
    if (prev["close"] < prev["open"] and curr["close"] > curr["open"]
            and curr["close"] >= prev["open"] and curr["open"] <= prev["close"] and cb >= pb * 0.9):
        return ("Bullish Engulfing", "bullish")
    if (prev["close"] > prev["open"] and curr["close"] < curr["open"]
            and curr["close"] <= prev["open"] and curr["open"] >= prev["close"] and cb >= pb * 0.9):
        return ("Bearish Engulfing", "bearish")
    return None


# ─── POOL DETECTION (G1) ──────────────────────────────────

def find_pools(swings, kind):
    """
    Cluster swing points (list of (idx, price)) within POOL_TOL.
    kind = 'high' or 'low'. Returns pools with 2+ touches.
    """
    pools = []
    for idx, price in swings:
        placed = False
        for p in pools:
            if abs(price - p["level"]) / p["level"] <= POOL_TOL:
                p["members"].append((idx, price))
                # pool level = extreme of the cluster (where the stops actually sit)
                p["level"] = max(p["level"], price) if kind == "high" else min(p["level"], price)
                placed = True
                break
        if not placed:
            pools.append({"level": price, "members": [(idx, price)], "kind": kind})
    return [p for p in pools if len(p["members"]) >= 2]


# ─── SETUP DETECTION (state machine per run) ──────────────

def detect_setups(h4, daily_bias):
    """
    Scan recent 4H candles for the full sequence.
    Returns list of setup dicts with current stage.
    """
    n = len(h4)
    swings_h, swings_l = find_swings(h4)
    pools_high = find_pools(swings_h, "high")   # BSL -> bearish setups
    pools_low  = find_pools(swings_l, "low")    # SSL -> bullish setups
    setups = []
    scan_from = max(SWING_LOOKBACK, n - SCAN_WINDOW)

    for direction, pools, swings_protect in (
            ("SHORT", pools_high, swings_l),
            ("LONG",  pools_low,  swings_h)):

        for pool in pools:
            lvl        = pool["level"]
            last_touch = max(i for i, _ in pool["members"])

            # find sweep candle after the pool formed
            for i in range(max(scan_from, last_touch + 1), n):
                c = h4[i]
                if direction == "SHORT":
                    beyond = c["high"] > lvl
                    depth  = (c["high"] - lvl) / lvl
                    closed_back = c["close"] < lvl
                    # pool must be untaken before i (no close beyond level)
                    taken_before = any(h4[k]["close"] > lvl for k in range(last_touch + 1, i))
                else:
                    beyond = c["low"] < lvl
                    depth  = (lvl - c["low"]) / lvl
                    closed_back = c["close"] > lvl
                    taken_before = any(h4[k]["close"] < lvl for k in range(last_touch + 1, i))

                if not (beyond and closed_back) or taken_before:
                    continue
                if depth > SWEEP_MAX_DEPTH:
                    continue  # too deep — likely real breakout

                # G2 passed: sweep at index i
                sweep_idx = i
                sweep_ext = c["high"] if direction == "SHORT" else c["low"]

                # protected swing (most recent opposite swing before sweep)
                prot = [(k, p) for k, p in swings_protect if k < sweep_idx]
                if not prot:
                    continue
                prot_idx, prot_price = prot[-1]

                # G3: CHoCH — body close beyond protected swing within window
                choch_idx = None
                for j in range(sweep_idx + 1, min(sweep_idx + 1 + CHOCH_MAX_DELAY, n)):
                    if direction == "SHORT" and h4[j]["close"] < prot_price and h4[j]["close"] < h4[j]["open"]:
                        choch_idx = j; break
                    if direction == "LONG" and h4[j]["close"] > prot_price and h4[j]["close"] > h4[j]["open"]:
                        choch_idx = j; break
                if choch_idx is None:
                    continue

                # displacement check
                body_j = abs(h4[choch_idx]["close"] - h4[choch_idx]["open"])
                displaced = body_j > DISPLACEMENT_X * avg_body(h4, choch_idx)
                fvg = left_fvg(h4, choch_idx) if choch_idx + 1 < n else None

                # invalidation: any body close beyond sweep extreme after sweep
                invalid = False
                for k in range(sweep_idx + 1, n):
                    if direction == "SHORT" and h4[k]["close"] > sweep_ext:
                        invalid = True; break
                    if direction == "LONG" and h4[k]["close"] < sweep_ext:
                        invalid = True; break
                if invalid:
                    continue

                # G4: daily bias agreement
                bias_ok = (daily_bias == "RANGING" or
                           (direction == "SHORT" and daily_bias == "BEARISH") or
                           (direction == "LONG"  and daily_bias == "BULLISH"))

                # leg + retrace zone
                post = h4[choch_idx:]
                if direction == "SHORT":
                    leg_ext = min(x["low"] for x in post)        # leg low
                    leg_rng = sweep_ext - leg_ext
                    zone_a  = leg_ext + leg_rng * ZONE_LO
                    zone_b  = leg_ext + leg_rng * ZONE_HI
                    stop    = round(sweep_ext * (1 + STOP_BUFFER), 2)
                else:
                    leg_ext = max(x["high"] for x in post)       # leg high
                    leg_rng = leg_ext - sweep_ext
                    zone_a  = leg_ext - leg_rng * ZONE_HI
                    zone_b  = leg_ext - leg_rng * ZONE_LO
                    stop    = round(sweep_ext * (1 - STOP_BUFFER), 2)
                if leg_rng <= 0:
                    continue

                price = h4[-1]["close"]
                if direction == "SHORT":
                    r = (price - leg_ext) / leg_rng
                else:
                    r = (leg_ext - price) / leg_rng
                in_zone     = ZONE_LO <= r <= ZONE_HI
                zone_passed = r > ZONE_HI + 0.05  # retraced too deep

                setups.append({
                    "id":         f"{direction}_{h4[sweep_idx]['time']}",
                    "dir":        direction,
                    "pool_level": round(lvl, 2),
                    "pool_touches": len(pool["members"]),
                    "sweep_idx":  sweep_idx,
                    "sweep_time": h4[sweep_idx]["time"],
                    "sweep_ext":  round(sweep_ext, 2),
                    "sweep_depth": round(depth * 100, 2),
                    "sweep_close_strong": _sweep_close_strong(h4[sweep_idx], direction),
                    "sweep_vol_spike": h4[sweep_idx]["volume"] > 1.3 * avg_volume(h4, sweep_idx),
                    "prot_price": round(prot_price, 2),
                    "choch_idx":  choch_idx,
                    "choch_time": h4[choch_idx]["time"],
                    "displaced":  displaced,
                    "fvg":        [round(fvg[0],2), round(fvg[1],2)] if fvg else None,
                    "bias_ok":    bias_ok,
                    "leg_ext":    round(leg_ext, 2),
                    "zone":       [round(zone_a, 2), round(zone_b, 2)],
                    "stop":       stop,
                    "retrace_r":  round(r, 3),
                    "in_zone":    in_zone,
                    "zone_passed": zone_passed,
                    "price":      price,
                    "rsi_at_sweep": calculate_rsi(h4[:sweep_idx + 1]),
                })
                break  # one setup per pool (first valid sweep)
    return setups

def _sweep_close_strong(c, direction):
    rng = c["high"] - c["low"]
    if rng < 1e-9:
        return False
    pos = (c["close"] - c["low"]) / rng
    return pos <= 0.33 if direction == "SHORT" else pos >= 0.67


# ─── 15M TRIGGER (part of G5/ENTRY) ───────────────────────

def check_15m_trigger(m15, direction, zone):
    """Micro-rejection inside the zone on the last closed 15M candles."""
    za, zb = min(zone), max(zone)
    for c_prev, c in ((m15[-3], m15[-2]), (m15[-2], m15[-1])):
        touches_zone = c["low"] <= zb and c["high"] >= za
        if not touches_zone:
            continue
        want = "bearish" if direction == "SHORT" else "bullish"
        pat = candle_pattern(c)
        if pat and pat[1] == want:
            return pat[0]
        eng = engulfing(c_prev, c)
        if eng and eng[1] == want:
            return eng[0]
    return None


# ─── SCORING (section 6 of analysis doc) ──────────────────

def score_setup(s, targets, trigger):
    pts, notes = 0, []
    if s["displaced"]:
        pts += 3; notes.append("Displacement CHoCH (+3)")
        if s["fvg"]:
            notes[-1] = "Displacement CHoCH leaving FVG (+3)"
    if s["sweep_vol_spike"]:
        pts += 2; notes.append("Sweep volume spike (+2)")
    if s["fvg"] and _overlaps(s["fvg"], s["zone"]):
        pts += 2; notes.append("Entry zone overlaps FVG (+2)")
    if s["pool_touches"] >= 3:
        pts += 2; notes.append(f"Pool had {s['pool_touches']} touches (+2)")
    if trigger:
        pts += 2; notes.append(f"15M trigger: {trigger} (+2)")
    if s["sweep_close_strong"]:
        pts += 1; notes.append("Sweep closed in outer 1/3 (+1)")
    rsi = s["rsi_at_sweep"]
    if rsi is not None and ((s["dir"] == "SHORT" and rsi > 65) or (s["dir"] == "LONG" and rsi < 35)):
        pts += 1; notes.append(f"RSI extreme at sweep ({rsi}) (+1)")
    if (s["dir"] == "SHORT" and s["retrace_r"] >= OTE_LO) or \
       (s["dir"] == "LONG"  and s["retrace_r"] >= OTE_LO):
        pts += 1; notes.append("Retrace reached OTE part of zone (+1)")
    # R:R from zone midpoint to first target
    mid = sum(s["zone"]) / 2
    if targets:
        risk = abs(mid - s["stop"])
        rew  = abs(targets[0] - mid)
        rr   = round(rew / risk, 1) if risk > 0 else 0
        if rr >= 2.5:
            pts += 1; notes.append(f"R:R to T1 = {rr} (+1)")
    else:
        rr = None
    grade = "STRONG" if pts >= 10 else ("MODERATE" if pts >= 7 else "LOW")
    return pts, grade, notes, (rr if targets else None)

def _overlaps(a, b):
    return max(a[0], b[0]) <= min(a[1], b[1])

def find_targets(h4, direction, price):
    swings_h, swings_l = find_swings(h4)
    if direction == "SHORT":
        below = sorted({round(p, 2) for _, p in swings_l if p < price}, reverse=True)
        return below[:3]
    above = sorted({round(p, 2) for _, p in swings_h if p > price})
    return above[:3]


# ─── STATE (persist alert stages between runs) ────────────

def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerted": {}}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    # prune entries older than 30 days
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    state["alerted"] = {k: v for k, v in state["alerted"].items()
                        if int(k.split("_")[1]) > cutoff}
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ─── HTML REPORT ──────────────────────────────────────────

def setup_card(s, targets, pts, grade, notes, rr, stage, trigger):
    dir_c   = "#f85149" if s["dir"] == "SHORT" else "#3fb950"
    icon    = "&#9660;" if s["dir"] == "SHORT" else "&#9650;"
    stage_c = {"ENTRY": "#3fb950", "IN_ZONE": "#d29922",
               "AWAITING_RETRACE": "#58a6ff", "ZONE_PASSED": "#8b949e"}.get(stage, "#8b949e")
    grade_c = {"STRONG": "#3fb950", "MODERATE": "#d29922", "LOW": "#8b949e"}[grade]
    ts      = datetime.fromtimestamp(s["sweep_time"], tz=timezone.utc).strftime("%m-%d %H:%M")
    tgt_html = " ".join(f"<span style='color:#3fb950;font-weight:bold;'>${t:.2f}</span>" for t in targets) or "—"
    notes_html = "".join(f"<div style='color:#8b949e;font-size:12px;padding:2px 0;'>&#10003; {x}</div>" for x in notes)
    gates = [("G1 Pool", True), ("G2 Sweep", True), ("G3 CHoCH", True),
             ("G4 Daily bias", s["bias_ok"]), ("G5 In zone", s["in_zone"])]
    gates_html = " ".join(
        f"<span style='background:#21262d;border-radius:4px;padding:2px 8px;font-size:11px;"
        f"color:{'#3fb950' if ok else '#f85149'};'>{'&#10003;' if ok else '&#10007;'} {g}</span>"
        for g, ok in gates)
    return f"""
<div style='background:#161b22;border:1px solid {stage_c};border-radius:8px;margin:12px 0;padding:16px;'>
  <div style='display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px;'>
    <div>
      <span style='font-size:20px;font-weight:bold;color:{dir_c};'>{icon} {s["dir"]}</span>
      <span style='color:#8b949e;font-size:13px;margin-left:8px;'>pool ${s["pool_level"]:.2f} swept {ts} UTC</span>
    </div>
    <div>
      <span style='background:{stage_c};color:#000;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:bold;'>{stage.replace("_"," ")}</span>
      <span style='background:{grade_c};color:#000;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:bold;margin-left:6px;'>{grade} {pts}</span>
    </div>
  </div>
  <div style='margin-bottom:10px;'>{gates_html}</div>
  <div style='display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;'>
    <div style='background:#21262d;border-radius:6px;padding:8px 12px;'><div style='color:#8b949e;font-size:10px;'>ENTRY ZONE</div>
      <div style='color:#58a6ff;font-weight:bold;'>${s["zone"][0]:.2f} — ${s["zone"][1]:.2f}</div></div>
    <div style='background:#21262d;border-radius:6px;padding:8px 12px;'><div style='color:#8b949e;font-size:10px;'>STOP</div>
      <div style='color:#f85149;font-weight:bold;'>${s["stop"]:.2f}</div></div>
    <div style='background:#21262d;border-radius:6px;padding:8px 12px;'><div style='color:#8b949e;font-size:10px;'>TARGETS</div>
      <div>{tgt_html}</div></div>
    <div style='background:#21262d;border-radius:6px;padding:8px 12px;'><div style='color:#8b949e;font-size:10px;'>R:R (T1)</div>
      <div style='color:#f0f6fc;font-weight:bold;'>{rr if rr else "—"}</div></div>
    <div style='background:#21262d;border-radius:6px;padding:8px 12px;'><div style='color:#8b949e;font-size:10px;'>RETRACE</div>
      <div style='color:#f0f6fc;font-weight:bold;'>{s["retrace_r"]*100:.0f}%</div></div>
  </div>
  {f"<div style='background:#1b2d1b;border-left:3px solid #3fb950;padding:8px 12px;margin-bottom:10px;color:#3fb950;font-size:13px;'>15M TRIGGER: {trigger}</div>" if trigger else ""}
  {notes_html}
</div>"""

def generate_html(cards_html, price, daily_bias, now, n_setups):
    bias_c = {"BULLISH": "#3fb950", "BEARISH": "#f85149", "RANGING": "#d29922"}[daily_bias]
    body = cards_html or """
<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:12px 0;color:#8b949e;'>
  No active sweep+CHoCH setups. The detector requires: equal highs/lows pool -> sweep -> body-close CHoCH.
  Scanning continues every run.</div>"""
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>SOL Sweep+CHoCH Detector — {now}</title></head>
<body style='margin:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'>
<div style='max-width:800px;margin:0 auto;padding:16px;'>
  <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:12px;'>
    <div style='color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>SOL/USDT — Sweep &rarr; CHoCH &rarr; Retrace Detector</div>
    <div style='display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px;margin-top:6px;'>
      <span style='font-size:34px;font-weight:bold;color:#f0f6fc;'>${price:.2f}</span>
      <div style='text-align:right;font-size:12px;color:#8b949e;'>
        Daily bias: <span style='color:{bias_c};font-weight:bold;'>{daily_bias}</span><br>{now} | Setups: {n_setups}
      </div>
    </div>
  </div>
  {body}
  <div style='text-align:center;color:#8b949e;font-size:11px;padding:16px;'>Phase 1 dry-run | {"Kraken" if IS_GITHUB else "Binance"} API | {now}</div>
</div></body></html>"""


# ─── EMAIL ────────────────────────────────────────────────

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


# ─── MAIN ─────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now}] Sweep+CHoCH Detector ({'Kraken' if IS_GITHUB else 'Binance'}, email={'ON' if SEND_EMAIL else 'OFF/dry-run'})")

    d1  = get_candles("D",   120)
    h4  = get_candles("4H",  300)
    m15 = get_candles("15M", 100)
    price      = h4[-1]["close"]
    daily_bias = market_structure(d1)
    print(f"  Price ${price:.2f} | Daily bias: {daily_bias}")

    setups = detect_setups(h4, daily_bias)
    print(f"  Sequences found (G1-G3 passed): {len(setups)}")

    state   = load_state()
    alerted = state["alerted"]
    cards   = []
    mails   = []   # (stage, subject) to send

    for s in setups:
        targets = find_targets(h4, s["dir"], price)
        trigger = check_15m_trigger(m15, s["dir"], s["zone"]) if s["in_zone"] else None
        pts, grade, notes, rr = score_setup(s, targets, trigger)

        if s["zone_passed"]:
            stage = "ZONE_PASSED"
        elif s["in_zone"] and trigger and s["bias_ok"]:
            stage = "ENTRY"
        elif s["in_zone"]:
            stage = "IN_ZONE"
        else:
            stage = "AWAITING_RETRACE"

        print(f"  [{s['dir']}] pool ${s['pool_level']} | stage {stage} | score {pts} ({grade}) | "
              f"zone {s['zone'][0]}-{s['zone'][1]} | retrace {s['retrace_r']*100:.0f}% | bias_ok={s['bias_ok']}")

        cards.append(setup_card(s, targets, pts, grade, notes, rr, stage, trigger))

        # alert logic: once per setup per stage milestone
        prev = alerted.get(s["id"])
        if stage == "ENTRY" and prev != "ENTRY":
            alerted[s["id"]] = "ENTRY"
            mails.append(f"SOL SWEEP+CHOCH [ENTRY {grade}] {s['dir']} zone ${s['zone'][0]}-${s['zone'][1]} | score {pts} | ${price:.2f}")
        elif stage in ("AWAITING_RETRACE", "IN_ZONE") and prev is None and s["bias_ok"]:
            alerted[s["id"]] = "ARMED"
            mails.append(f"SOL SWEEP+CHOCH [ARMED] {s['dir']} setup — CHoCH confirmed, zone ${s['zone'][0]}-${s['zone'][1]} | ${price:.2f}")

    html = generate_html("".join(cards), price, daily_bias, now, len(setups))
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Report saved -> docs/sweep_report.html")

    save_state(state)

    for subject in mails:
        if SEND_EMAIL:
            print(f"  EMAIL: {subject}")
            send_email(subject, html)
        else:
            print(f"  [DRY-RUN ALERT] {subject}")

    if not mails:
        print("  No new alerts this run.")
    print(f"[{now}] Done.")


if __name__ == "__main__":
    main()
