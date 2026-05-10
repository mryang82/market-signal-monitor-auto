"""
Market Signal Detector v8-fast — 완전 자동화
=================================================
v8 React 도구의 모든 로직을 정확히 Python으로 포팅:
  ✅ 10개 점수 함수 (VIX squeeze 포함)
  ✅ 4단계 Regime detection
  ✅ Mid-Decline 매수 함정 게이트
  ✅ Score Momentum 추세
  ✅ ±15% PROB_TABLE
"""
import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# 환경변수
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FRED_API_KEY = os.getenv("FRED_API_KEY")

STATE_FILE = Path("previous_signal.json")
ALERT_ON_LEVEL_CHANGE = True
ALERT_ON_DAILY_REPORT = False


# ════════════════════════════════════════════════════════════════
# 1. 데이터 수집
# ════════════════════════════════════════════════════════════════

def fetch_yahoo(ticker, period="2y"):
    try:
        return yf.Ticker(ticker).history(period=period, interval="1d")
    except Exception as e:
        print(f"❌ {ticker}: {e}")
        return None


def fetch_fred(series_id):
    if not FRED_API_KEY:
        print("⚠️ FRED_API_KEY 없음")
        return None
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": FRED_API_KEY,
              "file_type": "json", "sort_order": "desc", "limit": 200}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()["observations"]
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna()
    except Exception as e:
        print(f"❌ FRED {series_id}: {e}")
        return None


def fetch_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=30", timeout=10)
        data = r.json()["data"]
        df = pd.DataFrame(data)
        df["value"] = pd.to_numeric(df["value"])
        return df
    except Exception as e:
        print(f"❌ F&G: {e}")
        return None


def compute_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def collect_data():
    print("📊 데이터 수집 중...")
    d = {}
    
    spx = fetch_yahoo("^GSPC")
    if spx is not None and len(spx) >= 200:
        curr = float(spx["Close"].iloc[-1])
        ma200 = float(spx["Close"].rolling(200).mean().iloc[-1])
        high52w = float(spx["Close"].iloc[-252:].max())
        d["sp500"] = {"current": curr, "ma200": ma200, "high52w": high52w,
                      "low52w": float(spx["Close"].iloc[-252:].min())}
        print(f"  ✓ SPX {curr:.0f} (MA200 {(curr/ma200-1)*100:+.1f}%, DD {(curr/high52w-1)*100:+.1f}%)")
    
    ndx = fetch_yahoo("^IXIC")
    if ndx is not None and len(ndx) >= 200:
        curr = float(ndx["Close"].iloc[-1])
        high52w = float(ndx["Close"].iloc[-252:].max())
        d["nasdaq"] = {"current": curr, "high52w": high52w,
                       "low52w": float(ndx["Close"].iloc[-252:].min()),
                       "ma200": float(ndx["Close"].rolling(200).mean().iloc[-1])}
        print(f"  ✓ NDX {curr:.0f} (DD {(curr/high52w-1)*100:+.1f}%)")
    
    vix = fetch_yahoo("^VIX")
    if vix is not None and len(vix) >= 90:
        curr = float(vix["Close"].iloc[-1])
        avg3m = float(vix["Close"].iloc[-63:].mean())
        v3mago = float(vix["Close"].iloc[-63])
        d["vix"] = {"current": curr, "avg3m": avg3m, "value3mAgo": v3mago}
        print(f"  ✓ VIX {curr:.1f} (3M slope {(curr-v3mago)/v3mago*100:+.0f}%)")
    
    if spx is not None:
        weekly = spx["Close"].resample("W").last()
        rsi_w = compute_rsi(weekly, 14)
        if not rsi_w.empty:
            d["rsi"] = {"weekly": float(rsi_w.iloc[-1])}
            print(f"  ✓ RSI(W) {rsi_w.iloc[-1]:.1f}")
    
    dgs10 = fetch_fred("DGS10")
    dgs2 = fetch_fred("DGS2")
    if dgs10 is not None and dgs2 is not None:
        y10 = float(dgs10.iloc[0]["value"])
        y2 = float(dgs2.iloc[0]["value"])
        d["yieldCurve"] = {"y10": y10, "y2": y2, "tenMinus2y": y10 - y2}
        print(f"  ✓ 2y/10y {y10-y2:+.2f}%")
    
    hy = fetch_fred("BAMLH0A0HYM2")
    if hy is not None:
        curr = float(hy.iloc[0]["value"]) * 100
        target_6m = pd.Timestamp.now() - pd.DateOffset(months=6)
        idx_6m = (hy["date"] - target_6m).abs().idxmin()
        v6m = float(hy.loc[idx_6m, "value"]) * 100
        roc6m = (curr - v6m) / v6m * 100 if v6m > 0 else 0
        d["hySpread"] = {"value": curr, "value6mAgo": v6m, "roc6m": roc6m}
        print(f"  ✓ HY {curr:.0f}bp (6M ROC {d['hySpread']['roc6m']:+.0f}%)")
    
    fg = fetch_fear_greed()
    if fg is not None:
        curr = int(fg.iloc[0]["value"])
        v1mago = int(fg.iloc[min(29, len(fg) - 1)]["value"])
        d["fearGreed"] = {"value": curr, "value1mAgo": v1mago, "change1m": curr - v1mago}
        print(f"  ✓ F&G {curr} (1M {curr - v1mago:+d})")
    
    return d


