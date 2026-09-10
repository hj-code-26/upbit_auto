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

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import autotrade as A

app = FastAPI()
# research_bot_rules.txt ① 현재 봇 규칙, BTC 1배 현물, 2023-01-06~2026-09-04 (1338일). 대시보드 '장기 기대 수익률' 근거
BACKTEST = {"from": "2021-03-01", "to": "2026-09-06", "years": 2016 / 365, "bot": 117, "bot_mdd": -20.9, "trades": 85,
            "win": 36, "days_in": 299, "days": 2016, "hodl": 62, "hodl_mdd": -76.7}   # research_okx_short.txt (OKX 실제 봉)


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
            "leverage": A.LEVERAGE, "position_pct": A.POSITION_PCT, "allow_short": A.ALLOW_SHORT, "extreme_min": A.EXTREME_MIN, "lev_now": A.target_leverage(A.live_now()),
            "paper_done": A.paper_trades_done(), "live_after": A.LIVE_AFTER, "autorun": A.autorun(),
            "use_claude": A.USE_CLAUDE, "claude_model": A.CLAUDE_MODEL, "backtest": BACKTEST,
            "real_first": next((r["real_equity"] for r in rows("SELECT real_equity FROM runs WHERE real_equity > 0 ORDER BY id LIMIT 1")), None),
            "interval_min": A.INTERVAL_MIN, "next_run": A.NEXT_RUN.isoformat() if A.NEXT_RUN else None, "min_order": A.X.MIN_ORDER,
            "state": A.state(), "last": runs[0] if runs else None,
            "account": live_account(),
            "demo_keys": A.X.have_keys(True), "demo_account": live_account(True) if A.X.have_keys(True) and not A.X.DEMO else None,
            "runs": runs[::-1], "orders": rows("SELECT * FROM orders ORDER BY id DESC LIMIT 100")}


@app.get("/api/analysis")
def api_analysis():
    """코인 차트 + 알고리즘 진행 과정 (마지막 완성 일봉의 규칙 8개 판정 + 마지막 완성 4h 봉의 돌파·이탈 → 구역)."""
    pub = A.X.public()
    df, h4 = A.X.candles(pub, 120), A.X.candles(pub, A.H4_CANDLES, "4h")
    ex = A.M.signal(df.iloc[:-1], h4.iloc[:-1])
    ex["candles"] = [{"t": t.strftime("%Y-%m-%d"), "o": r.open, "h": r.high, "l": r.low, "c": r.close, "v": r.volume} for t, r in df.iterrows()]
    ex["price"] = A.X.price(pub)
    ex["next_bar"] = (h4.index[-1] + A.dt.timedelta(hours=4)).strftime("%m-%d %H:%M")   # 진행 중인 4h 봉 마감 (KST)
    ex["orders"] = rows("SELECT timestamp, action, price FROM orders WHERE status IN ('paper','submitted') ORDER BY id")
    return ex


@app.post("/api/run")
def api_run():
    threading.Thread(target=A.run_cycle, args=("버튼",), daemon=True).start()
    return {"ok": True}


@app.post("/api/autorun")
def api_autorun(body: dict):
    """자동실행 온오프. body = {"on": true|false}"""
    return {"ok": True, "autorun": A.set_autorun(bool(body.get("on")))}


