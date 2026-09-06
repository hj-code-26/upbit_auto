"""대시보드 (토스증권풍) + 자동매매 스케줄러. 이것만 띄우면 봇도 같이 돈다.

  · 실계좌: 업비트 잔고·보유수량·평단·평가손익을 폴링해서 그대로 보여준다 (키가 있을 때)
  · 스케줄러: 매일 TRADE_TIME(KST) 에 run_cycle() — autotrade.py 를 따로 띄울 필요가 없다
  · 수동 주문: 매수/매도 버튼. auto 모드의 '모의 N회' 게이트를 건너뛰고 바로 실주문이 나가므로
              '실주문' 을 타이핑해야만 전송된다 (실수 클릭·외부 요청 차단)
사용: python dashboard.py   → http://localhost:8000"""
import os
import sqlite3
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import autotrade as A

app = FastAPI()
SCHEDULE = os.environ.get("SCHEDULE", "1") != "0"      # 0 이면 화면만 띄우고 자동매매는 돌리지 않는다


def rows(sql):
    with A.db() as c:
        c.row_factory = sqlite3.Row
        out = [dict(r) for r in c.execute(sql)]

    return out


def live_account():
    """업비트 실계좌 현황. 키가 없거나 조회가 실패해도 화면은 떠야 하므로 예외를 값으로 돌려준다."""
    if not A.HAVE_KEYS:
        return None
    try:
        return A.X.snapshot(A.X.client())
    except Exception as e:                                             # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


@app.get("/api/state")
def api_state():
    runs = rows("SELECT * FROM runs ORDER BY id DESC LIMIT 200")
    return {"mode": A.MODE, "live_now": A.live_now(), "have_keys": A.HAVE_KEYS, "coin": A.X.COIN,
            "paper_done": A.paper_trades_done(), "live_after": A.LIVE_AFTER, "schedule": SCHEDULE,
            "trade_time": A.TRADE_TIME, "min_order": A.X.MIN_ORDER_KRW,
            "claude": A.USE_CLAUDE, "state": A.state(), "last": runs[0] if runs else None,
            "account": live_account(),
            "runs": runs[::-1], "orders": rows("SELECT * FROM orders ORDER BY id DESC LIMIT 100")}


@app.post("/api/run")
def api_run():
    threading.Thread(target=A.run_cycle, daemon=True).start()
    return {"ok": True}


@app.post("/api/order")
def api_order(body: dict):
    """수동 실주문. body = {"action": "buy"|"sell", "confirm": "실주문"}"""
    if body.get("confirm") != "실주문":
        return JSONResponse({"ok": False, "error": "확인 문구가 다릅니다"}, status_code=400)
    if body.get("action") not in ("buy", "sell"):
        return JSONResponse({"ok": False, "error": "action 은 buy 또는 sell"}, status_code=400)
    try:
        return {"ok": True, **A.manual_order(body["action"])}
    except Exception as e:                                             # noqa: BLE001
        A.log.error("수동 주문 실패: %s", e)
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=400)


PAGE = r"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>BTC 관망형 봇</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f2f4f6;--card:#fff;--t1:#191f28;--t2:#4e5968;--t3:#8b95a1;--line:#f2f4f6;--blue:#3182f6;--blue-bg:#e8f3ff;--red:#f04452;--red-bg:#ffeef0;--gray-bg:#f2f4f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font-family:Pretendard,-apple-system,BlinkMacSystemFont,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
header h1{font-size:22px;font-weight:700;margin:0;letter-spacing:-.3px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;background:var(--gray-bg);color:var(--t2)}
.pill.live{background:var(--red-bg);color:var(--red)}.pill.test{background:var(--blue-bg);color:var(--blue)}
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
.zone.long{background:var(--red-bg);color:var(--red)}.zone.short{background:var(--blue-bg);color:var(--blue)}.zone.wait{background:var(--gray-bg);color:var(--t2)}
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
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#191f28;color:#fff;padding:12px 18px;border-radius:12px;font-size:14px;opacity:0;transition:.25s;pointer-events:none}.toast.on{opacity:1}
</style>
<div class=wrap>
<header><h1>BTC 관망형 봇</h1><div style="display:flex;gap:10px;align-items:center"><span class=pill id=mode><i></i>불러오는 중</span><button class=btn id=runbtn onclick="run()">지금 판단하기</button></div></header>
<div class=hero>
  <div class=card><div class=label>비트코인 · 오늘 신호</div><div class=big id=price>-</div><div class=sub id=signal>-</div></div>
  <div class=card><div class=label id=eqlabel>내 자산</div><div class=big id=equity>-</div><div class=sub id=eqchg>-</div></div>