# ════════════════════════════════════════════════════════════════
# 2. v8 점수 함수
# ════════════════════════════════════════════════════════════════

def score_vix(vix, vix_3m):
    if vix is None: return 0
    if vix >= 50: return 4
    if vix >= 40: return 3
    if vix >= 30: return 2
    if vix >= 20: return 0
    if vix <= 12 and vix_3m and vix < vix_3m * 0.85: return -2
    if vix <= 14: return -1
    return 0

def score_fg(fg):
    if fg is None: return 0
    if fg <= 10: return 3
    if fg <= 20: return 2
    if fg <= 30: return 1
    if fg >= 85: return -3
    if fg >= 75: return -2
    if fg >= 65: return -1
    return 0

def score_rsi(rsi):
    if rsi is None: return 0
    if rsi <= 30: return 2
    if rsi <= 35: return 1
    if rsi >= 75: return -2
    if rsi >= 70: return -1
    return 0

def score_ma(d):
    if d is None: return 0
    if d <= -20: return 3
    if d <= -10: return 2
    if d <= -5: return 1
    if d >= 18: return -3
    if d >= 12: return -2
    if d >= 8: return -1
    return 0

def score_dd(dd):
    if dd is None: return 0
    if dd <= -30: return 4
    if dd <= -25: return 3
    if dd <= -15: return 2
    if dd <= -10: return 1
    return 0

def score_hy(hy):
    if hy is None: return 0
    if hy >= 800: return 3
    if hy >= 600: return 2
    if hy >= 500: return 1
    if hy >= 400: return -1
    if hy >= 350: return -1
    return 0

def score_yc(spread):
    if spread is None: return 0
    if spread < -0.5: return -2
    if spread < 0: return -1
    if spread < 0.1: return -1
    return 0

def score_hy_momentum(roc):
    if roc is None: return 0
    if roc >= 50: return 3
    if roc >= 25: return 2
    if roc >= 10: return 1
    if roc <= -25: return 1
    return 0

def score_fg_momentum(change):
    if change is None: return 0
    if change >= 25: return -2
    if change >= 15: return -1
    if change <= -25: return 2
    if change <= -15: return 1
    return 0

def score_vix_slope(slope):
    if slope is None: return 0
    if slope >= 50: return -2
    if slope >= 25: return -1
    if slope <= -30: return 1
    return 0


