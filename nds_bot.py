# -*- coding: utf-8 -*-
"""
ربات NDS — رصد خودکار بازار از دید Nodal Displacement Sequencing
=================================================================
همان منطق اندیکاتور «NDS Pro» در TradingView:
  • سکانس نودها (1-2-3)، 1-2 های باز، هوک و سطح 86.4%، 123Flag
  • جهت از دو تایم‌فریم (پیش‌فرض 15 دقیقه و 4 ساعت)
  • «ناحیه خوش‌قیمت» و ماشه ورود (123Flag یا نود 3 داخل ناحیه)
  • انتخاب خودکار طول پیوت بر اساس بک‌تست کندل‌های اخیر (تطبیق با بازار)
  • پیام فارسی در تلگرام + کارنامه سیگنال‌ها در signals.csv

فقط از کتابخانه‌های استاندارد پایتون استفاده می‌کند (نیازی به pip نیست).
داده از API عمومی بایننس گرفته می‌شود (بدون کلید). هیچ سفارشی ثبت نمی‌کند.

اجرا:      python nds_bot.py
بک‌تست:    python nds_bot.py --backtest
تست پیام:  python nds_bot.py --test
"""
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.txt")
STATE_PATH = os.path.join(HERE, "state.json")
SIGNALS_PATH = os.path.join(HERE, "signals.csv")
LOG_PATH = os.path.join(HERE, "nds_bot.log")
OFFLINE_DIR = os.environ.get("NDS_OFFLINE")  # only used for testing with saved candles

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULTS = {
    "TELEGRAM_TOKEN": "",
    "CHAT_ID": "",
    "SYMBOLS": "ETHUSDT,BTCUSDT",
    "ENTRY_TF": "5m",
    "DIR_TF_A": "15m",
    "DIR_TF_B": "4h",
    "NEED_BOTH": "yes",
    "PIVOT": "auto",
    "PIVOT_CHOICES": "3,5,8",
    "TREND_PIVOT": "5",
    "ZONE_WIDTH_ATR": "0.6",
    "ZONE_MEMORY_BARS": "40",
    "TRIGGER_NODE3": "yes",
    "CHECK_SECONDS": "30",
    "REOPTIMIZE_HOURS": "6",
    "SEND_WAIT_MESSAGES": "yes",
    "MODE": "paper",
    "ACCOUNT_BALANCE": "1000",
    "RISK_PERCENT": "1",
    "MAX_LEVERAGE": "5",
    "MAX_OPEN_POSITIONS": "2",
    "DAILY_MAX_LOSS_PERCENT": "3",
    "PARTIAL_TP": "yes",
    "FEE_PERCENT": "0.04",
    "REPORT_EVERY_HOURS": "2",
    "CANDLES": "heikin_ashi",
    "MARKET": "futures",
    "SCAN": "yes",
    "SCAN_TOP": "30",
    "SCAN_REFRESH_HOURS": "6",
    "MIN_ATR_PCT": "0.05",
    "MAX_ATR_PCT": "3",
}

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
TF_FA = {"1m": "۱ دقیقه", "3m": "۳ دقیقه", "5m": "۵ دقیقه", "15m": "۱۵ دقیقه", "30m": "۳۰ دقیقه", "1h": "۱ ساعته", "2h": "۲ ساعته", "4h": "۴ ساعته", "1d": "روزانه"}


# ------------------------------------------------------------------ utilities
def log(msg):
    line = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip().upper()] = v.strip()
    return cfg


def save_config_value(key, value):
    lines = []
    found = False
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip().upper() == key:
            lines[i] = key + "=" + value
            found = True
    if not found:
        lines.append(key + "=" + value)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def yes(v):
    return str(v).strip().lower() in ("1", "yes", "y", "true", "on", "بله")


