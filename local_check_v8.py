"""
로컬 테스트 — Telegram 없이 신호만 확인
=======================================
사용:
  pip install -r requirements.txt
  export FRED_API_KEY="your_key"   # macOS/Linux
  set FRED_API_KEY=your_key        # Windows
  python local_check_v8.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_signal_v8 import collect_data, compute_v8, format_alert, PROB_TABLE


def main():
    print("=" * 70)
    print("Market Signal v8 — 로컬 진단")
    print("=" * 70)
    
    d = collect_data()
    if "sp500" not in d:
        print("\n❌ 데이터 수집 실패. FRED_API_KEY 확인하세요.")
        return
    
    print()
    print("=" * 70)
    print("📊 수집된 지표")
    print("=" * 70)
    
    sp = d.get("sp500", {})
    vix = d.get("vix", {})
    fg = d.get("fearGreed", {})
    yc = d.get("yieldCurve", {})
    hy = d.get("hySpread", {})
    
    if sp.get("current"):
        print(f"  SPX     : {sp['current']:.0f}, MA200 {(sp['current']/sp['ma200']-1)*100:+.2f}%, 52w DD {(sp['current']/sp['high52w']-1)*100:+.2f}%")
    if d.get("nasdaq", {}).get("current"):
        nq = d["nasdaq"]
        print(f"  NDX     : {nq['current']:.0f}, 52w DD {(nq['current']/nq['high52w']-1)*100:+.2f}%")
    if vix.get("current"):
        slope = (vix["current"] - vix.get("value3mAgo", vix["current"])) / vix.get("value3mAgo", vix["current"]) * 100
        print(f"  VIX     : {vix['current']:.2f}, 3M slope {slope:+.0f}%")
    if d.get("rsi", {}).get("weekly"):
        print(f"  RSI(W)  : {d['rsi']['weekly']:.2f}")
    if fg.get("value"):
        print(f"  F&G     : {fg['value']} (1M {fg.get('change1m', 0):+d})")
    if hy.get("value"):
        print(f"  HY      : {hy['value']:.0f}bp (6M ROC {hy.get('roc6m', 0):+.1f}%)")
    if yc.get("tenMinus2y") is not None:
        print(f"  2y/10y  : {yc['tenMinus2y']:+.2f}%")
    
    signal = compute_v8(d)
    
    print()
    print("=" * 70)
    print(f"🎯 신호: {signal['level']}  (점수 {signal['score']:+.2f})")
    print("=" * 70)
    print(f"  국면        : {signal['gates']['regime']['regime']} ({signal['gates']['regime']['label']})")
    print(f"  매도 conf   : {signal['gates']['sellConf']}/5")
    print(f"  매수 패닉   : {signal['gates']['buyPanic']}/4")
    print(f"  Mid-Decline : {'⚠️ YES' if signal['gates']['midDecline'] and signal['gates']['midDecline']['triggered'] else 'NO'}")
    
    prob = PROB_TABLE.get(signal['level'], {})
    if prob.get("pUp15"):
        print(f"\n📈 12M 내 +15% 확률: {prob['pUp15']}% (n={prob['n']})")
    if prob.get("pDown15"):
        print(f"\n📉 12M 내 -15% MDD 확률: {prob['pDown15']}% (n={prob['n']})")
    if prob.get("tag"):
        print(f"   특징: {prob['tag']}")
    
    print()
    print("=" * 70)
    print("📱 Telegram 메시지 미리보기")
    print("=" * 70)
    print(format_alert(signal, d))


if __name__ == "__main__":
    main()