WEIGHTS = {
    "vix": 1.0, "fg": 1.0, "rsi": 0.6, "ma": 0.7, "dd": 0.9,
    "hy": 1.0, "yc": 0.8, "hy_mom": 0.7, "fg_mom": 0.5, "vix_slope": 0.5,
}


# ════════════════════════════════════════════════════════════════
# 3. Regime + Mid-Decline + Momentum
# ════════════════════════════════════════════════════════════════

REGIME_WEIGHTS = {
    "BULL_EARLY": {"sell": 0.85, "buy": 1.00},
    "BULL_MID":   {"sell": 1.00, "buy": 1.00},
    "LATE_CYCLE": {"sell": 1.10, "buy": 0.95},
    "BEAR":       {"sell": 0.70, "buy": 1.10},
}


def compute_avg_dd(d):
    sp = d.get("sp500", {})
    nq = d.get("nasdaq", {})
    if not sp.get("current") or not sp.get("high52w"): return None
    if not nq.get("current") or not nq.get("high52w"): return None
    return ((sp["current"] / sp["high52w"] - 1) * 100 + (nq["current"] / nq["high52w"] - 1) * 100) / 2


def classify_regime(d):
    yc = d.get("yieldCurve", {}).get("tenMinus2y")
    hy = d.get("hySpread", {}).get("value")
    avg_dd = compute_avg_dd(d)
    sp = d.get("sp500", {})
    
    if avg_dd is not None and avg_dd <= -15:
        return {"regime": "BEAR", "label": "약세장 (큰 하락)",
                "desc": "매도 가중치 0.70x, 매수 1.10x"}
    if yc is not None and yc < 0.3 and hy is not None and hy >= 350:
        return {"regime": "LATE_CYCLE", "label": "후기 사이클",
                "desc": "매도 가중치 1.10x"}
    
    ma_dist = (sp.get("current", 0) / sp.get("ma200", 1) - 1) * 100 if sp.get("ma200") else 0
    if yc is not None and yc >= 1.5 and ma_dist < 10:
        return {"regime": "BULL_EARLY", "label": "초기 강세장",
                "desc": "매도 가중치 0.85x"}
    
    return {"regime": "BULL_MID", "label": "중기 강세장", "desc": "표준 가중치"}


def check_momentum_trend(d):
    info = {"deterioration": False, "recovery": False,
            "deteriorationFactors": [], "recoveryFactors": []}
    hy_roc = d.get("hySpread", {}).get("roc6m")
    fg_change = d.get("fearGreed", {}).get("change1m")
    vix_slope = None
    if d.get("vix", {}).get("current") and d.get("vix", {}).get("value3mAgo"):
        v = d["vix"]
        vix_slope = (v["current"] - v["value3mAgo"]) / v["value3mAgo"] * 100 if v["value3mAgo"] > 0 else 0
    
    if hy_roc is not None and hy_roc >= 10:
        info["deterioration"] = True
        info["deteriorationFactors"].append(f"HY ROC +{hy_roc:.0f}%")
    if vix_slope is not None and vix_slope >= 25:
        info["deterioration"] = True
        info["deteriorationFactors"].append(f"VIX slope +{vix_slope:.0f}%")
    if fg_change is not None and fg_change >= 10:
        info["deterioration"] = True
        info["deteriorationFactors"].append(f"F&G +{fg_change}")
    
    if hy_roc is not None and hy_roc <= -10:
        info["recovery"] = True
        info["recoveryFactors"].append(f"HY ROC {hy_roc:.0f}%")
    if vix_slope is not None and vix_slope <= -25:
        info["recovery"] = True
        info["recoveryFactors"].append(f"VIX slope {vix_slope:.0f}%")
    if fg_change is not None and fg_change <= -10:
        info["recovery"] = True
        info["recoveryFactors"].append(f"F&G {fg_change}")
    
    return info


