"""대시보드 (토스증권풍) + 자동매매 스케줄러. 이것만 띄우면 봇도 같이 돈다.

  · 거래소 계좌: OKX(데모 또는 실계좌) 잔고·포지션·평단·평가손익을 폴링해서 그대로 보여준다 (키가 있을 때)
  · 스케줄러: INTERVAL_MIN 분마다 run_cycle() — autotrade.py 를 따로 띄울 필요가 없다
  · 수동 주문: 매수/매도 버튼. auto 모드의 '모의 N회' 게이트를 건너뛰고 바로 거래소 주문이 나가므로
              '실주문' 을 타이핑해야만 전송된다 (실수 클릭·외부 요청 차단)
  · 자동실행 ON/OFF 버튼: autorun.off 파일 토글. make on / make off 와 같다
사용: python dashboard.py   → http://localhost:8000   (make run 이 포트 정리까지 해 준다)"""
import os
import sqlite3
import threading

import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import autotrade as A
import strategy as S

app = FastAPI()
BOT_HERE = False          # 이 프로세스가 주문 락을 잡고 스케줄러를 돌리는가 (__main__ 에서 정한다)


def next_run(last_ts):
    """다음 판단 시각. 스케줄러가 이 프로세스에 없으면(터미널 봇) 마지막 판단 + 간격으로 추정한다."""
    if A.NEXT_RUN:
        return A.NEXT_RUN.isoformat()
    if not last_ts:
        return None
    return (A.dt.datetime.fromisoformat(last_ts) + A.dt.timedelta(minutes=A.INTERVAL_MIN)).isoformat()


# '장기 기대 수익률' 근거 = research/aoa/dip_lev2_result.txt (strategy.py 규칙 그대로 재생, 1배, 비용·펀딩 포함).
# 대표값은 실제로 거래할 OKX 구간. 이 레버리지와 .env LEVERAGE 가 다르면 화면에 '재측정 필요' 가 뜬다.
BACKTEST = {"src": "OKX 2021-05~2026-09", "lev": 1, "cagr": 8.7, "mdd": -13, "win": 75, "trades": 32, "per_trade": 1.49,
            "more": [["바이낸스 2020-03~2026-09", 20.0, -16], ["BitMEX 2018-03~2021-12", 27.9, -37]]}


def rows(sql):
    with A.db() as c:
        c.row_factory = sqlite3.Row
        out = [dict(r) for r in c.execute(sql)]

    return out


def live_account(demo=None):
    """OKX 계좌 현황(데모/실계좌). 키가 없거나 조회가 실패해도 화면은 떠야 하므로 예외를 값으로 돌려준다."""
    if not A.X.have_keys(A.X.DEMO if demo is None else demo):
        return None
    try:
        return A.X.snapshot(A.X.client(demo))
    except Exception as e:                                             # noqa: BLE001
        return {"error": A.X.explain(e)}


@app.get("/api/state")
def api_state():
    runs = rows("SELECT * FROM runs ORDER BY id DESC LIMIT 200")
    return {"mode": A.MODE, "live_now": A.live_now(), "have_keys": A.HAVE_KEYS, "coin": A.X.COIN, "demo": A.X.DEMO,
            "leverage": A.LEVERAGE, "position_pct": A.POSITION_PCT, "rule": {"D": S.D, "TP": S.TP, "SL": S.SL, "hours": S.MAXB * 5 / 60},
            "paper_done": A.paper_trades_done(), "live_after": A.LIVE_AFTER, "autorun": A.autorun(),
            "use_claude": A.USE_CLAUDE, "claude_model": A.CLAUDE_MODEL, "backtest": BACKTEST,
            "real_first": next((r["real_equity"] for r in rows("SELECT real_equity FROM runs WHERE real_equity > 0 ORDER BY id LIMIT 1")), None),
            "interval_min": A.INTERVAL_MIN, "next_run": next_run(runs[0]["timestamp"] if runs else None), "bot_here": BOT_HERE, "min_order": A.X.MIN_ORDER,
            "state": A.state(), "last": runs[0] if runs else None,
            "account": live_account(),
            "demo_keys": A.X.have_keys(True), "demo_account": live_account(True) if A.X.have_keys(True) and not A.X.DEMO else None,
            "runs": runs[::-1], "orders": rows("SELECT * FROM orders ORDER BY id DESC LIMIT 100")}


