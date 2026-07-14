"""
SOL/USDT Hourly Analysis v2 — CHART_ANALYSIS_RULES engine
=========================================================
Implements analysis/CHART_ANALYSIS_RULES.txt every hour:
S/R zones, liquidity pools + sweeps, retests, confluences, trendlines +
channels, structure/CHoCH, RSI/ATR/regime, DON-55 cross-check, BOTH-side
trade plans with trap scenarios, explicit WAIT verdict.

Sends HTML email + saves docs/analysis_v2.html (GitHub Pages).
Runs on GitHub Actions hourly (Kraken data) or locally (Binance).
Usage: python sol_hourly_analysis_v2.py [--dry-run]
"""

import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
IS_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"
DRY_RUN = "--dry-run" in sys.argv
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "analysis_v2.html")

DON_LEN = 55
ATR_LEN = 14
RSI_LEN = 14
POOL_TOL = 0.003
ZONE_TOL = 0.0075
SWEEP_LOOK = 10


# ── data ─────────────────────────────────────────────────────────────────────
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def candles_binance(interval, limit=400):
    raw = fetch(f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval={interval}&limit={limit}")
    return [{"time": int(c[0]) // 1000, "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])} for c in raw]


def candles_kraken(minutes, limit=400):
    res = fetch(f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval={minutes}")["result"]
    key = [k for k in res if k != "last"][0]
    raw = res[key][-limit:]
    return [{"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "volume": float(c[6])} for c in raw]


def get_candles(tf, limit=400):
    if IS_GITHUB:
        return candles_kraken({"1d": 1440, "4h": 240, "1h": 60}[tf], limit)
    return candles_binance(tf, limit)


# ── indicators ───────────────────────────────────────────────────────────────
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def atr(cs, n=ATR_LEN):
    trs = []
    for i in range(1, len(cs)):
        p = cs[i - 1]["close"]
        trs.append(max(cs[i]["high"] - cs[i]["low"], abs(cs[i]["high"] - p), abs(cs[i]["low"] - p)))
    return sum(trs[-n:]) / n if len(trs) >= n else None


def rsi_series(cs, n=RSI_LEN):
    closes = [c["close"] for c in cs]
    if len(closes) < n + 1:
        return [None] * len(closes)
    out = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def swings(cs, lb=3):
    hs, ls = [], []
    for i in range(lb, len(cs) - lb):
        win = cs[i - lb:i + lb + 1]
        if cs[i]["high"] == max(c["high"] for c in win):
            hs.append(i)
        if cs[i]["low"] == min(c["low"] for c in win):
            ls.append(i)
    return hs, ls


# ── checklist components ─────────────────────────────────────────────────────
def cluster_zones(levels, tol=ZONE_TOL):
    """levels: list of (price, idx). Returns zones sorted by touches."""
    zones = []
    for p, i in sorted(levels):
        for z in zones:
            if abs(p - z["mid"]) / z["mid"] <= tol:
                z["members"].append((p, i))
                z["mid"] = sum(x for x, _ in z["members"]) / len(z["members"])
                break
        else:
            zones.append({"mid": p, "members": [(p, i)]})
    for z in zones:
        z["lo"] = min(x for x, _ in z["members"])
        z["hi"] = max(x for x, _ in z["members"])
        z["touches"] = len(z["members"])
        z["last_idx"] = max(i for _, i in z["members"])
    return zones


def equal_pools(cs, sw_idx, kind, tol=POOL_TOL):
    """Clusters of >=2 swing levels within tol -> liquidity pools."""
    lvls = [(cs[i]["high"] if kind == "high" else cs[i]["low"], i) for i in sw_idx]
    return [z for z in cluster_zones(lvls, tol) if z["touches"] >= 2]


def recent_sweeps(cs, look=SWEEP_LOOK, last=30):
    out = []
    for i in range(max(look, len(cs) - last), len(cs)):
        lo = min(cs[i - k]["low"] for k in range(1, look + 1))
        hi = max(cs[i - k]["high"] for k in range(1, look + 1))
        if cs[i]["low"] < lo and cs[i]["close"] > lo:
            out.append(("low", i, lo, cs[i]["low"], cs[i]["close"]))
        if cs[i]["high"] > hi and cs[i]["close"] < hi:
            out.append(("high", i, hi, cs[i]["high"], cs[i]["close"]))
    return out


def structure_state(cs, sh, sl):
    """LH/LL vs HH/HL from last swings + CHoCH levels."""
    hs = [cs[i]["high"] for i in sh[-3:]]
    ls = [cs[i]["low"] for i in sl[-3:]]
    if len(hs) < 2 or len(ls) < 2:
        return "UNCLEAR", None, None
    up = hs[-1] > hs[-2] and ls[-1] > ls[-2]
    dn = hs[-1] < hs[-2] and ls[-1] < ls[-2]
    state = "UPTREND (HH/HL)" if up else "DOWNTREND (LH/LL)" if dn else "RANGE/MIXED"
    choch_up = hs[-1]   # close above last swing high breaks a downtrend
    choch_dn = ls[-1]   # close below last swing low breaks an uptrend
    return state, choch_up, choch_dn


def channel_read(cs, sh, sl):
    """Fit lines through last 3 swing highs / lows; classify channel."""
    if len(sh) < 3 or len(sl) < 3:
        return None
    def fit(pts):
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        den = sum((p[0] - mx) ** 2 for p in pts)
        if den == 0:
            return 0.0, my
        b = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
        return b, my - b * mx
    hp = [(i, cs[i]["high"]) for i in sh[-3:]]
    lp = [(i, cs[i]["low"]) for i in sl[-3:]]
    bh, ah = fit(hp)
    bl, al = fit(lp)
    t = len(cs) - 1
    upper, lower = bh * t + ah, bl * t + al
    if upper <= lower:
        return None
    price = cs[-1]["close"]
    pos = (price - lower) / (upper - lower)
    if bh > 0 and bl > 0:
        kind = "ASCENDING"
    elif bh < 0 and bl < 0:
        kind = "DESCENDING"
    else:
        kind = "RANGE/BOX"
    third = "lower third" if pos < 0.34 else "upper third" if pos > 0.66 else "middle"
    return {"kind": kind, "upper": upper, "lower": lower, "pos": pos, "third": third}


def divergence_check(cs, rsis, sl, sh):
    """Regular divergence on the last two swing lows/highs."""
    notes = []
    if len(sl) >= 2 and rsis[sl[-1]] and rsis[sl[-2]]:
        p1, p2 = cs[sl[-2]]["low"], cs[sl[-1]]["low"]
        r1, r2 = rsis[sl[-2]], rsis[sl[-1]]
        if p2 < p1 and r2 > r1:
            notes.append(f"BULLISH divergence: price LL {p2:.2f} vs {p1:.2f}, RSI {r2:.0f} > {r1:.0f}")
    if len(sh) >= 2 and rsis[sh[-1]] and rsis[sh[-2]]:
        p1, p2 = cs[sh[-2]]["high"], cs[sh[-1]]["high"]
        r1, r2 = rsis[sh[-2]], rsis[sh[-1]]
        if p2 > p1 and r2 < r1:
            notes.append(f"BEARISH divergence: price HH {p2:.2f} vs {p1:.2f}, RSI {r2:.0f} < {r1:.0f}")
    return notes


def confluence_factors(level, price_ctx):
    """Names of factors within 0.6% of level."""
    f = []
    for name, val in price_ctx.items():
        if val and abs(level - val) / level <= 0.006:
            f.append(name)
    if abs(level - round(level / 5) * 5) / level <= 0.004:
        f.append("round number")
    return f


# ── analysis + report ────────────────────────────────────────────────────────
def analyze():
    c4 = get_candles("4h", 400)
    c1d = get_candles("1d", 300)
    c1h = get_candles("1h", 200)
    price = c1h[-1]["close"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    closes4 = [c["close"] for c in c4]
    closes1d = [c["close"] for c in c1d]
    atr4 = atr(c4)
    rsi4s = rsi_series(c4)
    rsi4 = rsi4s[-1]
    sma50_4, sma100_4, sma200_4 = sma(closes4, 50), sma(closes4, 100), sma(closes4, 200)
    sma200_1d = sma(closes1d, 200)
    regime_1d = sma200_1d is not None and closes1d[-1] > sma200_1d

    # Donchian triggers (prior 55 bars)
    don_hi_1d = max(c["high"] for c in c1d[-(DON_LEN + 1):-1])
    don_hi_4h = max(c["high"] for c in c4[-(DON_LEN + 1):-1])
    don_lo_4h = min(c["low"] for c in c4[-(DON_LEN + 1):-1])

    sh, sl = swings(c4)
    res_zones = [z for z in cluster_zones([(c4[i]["high"], i) for i in sh]) if z["mid"] > price]
    sup_zones = [z for z in cluster_zones([(c4[i]["low"], i) for i in sl]) if z["mid"] < price]
    res_zones.sort(key=lambda z: z["mid"])
    sup_zones.sort(key=lambda z: -z["mid"])
    pools_hi = [z for z in equal_pools(c4, sh, "high") if z["mid"] > price]
    pools_lo = [z for z in equal_pools(c4, sl, "low") if z["mid"] < price * 1.005]
    sweeps = recent_sweeps(c4)
    struct, choch_up, choch_dn = structure_state(c4, sh, sl)
    chan = channel_read(c4, sh, sl)
    divs = divergence_check(c4, rsi4s, sl, sh)

    ctx = {"4H SMA50": sma50_4, "4H SMA100": sma100_4, "4H SMA200": sma200_4,
           "1D SMA200": sma200_1d, "DON55-4H high": don_hi_4h, "DON55-4H low": don_lo_4h}

    key_sup = sup_zones[0] if sup_zones else None
    key_res = res_zones[0] if res_zones else None

    # plans
    long_trig = don_hi_1d
    long_stop = long_trig - 2 * (atr(c1d) or 2 * atr4)
    early_long = choch_up
    short_trig = key_sup["lo"] if key_sup else None
    t_lo = [z["mid"] for z in sup_zones[1:3]]
    t_hi = [z["mid"] for z in res_zones if z["mid"] > long_trig * 1.01][:2]

    pool_lo_near_sup = key_sup and any(abs(p["mid"] - key_sup["mid"]) / key_sup["mid"] < 0.01 for p in pools_lo)
    pool_hi_near_trig = any(abs(p["mid"] - long_trig) / long_trig < 0.01 for p in pools_hi)

    d = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")

    # ── build report lines (plain-ish HTML) ──────────────────────────────
    P = []
    P.append(f"<h2>SOL/USDT — {now}</h2>")
    P.append(f"<p><b>Price {price:.2f}</b> | RSI(4H) {rsi4:.0f} | ATR(4H) {atr4:.2f} "
             f"({100*atr4/price:.1f}%) | 1D regime: {'UP (>SMA200)' if regime_1d else 'DOWN (<SMA200)'}</p>")

    P.append("<h3>1) Support / Resistance zones (4H swings)</h3><ul>")
    for z in res_zones[:4]:
        fac = confluence_factors(z["mid"], ctx)
        P.append(f"<li>RES {z['lo']:.2f}-{z['hi']:.2f} (touches {z['touches']}"
                 f"{', +' + ', '.join(fac) if fac else ''})</li>")
    for z in sup_zones[:4]:
        fac = confluence_factors(z["mid"], ctx)
        P.append(f"<li>SUP {z['lo']:.2f}-{z['hi']:.2f} (touches {z['touches']}"
                 f"{', +' + ', '.join(fac) if fac else ''})</li>")
    P.append("</ul>")

    P.append("<h3>2) Liquidity pools & sweeps</h3><ul>")
    for p in pools_hi[:3]:
        P.append(f"<li>Equal HIGHS ~{p['mid']:.2f} ({p['touches']} touches) — stops above</li>")
    for p in pools_lo[:3]:
        P.append(f"<li>Equal LOWS ~{p['mid']:.2f} ({p['touches']} touches) — stops below</li>")
    for kind, i, lvl, ext, cl in sweeps[-4:]:
        P.append(f"<li>SWEEP of {kind} {lvl:.2f} on {d(c4[i]['time'])} "
                 f"(wick {ext:.2f}, closed back {cl:.2f})</li>")
    if not sweeps:
        P.append("<li>No sweep bars in last 30 bars</li>")
    P.append("</ul>")

    P.append(f"<h3>3) Structure & channel</h3><ul><li>4H structure: <b>{struct}</b></li>")
    if choch_up:
        P.append(f"<li>CHoCH UP above {choch_up:.2f} | CHoCH DOWN below {choch_dn:.2f}</li>")
    if chan:
        P.append(f"<li>Channel: <b>{chan['kind']}</b>, boundaries now "
                 f"{chan['lower']:.2f} / {chan['upper']:.2f}, price in {chan['third']}</li>")
    for n in divs:
        P.append(f"<li><b>{n}</b></li>")
    P.append("</ul>")

    P.append("<h3>4) System cross-check (validated)</h3><ul>")
    P.append(f"<li>DON-55 1D trigger: close &gt; <b>{don_hi_1d:.2f}</b> "
             f"({100*(don_hi_1d-price)/price:+.1f}% away). Regime {'OK' if regime_1d else 'NOT OK — signal blocked'}.</li>")
    P.append(f"<li>DON-55 4H trigger: {don_hi_4h:.2f} ({100*(don_hi_4h-price)/price:+.1f}% away)</li></ul>")

    sig = regime_1d and price > don_hi_1d
    P.append("<h3>5) TRADE PLAN</h3>")
    P.append(f"<p><b>{'VALIDATED LONG SIGNAL ACTIVE — DON-55 breakout' if sig else 'No validated signal — WAIT'}</b></p>")
    P.append("<ul>")
    P.append(f"<li><b>LONG (system)</b>: 1D close &gt; {long_trig:.2f}; stop ~{long_stop:.2f}; "
             f"targets {', '.join(f'{x:.2f}' for x in t_hi) or 'open sky'}"
             f"{' — TRAP-PRONE: equal highs sit at trigger, require close through the full zone + follow-through' if pool_hi_near_trig else ''}</li>")
    if early_long and key_sup:
        P.append(f"<li><b>LONG (early, half-size)</b>: 4H close &gt; {early_long:.2f} (CHoCH"
                 f"{' + channel exit' if chan and chan['kind'] == 'DESCENDING' else ''}); "
                 f"stop under {key_sup['lo']:.2f}</li>")
    if short_trig:
        P.append(f"<li><b>SHORT (lower-confidence, small size)</b>: 4H close &lt; {short_trig:.2f}"
                 f"; stop above {key_sup['hi'] + atr4:.2f}; targets "
                 f"{', '.join(f'{x:.2f}' for x in t_lo) or 'prior lows'}"
                 f"{' — TRAP-PRONE: equal lows at level; a wick below that closes back above = BEAR TRAP -> aggressive long, stop under trap low' if pool_lo_near_sup else ''}</li>")
    P.append("</ul>")

    P.append("<h3>6) What to WATCH / WAIT for</h3><ol>")
    if key_sup:
        P.append(f"<li>Does support {key_sup['lo']:.2f}-{key_sup['hi']:.2f} hold on retest?</li>")
    if chan:
        P.append(f"<li>Channel exit: close beyond {chan['upper']:.2f} (up) / {chan['lower']:.2f} (down)</li>")
    if choch_up:
        P.append(f"<li>Structure break: close &gt; {choch_up:.2f} (bull) or &lt; {choch_dn:.2f} (bear)</li>")
    P.append(f"<li>The real entry: 1D close &gt; {don_hi_1d:.2f} (DON-55)</li></ol>")
    P.append("<p style='color:#888'>Auto-generated per analysis/CHART_ANALYSIS_RULES.txt. "
             "Shorts are lower-confidence (systematic tests failed). Not financial advice to anyone else.</p>")

    state = "SIGNAL" if sig else struct.split(" ")[0]
    subject = f"SOL {price:.2f} | {state} | RSI {rsi4:.0f} | hourly analysis"
    html = ("<html><body style='font-family:Segoe UI,Arial,sans-serif;max-width:720px'>"
            + "\n".join(P) + "</body></html>")
    return subject, html


def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())


def main():
    subject, html = analyze()
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(subject)
    if DRY_RUN:
        print("[dry-run] email not sent; report written to", REPORT_PATH)
        return
    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASSWORD):
        print("Missing EMAIL_* env vars; report saved only.")
        return
    try:
        send_email(subject, html)
        print("Email sent to", EMAIL_TO)
    except Exception as e:
        # never fail the workflow over mail: report still gets published
        print("EMAIL FAILED (report still published):", e)


if __name__ == "__main__":
    main()