def is_mid_decline(d):
    hy = d.get("hySpread", {})
    vix = d.get("vix", {}).get("current")
    fg = d.get("fearGreed", {}).get("value")
    hy_roc = hy.get("roc6m")
    hy_curr = hy.get("value")
    avg_dd = compute_avg_dd(d)
    
    if hy_roc is None or hy_roc < 25:
        return {"triggered": False, "factors": []}
    if hy_curr is None or hy_curr >= 1000:
        return {"triggered": False, "factors": []}
    if avg_dd is None or avg_dd <= -25:
        return {"triggered": False, "factors": []}
    
    if vix is not None:
        if vix >= 40: return {"triggered": False, "factors": []}
        if vix >= 25:
            if fg is not None and fg <= 20: return {"triggered": False, "factors": []}
            if avg_dd <= -20: return {"triggered": False, "factors": []}
    
    return {
        "triggered": True,
        "factors": [
            f"HY ROC +{hy_roc:.0f}%",
            f"HY {hy_curr:.0f}bp",
            f"DD {avg_dd:.0f}%",
            f"VIX {vix:.0f} 패닉 미달" if vix else "",
        ],
    }


# ════════════════════════════════════════════════════════════════
# 4. v8 게이트
# ════════════════════════════════════════════════════════════════

def apply_v8_gates(weighted, d):
    info = {"sellChecks": [], "sellConf": 0, "buyPanic": 0, "buyExtreme": 0,
            "regime": None, "momentum": None, "midDecline": None,
            "buyChecks": []}
    
    regime_info = classify_regime(d)
    info["regime"] = regime_info
    rw = REGIME_WEIGHTS[regime_info["regime"]]
    if weighted < 0: weighted *= rw["sell"]
    elif weighted > 0: weighted *= rw["buy"]
    
    # 매도 confirmation
    if weighted <= -3:
        sell_checks = []
        yc = d.get("yieldCurve", {}).get("tenMinus2y")
        hy = d.get("hySpread", {})
        sp = d.get("sp500", {})
        ma_dist = (sp["current"] / sp["ma200"] - 1) * 100 if sp.get("ma200") and sp.get("current") else None
        fg = d.get("fearGreed", {}).get("value")
        
        if yc is not None and yc < 0.1: sell_checks.append(("YC<0.1", yc))
        if hy.get("value", 0) >= 400: sell_checks.append(("HY≥400", hy["value"]))
        if ma_dist is not None and ma_dist >= 12: sell_checks.append(("MA+12%", f"{ma_dist:.1f}%"))
        if fg is not None and fg > 80: sell_checks.append(("F&G>80", fg))
        if hy.get("roc6m", 0) >= 25: sell_checks.append(("HY ROC≥25%", f"{hy['roc6m']:.0f}%"))
        
        info["sellChecks"] = sell_checks
        info["sellConf"] = len(sell_checks)
        
        if info["sellConf"] == 0:
            weighted = max(weighted, -3)
        elif info["sellConf"] == 1:
            weighted = max(weighted, -6.5)
    
    # Score Momentum 게이트
    momentum = check_momentum_trend(d)
    info["momentum"] = momentum
    if weighted <= -7 and not momentum["deterioration"]:
        weighted = max(weighted, -6.5)
    
    # 매수 패닉 게이트
    avg_dd = compute_avg_dd(d)
    if weighted >= 8:
        panic = 0
        buy_checks = []
        if d.get("vix", {}).get("current", 0) >= 30:
            panic += 1; buy_checks.append(("VIX≥30", d["vix"]["current"]))
        if avg_dd is not None and avg_dd <= -15:
            panic += 1; buy_checks.append(("DD≤-15%", f"{avg_dd:.1f}%"))
        if d.get("hySpread", {}).get("value", 0) >= 600:
            panic += 1; buy_checks.append(("HY≥600", d["hySpread"]["value"]))
        if d.get("fearGreed", {}).get("value", 100) <= 15:
            panic += 1; buy_checks.append(("F&G≤15", d["fearGreed"]["value"]))
        info["buyPanic"] = panic
        info["buyChecks"] = buy_checks
        if panic < 2:
            weighted = min(weighted, 7)
    
    # 매수 극단 게이트
    if weighted >= 13:
        extreme = 0
        if d.get("vix", {}).get("current", 0) >= 40: extreme += 1
        if avg_dd is not None and avg_dd <= -25: extreme += 1
        if d.get("hySpread", {}).get("value", 0) >= 800: extreme += 1
        if d.get("fearGreed", {}).get("value", 100) <= 10: extreme += 1
        info["buyExtreme"] = extreme
        if extreme < 3:
            weighted = min(weighted, 12)
    
    # Mid-Decline 게이트
    if weighted >= 4:
        md = is_mid_decline(d)
        info["midDecline"] = md
        if md["triggered"]:
            if weighted >= 13: weighted = min(weighted, 12)
            elif weighted >= 8: weighted = min(weighted, 7)
            else: weighted = min(weighted, 3)
    
    return weighted, info