@app.get("/api/analysis")
def api_analysis():
    """차트 + 알고리즘 진행 과정: 전날 종가 vs 50·200일선 → 마지막 완성 5분봉의 1h 이평 괴리 → 자리 → 포지션 관리.
    봇과 같은 strategy.indicators 를 쓴다."""
    pub = A.X.public()
    df = A.X.candles(pub, A.DAILY)                                  # 마지막 행 = 진행 중인 오늘 (09:00 KST 경계)
    d1 = df.close.iloc[:-1]
    d1.index = d1.index.tz_convert("UTC")
    now = pd.Timestamp.now(tz="UTC")
    raw = A.X.bars_since(pub, now - A.WARMUP)
    c5 = raw[raw.index + S.BAR <= now]
    dev, bull = S.indicators(c5, d1)
    ema = c5.close.ewm(span=12, adjust=False).mean()
    m50, m200 = df.close.rolling(50).mean(), df.close.rolling(200).mean()
    t = c5.index[-1]
    out = {"candles": [{"t": i.strftime("%Y-%m-%d"), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume,
                        "m50": None if pd.isna(m50[i]) else m50[i], "m200": None if pd.isna(m200[i]) else m200[i]}   # NaN 은 JSON 불가
                       for i, r in df.tail(120).iterrows()],
           "price": A.X.price(pub), "date": d1.index[-1].strftime("%Y-%m-%d"), "prev_close": d1.iloc[-1],
           "sma50": d1.rolling(50).mean().iloc[-1], "sma200": d1.rolling(200).mean().iloc[-1], "trend_ok": bool(bull.iloc[-1]),
           "bar": t.tz_convert(A.KST).strftime("%m-%d %H:%M"), "close": c5.close.iloc[-1], "ema": ema.iloc[-1],
           "dev": dev.iloc[-1], "trigger_px": ema.iloc[-1] * (1 - S.D / 100), "signal": S.signal(dev.iloc[-1], bull.iloc[-1]),
           "rule": {"D": S.D, "TP": S.TP, "SL": S.SL, "hours": S.MAXB * 5 / 60},
           "next_bar": (t + 2 * S.BAR).tz_convert(A.KST).strftime("%H:%M"),       # 진행 중인 5분봉 마감
           "orders": rows("SELECT timestamp, action, price FROM orders WHERE status IN ('paper','submitted') ORDER BY id")}
    st = A.state()
    if st["side"] == "long":
        up, dn = S.levels(st["entry"])
        out["position"] = {"entry": st["entry"], "tp": up, "sl": dn,
                           "expiry": (A.entry_bar(st["entered_at"]) + S.MAXB * S.BAR).tz_convert(A.KST).strftime("%m-%d %H:%M")}
    return out


@app.post("/api/run")
def api_run():
    if not BOT_HERE:
        return JSONResponse({"ok": False, "error": "봇이 다른 프로세스(터미널 make bot 등)에서 돌고 있습니다 — 판단은 그쪽 스케줄러가 합니다"}, status_code=409)
    threading.Thread(target=A.run_cycle, args=("버튼",), daemon=True).start()
    return {"ok": True}


@app.post("/api/autorun")
def api_autorun(body: dict):
    """자동실행 온오프. body = {"on": true|false}"""
    return {"ok": True, "autorun": A.set_autorun(bool(body.get("on")))}


@app.post("/api/order")
def api_order(body: dict):
    """수동 주문. body = {"action": "buy"|"sell", "confirm": "실주문", "demo": true|false}"""
    if not BOT_HERE:
        return JSONResponse({"ok": False, "error": "봇이 다른 프로세스(터미널 make bot 등)에서 돌고 있습니다 — 수동 주문은 봇을 멈춘 뒤 대시보드 단독으로"}, status_code=409)
    if body.get("confirm") != "실주문":
        return JSONResponse({"ok": False, "error": "확인 문구가 다릅니다"}, status_code=400)
    if body.get("action") not in ("buy", "sell"):
        return JSONResponse({"ok": False, "error": "action 은 buy 또는 sell"}, status_code=400)
    try:
        return {"ok": True, **A.manual_order(body["action"], bool(body.get("demo")))}
    except Exception as e:                                             # noqa: BLE001
        A.log.error("수동 주문 실패: %s", e)
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=400)