@app.post("/api/order")
def api_order(body: dict):
    """수동 주문. body = {"action": "buy"|"sell", "confirm": "실주문", "demo": true|false}"""
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
<title>BTC 국면·돌파 봇 · OKX</title>
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
.rules{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:6px;margin-top:8px}
.rule{display:flex;justify-content:space-between;gap:8px;background:var(--gray-bg);border-radius:10px;padding:8px 12px;font-size:13px}.rule em{font-style:normal;font-weight:700}
.rule.bull em{color:var(--red)}.rule.bear em{color:var(--blue)}.rule.none em{color:var(--t3)}
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#191f28;color:#fff;padding:12px 18px;border-radius:12px;font-size:14px;opacity:0;transition:.25s;pointer-events:none}.toast.on{opacity:1}
</style>
<div class=wrap>
<header><h1>BTC 국면·돌파 봇 <span style="font-size:13px;color:var(--t3);font-weight:500">OKX 선물</span></h1><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class=pill id=mode><i></i>불러오는 중</span><span class=pill id=lev></span><button class=btn id=autobtn onclick="autorun()">-</button><button class=btn id=runbtn onclick="run()">지금 판단하기</button></div></header>
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
  document.getElementById('lev').textContent=(s.lev_now&&s.lev_now[1]?`${s.lev_now[0].toFixed(2)}배 (상한 ${s.leverage}) · ${Math.round(s.position_pct*100)}%`:`${s.leverage}배 · ${Math.round(s.position_pct*100)}%`);
  document.getElementById('lev').title=s.lev_now?s.lev_now[1]:'';
  const ab=document.getElementById('autobtn');ab.className=s.autorun?'btn':'btn off';ab.textContent=s.autorun?'자동실행 ON':'자동실행 OFF';
  document.getElementById('price').textContent=l.price?f(l.price,1)+' USDT':'-';
  document.getElementById('signal').innerHTML=l.zone?`<span class="zone ${l.zone}">${Z[l.zone]}</span>&nbsp; 일봉 롱 ${l.bull}/8 · 숏 ${l.bear}/8`:'아직 판단 전';
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
  const b=s.backtest, yr=x=>(Math.pow(1+x/100,1/b.years)-1)*100, L=s.leverage;
  document.getElementById('exp').innerHTML=`<span class=up>연 +${f(yr(b.bot)*L,1)}%</span> <span style="font-size:15px;color:var(--t3)">${L}배 근사</span>`;
  document.getElementById('expsub').innerHTML=`1배: 연 +${f(yr(b.bot),1)}% (누적 +${b.bot}%, MDD ${b.bot_mdd}%, 승률 ${b.win}% · ${b.trades}회) · ${L}배는 단리 근사, MDD ≈ ${f(b.bot_mdd*L,0)}% (청산·펀딩비 미반영)<br>비교 BTC 그냥 보유: 연 +${f(yr(b.hodl),1)}% (MDD ${b.hodl_mdd}%) · 검증 ${b.from}~${b.to}, 시장 진입 ${b.days_in}/${b.days}일`;
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
      document.getElementById('buybtn').disabled=paper||!!p||a.cash*s.leverage<s.min_order;
      document.getElementById('sellbtn').disabled=paper||!p;
      document.getElementById('accttitle').textContent=s.demo?'OKX 데모 계좌':'OKX 실계좌';
      document.getElementById('acctnote').textContent=(paper?'MODE=paper 라 실주문이 잠겨 있습니다':(s.demo?'모의투자 서버 · 가짜 돈':'진짜 돈'))+demoNote(s);
      demoBtns(s);
    }
  }else ac.style.display='none';
  const pos=st.side?`<span class="${st.side==='long'?'up':'down'}">${Z[st.side]}</span> ${f(st.qty,6)} ${s.coin}`:'없음';
  const held=st.entered_at?Math.floor((Date.now()-new Date(st.entered_at))/864e5)+'일째':'현금 대기';
  const stats=[['모의 장부 포지션',pos,held],['모의 장부 진입가',st.side?f(st.entry,1):'-',''],
    ...(st.watch_bar?[['진입 대기 (하한 방어)',`하한 ${f(st.watch_hi,0)} 확인 중`,`4h 돌파봉 ${st.watch_bar} · 분봉이 이 선을 지키거나 되찾으면 매수, 다음 4h 봉까지 못 지키면 자리 포기`]]:[]),
    ['마지막 판단',when(l.timestamp),l.action?ACT[l.action]:''],
    ['다음 판단',s.autorun?(s.next_run?when(s.next_run)+' 자동':'준비 중'):'자동실행 OFF',s.autorun?s.interval_min+'분마다':'make on 또는 버튼으로 켜기']];
  document.getElementById('stats').innerHTML=stats.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
  document.getElementById('runs').innerHTML=s.runs.length?s.runs.slice().reverse().map(r=>{
    const err=r.status!=='done', cls=err?'err':(r.action==='hold'?'hold':r.zone);
    return `<div class=row><div class="ic ${cls}">${err?'!':Z[r.zone]||'-'}</div><div class=m><b>${err?'실패':ACT[r.action]||r.action||'-'}${r.position?' · '+Z[r.position]+' 보유 중':''}</b><span>${err?r.status:r.reason||''}</span></div><div class=r><b>${r.bull!=null?'강세 '+r.bull+'/8':'-'}</b><span>${when(r.timestamp)} · ${r.mode}</span></div></div>`}).join(''):'<div class=empty>아직 판단 기록이 없습니다</div>';
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
  const d=s.demo_account, on=s.demo_keys&&!s.demo&&d&&!d.error;
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
async function run(){const b=document.getElementById('runbtn');b.disabled=true;b.textContent='판단 중…';await fetch('/api/run',{method:'POST'});toast('판단을 시작했어요. 잠시 뒤 갱신됩니다');setTimeout(()=>{load();b.disabled=false;b.textContent='지금 판단하기'},15000)}
async function analysis(){
  const a=await (await fetch('/api/analysis')).json(), st=window._state||{}, l=st.last||{};
  document.getElementById('price').textContent=f(a.price,1)+' USDT';
  document.getElementById('chartnote').textContent=`최근 60일 · 마지막은 진행 중인 오늘 봉 · 신호 기준봉 ${a.date}`;
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
    ['거래량',f(t.v,0)+' BTC',`어제 ${f(y.v,0)} BTC`],['기준봉 (신호 계산)',y.t.slice(5),`종가 ${f(y.c,1)} · ${sg(pct(y.c,y.o))}`]];
  document.getElementById('today').innerHTML=td.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
  // 캔들: Chart.js 플로팅 바 두 겹 (심지 [저,고] + 몸통 [시,종]). 마지막 봉은 진행 중
  const cs60=cs.slice(-60), col=c=>c.c>=c.o?'#f04452':'#3182f6';
  const data={labels:cs60.map(c=>c.t.slice(5)),datasets:[
    {type:'bar',data:cs60.map(c=>[c.l,c.h]),backgroundColor:cs60.map(col),barPercentage:.14,categoryPercentage:1,grouped:false,borderWidth:0,order:3},
    {type:'bar',data:cs60.map(c=>[Math.min(c.o,c.c),Math.max(c.o,c.c)]),backgroundColor:cs60.map(col),barPercentage:.72,categoryPercentage:1,grouped:false,borderWidth:0,minBarLength:2,order:2},
    {type:'line',data:cs60.map(c=>buys[c.t]??null),pointStyle:'triangle',pointRadius:9,pointBackgroundColor:'#f04452',pointBorderColor:'#fff',pointBorderWidth:1.5,showLine:false,order:1},
    {type:'line',data:cs60.map(c=>sells[c.t]??null),pointStyle:'triangle',pointRotation:180,pointRadius:9,pointBackgroundColor:'#3182f6',pointBorderColor:'#fff',pointBorderWidth:1.5,showLine:false,order:1}]};
  const opt={animation:false,plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false,filter:x=>x.datasetIndex!==0&&x.raw!=null,
      callbacks:{label:x=>{const c=cs60[x.dataIndex];return x.datasetIndex===1?`시 ${f(c.o,1)} · 고 ${f(c.h,1)} · 저 ${f(c.l,1)} · 종 ${f(c.c,1)} (${sg(pct(c.c,c.o))})`:(x.datasetIndex===2?'매수 ':'매도 ')+f(x.raw,1)}}}},
    scales:{x:{grid:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:10}},y:{beginAtZero:false,grace:'3%',grid:{color:'#f2f4f6'},border:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:6,callback:v=>f(v/1e3,1)+'K'}}}};
  if(pchart){pchart.data=data;pchart.update()}else pchart=new Chart(document.getElementById('pchart'),{type:'bar',data,options:opt});
  const rules=a.rules.map(r=>`<div class="rule ${r.bull?'bull':r.bear?'bear':'none'}"><span>${r.name}</span><em>${r.bull?'롱':r.bear?'숏':'중립'} · ${r.value}</em></div>`).join('');
  const exits=[a.exit_long?'롱 청산':'',a.exit_short?'숏 청산':''].filter(Boolean).join(' · ');
  const zoneWhy=a.zone==='long'?`일봉 롱 ${a.bull}≥${a.conf} 이고 4h 종가 ${f(a.close,0)} > 직전 ${a.h4_n}봉 고가 ${f(a.hi,0)} 돌파 → <b>롱 자리 (상승 베팅)</b>`
    :a.zone==='short'?`일봉 숏 ${a.bear}≥${a.conf} 이고 4h 종가 ${f(a.close,0)} < 직전 ${a.h4_n}봉 저가 ${f(a.lo_n,0)} 이탈 → <b>숏 자리 (하락 베팅)</b>`+(st.allow_short?'':' · <b>ALLOW_SHORT=0 이라 진입하지 않음</b>')
    :a.bull_regime?`일봉 롱 ${a.bull}≥${a.conf} 롱 국면이지만 4h 돌파 없음 (종가 ${f(a.close,0)} ≤ ${f(a.hi,0)}) → <b>자리 없음</b>`
    :a.bear_regime?`일봉 숏 ${a.bear}≥${a.conf} 숏 국면이지만 4h 이탈 없음 (종가 ${f(a.close,0)} ≥ ${f(a.lo_n,0)}) → <b>자리 없음</b>`
    :`국면 없음 (롱 ${a.bull}/8 · 숏 ${a.bear}/8, 둘 다 ${a.conf} 미만) → <b>관망</b>`;
  const steps=[
    ['일봉 수집',`OKX ${a.candles.length}일봉 중 진행 중인 오늘 봉은 제외 → 기준봉 <b>${a.date}</b> (국면은 09:00 KST 에 갱신)`,''],
    ['지표 8개 판정 (국면)',`롱 <b>${a.bull}</b>/8 · 숏 <b>${a.bear}</b>/8 (${a.conf}개 이상이면 그 방향 국면 — 롱 국면과 숏 국면은 동시에 성립하지 않는다)<div class=rules>${rules}</div>`,''],
    ['4h 극단 점수 (평균회귀 지표)',`고점 <b>${a.extreme_hi}</b>/${a.extreme_rules} · 저점 <b>${a.extreme_lo}</b>/${a.extreme_rules} (RSI·볼린저·스토캐스틱·도치안·꼬리·거래량·ATR)${st.extreme_min?`<br>EXTREME_MIN=${st.extreme_min} — 이 점수 미만이면 진입하지 않는다`:'<br>EXTREME_MIN=0 (필터 꺼짐). 참고용 표시'}`,''],
    ['4h 봉 돌파·이탈',`마지막 완성 4h 봉 <b>${a.bar}</b> 종가 <b>${f(a.close,0)}</b><br>롱 진입선 ${f(a.hi,0)} (직전 ${a.h4_n}봉 고가) · 롱 청산선 ${f(a.lo,0)} (${a.h4_m}봉 저가)<br>숏 진입선 ${f(a.lo_n,0)} (직전 ${a.h4_n}봉 저가) · 숏 청산선 ${f(a.hi_m,0)} (${a.h4_m}봉 고가)<br>다음 봉 마감 ${a.next_bar}, 그 뒤 ${st.interval_min}분 안에 판단${exits?'<br>지금 켜진 청산 조건: <b>'+exits+'</b>':''}`,''],
    ['자리 결정',zoneWhy,''],
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
    A.confirm_live()                                   # autotrade.py 와 같은 '실주문' 확인
    threading.Thread(target=A.schedule_forever, daemon=True).start()
    A.log.info("스케줄러 시작 — %d분마다 · 자동실행 %s · %s · %g배", A.INTERVAL_MIN, "ON" if A.autorun() else "OFF",
               "OKX 데모" if A.X.DEMO else "OKX 실계좌", A.LEVERAGE)
    # 인증이 없고 /api/order 가 실주문을 보내므로 이 PC 에서만 접속되게 묶는다.
    # 밖에서 봐야 하면 DASHBOARD_HOST 를 여는 대신 SSH 터널을 써라.
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                port=int(os.environ.get("DASHBOARD_PORT", 8000)))