def compute_v8(d):
    sp = d.get("sp500", {})
    vix = d.get("vix", {})
    fg = d.get("fearGreed", {})
    hy = d.get("hySpread", {})
    yc = d.get("yieldCurve", {})
    
    avg_dd = compute_avg_dd(d)
    ma_dist = (sp["current"] / sp["ma200"] - 1) * 100 if sp.get("ma200") and sp.get("current") else None
    vix_slope = None
    if vix.get("current") and vix.get("value3mAgo"):
        vix_slope = (vix["current"] - vix["value3mAgo"]) / vix["value3mAgo"] * 100 if vix["value3mAgo"] > 0 else 0
    
    raw = {
        "vix": score_vix(vix.get("current"), vix.get("avg3m")),
        "fg": score_fg(fg.get("value")),
        "rsi": score_rsi(d.get("rsi", {}).get("weekly")),
        "ma": score_ma(ma_dist),
        "dd": score_dd(avg_dd),
        "hy": score_hy(hy.get("value")),
        "yc": score_yc(yc.get("tenMinus2y")),
        "hy_mom": score_hy_momentum(hy.get("roc6m")),
        "fg_mom": score_fg_momentum(fg.get("change1m")),
        "vix_slope": score_vix_slope(vix_slope),
    }
    
    weighted = sum(s * WEIGHTS[k] for k, s in raw.items())
    weighted_final, gates = apply_v8_gates(weighted, d)
    
    if weighted_final >= 13: level = "CRASH_BUY"
    elif weighted_final >= 8: level = "STRONG_BUY"
    elif weighted_final >= 4: level = "BUY"
    elif weighted_final >= 1.5: level = "WATCH"
    elif weighted_final >= -1.5: level = "NEUTRAL"
    elif weighted_final >= -4: level = "CAUTION"
    elif weighted_final >= -7: level = "WARNING"
    elif weighted_final >= -10: level = "STRONG_SELL"
    else: level = "CRASH_WARNING"
    
    return {
        "level": level, "score": round(weighted_final, 2),
        "score_raw": round(weighted, 2), "raw_scores": raw, "gates": gates,
        "derived": {"avg_dd": round(avg_dd, 2) if avg_dd else None,
                    "ma_dist": round(ma_dist, 2) if ma_dist else None}
    }


# ════════════════════════════════════════════════════════════════
# 5. PROB_TABLE + Telegram
# ════════════════════════════════════════════════════════════════

PROB_TABLE = {
    "CRASH_BUY":     {"n": 5, "pUp15": 60, "tag": "역사적 매수 기회"},
    "STRONG_BUY":    {"n": 12, "pUp15": 75, "tag": "가장 신뢰 높은 매수"},
    "BUY":           {"n": 9, "pUp15": 56, "tag": "매수 우위"},
    "WATCH":         {"n": 6, "pUp15": 17, "tag": "관망"},
    "NEUTRAL":       {"n": 3, "tag": "중립"},
    "CAUTION":       {"n": 7, "pDown15": 43, "tag": "주의"},
    "WARNING":       {"n": 8, "pDown15": 62, "tag": "경보 (-15% MDD 62%)"},
    "STRONG_SELL":   {"n": 0, "pDown15": 100, "tag": "강력 매도"},
    "CRASH_WARNING": {"n": 1, "pDown15": 100, "tag": "패닉 경보"},
}