</div>
<div class=card id=acct style="display:none">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <h2 style="margin:0">실계좌 <span style="font-size:13px;font-weight:500;color:var(--t3)" id=acctnote>업비트</span></h2>
    <div style="display:flex;gap:8px">
      <button class="btn buy" id=buybtn onclick="order('buy')">매수</button>
      <button class="btn sell" id=sellbtn onclick="order('sell')">매도</button>
    </div>
  </div>
  <div class=grid id=acctgrid></div>
</div>
<div class=card><div class=grid id=stats></div></div>
<div class=card><h2>자산 흐름</h2><div class=legend><span><i style="background:#3182f6"></i>실계좌</span><span><i style="background:#b0b8c1"></i>모의</span></div><canvas id=chart height=90></canvas></div>
<div class=card><h2>판단 기록</h2><div class=list id=runs></div></div>
<div class=card><h2>주문</h2><div class=list id=orders></div></div>
</div>
<div class=toast id=toast></div>
<script>
let chart;
const f=(x,d=0)=>x==null||x===''?'-':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d});
const Z={long:'롱',short:'숏',wait:'관망'},ACT={open:'진입',close:'청산',flip:'전환',hold:'유지'};
const toast=t=>{const e=document.getElementById('toast');e.textContent=t;e.classList.add('on');setTimeout(()=>e.classList.remove('on'),2500)};
const when=t=>t?t.slice(5,16).replace('T',' '):'-';
async function load(){
  const s=await (await fetch('/api/state')).json(), l=s.last||{}, st=s.state, done=s.runs.filter(r=>r.status==='done');
  const md=document.getElementById('mode');
  if(s.live_now){md.className='pill live';md.innerHTML='<i></i>실주문 중'}
  else{md.className='pill';md.innerHTML='<i></i>'+(s.mode==='auto'&&s.have_keys?`모의 ${s.paper_done}/${s.live_after} · 완료 후 실주문`:'모의 투자')}
  document.getElementById('price').textContent=l.price?f(l.price)+'원':'-';
  document.getElementById('signal').innerHTML=l.zone?`<span class="zone ${l.zone}">${Z[l.zone]}</span>&nbsp; 상승확률 ${f(l.p*100)}% · 강세 ${l.bull}/8 · 약세 ${l.bear}/8`:'아직 판단 전';
  const a=s.account, live=a&&!a.error;
  const eq=live?a.equity:l.paper_equity;
  const first=done.length?done[0].paper_equity:null;
  document.getElementById('eqlabel').textContent=live?'내 자산 (실계좌)':'내 자산 (모의)';
  document.getElementById('equity').textContent=eq!=null?f(eq)+'원':'-';
  if(live){
    const p=a.position, pnl=p?a.coin_value-p.entry*p.qty:0, pct=p&&p.entry?pnl/(p.entry*p.qty)*100:null;
    document.getElementById('eqchg').innerHTML=p
      ?`<span class="${pnl>0?'up':pnl<0?'down':'flat'}">${pnl>0?'+':''}${f(pnl,0)}원 (${pct>0?'+':''}${f(pct,2)}%)</span> 평가손익`
      :'전액 현금 · 신호 대기 중';
  }else{
    const chg=eq!=null&&first?eq-first:null, pc=chg!=null?chg/first*100:null;
    document.getElementById('eqchg').innerHTML=chg==null?'첫 판단 뒤 표시됩니다':`<span class="${chg>0?'up':chg<0?'down':'flat'}">${chg>0?'+':''}${f(chg,0)}원 (${pc>0?'+':''}${f(pc,2)}%)</span> 시작 대비`;
  }
  const ac=document.getElementById('acct');
  if(a){
    ac.style.display='';
    if(a.error){
      document.getElementById('acctgrid').innerHTML=`<div class=stat><div class=label>조회 실패</div><div class=v style="font-size:14px">${a.error}</div></div>`;
      document.getElementById('buybtn').disabled=document.getElementById('sellbtn').disabled=true;
    }else{
      const p=a.position;
      const g=[['원화',f(a.krw)+'원',a.krw<s.min_order?'최소 주문 미만':'매수 가능'],
        ['보유 '+s.coin,f(a.coin_qty,8),p?f(a.coin_value)+'원':'없음'],
        ['평단',p?f(p.entry)+'원':'-',p?`현재 ${f(a.price)}원`:''],
        ['평가손익',p?(p.pnl>0?'+':'')+f(p.pnl,0)+'원':'-',p&&p.entry?`${(p.pnl/(p.entry*p.qty)*100).toFixed(2)}%`:'']];
      document.getElementById('acctgrid').innerHTML=g.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
      const paper=s.mode==='paper';
      document.getElementById('buybtn').disabled=paper||!!p||a.krw<s.min_order;
      document.getElementById('sellbtn').disabled=paper||!p;
      document.getElementById('acctnote').textContent=paper?'MODE=paper 라 수동 주문이 잠겨 있습니다':'';
    }
  }else ac.style.display='none';
  const pos=st.side?`<span class="${st.side==='long'?'up':'down'}">${Z[st.side]}</span> ${f(st.qty,8)} ${s.coin}`:'없음';
  const held=st.entered_at?Math.floor((Date.now()-new Date(st.entered_at))/864e5)+'일째':'현금 대기';
  const stats=[['모의 포지션',pos,held],['모의 진입가',st.side?f(st.entry)+'원':'-',''],
    ['마지막 판단',when(l.timestamp),l.action?ACT[l.action]:''],
    ['다음 판단',s.schedule?'매일 '+s.trade_time:'스케줄 꺼짐','KST'],['Claude 검토',s.claude?'사용':'미사용','']];
  document.getElementById('stats').innerHTML=stats.map(([k,v,d])=>`<div class=stat><div class=label>${k}</div><div class=v>${v}</div>${d?`<div class=label style="margin:4px 0 0">${d}</div>`:''}</div>`).join('');
  document.getElementById('runs').innerHTML=s.runs.length?s.runs.slice().reverse().map(r=>{
    const err=r.status!=='done', cls=err?'err':(r.action==='hold'?'hold':r.zone);
    return `<div class=row><div class="ic ${cls}">${err?'!':Z[r.zone]||'-'}</div><div class=m><b>${err?'실패':ACT[r.action]||r.action||'-'}${r.position?' · '+Z[r.position]+' 보유 중':''}</b><span>${err?r.status:r.reason||''}</span></div><div class=r><b>${r.p!=null?f(r.p*100)+'%':'-'}</b><span>${when(r.timestamp)} · ${r.mode}</span></div></div>`}).join(''):'<div class=empty>아직 판단 기록이 없습니다</div>';
  document.getElementById('orders').innerHTML=s.orders.length?s.orders.map(o=>{
    const ok=o.status==='paper'||o.status==='submitted';
    return `<div class=row><div class="ic ${ok?o.side:'err'}">${Z[o.side]}</div><div class=m><b>${ACT[o.action]} · ${o.mode==='live'?'실주문':'모의'}</b><span>${o.reason||''}</span></div><div class=r><b>${f(o.notional_krw)}원</b><span>${f(o.qty,8)} ${s.coin} @ ${f(o.price)} · ${ok?'체결':o.status}</span></div></div>`}).join(''):'<div class=empty>아직 주문이 없습니다</div>';
  const data={labels:done.map(r=>r.timestamp.slice(5,10)),datasets:[
    {label:'실계좌',data:done.map(r=>r.mode.includes('모의')?null:r.equity),borderColor:'#3182f6',borderWidth:2.5,pointRadius:0,tension:.3},
    {label:'모의',data:done.map(r=>r.paper_equity),borderColor:'#b0b8c1',borderWidth:2,pointRadius:0,tension:.3}]};
  const opt={animation:false,spanGaps:true,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:8}},y:{grid:{color:'#f2f4f6'},border:{display:false},ticks:{color:'#8b95a1',maxTicksLimit:5,callback:v=>f(v,1)}}}};
  if(chart){chart.data=data;chart.update()}else chart=new Chart(document.getElementById('chart'),{type:'line',data,options:opt});
}
async function order(action){
  const name=action==='buy'?'매수':'매도';
  const t=prompt(`${name} 실주문을 거래소로 보냅니다. 되돌릴 수 없습니다.\n계속하려면 '실주문' 을 입력하세요.`);
  if(t===null)return;
  const b=document.getElementById(action==='buy'?'buybtn':'sellbtn');
  b.disabled=true;
  const r=await (await fetch('/api/order',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,confirm:t.trim()})})).json();
  toast(r.ok?`${name} 주문을 보냈습니다 (${f(r.price)}원)`:`주문 실패: ${r.error}`);
  setTimeout(load,2000);
}
async function run(){const b=document.getElementById('runbtn');b.disabled=true;b.textContent='판단 중…';await fetch('/api/run',{method:'POST'});toast('판단을 시작했어요. 잠시 뒤 갱신됩니다');setTimeout(()=>{load();b.disabled=false;b.textContent='지금 판단하기'},15000)}
load();setInterval(load,30000);
</script></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    A.confirm_live()                                   # autotrade.py 와 같은 '실주문' 확인
    if SCHEDULE:
        threading.Thread(target=A.schedule_forever, daemon=True).start()
        A.log.info("스케줄러 시작 — 매일 %s KST", A.TRADE_TIME)
    else:
        A.log.info("SCHEDULE=0 — 화면만 띄운다 (자동 매매 없음)")
    # 인증이 없고 /api/order 가 실주문을 보내므로 이 PC 에서만 접속되게 묶는다.
    # 밖에서 봐야 하면 DASHBOARD_HOST 를 여는 대신 SSH 터널을 써라.
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                port=int(os.environ.get("DASHBOARD_PORT", 8000)))