PAGE = r"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>BTC 급락 매수 봇 · OKX</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f2f4f6;--card:#fff;--t1:#191f28;--t2:#4e5968;--t3:#8b95a1;--line:#f2f4f6;--blue:#3182f6;--blue-bg:#e8f3ff;--red:#f04452;--red-bg:#ffeef0;--gray-bg:#f2f4f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font-family:Pretendard,-apple-system,BlinkMacSystemFont,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
header h1{font-size:22px;font-weight:700;margin:0;letter-spacing:-.3px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;background:var(--gray-bg);color:var(--t2)}
.pill.live{background:var(--red-bg);color:var(--red)}.pill.test{background:var(--blue-bg);color:var(--blue)}.pill.off{background:#fff3e0;color:#e67e22}
.btn.off{background:#e67e22}.btn.off:hover{background:#c96a15}
.pill i{width:7px;height:7px;border-radius:50%;background:currentColor;display:inline-block}
.btn{border:0;background:var(--blue);color:#fff;font:inherit;font-weight:600;font-size:14px;padding:11px 18px;border-radius:12px;cursor:pointer;transition:.15s}
.btn:hover{background:#1b64da}.btn:disabled{background:#b0c8f5;cursor:default}
.btn.buy{background:var(--red)}.btn.buy:hover{background:#d63447}
.btn.sell{background:var(--t2)}.btn.sell:hover{background:#333d4b}
.btn.buy:disabled,.btn.sell:disabled{background:#d1d6db}
.card{background:var(--card);border-radius:20px;padding:24px;margin-bottom:14px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.hero{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:640px){.hero{grid-template-columns:1fr}}
.label{font-size:14px;color:var(--t3);font-weight:500;margin-bottom:6px}
.big{font-size:34px;font-weight:700;letter-spacing:-.8px;line-height:1.15}
.sub{font-size:14px;color:var(--t2);margin-top:6px;font-weight:500}
.up{color:var(--red)}.down{color:var(--blue)}.flat{color:var(--t3)}
.zone{display:inline-block;padding:4px 10px;border-radius:8px;font-size:13px;font-weight:700;vertical-align:middle}
.zone.long{background:var(--red-bg);color:var(--red)}.zone.short{background:var(--blue-bg);color:var(--blue)}.zone.wait{background:var(--gray-bg);color:var(--t2)}.zone.exit{background:var(--gray-bg);color:var(--t2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.stat{background:var(--gray-bg);border-radius:14px;padding:14px 16px}.stat .label{margin-bottom:4px;font-size:13px}.stat .v{font-size:18px;font-weight:700;letter-spacing:-.3px}
h2{font-size:17px;font-weight:700;margin:0 0 14px;letter-spacing:-.3px}
.list{display:flex;flex-direction:column}
.row{display:flex;align-items:center;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}.row:last-child{border-bottom:0}
.row .ic{width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0}
.ic.long{background:var(--red-bg);color:var(--red)}.ic.short{background:var(--blue-bg);color:var(--blue)}.ic.wait,.ic.hold{background:var(--gray-bg);color:var(--t2)}.ic.err{background:#fff3e0;color:#e67e22}
.row .m{flex:1;min-width:0}.row .m b{display:block;font-size:15px;font-weight:600}.row .m span{display:block;font-size:13px;color:var(--t3);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .r{text-align:right;flex-shrink:0}.row .r b{display:block;font-size:15px;font-weight:700}.row .r span{display:block;font-size:12px;color:var(--t3);margin-top:2px}
.empty{color:var(--t3);font-size:14px;padding:20px 0;text-align:center}
.legend{display:flex;gap:16px;font-size:13px;color:var(--t2);margin-bottom:8px}.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.steps{display:flex;flex-direction:column;gap:0}
.step{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--line)}.step:last-child{border-bottom:0}
.step .n{width:28px;height:28px;border-radius:50%;background:var(--blue-bg);color:var(--blue);font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.step .n.off{background:var(--gray-bg);color:var(--t3)}.step .n.act{background:var(--red-bg);color:var(--red)}
.step .b{flex:1;min-width:0}.step .b b{display:block;font-size:15px;font-weight:600}.step .b span{display:block;font-size:13px;color:var(--t2);margin-top:3px;line-height:1.5}
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#191f28;color:#fff;padding:12px 18px;border-radius:12px;font-size:14px;opacity:0;transition:.25s;pointer-events:none}.toast.on{opacity:1}
</style>
<div class=wrap>
<header><h1>BTC 급락 매수 봇 <span style="font-size:13px;color:var(--t3);font-weight:500">OKX 선물</span></h1><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class=pill id=mode><i></i>불러오는 중</span><span class=pill id=lev></span><button class=btn id=autobtn onclick="autorun()">-</button><button class=btn id=runbtn onclick="run()">지금 판단하기</button></div></header>
<div class=hero>
  <div class=card><div class=label>비트코인 · 현재가 · 오늘 신호</div><div class=big id=price>-</div><div class=sub id=signal>-</div></div>
  <div class=card><div class=label id=eqlabel>내 자산</div><div class=big id=equity>-</div><div class=sub id=eqchg>-</div></div>
</div>
<div class=hero>
  <div class=card><div class=label id=retlabel>현재 수익률</div><div class=big id=ret>-</div><div class=sub id=retsub>-</div></div>
  <div class=card><div class=label>장기 기대 수익률 (백테스트)</div><div class=big id=exp>-</div><div class=sub id=expsub>-</div></div>
</div>
<div class=card><div style="display:flex;justify-content:space-between;align-items:baseline"><h2>오늘 일봉 (진행 중)</h2><span class=label id=todaynote></span></div><div class=grid id=today></div></div>
<div class=card id=acct style="display:none">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin:0"><span id=accttitle>거래소 계좌</span> <span style="font-size:13px;font-weight:500;color:var(--t3)" id=acctnote></span></h2>
    <div style="display:flex;gap:8px">
      <button class="btn buy" id=buybtn onclick="order('buy')">매수</button>
      <button class="btn sell" id=sellbtn onclick="order('sell')">매도</button>
      <button class=btn id=dbuybtn onclick="order('buy',true)">데모 매수</button>
      <button class=btn id=dsellbtn onclick="order('sell',true)">데모 매도</button>
    </div>
  </div>
  <div class=grid id=acctgrid></div>
</div>
<div class=card><div style="display:flex;justify-content:space-between;align-items:baseline"><h2>비트코인 일봉</h2><span class=label id=chartnote></span></div><canvas id=pchart height=150></canvas></div>
<div class=card><h2>알고리즘 진행 과정</h2><div class=steps id=steps></div></div>
<div class=card><div class=grid id=stats></div></div>
<div class=card><h2>자산 흐름</h2><div class=legend><span><i style="background:#3182f6"></i>OKX 실계좌</span><span><i style="background:#b0b8c1"></i>모의 장부</span></div><canvas id=chart height=90></canvas></div>
<div class=card><h2>판단 기록</h2><div class=list id=runs></div></div>
<div class=card><h2>주문</h2><div class=list id=orders></div></div>
</div>
<div class=toast id=toast></div>
<script>
let chart,pchart;
const f=(x,d=0)=>x==null||x===''?'-':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d});
const Z={long:'롱',short:'숏',wait:'관망',exit:'청산'},ACT={open:'진입',close:'청산',flip:'전환',hold:'유지'};
const toast=t=>{const e=document.getElementById('toast');e.textContent=t;e.classList.add('on');setTimeout(()=>e.classList.remove('on'),2500)};
const when=t=>t?t.slice(5,16).replace('T',' '):'-';
async function load(){
  const s=await (await fetch('/api/state')).json(), l=s.last||{}, st=s.state, done=s.runs.filter(r=>r.status==='done');
  window._state=s;
  const md=document.getElementById('mode'), tag=s.demo?'OKX 데모':'실주문';
  if(s.live_now){md.className=s.demo?'pill test':'pill live';md.innerHTML='<i></i>'+tag+' 중'}
  else{md.className='pill';md.innerHTML='<i></i>'+(s.mode==='auto'&&s.have_keys?`모의 장부 ${s.paper_done}/${s.live_after} · 완료 후 ${tag}`:'모의 장부'+(s.have_keys&&!s.demo?' · 실계좌는 잔고만 읽음':''))}
  document.getElementById('lev').textContent=`${s.leverage}배 · ${Math.round(s.position_pct*100)}%`;
  const rb=document.getElementById('runbtn');if(!s.bot_here){rb.disabled=true;rb.textContent='다른 곳에서 봇 실행 중';rb.title='판단·주문은 터미널(make bot)이 한다 · 이 화면은 보기 전용'}
  const ab=document.getElementById('autobtn');ab.className=s.autorun?'btn':'btn off';ab.textContent=s.autorun?'자동실행 ON':'자동실행 OFF';
  document.getElementById('price').textContent=l.price?f(l.price,1)+' USDT':'-';
  document.getElementById('signal').innerHTML=l.zone?`<span class="zone ${l.zone}">${Z[l.zone]}</span>&nbsp; 1h 이평 대비 ${l.p!=null?(l.p>0?'+':'')+f(l.p,2)+'%':'-'} · 추세 ${l.bull?'통과':'차단'}`:'아직 판단 전';
  const a=s.account, live=a&&!a.error;
  const eq=live?a.equity:l.paper_equity;
  const first=done.length?done[0].paper_equity:null;
  document.getElementById('eqlabel').textContent=live?(s.demo?'내 자산 (OKX 데모)':'내 자산 (OKX 실계좌)'):'내 자산 (모의 장부)';
  document.getElementById('equity').textContent=eq!=null?f(eq,2)+' USDT':'-';
  if(live){
    const p=a.position, pnl=p?a.coin_value-p.entry*p.qty:0, pct=p&&p.entry?pnl/(p.entry*p.qty)*100:null;
    document.getElementById('eqchg').innerHTML=p
      ?`<span class="${pnl>0?'up':pnl<0?'down':'flat'}">${pnl>0?'+':''}${f(pnl,2)} USDT (${pct>0?'+':''}${f(pct,2)}%)</span> 평가손익`
      :'전액 현금 · 신호 대기 중';
  }else{
    const chg=eq!=null&&first?eq-first:null, pc=chg!=null?chg/first*100:null;
    document.getElementById('eqchg').innerHTML=chg==null?'첫 판단 뒤 표시됩니다':`<span class="${chg>0?'up':chg<0?'down':'flat'}">${chg>0?'+':''}${f(chg,2)} USDT (${pc>0?'+':''}${f(pc,2)}%)</span> 시작 대비`;
  }
  // 현재 수익률: 실계좌면 첫 기록된 실계좌 자산 대비, 없으면 모의 장부 시작 대비
  const base=live?s.real_first:first, cur=eq, rt=document.getElementById('ret');
  document.getElementById('retlabel').textContent=live?(s.demo?'현재 수익률 (OKX 데모)':'현재 수익률 (OKX 실계좌)'):'현재 수익률 (모의 장부)';
  if(base&&cur!=null){const d=cur-base,pc=d/base*100;rt.innerHTML=`<span class="${pc>0?'up':pc<0?'down':'flat'}">${pc>0?'+':''}${f(pc,2)}%</span>`;
    document.getElementById('retsub').textContent=`${d>0?'+':''}${f(d,2)} USDT · 시작 ${f(base,2)} → 현재 ${f(cur,2)}`+(live&&a.position?' · 미실현 포함':'')}
  else{rt.textContent='-';document.getElementById('retsub').textContent='첫 판단 기록 뒤 표시됩니다'}
  const b=s.backtest, L=s.leverage, stale=b.lev!==L;
  document.getElementById('exp').innerHTML=`<span class=up>연 +${b.cagr}%</span> <span style="font-size:15px;color:var(--t3)">${b.src} · ${b.lev}배</span>`;
  document.getElementById('expsub').innerHTML=`최대 낙폭 <b class=down>${b.mdd}%</b> · 승률 ${b.win}% · ${b.trades}회 · 거래당 +${b.per_trade}%<br>`
    +b.more.map(([n,c,m])=>`${n} 연 +${c}% / MDD ${m}%`).join(' · ')
    +(stale?`<br><b class=down>지금 설정은 ${L}배 — 위 수치는 ${b.lev}배 기준입니다 (research/aoa/dip_lev2.py 로 재측정)</b>`:'');
  const ac=document.getElementById('acct');
  if(a){
    ac.style.display='';
    if(a.error){
      document.getElementById('acctgrid').innerHTML=`<div class=stat><div class=label>조회 실패</div><div class=v style="font-size:14px">${a.error}</div></div>`;
      document.getElementById('buybtn').disabled=document.getElementById('sellbtn').disabled=true;
      demoBtns(s);
    }else{
      const p=a.position;
      const g=[['가용 USDT',f(a.cash,2),a.cash*s.leverage<s.min_order?'최소 주문 미만':`${s.leverage}배 진입 가능`],
        ['포지션 '+s.coin,f(a.coin_qty,6),p?f(a.coin_value,2)+' USDT':'없음'],
        ['평단',p?f(p.entry,1):'-',p?`현재 ${f(a.price,1)}${p.liq?' · 청산가 '+f(p.liq,1):''}`:''],
        ['평가손익',p?(p.pnl>0?'+':'')+f(p.pnl,2)+' USDT':'-',p&&p.entry?`${(p.pnl/(p.entry*p.qty)*100).toFixed(2)}%`:'']];
      document.getElementById('acctgrid').innerHTML=g.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
      const paper=s.mode==='paper';
      document.getElementById('buybtn').disabled=!s.bot_here||paper||!!p||a.cash*s.leverage<s.min_order;
      document.getElementById('sellbtn').disabled=!s.bot_here||paper||!p;
      document.getElementById('accttitle').textContent=s.demo?'OKX 데모 계좌':'OKX 실계좌';
      document.getElementById('acctnote').textContent=(paper?'MODE=paper 라 실주문이 잠겨 있습니다':(s.demo?'모의투자 서버 · 가짜 돈':'진짜 돈'))+demoNote(s);
      demoBtns(s);
    }
  }else ac.style.display='none';
  const pos=st.side?`<span class="${st.side==='long'?'up':'down'}">${Z[st.side]}</span> ${f(st.qty,6)} ${s.coin}`:'없음';
  const held=st.entered_at?Math.floor((Date.now()-new Date(st.entered_at))/36e5)+'시간째':'현금 대기', R=s.rule;
  const stats=[['모의 장부 포지션',pos,held],['모의 장부 진입가',st.side?f(st.entry,1):'-',
      st.side?`익절 ${f(st.entry*(1+R.TP/100),0)} · 손절 ${f(st.entry*(1-R.SL/100),0)} · 최대 ${R.hours}시간`:''],
    ['마지막 판단',when(l.timestamp),l.action?ACT[l.action]:''],
    ['다음 판단',s.autorun?(s.next_run?when(s.next_run)+' 자동':'준비 중'):'자동실행 OFF',s.autorun?s.interval_min+'분마다':'make on 또는 버튼으로 켜기']];
  document.getElementById('stats').innerHTML=stats.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
  document.getElementById('runs').innerHTML=s.runs.length?s.runs.slice().reverse().map(r=>{
    const err=r.status!=='done', cls=err?'err':(r.action==='hold'?'hold':r.zone);
    return `<div class=row><div class="ic ${cls}">${err?'!':Z[r.zone]||'-'}</div><div class=m><b>${err?'실패':ACT[r.action]||r.action||'-'}${r.position?' · '+Z[r.position]+' 보유 중':''}</b><span>${err?r.status:r.reason||''}</span></div><div class=r><b>${r.p!=null?(r.p>0?'+':'')+f(r.p,2)+'%':'-'}</b><span>${when(r.timestamp)} · ${r.mode}</span></div></div>`}).join(''):'<div class=empty>아직 판단 기록이 없습니다</div>';
  document.getElementById('orders').innerHTML=s.orders.length?s.orders.map(o=>{
    const ok=o.status==='paper'||o.status==='submitted';
    return `<div class=row><div class="ic ${ok?o.side:'err'}">${Z[o.side]}</div><div class=m><b>${ACT[o.action]} · ${o.mode==='live'?tag:o.mode==='demo'?'OKX 데모':'모의 장부'}</b><span>${o.reason||''}</span></div><div class=r><b>${f(o.notional,2)} USDT</b><span>${f(o.qty,6)} ${s.coin} @ ${f(o.price,1)} · ${ok?'체결':o.status}</span></div></div>`}).join(''):'<div class=empty>아직 주문이 없습니다</div>';
  const data={labels:done.map(r=>r.timestamp.slice(5,10)),datasets:[
    {label:'거래소',data:done.map(r=>r.real_equity),borderColor:'#3182f6',borderWidth:2.5,pointRadius:0,tension:.3},
    {label:'모의',data:done.map(r=>r.paper_equity),borderColor:'#b0b8c1',borderWidth:2,pointRadius:0,tension:.3}]};
  const opt={animation:false,spanGaps:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:8}},y:{grid:{color:'#f2f4f6'},border:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:5,callback:v=>f(v,1)}}}};
  if(chart){chart.data=data;chart.update()}else chart=new Chart(document.getElementById('chart'),{type:'line',data,options:opt});
}
async function autorun(){
  const on=!window._state.autorun;
  const r=await (await fetch('/api/autorun',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})})).json();
  toast(r.autorun?'자동실행을 켰어요':'자동실행을 껐어요 (판단 건너뜀)');load();
}
function demoNote(s){
  const d=s.demo_account;
  return !s.demo_keys?' · 데모 키 없음 (.env 의 OKX_DEMO_API_KEY)':d?(d.error?' · 데모 조회 실패':` · 데모 계좌 ${f(d.equity,2)} USDT · 포지션 ${d.position?Z[d.position.side]+' '+f(d.position.qty,6):'없음'}`):'';
}
function demoBtns(s){
  const d=s.demo_account, on=s.bot_here&&s.demo_keys&&!s.demo&&d&&!d.error;
  document.getElementById('dbuybtn').disabled=!on||!!(d&&d.position);
  document.getElementById('dsellbtn').disabled=!on||!(d&&d.position);
}
async function order(action,demo){
  const name=(demo?'데모 ':'')+(action==='buy'?'매수':'매도'), s=window._state;
  const t=prompt(`${name} 주문을 OKX ${demo||s.demo?'데모(모의투자 서버)':'실계좌'}로 보냅니다 (${s.leverage}배). 되돌릴 수 없습니다.\n계속하려면 '실주문' 을 입력하세요.`);
  if(t===null)return;
  const b=document.getElementById((demo?'d':'')+(action==='buy'?'buybtn':'sellbtn'));
  b.disabled=true;
  const r=await (await fetch('/api/order',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,confirm:t.trim(),demo:!!demo})})).json();
  toast(r.ok?`${name} 주문을 보냈습니다 (${f(r.price,1)} USDT)`:`주문 실패: ${r.error}`);
  setTimeout(load,2000);
}
async function run(){const b=document.getElementById('runbtn');b.disabled=true;b.textContent='판단 중…';const r=await (await fetch('/api/run',{method:'POST'})).json();if(!r.ok){toast(r.error);return load()}toast('판단을 시작했어요. 잠시 뒤 갱신됩니다');setTimeout(()=>{load();b.disabled=false;b.textContent='지금 판단하기'},15000)}
async function analysis(){
  const a=await (await fetch('/api/analysis')).json(), st=window._state||{}, l=st.last||{};
  document.getElementById('price').textContent=f(a.price,1)+' USDT';
  document.getElementById('chartnote').textContent=`최근 60일 · 선: 50일선(주황)·200일선(보라) · 마지막은 진행 중인 오늘 봉`;
  const buys=Object.fromEntries(a.orders.filter(o=>o.action==='open').map(o=>[o.timestamp.slice(0,10),o.price]));
  const sells=Object.fromEntries(a.orders.filter(o=>o.action==='close').map(o=>[o.timestamp.slice(0,10),o.price]));
  // 오늘 봉(진행 중) 상세. 봉 경계 09:00 KST — PC 시계가 KST 라고 본다
  const cs=a.candles, t=cs[cs.length-1], y=cs[cs.length-2], pct=(x,y)=>(x/y-1)*100, sg=x=>(x>0?'+':'')+f(x,2)+'%', cl=x=>x>0?'up':x<0?'down':'flat';
  const end=new Date();end.setHours(9,0,0,0);if(end<=Date.now())end.setDate(end.getDate()+1);const left=end-Date.now();
  document.getElementById('todaynote').textContent=`${t.t} · 마감 ${Math.floor(left/36e5)}시간 ${Math.floor(left%36e5/6e4)}분 후 (09:00)`;
  const td=[['현재가',f(t.c,1),`<span class=${cl(pct(t.c,t.o))}>시가 대비 ${sg(pct(t.c,t.o))}</span>`],
    ['전일 종가 대비',`<span class=${cl(pct(t.c,y.c))}>${sg(pct(t.c,y.c))}</span>`,`어제 종가 ${f(y.c,1)}`],
    ['시가',f(t.o,1),''],['고가',f(t.h,1),`시가 대비 ${sg(pct(t.h,t.o))}`],['저가',f(t.l,1),`시가 대비 ${sg(pct(t.l,t.o))}`],
    ['변동폭',sg(pct(t.h,t.l)).slice(1),`${f(t.h-t.l,1)} USDT (어제 ${f(pct(y.h,y.l),2)}%)`],
    ['거래량',f(t.v,0)+' BTC',`어제 ${f(y.v,0)} BTC`],['추세 필터 기준 (전날)',y.t.slice(5),`종가 ${f(y.c,1)} · 50일선 ${f(y.m50,0)} · 200일선 ${f(y.m200,0)}`]];
  document.getElementById('today').innerHTML=td.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
  // 캔들: Chart.js 플로팅 바 두 겹 (심지 [저,고] + 몸통 [시,종]). 마지막 봉은 진행 중
  const cs60=cs.slice(-60), col=c=>c.c>=c.o?'#f04452':'#3182f6';
  const data={labels:cs60.map(c=>c.t.slice(5)),datasets:[
    {type:'bar',data:cs60.map(c=>[c.l,c.h]),backgroundColor:cs60.map(col),barPercentage:.14,categoryPercentage:1,grouped:false,borderWidth:0,order:3},
    {type:'bar',data:cs60.map(c=>[Math.min(c.o,c.c),Math.max(c.o,c.c)]),backgroundColor:cs60.map(col),barPercentage:.72,categoryPercentage:1,grouped:false,borderWidth:0,minBarLength:2,order:2},
    {type:'line',data:cs60.map(c=>buys[c.t]??null),pointStyle:'triangle',pointRadius:9,pointBackgroundColor:'#f04452',pointBorderColor:'#fff',pointBorderWidth:1.5,showLine:false,order:1},
    {type:'line',data:cs60.map(c=>sells[c.t]??null),pointStyle:'triangle',pointRotation:180,pointRadius:9,pointBackgroundColor:'#3182f6',pointBorderColor:'#fff',pointBorderWidth:1.5,showLine:false,order:1},
    {type:'line',data:cs60.map(c=>c.m50),borderColor:'#f59f00',borderWidth:1.5,pointRadius:0,tension:.2,order:0},
    {type:'line',data:cs60.map(c=>c.m200),borderColor:'#7048e8',borderWidth:1.5,pointRadius:0,tension:.2,order:0}]};
  const opt={animation:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false,filter:x=>x.datasetIndex!==0&&x.datasetIndex<4&&x.raw!=null,
      callbacks:{label:x=>{const c=cs60[x.dataIndex];return x.datasetIndex===1?`시 ${f(c.o,1)} · 고 ${f(c.h,1)} · 저 ${f(c.l,1)} · 종 ${f(c.c,1)} (${sg(pct(c.c,c.o))})`:(x.datasetIndex===2?'매수 ':'매도 ')+f(x.raw,1)}}}},
    scales:{x:{grid:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:10}},y:{beginAtZero:false,grace:'3%',grid:{color:'#f2f4f6'},border:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:6,callback:v=>f(v/1e3,1)+'K'}}}};
  if(pchart){pchart.data=data;pchart.update()}else pchart=new Chart(document.getElementById('pchart'),{type:'bar',data,options:opt});
  const R=a.rule, pos=a.position, dv=(a.dev>0?'+':'')+f(a.dev,2)+'%';
  const steps=[
    ['추세 필터 (일봉)',`전날(${a.date}) 종가 <b>${f(a.prev_close,0)}</b> · 50일선 ${f(a.sma50,0)} · 200일선 ${f(a.sma200,0)} → `
      +(a.trend_ok?'<b>통과</b> — 급락이 오면 산다':'<b>차단</b> — 둘 다 위가 아니면 급락이 와도 사지 않는다')+' (매일 09:00 KST 갱신)',a.trend_ok?'':'off'],
    ['급락 확인 (5분봉)',`마지막 완성 5분봉 <b>${a.bar}</b> 종가 <b>${f(a.close,0)}</b> · 1h 이평 ${f(a.ema,0)} · 괴리 <b>${dv}</b><br>`
      +`진입선 <b>${f(a.trigger_px,0)}</b> (1h 이평 −${R.D}%) — 5분봉 종가가 이 아래로 닫히면 매수 · 다음 봉 마감 ${a.next_bar}, 15초 뒤 판단`,''],
    ['자리 결정',a.signal?'<b>급락 매수 자리</b> — 다음 판단에서 진입':(a.trend_ok?`괴리 ${dv} > −${R.D}% → 관망`:'추세 차단 → 관망'),a.signal?'act':''],
    ['포지션 관리',pos?`진입 ${f(pos.entry,1)} → 익절 <b>${f(pos.tp,0)}</b> (+${R.TP}%) / 손절 <b>${f(pos.sl,0)}</b> (−${R.SL}%) / 만기 ${pos.expiry}`
      :`포지션 없음 · 들어가면 +${R.TP}% 익절 / −${R.SL}% 손절 / 최대 ${R.hours}시간`,pos?'act':'off'],
    ['상태기계',l.reason?`${ACT[l.action]||l.action} — ${l.reason}`:'아직 판단 전',l.action==='open'||l.action==='close'?'act':''],
    ['Claude 검토 (체결 직전)',!st.use_claude?'CLAUDE_BASE_URL 이 비어 있어 생략 — 정량 신호만으로 매매':(l.reason||'').includes('Claude')?l.reason.slice(l.reason.indexOf('Claude')):`실제로 주문을 내기 직전에만 호출 · ${st.claude_model} (OmniRoute) · 알고리즘 값을 캔들과 대조하고 사건·급변이면 거부`,(l.reason||'').includes('Claude 거부')?'act':(l.reason||'').includes('Claude')?'':'off'],
    ['주문',l.action==='open'||l.action==='close'?`${ACT[l.action]} 주문 전송 (아래 주문 목록)`:'없음 (보유 유지 또는 관망)',l.action==='open'||l.action==='close'?'act':'off']];
  document.getElementById('steps').innerHTML=steps.map(([t,d,c],i)=>`<div class=step><div class="n ${c}">${i+1}</div><div class=b><b>${t}</b><span>${d}</span></div></div>`).join('');
}
const _load=load;load=async()=>{await _load();analysis().catch(e=>console.error(e))};
load();setInterval(load,30000);
</script></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    BOT_HERE = A.single_instance()                     # 터미널 봇(make bot)이 이미 락을 잡았으면 보기 전용으로 뜬다
    if BOT_HERE:
        A.confirm_live()                               # autotrade.py 와 같은 '실주문' 확인
        threading.Thread(target=A.schedule_forever, daemon=True).start()
        A.log.info("스케줄러 시작 — %d분마다 · 자동실행 %s · %s · %g배", A.INTERVAL_MIN, "ON" if A.autorun() else "OFF",
                   "OKX 데모" if A.X.DEMO else "OKX 실계좌", A.LEVERAGE)
    else:
        A.log.info("봇이 다른 프로세스(터미널 make bot)에서 돌고 있습니다 — 대시보드는 보기 전용 (판단·주문 버튼 잠김)")
    # 인증이 없고 /api/order 가 실주문을 보내므로 이 PC 에서만 접속되게 묶는다.
    # 밖에서 봐야 하면 DASHBOARD_HOST 를 여는 대신 SSH 터널을 써라.
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                port=int(os.environ.get("DASHBOARD_PORT", 8000)))