LEVEL_EMOJI = {
    "CRASH_BUY": "🟢🟢🟢", "STRONG_BUY": "🟢🟢", "BUY": "🟢",
    "WATCH": "⚪", "NEUTRAL": "⚪", "CAUTION": "🟡",
    "WARNING": "🔴🔴", "STRONG_SELL": "🔴🔴🔴", "CRASH_WARNING": "🚨🚨🚨",
}


def format_alert(signal, d, prev_level=None):
    level = signal["level"]
    emoji = LEVEL_EMOJI.get(level, "⚪")
    prob = PROB_TABLE.get(level, {})
    gates = signal["gates"]
    
    msg = f"{emoji} <b>{level}</b>\n"
    msg += f"점수: {signal['score']:+.2f} | 국면: {gates['regime']['regime']}\n"
    
    if prev_level and prev_level != level:
        msg += f"\n🚨 <b>신호 변경: {prev_level} → {level}</b>\n"
    
    if prob.get("pUp15"):
        msg += f"\n📊 12M 내 +15% 확률: <b>{prob['pUp15']}%</b> (n={prob['n']})\n"
    if prob.get("pDown15"):
        msg += f"\n📊 12M 내 -15% MDD 확률: <b>{prob['pDown15']}%</b> (n={prob['n']})\n"
    if prob.get("tag"):
        msg += f"<i>{prob['tag']}</i>\n"
    
    sp = d.get("sp500", {})
    vix = d.get("vix", {})
    fg = d.get("fearGreed", {})
    hy = d.get("hySpread", {})
    yc = d.get("yieldCurve", {})
    
    msg += "\n<b>📈 핵심 지표:</b>\n"
    if sp.get("current"):
        avg_dd = signal['derived'].get('avg_dd')
        ma_dist_str = ""
        if sp.get("ma200"):
            ma_dist = (sp['current']/sp['ma200']-1)*100
            ma_dist_str = f", MA200 {ma_dist:+.1f}%"
        dd_str = f", DD {avg_dd:+.1f}%" if avg_dd is not None else ""
        msg += f"• S&P 500: {sp['current']:.0f}{ma_dist_str}{dd_str}\n"
    if vix.get("current"):
        v3m = vix.get("value3mAgo")
        if v3m and v3m > 0:
            slope = (vix["current"] - v3m) / v3m * 100
            msg += f"• VIX: {vix['current']:.1f} (slope {slope:+.0f}%)\n"
        else:
            msg += f"• VIX: {vix['current']:.1f}\n"
    if fg.get("value"):
        msg += f"• F&G: {fg['value']} (1M {fg.get('change1m', 0):+d})\n"
    if hy.get("value"):
        msg += f"• HY: {hy['value']:.0f}bp (ROC {hy.get('roc6m', 0):+.0f}%)\n"
    if yc.get("tenMinus2y") is not None:
        msg += f"• 2y/10y: {yc['tenMinus2y']:+.2f}%\n"
    if d.get("rsi", {}).get("weekly"):
        msg += f"• RSI(W): {d['rsi']['weekly']:.1f}\n"
    
    if gates["regime"]:
        msg += f"\n🌐 <b>{gates['regime']['label']}</b>\n<i>{gates['regime']['desc']}</i>\n"
    
    if gates["momentum"]:
        if gates["momentum"]["deterioration"]:
            msg += f"\n📉 악화 추세: {' · '.join(gates['momentum']['deteriorationFactors'])}\n"
        if gates["momentum"]["recovery"]:
            msg += f"\n📈 개선 추세: {' · '.join(gates['momentum']['recoveryFactors'])}\n"
    
    if gates["midDecline"] and gates["midDecline"]["triggered"]:
        msg += f"\n⚠️ <b>Mid-Decline 함정 — 매수 신호 강등</b>\n"
        msg += f"<i>{' · '.join(gates['midDecline']['factors'])}</i>\n"
    
    if gates["sellChecks"]:
        msg += f"\n🔻 매도 confirmation ({gates['sellConf']}/5): "
        msg += ", ".join([c[0] for c in gates["sellChecks"]]) + "\n"
    if gates["buyChecks"]:
        msg += f"\n🟢 매수 패닉 ({gates['buyPanic']}/4): "
        msg += ", ".join([c[0] for c in gates["buyChecks"]]) + "\n"
    
    msg += f"\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    return msg