def fmt(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    a = abs(p)
    d = 2 if a >= 100 else 4 if a >= 1 else 6
    return f"{p:,.{d}f}"


def fa_dir(d):
    return "صعودی ↑" if d == 1 else "نزولی ↓" if d == -1 else "نامشخص"


# ------------------------------------------------------------------ market data (Binance public API)
BASES = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com"]


def http_json(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "nds-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


FAPI_BASES = ["https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com", "https://fapi3.binance.com"]
MARKET = {"name": "futures"}
STABLE_BASES = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USDE", "RLUSD", "USD1", "XUSD")


def get_top_symbols(n):
    """Most liquid USDT perpetual futures (or spot pairs) by 24h quote volume."""
    if OFFLINE_DIR:
        with open(os.path.join(OFFLINE_DIR, "_top.json")) as f:
            return json.load(f)[:n]
    last_err = None
    if MARKET["name"] == "futures":
        for base in FAPI_BASES:
            try:
                info = http_json(f"{base}/fapi/v1/exchangeInfo")
                ok = {x["symbol"] for x in info["symbols"] if x.get("contractType") == "PERPETUAL"
                      and x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT"}
                tick = http_json(f"{base}/fapi/v1/ticker/24hr")
                rows = [t for t in tick if t["symbol"] in ok and not t["symbol"].startswith(STABLE_BASES)]
                rows.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
                return [t["symbol"] for t in rows[:n]]
            except Exception as e:
                last_err = e
    else:
        for base in BASES:
            try:
                tick = http_json(f"{base}/api/v3/ticker/24hr")
                rows = [t for t in tick if t["symbol"].endswith("USDT") and not t["symbol"].startswith(STABLE_BASES)
                        and not any(x in t["symbol"] for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"))]
                rows.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
                return [t["symbol"] for t in rows[:n]]
            except Exception as e:
                last_err = e
    raise RuntimeError(f"دریافت فهرست ارزها ناموفق بود: {last_err}")


def get_klines(symbol, tf, limit=1000):
    """Closed candles only: list of (open_time, high, low, close, close_time, open)."""
    if OFFLINE_DIR:
        with open(os.path.join(OFFLINE_DIR, f"{symbol}_{tf}.json")) as f:
            rows = json.load(f)
        out = []
        for k, r in enumerate(rows[:-1]):
            o = r[5] if len(r) > 5 else (rows[k - 1][3] if k > 0 else r[3])
            out.append((r[0], r[1], r[2], r[3], r[4], o))
        return out
    last_err = None
    q = urllib.parse.urlencode({"symbol": symbol, "interval": tf, "limit": limit})
    urls = [f"{b}/fapi/v1/klines?{q}" for b in FAPI_BASES] if MARKET["name"] == "futures" else [f"{b}/api/v3/klines?{q}" for b in BASES]
    for url in urls:
        try:
            rows = http_json(url)
            now_ms = int(time.time() * 1000)
            out = [(int(k[0]), float(k[2]), float(k[3]), float(k[4]), int(k[6]), float(k[1])) for k in rows]
            return [r for r in out if r[4] < now_ms]
        except Exception as e:
            last_err = e
    raise RuntimeError(f"دریافت داده {symbol} {tf} ناموفق بود: {last_err}")


# ------------------------------------------------------------------ indicators
def heikin_ashi(rows):
    """Heikin Ashi candles in the same tuple layout (open_time, high, low, close, close_time, open)."""
    out = []
    ha_o = ha_c = None
    for r in rows:
        o, h, l, c = r[5], r[1], r[2], r[3]
        new_c = (o + h + l + c) / 4
        new_o = (o + c) / 2 if ha_o is None else (ha_o + ha_c) / 2
        ha_o, ha_c = new_o, new_c
        out.append((r[0], max(h, new_o, new_c), min(l, new_o, new_c), new_c, r[4], new_o))
    return out


def use_ha(cfg):
    return cfg.get("CANDLES", "heikin_ashi").strip().lower() in ("heikin_ashi", "heikinashi", "ha", "heikin")


def atr_series(h, l, c, n=14):
    out = [float("nan")] * len(c)
    tr = []
    for i in range(len(c)):
        if i == 0:
            tr.append(h[i] - l[i])
        else:
            tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if len(c) >= n:
        v = sum(tr[:n]) / n
        out[n - 1] = v
        for i in range(n, len(c)):
            v = (v * (n - 1) + tr[i]) / n
            out[i] = v
    return out


def nz(x, d=0.0):
    return d if x is None or (isinstance(x, float) and math.isnan(x)) else x


def pivots(h, l, L):
    """Like Pine ta.pivothigh/low(L, L): value reported on the confirming bar (index c + L)."""
    n = len(h)
    ph = [None] * n
    pl = [None] * n
    for i in range(2 * L, n):
        c = i - L
        hv, lv = h[c], l[c]
        if all(hv > h[j] for j in range(c - L, c)) and all(hv >= h[j] for j in range(c + 1, c + L + 1)):
            ph[i] = hv
        if all(lv < l[j] for j in range(c - L, c)) and all(lv <= l[j] for j in range(c + 1, c + L + 1)):
            pl[i] = lv
    return ph, pl


def bias_series_hlc(h, l, c, L):
    """NDS trend of a timeframe: after peak node 3 → -1 (down), after trough node 3 → +1 (up).
    Returns per bar (bias, target) — target = level the next node 3 must pass."""
    atr = atr_series(h, l, c, 14)
    ph, pl = pivots(h, l, L)
    h1 = h2 = l1 = l2 = None
    bias = 0
    out = []
    for i in range(len(h)):
        tb = 0.05 * nz(atr[i])
        p = ph[i]
        if p is not None:
            if h1 is None:
                h1 = p
            elif h2 is None:
                if p > h1 + tb:
                    h2 = p
                else:
                    h1 = p
            else:
                if p > h2 + tb:
                    bias, h1, h2 = -1, p, None
                elif p < h1:
                    h1, h2 = p, None
        p = pl[i]
        if p is not None:
            if l1 is None:
                l1 = p
            elif l2 is None:
                if p < l1 - tb:
                    l2 = p
                else:
                    l1 = p
            else:
                if p < l2 - tb:
                    bias, l1, l2 = 1, p, None
                elif p > l1:
                    l1, l2 = p, None
        tgt = h2 if bias == 1 else l2 if bias == -1 else None
        out.append((bias, tgt))
    return out


# ------------------------------------------------------------------ NDS engine (port of the Pine indicator)
class NDSEngine:
    def __init__(self, L=5, eq_tol=0.05, max_open=10, lvl=0.864, r_min=0.70, r_max=1.00,
                 enforce_zone=True, f_exc=0.10, sl_buf=0.10):
        self.L, self.eq_tol, self.max_open = L, eq_tol, max_open
        self.lvl, self.r_min, self.r_max, self.enf = lvl, r_min, r_max, enforce_zone
        self.f_exc, self.sl_buf = f_exc, sl_buf

    @staticmethod
    def _process(st, v, tol, commit, new_ok, max_open):
        st2 = st if commit else list(st)
        closed, cand = 0, None
        i = len(st2) - 1
        while i >= 0:
            v1, v2 = st2[i]
            if v2 is not None:
                if v > v2 + tol:
                    closed += 1
                    st2.pop(i)
                else:
                    break
            else:
                if v > v1 + tol:
                    cand = st2[i]
                    st2.pop(i)
                else:
                    break
            i -= 1
        ext = cand is not None
        code = 3 if closed > 0 else 2 if ext else 1
        if commit:
            if ext:
                st2.append((cand[0], v))
            elif closed == 0 or new_ok:
                st2.append((v, None))
            while len(st2) > max_open:
                st2.pop(0)
        return closed, code, ext

    def run(self, h, l, c):
        n = len(c)
        L = self.L
        atr = atr_series(h, l, c, 14)
        ph, pl = pivots(h, l, L)
        zP, zB, zT, zC = [], [], [], []
        sides = {1: [], -1: []}
        recs = []
        last_t, last_p, last_code = 0, None, 0
        leg864 = None
        hkD, hkK, hkLvl = 0, 0, None
        f_side, f_sl, f_tp1, f_tp2 = 0, None, None, None
        last_flag_bar = {1: None, -1: None}
        last_ev_bar = None

        def is_extreme(idx):
            t, p = zT[idx], zP[idx]
            for j in range(1, 5):
                k = idx - 2 * j
                if k >= 0 and not (t * p > t * zP[k]):
                    return False
            return True

        for i in range(n):
            a = nz(atr[i])
            tol = self.eq_tol * a
            changed = False
            lt_before = zT[-1] if zT else 0
            steps = [(1, ph[i]), (-1, pl[i])] if lt_before != 1 else [(-1, pl[i]), (1, ph[i])]
            for t, pv in steps:
                if pv is None:
                    continue
                pb = i - L
                res = 0
                if not zP:
                    zP.append(pv); zB.append(pb); zT.append(t); zC.append(0); res = 1
                elif zT[-1] == t:
                    if (t == 1 and pv > zP[-1]) or (t == -1 and pv < zP[-1]):
                        zP[-1], zB[-1], zC[-1] = pv, pb, 0
                        res = 2
                else:
                    zP.append(pv); zB.append(pb); zT.append(t); zC.append(0); res = 3
                if res == 3:
                    idx = len(zP) - 2
                    new_ok = True
                    if idx > 0:
                        new_ok = zC[idx - 1] == 3 or is_extreme(idx - 1)
                    _, cd, _ = self._process(sides[zT[idx]], zT[idx] * zP[idx], tol, True, new_ok, self.max_open)
                    zC[idx] = cd
                if res > 0:
                    changed = True
            if len(zP) > 80:
                zP.pop(0); zB.pop(0); zT.pop(0); zC.pop(0)
            zN = len(zP)
            ev = {"bull_flag": False, "bear_flag": False, "h3": False, "l3": False}
            if changed and zN > 0:
                last_t, last_p = zT[-1], zP[-1]
                _, scode, _ = self._process(sides[last_t], last_t * last_p, tol, False, False, self.max_open)
                last_code = scode
                if scode == 3 and last_ev_bar != zB[-1]:
                    last_ev_bar = zB[-1]
                    ev["h3" if last_t == 1 else "l3"] = True
                if zN >= 3:
                    leg864 = zP[-1] + self.lvl * (zP[-2] - zP[-1])
                # hook stage
                best = (0, False, None, 0)
                for d in (1, -1):
                    k_, b_, r_ = self._hook(zP, zT, d, tol)
                    if k_ > best[0]:
                        best = (k_, b_, r_, d)
                hkK, hkD = best[0], best[3] if best[0] else 0
                hkLvl = None
                if hkK >= 6:
                    s0 = zN - hkK
                    top = zP[s0 + 3] if best[1] else zP[s0 + 5]
                    hkLvl = top - self.lvl * (top - zP[s0])
                # 123Flag
                for d in (1, -1):
                    if self._flag(zP, zT, d, tol, a):
                        fb = zB[-1]
                        if last_flag_bar[d] != fb:
                            last_flag_bar[d] = fb
                            F, Z, R0 = zP[-1], zP[-2], zP[-8]
                            f_side = -d
                            f_sl = F + d * self.sl_buf * a
                            f_tp1 = Z
                            f_tp2 = F - self.lvl * (F - R0)
                            ev["bear_flag" if d == 1 else "bull_flag"] = True
            # open 1-2 pairs relative to the current close
            up = self._open_info(sides[1], 1, c[i])
            dn = self._open_info(sides[-1], -1, c[i])
            recs.append({
                "atr": a, "ev": ev, "last_t": last_t, "last_p": last_p, "last_code": last_code,
                "leg864": leg864, "hkD": hkD, "hkK": hkK, "hkLvl": hkLvl,
                "f_side": f_side, "f_sl": f_sl, "f_tp1": f_tp1, "f_tp2": f_tp2,
                "up": up, "dn": dn,
            })
        return recs

    @staticmethod
    def _open_info(st, d, close):
        best = iv1 = iv2 = None
        cnt = 0
        for v1, v2 in reversed(st):
            if v2 is None:
                continue
            cnt += 1
            lv = d * v2
            if iv2 is None:
                iv1, iv2 = d * v1, lv
            beyond = lv > close if d == 1 else lv < close
            if beyond and (best is None or abs(lv - close) < abs(best - close)):
                best = lv
        return {"near": best, "i1": iv1, "i2": iv2, "cnt": cnt}

    def _hook(self, zP, zT, d, tol):
        n = len(zP)
        for k in (7, 6, 5, 4):
            if n < k:
                continue
            s = n - k
            if zT[s] != -d:
                continue
            w = [d * zP[s + j] for j in range(k)]
            ok = w[1] > w[0] and w[2] > w[0] and w[2] < w[1] and w[3] > w[1] + tol
            is_b, r = False, None
            if ok and k >= 5:
                ok = w[4] < w[2] - tol and w[4] > w[0]
                if ok and k >= 6:
                    tA = w[5] > w[3] + tol
                    tB = (w[5] <= w[3] + tol) and w[5] > w[4]
                    ok, is_b = (tA or tB), tB
                    if ok and k >= 7:
                        top = w[3] if is_b else w[5]
                        r = (top - w[6]) / (top - w[0]) if top != w[0] else 0
                        ok = w[6] < w[4] - tol and (not self.enf or (self.r_min <= r <= self.r_max))
            if ok:
                return k, is_b, r
        return 0, False, None

    def _flag(self, zP, zT, d, tol, a):
        n = len(zP)
        if n < 8 or zT[-1] != d:
            return False
        w = [d * zP[n - 8 + j] for j in range(8)]
        return (w[1] + tol < w[3] and w[3] + tol < w[5] and w[2] > w[0] and w[4] > w[0]
                and w[7] > w[5] + self.f_exc * a)


# ------------------------------------------------------------------ entry planner ("wait for a good price")
def align_bias(entry_rows, htf_rows, htf_bias):
    """For each entry bar, NDS trend of the last HTF candle closed at or before it."""
    out = []
    j = -1
    for r in entry_rows:
        while j + 1 < len(htf_rows) and htf_rows[j + 1][4] <= r[4]:
            j += 1
        out.append(htf_bias[j] if j >= 0 else (0, None))
    return out


def run_planner(entry_rows, biasA, biasB, cfg, L):
    # real prices: entries, zone touches, stop/target hits
    h = [r[1] for r in entry_rows]
    l = [r[2] for r in entry_rows]
    c = [r[3] for r in entry_rows]
    # NDS structure (nodes, hooks, flags, zone levels): Heikin Ashi candles by default
    srows = heikin_ashi(entry_rows) if use_ha(cfg) else entry_rows
    recs = NDSEngine(L=L).run([r[1] for r in srows], [r[2] for r in srows], [r[3] for r in srows])
    need_both = yes(cfg["NEED_BOTH"])
    zone_w = float(cfg["ZONE_WIDTH_ATR"])
    zone_mem = int(float(cfg["ZONE_MEMORY_BARS"]))
    trig_n3 = yes(cfg["TRIGGER_NODE3"])
    zTop = zBot = None
    zDir, zTouch = 0, None
    plan = []
    for i, rc in enumerate(recs):
        dA, tgA = biasA[i]
        dB, _ = biasB[i]
        plan_dir = (dA if dA == dB else 0) if need_both else dA
        up, dn = rc["up"], rc["dn"]
        n3L = dn["i2"] - (dn["i1"] - dn["i2"]) if dn["i2"] is not None else None
        n3H = up["i2"] + (up["i2"] - up["i1"]) if up["i2"] is not None else None
        cands = []
        if plan_dir == 1:
            cands = [dn["near"], n3L, rc["hkLvl"] if (rc["hkD"] == 1 and rc["hkK"] == 6) else None,
                     rc["leg864"] if rc["last_t"] == 1 else None]
            cands = [x for x in cands if x is not None and x < c[i]]
        elif plan_dir == -1:
            cands = [up["near"], n3H, rc["hkLvl"] if (rc["hkD"] == -1 and rc["hkK"] == 6) else None,
                     rc["leg864"] if rc["last_t"] == -1 else None]
            cands = [x for x in cands if x is not None and x > c[i]]
        cTop = max(cands) if cands else None
        cBot = min(cands) if cands else None
        if cTop is not None and cTop - cBot < zone_w * rc["atr"]:
            if plan_dir == 1:
                cBot = cTop - zone_w * rc["atr"]
            else:
                cTop = cBot + zone_w * rc["atr"]
        frozen = zTouch is not None and i - zTouch <= zone_mem and zDir == plan_dir
        if not frozen:
            zTop, zBot, zDir, zTouch = cTop, cBot, plan_dir, None
        in_zone = zTop is not None and h[i] >= zBot and l[i] <= zTop
        if in_zone and zDir != 0 and zTouch is None:
            zTouch = i
        if zTop is not None and ((zDir == 1 and c[i] < zBot - rc["atr"]) or (zDir == -1 and c[i] > zTop + rc["atr"])):
            zTouch = None
        touched = zTouch is not None and i - zTouch <= zone_mem
        ev = rc["ev"]
        trig = 0
        sl = tp = None
        why = ""
        if zDir == 1 and touched and (ev["bull_flag"] or (trig_n3 and ev["l3"])):
            trig = 1
            why = "123Flag خرید" if ev["bull_flag"] else "نود ۳ کف"
            sl = rc["f_sl"] if ev["bull_flag"] else nz(rc["last_p"], l[i]) - 0.10 * rc["atr"]
            tp = up["near"] if up["near"] is not None else tgA if (tgA is not None and tgA > c[i]) else c[i] + 2 * (c[i] - sl)
        elif zDir == -1 and touched and (ev["bear_flag"] or (trig_n3 and ev["h3"])):
            trig = -1
            why = "123Flag فروش" if ev["bear_flag"] else "نود ۳ سقف"
            sl = rc["f_sl"] if ev["bear_flag"] else nz(rc["last_p"], h[i]) + 0.10 * rc["atr"]
            tp = dn["near"] if dn["near"] is not None else tgA if (tgA is not None and tgA < c[i]) else c[i] - 2 * (sl - c[i])
        plan.append({
            "dA": dA, "dB": dB, "dir": plan_dir, "zTop": zTop, "zBot": zBot, "zDir": zDir,
            "in_zone": in_zone, "touched": touched, "trig": trig, "sl": sl, "tp": tp, "why": why,
            "close": c[i], "high": h[i], "low": l[i], "time": entry_rows[i][4], "atr": rc["atr"],
        })
    return plan


def backtest(plan, fee=0.0004):
    """Simple simulation of planner entries: enter at close, exit at SL/TP (SL first if both)."""
    trades = []
    pos = None
    for i, p in enumerate(plan):
        if pos:
            if pos["side"] == 1:
                hit_sl, hit_tp = p["low"] <= pos["sl"], p["high"] >= pos["tp"]
            else:
                hit_sl, hit_tp = p["high"] >= pos["sl"], p["low"] <= pos["tp"]
            if hit_sl or hit_tp:
                exit_p = pos["sl"] if hit_sl else pos["tp"]
                ret = pos["side"] * (exit_p - pos["entry"]) / pos["entry"] - 2 * fee
                trades.append(ret)
                pos = None
        if pos is None and p["trig"] != 0 and p["sl"] is not None and p["tp"] is not None:
            if (p["trig"] == 1 and p["sl"] < p["close"] < p["tp"]) or (p["trig"] == -1 and p["tp"] < p["close"] < p["sl"]):
                pos = {"side": p["trig"], "entry": p["close"], "sl": p["sl"], "tp": p["tp"]}
    wins = sum(1 for t in trades if t > 0)
    gross_w = sum(t for t in trades if t > 0)
    gross_l = -sum(t for t in trades if t < 0)
    return {
        "n": len(trades), "wins": wins,
        "win_rate": (wins / len(trades) * 100) if trades else 0.0,
        "total": sum(trades) * 100,
        "pf": (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0),
    }


# ------------------------------------------------------------------ Telegram
class Telegram:
    def __init__(self, token, chat_id):
        self.token, self.chat_id, self.offset = token.strip(), str(chat_id).strip(), None

    def _api(self, method, params=None):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        return http_json(url, data=data, timeout=30)

    def find_chat_id(self):
        r = self._api("getUpdates", {"timeout": 0})
        for u in reversed(r.get("result", [])):
            m = u.get("message") or u.get("edited_message")
            if m and m.get("chat", {}).get("id"):
                return str(m["chat"]["id"])
        return ""

    def send(self, text):
        if not self.token or not self.chat_id:
            log("[no Telegram] message not sent (set TELEGRAM_TOKEN in config.txt)")
            return
        try:
            self._api("sendMessage", {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"})
        except Exception as e:
            log(f"Telegram send error: {e}")

    def commands(self):
        """Returns new text commands from the owner chat (e.g. /status)."""
        if not self.token:
            return []
        try:
            params = {"timeout": 0}
            if self.offset is not None:
                params["offset"] = self.offset
            r = self._api("getUpdates", params)
        except Exception:
            return []
        out = []
        for u in r.get("result", []):
            self.offset = u["update_id"] + 1
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) == self.chat_id and m.get("text"):
                out.append(m["text"].strip().split()[0].split("@")[0].lower())
        return out


# ------------------------------------------------------------------ the bot
class NDSBot:
    def __init__(self, cfg):
        self.cfg = cfg
        MARKET["name"] = "spot" if cfg["MARKET"].strip().lower() == "spot" else "futures"
        self.scan = yes(cfg["SCAN"])
        self.fixed = [s.strip().upper() for s in cfg["SYMBOLS"].split(",") if s.strip()]
        self.symbols = list(self.fixed)
        self.symbols_at = 0
        self.cache = {}
        self.next_due = {}
        self.coin_stats = {}
        self.skip_note = {}
        self.tf, self.tfA, self.tfB = cfg["ENTRY_TF"], cfg["DIR_TF_A"], cfg["DIR_TF_B"]
        self.trend_L = int(float(cfg["TREND_PIVOT"]))
        self.tg = Telegram(cfg["TELEGRAM_TOKEN"], cfg["CHAT_ID"])
        self.state = self._load_state()
        self.last_bar = {}
        self.pivot = {}
        self.pivot_at = {}
        self.snap = {}

    def _load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log(f"State save error: {e}")

    def refresh_symbols(self, force=False):
        if not self.scan:
            return
        hrs = float(self.cfg["SCAN_REFRESH_HOURS"])
        if not force and time.time() - self.symbols_at < hrs * 3600:
            return
        try:
            top = get_top_symbols(int(float(self.cfg["SCAN_TOP"])))
        except Exception as e:
            log(f"Symbol list error: {e}")
            return
        keep = [k for k, v in self.state.items() if not k.startswith("_") and v.get("open")]
        new = list(dict.fromkeys(top + [k for k in keep if k not in top]))
        added = [x for x in new if x not in self.symbols]
        self.symbols, self.symbols_at = new, time.time()
        log(f"Scanning {len(new)} symbols" + (f" (+{len(added)} new)" if added else ""))

    def _rows(self, sym, tf, limit):
        """Candles with a small cache: refetch only after a new candle of that timeframe has closed."""
        key = (sym, tf)
        now_ms = time.time() * 1000
        c = self.cache.get(key)
        if c and c and now_ms < c[-1][4] + TF_SECONDS.get(tf, 300) * 1000 + 1500:
            return c
        rows = get_klines(sym, tf, limit)
        self.cache[key] = rows
        return rows

    def _data(self, sym):
        e = self._rows(sym, self.tf, 1000)
        a = self._rows(sym, self.tfA, 1000)
        b = self._rows(sym, self.tfB, 500)
        if len(e) < 300 or len(a) < 200 or len(b) < 60:
            raise RuntimeError("تاریخچه کافی نیست (ارز تازه لیست شده)")
        sa = heikin_ashi(a) if use_ha(self.cfg) else a
        sb = heikin_ashi(b) if use_ha(self.cfg) else b
        bA = bias_series_hlc([r[1] for r in sa], [r[2] for r in sa], [r[3] for r in sa], self.trend_L)
        bB = bias_series_hlc([r[1] for r in sb], [r[2] for r in sb], [r[3] for r in sb], self.trend_L)
        return e, align_bias(e, a, bA), align_bias(e, b, bB)

    def choose_pivot(self, sym, e, biasA, biasB):
        """Adapt to the market: pick the pivot length that worked best on recent candles."""
        if self.cfg["PIVOT"].strip().lower() != "auto":
            return int(float(self.cfg["PIVOT"])), None
        # only move away from the default when another setting was clearly profitable on
        # at least 2 recent trades; otherwise keep 5 (avoids chasing noise)
        default = 5
        results = {}
        for L in [int(x) for x in self.cfg["PIVOT_CHOICES"].split(",") if x.strip()]:
            results[L] = backtest(run_planner(e, biasA, biasB, self.cfg, L))
        good = [(st["total"], L) for L, st in results.items() if st["n"] >= 2 and st["total"] > 0 and st["pf"] > 1.2]
        if not good:
            return default, results.get(default)
        L = max(good)[1]
        return L, results[L]

    def analyse(self, sym, force_reopt=False):
        e, biasA, biasB = self._data(sym)
        if len(e) < 200:
            raise RuntimeError(f"داده کافی برای {sym} نیست")
        reopt_h = float(self.cfg["REOPTIMIZE_HOURS"])
        if force_reopt or sym not in self.pivot or time.time() - self.pivot_at.get(sym, 0) > reopt_h * 3600:
            L, st = self.choose_pivot(sym, e, biasA, biasB)
            self.coin_stats[sym] = st or {"total": 0.0, "n": 0, "win_rate": 0.0}
            changed = self.pivot.get(sym) != L
            self.pivot[sym], self.pivot_at[sym] = L, time.time()
            if st is not None and changed and not self.scan:
                self.tg.send(f"⚙️ {sym}: طول پیوت برای شرایط فعلی بازار روی {L} تنظیم شد "
                             f"(روی {len(e)} کندل اخیر {TF_FA.get(self.tf, self.tf)}: {st['n']} معامله، "
                             f"برد {st['win_rate']:.0f}%، جمع {st['total']:+.2f}%)")
        plan = run_planner(e, biasA, biasB, self.cfg, self.pivot[sym])
        return e, plan

    def status_key(self, p):
        if p["dir"] == 0:
            return "nodir"
        if p["zTop"] is None:
            return f"nozone{p['dir']}"
        if p["touched"]:
            return f"inzone{p['zDir']}"
        return f"wait{p['zDir']}"

    def step(self, sym):
        e, plan = self.analyse(sym)
        p = plan[-1]
        bar_t = p["time"]
        if self.last_bar.get(sym) == bar_t:
            return None
        self.last_bar[sym] = bar_t
        self.snap[sym] = p
        log(f"{sym} checked | price {fmt(p['close'])} | trend {self.tfA}:{p['dA']:+d} {self.tfB}:{p['dB']:+d} | state {self.status_key(p)}")
        st = self.state.setdefault(sym, {})
        # --- manage the open position (paper account) / signal result
        self._manage_position(sym, st, plan)
        # --- new entry signal → collected, ranked and opened by the main loop
        pending = None
        if p["trig"] != 0 and not st.get("open"):
            atr_pct = p["atr"] / p["close"] * 100 if p["close"] else 0
            if float(self.cfg["MIN_ATR_PCT"]) <= atr_pct <= float(self.cfg["MAX_ATR_PCT"]):
                pending = p
            else:
                log(f"{sym} trigger skipped: volatility {atr_pct:.2f}% outside filter")
        # --- state messages (only when the situation changes)
        key = self.status_key(p)
        zone_sig = None if p["zTop"] is None else (round(p["zBot"] / max(p["atr"], 1e-9)), round(p["zTop"] / max(p["atr"], 1e-9)))
        prev_key, prev_zone = st.get("key"), st.get("zone")
        if key != prev_key or (key.startswith("wait") and zone_sig is not None and prev_zone is not None and tuple(prev_zone) != zone_sig and time.time() - st.get("zone_msg_at", 0) > 1800):
            if not self.scan and (yes(self.cfg["SEND_WAIT_MESSAGES"]) or key.startswith("inzone")):
                self.tg.send(self.describe(sym, p))
            st["zone_msg_at"] = time.time()
        st["key"], st["zone"] = key, list(zone_sig) if zone_sig else None
        self._save_state()
        return pending

    # ------------------------------------------------------------ money management
    def _acct(self):
        a = self.state.setdefault("_account", {})
        if "equity" not in a:
            a["equity"] = float(self.cfg["ACCOUNT_BALANCE"])
            a["start"] = a["equity"]
            a["peak"] = a["equity"]
            a["max_dd"] = 0.0
        return a

    def _today_pnl(self):
        a = self._acct()
        today = dt.date.today().isoformat()
        if a.get("day") != today:
            a["day"], a["day_pnl"], a["day_start"] = today, 0.0, a["equity"]
        return a["day_pnl"]

    def size_position(self, entry, sl):
        """Risk-based sizing: lose at most RISK_PERCENT of equity if the stop is hit."""
        a = self._acct()
        eq = a["equity"]
        risk_pct = float(self.cfg["RISK_PERCENT"])
        max_lev = float(self.cfg["MAX_LEVERAGE"])
        fee = float(self.cfg["FEE_PERCENT"]) / 100
        risk_amt = eq * risk_pct / 100
        dist = abs(entry - sl)
        if dist <= 0:
            return None
        qty = risk_amt / (dist + 2 * fee * entry)       # stop distance + round-trip fees
        notional = qty * entry
        capped = False
        if notional > eq * max_lev:
            qty = eq * max_lev / entry
            notional = qty * entry
            capped = True
        lev = max(1, math.ceil(notional / eq)) if eq > 0 else 1
        return {"qty": qty, "notional": notional, "lev": lev, "risk_amt": qty * (dist + 2 * fee * entry),
                "risk_pct": qty * (dist + 2 * fee * entry) / eq * 100 if eq else 0, "capped": capped}

    def _open_position(self, sym, st, p):
        side = p["trig"]
        entry, sl, tp = p["close"], p["sl"], p["tp"]
        if not ((side == 1 and sl < entry < tp) or (side == -1 and tp < entry < sl)):
            return
        a = self._acct()
        paper = self.cfg["MODE"].strip().lower() == "paper"
        name = "LONG (خرید) ▲" if side == 1 else "SHORT (فروش) ▼"
        # risk limits
        open_n = sum(1 for k, v in self.state.items() if not k.startswith("_") and v.get("open"))
        day_loss_lim = a["equity"] * float(self.cfg["DAILY_MAX_LOSS_PERCENT"]) / 100
        if self._today_pnl() <= -day_loss_lim:
            if a.get("halt_note") != a.get("day"):
                a["halt_note"] = a.get("day")
                self.tg.send(f"⛔ سقف ضرر روزانه ({self.cfg['DAILY_MAX_LOSS_PERCENT']}٪) پر شد — تا فردا سیگنال ورود جدید فرستاده نمی‌شود.")
            log(f"{sym} {name} skipped: daily loss limit")
            return
        if open_n >= int(float(self.cfg["MAX_OPEN_POSITIONS"])):
            log(f"{sym} {name} skipped: max open positions ({open_n})")
            return
        sz = self.size_position(entry, sl)
        if not sz:
            return
        dist = abs(entry - sl)
        tp1 = entry + side * dist          # 1R → take half, stop to breakeven
        use_partial = yes(self.cfg["PARTIAL_TP"]) and abs(tp - entry) > dist * 1.2
        rr = abs(tp - entry) / dist
        base = sym.replace("USDT", "")
        lines = [
            f"📢📢 الان پوزیشن {name} روی {sym} باز کن",
            ("(ربات همین پوزیشن را روی حساب آزمایشی هم باز کرد)" if paper else None),
            f"دلیل: {p['why']} داخل ناحیه خوش‌قیمت",
            f"ورود ≈ {fmt(entry)}",
            f"حدضرر: {fmt(sl)}  ({dist / entry * 100:.2f}%)",
            (f"هدف ۱ (نیمی از حجم): {fmt(tp1)}  → حدضرر به نقطه ورود" if use_partial else None),
            f"هدف نهایی: {fmt(tp)}  (R/R {rr:.1f})",
            "",
            "💰 مدیریت سرمایه:",
            f"حجم: {sz['qty']:.4f} {base}  ≈ {sz['notional']:,.0f} USDT",
            f"اهرم لازم: ×{sz['lev']}" + (" (به سقف اهرم محدود شد)" if sz["capped"] else ""),
            f"ریسک: {sz['risk_amt']:,.2f} USDT  ({sz['risk_pct']:.2f}% از {a['equity']:,.0f})",
            f"جهت {TF_FA.get(self.tfA)}: {fa_dir(p['dA'])} | {TF_FA.get(self.tfB)}: {fa_dir(p['dB'])}",
        ]
        lines += ["",
                  "📝 مراحل در صرافی (Futures):",
                  f"۱) اهرم را روی ×{sz['lev']} و حالت Isolated بگذار",
                  f"۲) سفارش {'Buy/Long' if side == 1 else 'Sell/Short'} مارکت با حجم {sz['qty']:.4f} {base}",
                  f"۳) Stop Loss روی {fmt(sl)} و Take Profit روی {fmt(tp)}",
                  ("۴) وقتی ربات خبر داد هدف ۱ خورد، نصف حجم را ببند و حدضرر را به نقطه ورود ببر" if use_partial else None),
                  "⏱ اگر قیمت بیش از نیم درصد از نقطه ورود دور شده، وارد نشو."]
        self.tg.send("\n".join(x for x in lines if x is not None))
        st["open"] = {"side": side, "entry": entry, "sl": sl, "tp": tp, "tp1": tp1 if use_partial else None,
                      "qty": sz["qty"], "qty_left": sz["qty"], "be": False, "pnl": 0.0,
                      "time": p["time"], "why": p["why"], "paper": paper}
        self._log_signal(sym, st["open"], "OPEN", None, None)

    def _manage_position(self, sym, st, plan):
        op = st.get("open")
        if not op:
            return
        fee = float(self.cfg["FEE_PERCENT"]) / 100
        a = self._acct()
        side = op["side"]
        name = "LONG" if side == 1 else "SHORT"
        for q in plan:
            if q["time"] <= op["time"]:
                continue
            hi, lo = q["high"], q["low"]
            hit_sl = lo <= op["sl"] if side == 1 else hi >= op["sl"]
            hit_tp1 = op.get("tp1") is not None and (hi >= op["tp1"] if side == 1 else lo <= op["tp1"])
            hit_tp = hi >= op["tp"] if side == 1 else lo <= op["tp"]
            if hit_sl:                                   # conservative: stop first
                self._close(sym, st, op, op["sl"], op["qty_left"], "SL" if not op["be"] else "BE", fee, a)
                return
            if hit_tp1 and not op["be"]:
                half = op["qty"] / 2
                pnl = side * (op["tp1"] - op["entry"]) * half - fee * (op["entry"] + op["tp1"]) * half
                op["qty_left"] -= half
                op["pnl"] += pnl
                op["be"] = True
                op["sl"] = op["entry"]
                op["tp1"] = None
                self._book(a, pnl)
                self.tg.send(f"💵 {sym} {name}: هدف ۱ خورد — نیمی از حجم با سود {pnl:+,.2f} USDT بسته شد؛ حدضرر به نقطه ورود ({fmt(op['entry'])}) منتقل شد.")
            if hit_tp:
                self._close(sym, st, op, op["tp"], op["qty_left"], "TP", fee, a)
                return
            op["time"] = q["time"]

    def _book(self, a, pnl):
        self._today_pnl()
        a["equity"] += pnl
        a["day_pnl"] += pnl
        a["peak"] = max(a["peak"], a["equity"])
        dd = (a["peak"] - a["equity"]) / a["peak"] * 100 if a["peak"] else 0
        a["max_dd"] = max(a.get("max_dd", 0.0), dd)

    def _close(self, sym, st, op, exit_p, qty, why, fee, a):
        side = op["side"]
        pnl = side * (exit_p - op["entry"]) * qty - fee * (op["entry"] + exit_p) * qty
        op["pnl"] += pnl
        self._book(a, pnl)
        total = op["pnl"]
        ret = total / (op["entry"] * op["qty"]) * 100
        icon = {"TP": "🏁", "SL": "🛑", "BE": "⚖️"}[why]
        txt = {"TP": "به هدف نهایی رسید", "SL": "با حدضرر بسته شد", "BE": "در نقطه ورود (سر به سر) بسته شد"}[why]
        self.tg.send(f"{icon} {sym} {'LONG' if side == 1 else 'SHORT'}: {txt}\n"
                     f"سود/زیان این معامله: {total:+,.2f} USDT ({ret:+.2f}% از حجم)\n"
                     f"موجودی {'حساب آزمایشی' if op.get('paper') else 'فرضی'}: {a['equity']:,.2f} USDT")
        self._log_signal(sym, op, why, exit_p, ret, total)
        st["open"] = None

    def balance_text(self):
        a = self._acct()
        self._today_pnl()
        opens = []
        for k, v in self.state.items():
            if not k.startswith("_") and v.get("open"):
                o = v["open"]
                last = self.snap.get(k, {}).get("close")
                upnl = o["side"] * (last - o["entry"]) * o["qty_left"] if last else 0
                opens.append(f"• {k} {'LONG' if o['side'] == 1 else 'SHORT'} از {fmt(o['entry'])} | SL {fmt(o['sl'])} | TP {fmt(o['tp'])} | سود شناور {upnl:+,.2f}")
        mode = "آزمایشی (Paper)" if self.cfg["MODE"].strip().lower() == "paper" else "فقط سیگنال (حساب فرضی برای محاسبه حجم)"
        return (f"💼 حساب — حالت: {mode}\n"
                f"موجودی: {a['equity']:,.2f} USDT (شروع {a['start']:,.0f}، {(a['equity'] / a['start'] - 1) * 100:+.2f}%)\n"
                f"سود/زیان امروز: {a['day_pnl']:+,.2f} USDT | بیشترین افت: {a.get('max_dd', 0):.2f}%\n"
                f"ریسک هر معامله: {self.cfg['RISK_PERCENT']}% | سقف ضرر روزانه: {self.cfg['DAILY_MAX_LOSS_PERCENT']}% | حداکثر پوزیشن باز: {self.cfg['MAX_OPEN_POSITIONS']}\n"
                + ("پوزیشن‌های باز:\n" + "\n".join(opens) if opens else "پوزیشن بازی نیست."))

    def describe(self, sym, p):
        head = f"{sym} · {TF_FA.get(self.tfA)}: {fa_dir(p['dA'])} | {TF_FA.get(self.tfB)}: {fa_dir(p['dB'])}"
        if p["dir"] == 0:
            return f"⏸ {head}\nجهت دو تایم‌فریم یکی نیست — صبر کن، معامله نکن."
        side = "خرید" if p["dir"] == 1 else "فروش"
        if p["zTop"] is None:
            return f"⏳ {head}\nجهت برای {side} مناسب است، ولی ناحیه خوش‌قیمت هنوز شکل نگرفته — صبر کن."
        zone = f"{fmt(p['zBot'])} – {fmt(p['zTop'])}"
        if p["touched"]:
            trig = ("123Flag خرید یا نود ۳ کف" if p["zDir"] == 1 else "123Flag فروش یا نود ۳ سقف")
            return f"🎯 {head}\nقیمت در ناحیه خوش‌قیمت {side} است ({zone}).\nمنتظر ماشه ورود بمان: {trig}."
        dist = (p["close"] - p["zTop"]) / p["close"] * 100 if p["zDir"] == 1 else (p["zBot"] - p["close"]) / p["close"] * 100
        return f"⏳ {head}\nصبر کن تا قیمت به ناحیه {side} برسد: {zone}\nقیمت فعلی {fmt(p['close'])} (فاصله {dist:.2f}%)"

    def _log_signal(self, sym, op, event, exit_p, ret, pnl_usdt=None):
        new = not os.path.exists(SIGNALS_PATH)
        try:
            with open(SIGNALS_PATH, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "symbol", "event", "side", "reason", "entry", "sl", "tp", "exit", "return_%", "pnl_usdt", "qty", "pivot"])
                w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M"), sym, event, "BUY" if op["side"] == 1 else "SELL",
                            op.get("why", ""), op["entry"], op["sl"], op["tp"], exit_p if exit_p is not None else "",
                            f"{ret:.3f}" if ret is not None else "", f"{pnl_usdt:.2f}" if pnl_usdt is not None else "",
                            f"{op.get('qty', 0):.6f}", self.pivot.get(sym, "")])
        except Exception as e:
            log(f"signals.csv write error: {e}")

    def report(self):
        """Track record from signals.csv."""
        if not os.path.exists(SIGNALS_PATH):
            return "هنوز معامله‌ای بسته نشده است."
        rets = []
        with open(SIGNALS_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["event"] in ("TP", "SL", "BE") and r["return_%"]:
                    rets.append(float(r["return_%"]))
        if not rets:
            return "هنوز معامله‌ای بسته نشده است."
        wins = sum(1 for x in rets if x > 0)
        return f"کارنامه: {len(rets)} معامله بسته‌شده، برد {wins / len(rets) * 100:.0f}%، جمع {sum(rets):+.2f}%"

    def handle_commands(self):
        for cmd in self.tg.commands():
            if cmd in ("/scan", "/اسکن"):
                self.tg.send(self.scan_text())
            elif cmd in ("/status", "/وضعیت"):
                parts = [self.describe(s, self.snap[s]) for s in (self.fixed if self.scan else self.symbols) if s in self.snap]
                self.tg.send("\n\n".join(parts) if parts else "در حال دریافت داده…")
            elif cmd in ("/report", "/کارنامه"):
                self.tg.send(self.report())
            elif cmd in ("/balance", "/حساب"):
                self.tg.send(self.balance_text())
            elif cmd in ("/help", "/start"):
                self.tg.send("دستورها:\n/scan — ارزهای مستعد لانگ و شورت\n/balance — موجودی و پوزیشن‌های باز\n/report — کارنامه سیگنال‌ها\n/status — وضعیت ارزهای ثابت\n/help — راهنما")

    def periodic_report(self):
        hours = float(self.cfg["REPORT_EVERY_HOURS"])
        if hours <= 0 or not self.snap:
            return
        a = self.state.setdefault("_meta", {})
        if time.time() - a.get("report_at", 0) < hours * 3600:
            return
        a["report_at"] = time.time()
        parts = ["🗒 گزارش دوره‌ای NDS"] + ([self.scan_text()] if self.scan else [self.describe(s, self.snap[s]) for s in self.symbols if s in self.snap])
        parts.append(self.balance_text())
        self.tg.send("\n\n".join(parts))
        self._save_state()

    def score(self, sym, p):
        dist = abs(p["close"] - p["sl"])
        rr = abs(p["tp"] - p["close"]) / dist if dist else 0
        bt = self.coin_stats.get(sym, {}).get("total", 0.0)
        return min(rr, 6) + 0.3 * max(-5.0, min(5.0, bt)) + (0.5 if "123Flag" in p.get("why", "") else 0)

    def scan_text(self):
        longs, shorts, ready = [], [], []
        for sym, p in self.snap.items():
            if p["dir"] == 0 or p["zTop"] is None:
                continue
            if p["touched"]:
                d = 0.0
            elif p["zDir"] == 1:
                d = (p["close"] - p["zTop"]) / p["close"] * 100
            else:
                d = (p["zBot"] - p["close"]) / p["close"] * 100
            row = (d, sym, p)
            (longs if p["zDir"] == 1 else shorts).append(row)
        longs.sort(key=lambda r: r[0])
        shorts.sort(key=lambda r: r[0])

        def line(r):
            d, sym, p = r
            where = "🎯 داخل ناحیه — منتظر ماشه" if d <= 0 else f"{d:.2f}% تا ناحیه"
            return f"• {sym.replace('USDT', '')}: ناحیه {fmt(p['zBot'])}–{fmt(p['zTop'])} | {where}"
        n_all = len(self.snap)
        n_dir = sum(1 for p in self.snap.values() if p["dir"] != 0)
        parts = [f"🔎 اسکن NDS — {n_all} ارز | هم‌جهت ۱۵د و ۴س: {n_dir}"]
        parts.append("🟢 مستعد LONG:\n" + ("\n".join(line(r) for r in longs[:5]) if longs else "—"))
        parts.append("🔴 مستعد SHORT:\n" + ("\n".join(line(r) for r in shorts[:5]) if shorts else "—"))
        parts.append("پیام «📢 الان پوزیشن باز کن» فقط وقتی می‌آید که ماشه داخل ناحیه بخورد.")
        return "\n\n".join(parts)

    def cycle(self):
        """One pass: analyse every symbol whose entry candle just closed, then open the best triggers."""
        self.refresh_symbols()
        now = time.time()
        pend = []
        for sym in list(self.symbols):
            if now < self.next_due.get(sym, 0):
                continue
            try:
                p = self.step(sym)
                if p:
                    pend.append((self.score(sym, p), sym, p))
                self.next_due[sym] = self.snap[sym]["time"] / 1000 + TF_SECONDS.get(self.tf, 300) + 3 if sym in self.snap else now + 60
            except Exception as e:
                log(f"Error on {sym}: {e}")
                self.next_due[sym] = now + 300
        for sc, sym, p in sorted(pend, key=lambda r: r[0], reverse=True):
            self._open_position(sym, self.state.setdefault(sym, {}), p)
        self._save_state()

    def run_forever(self):
        self.refresh_symbols(force=True)
        self.tg.send(f"🤖 ربات NDS روشن شد\n{'اسکنر: ' + str(len(self.symbols)) + ' ارز پرحجم ' + ('فیوچرز' if MARKET['name'] == 'futures' else 'اسپات') if self.scan else 'نمادها: ' + ', '.join(self.symbols)}\nنوع کندل: {'هیکن آشی (ساختار) + قیمت واقعی (ورود/حدضرر)' if use_ha(self.cfg) else 'معمولی'}\nتایم ورود: {TF_FA.get(self.tf)} | "
                     f"جهت: {TF_FA.get(self.tfA)} و {TF_FA.get(self.tfB)}\n"
                     + ("فقط سیگنال‌های تأییدشده فرستاده می‌شود. فهرست مستعدها: /scan" if self.scan else "برای وضعیت لحظه‌ای /status بفرستید."))
        wait = int(float(self.cfg["CHECK_SECONDS"]))
        while True:
            try:
                self.cycle()
            except Exception as e:
                log(f"Cycle error: {e}")
                log(traceback.format_exc(limit=2))
            self.handle_commands()
            self.periodic_report()
            time.sleep(wait)


# ------------------------------------------------------------------ entry point
def main():
    cfg = load_config()
    args = sys.argv[1:]
    token = cfg["TELEGRAM_TOKEN"].strip()
    if not token or "PASTE" in token.upper():
        print("WARNING: Telegram token is not set in config.txt - messages are shown only in this window.")
        cfg["TELEGRAM_TOKEN"] = ""
    elif not cfg["CHAT_ID"].strip():
        try:
            cid = Telegram(token, "").find_chat_id()
        except Exception as e:
            cid = ""
            print(f"Could not connect to Telegram: {e}")
        if cid:
            save_config_value("CHAT_ID", cid)
            cfg["CHAT_ID"] = cid
            print("OK: Telegram chat found and saved to config.txt")
        else:
            print("Open your bot in Telegram, press START (send /start), then run this again.")
            input("Press Enter to exit...")
            return
    bot = NDSBot(cfg)
    bot.refresh_symbols(force=True)
    if "--backtest" in args:
        for sym in bot.symbols:
            e, bA, bB = bot._data(sym)
            print(f"\n=== {sym} — {len(e)} کندل {TF_FA.get(bot.tf)} ===")
            for L in [int(x) for x in cfg["PIVOT_CHOICES"].split(",")]:
                s = backtest(run_planner(e, bA, bB, cfg, L))
                print(f"  پیوت {L}: {s['n']} معامله | برد {s['win_rate']:.0f}% | جمع {s['total']:+.2f}% | PF {s['pf']:.2f}")
        return
    if "--test" in args:
        bot.tg.send("✅ پیام آزمایشی ربات NDS — اتصال تلگرام درست است.")
        for sym in bot.symbols:
            e, plan = bot.analyse(sym, force_reopt=True)
            msg = bot.describe(sym, plan[-1])
            print(msg)
            bot.tg.send(msg)
        return
    log("NDS bot is running. Telegram messages are in Persian. Close this window to stop.")
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        log("Bot stopped.")


if __name__ == "__main__":
    main()