def send_telegram(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 미설정. 메시지 출력만:\n")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            print("✓ Telegram 전송 성공")
            return True
        print(f"❌ Telegram 실패: {r.text}")
        return False
    except Exception as e:
        print(f"❌ Telegram 오류: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 6. Main
# ════════════════════════════════════════════════════════════════

def load_previous_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def main():
    print(f"🚀 v8-fast 자동화 시작 — {datetime.now()}\n")
    
    # 환경변수 확인
    print("🔑 환경변수 체크:")
    print(f"   FRED_API_KEY: {'✓ 설정됨' if FRED_API_KEY else '❌ 없음'}")
    print(f"   TELEGRAM_BOT_TOKEN: {'✓ 설정됨' if TG_BOT_TOKEN else '❌ 없음'}")
    print(f"   TELEGRAM_CHAT_ID: {'✓ 설정됨' if TG_CHAT_ID else '❌ 없음'}")
    print()
    
    if not FRED_API_KEY:
        print("⚠️ FRED_API_KEY가 없습니다. GitHub Secrets에 등록하세요.")
        print("   Settings → Secrets and variables → Actions → New repository secret")
        return
    
    try:
        d = collect_data()
    except Exception as e:
        print(f"❌ 데이터 수집 중 예외 발생: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return
    
    if "sp500" not in d or "vix" not in d:
        print("❌ 핵심 데이터 수집 실패 (SPX 또는 VIX 누락)")
        print("   Yahoo Finance 일시 장애 가능. 30분 후 재시도 권장.")
        return
    
    print()
    
    try:
        signal = compute_v8(d)
    except Exception as e:
        print(f"❌ 신호 계산 중 예외 발생: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print(f"🎯 {signal['level']}")
    print(f"   점수: {signal['score']:+.2f}")
    print(f"   국면: {signal['gates']['regime']['regime']}")
    print(f"   매도 conf: {signal['gates']['sellConf']}/5")
    print(f"   매수 패닉: {signal['gates']['buyPanic']}/4")
    if signal['gates']['midDecline'] and signal['gates']['midDecline']['triggered']:
        print(f"   ⚠️ Mid-Decline 감지!")
    
    prev_state = load_previous_state()
    prev_level = prev_state.get("level")
    
    should_alert = False
    if ALERT_ON_LEVEL_CHANGE and prev_level != signal["level"]:
        should_alert = True
        print(f"\n📢 등급 변경: {prev_level} → {signal['level']}")
    if ALERT_ON_DAILY_REPORT:
        should_alert = True
    
    if should_alert:
        try:
            msg = format_alert(signal, d, prev_level)
            send_telegram(msg)
        except Exception as e:
            print(f"⚠️ 알림 전송 중 오류: {type(e).__name__}: {e}")
    else:
        print(f"\n⏭️ 알림 스킵 (변경 없음: {prev_level})")
    
    try:
        save_state({
            "level": signal["level"], "score": signal["score"],
            "regime": signal["gates"]["regime"]["regime"],
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        print(f"⚠️ 상태 저장 오류: {type(e).__name__}: {e}")
    
    print("\n✅ 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 예상치 못한 오류: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        # exit code 1로 종료하되, 에러 메시지를 명확히 표시
        import sys
        sys.exit(1)
