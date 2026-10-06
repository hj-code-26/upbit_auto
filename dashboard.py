"""읽기 전용 대시보드 + 스케줄러 (매일 00:05 UTC run_cycle). 수동 주문은 없다 (리스크 규칙 우회 방지).
사용: python dashboard.py   → http://localhost:8000   (SCHEDULE=0 이면 화면만)"""
import os
import sqlite3
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import autotrade as A

app = FastAPI()
SCHEDULE = os.environ.get("SCHEDULE", "1") != "0"


def rows(sql):
    with A.db() as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql)]


@app.get("/api/state")
def api_state():
    return {"mode": A.MODE, "schedule": SCHEDULE,
            "daily": rows("SELECT * FROM daily ORDER BY date, inst"),
            "orders": rows("SELECT * FROM orders ORDER BY ts DESC LIMIT 100"),
            "ledger": {i: A.kv_get(i) for i in A.INSTS}}


PAGE = r"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>RLS 봇</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f2f4f6;--card:#fff;--t1:#191f28;--t3:#8b95a1;--red:#f04452;--blue:#3182f6}
@media (prefers-color-scheme:dark){:root{--bg:#101318;--card:#1b1f26;--t1:#e8ebed;--t3:#8b95a1}}
body{margin:0;background:var(--bg);color:var(--t1);font-family:-apple-system,system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:20px 16px 60px}.card{background:var(--card);border-radius:16px;padding:20px;margin-bottom:12px;overflow-x:auto}
h1{font-size:20px}h2{font-size:16px;margin:0 0 12px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:6px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--bg)}th{color:var(--t3);font-weight:500}td:first-child,th:first-child{text-align:left}
.L{color:var(--red)}.S{color:var(--blue)}.f{color:#e67e22}
</style><div class=wrap><h1>RLS 레짐 롱/숏 <span id=mode style="font-size:14px;color:var(--t3)"></span></h1>
<div class=card id=now></div><div class=card><h2>종목 자산 vs 기준선 (시작 대비 %)</h2><canvas id=chart height=90></canvas></div>
<div class=card><h2>일별 기록</h2><table id=daily></table></div><div class=card><h2>주문</h2><table id=orders></table></div></div>
<script>
const f=(x,d=2)=>x==null?'-':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d}),P=x=>x==null?'-':(x*100).toFixed(2)+'%';
const S={1:'<span class=L>롱</span>','-1':'<span class=S>숏</span>',0:'무포지션',null:'-'};
let chart;
async function load(){
  const s=await (await fetch('/api/state')).json();
  document.getElementById('mode').textContent=`MODE=${s.mode} · ${s.schedule?'매일 00:05 UTC':'스케줄 꺼짐'}`;
  const last={};s.daily.forEach(r=>last[r.inst]=r);
  document.getElementById('now').innerHTML='<table><tr><th>종목</th><th>날짜</th><th>레짐</th><th>목표</th><th>실제</th><th>손절가</th><th>종목자산</th><th>낙폭</th><th>수익</th><th>매수보유</th><th>R4</th><th>플래그</th></tr>'+
    Object.values(last).map(r=>`<tr><td>${r.inst}</td><td>${r.date}</td><td>${S[r.regime]}</td><td>${S[r.target]}</td><td>${S[r.actual]}</td><td>${f(r.stop_px)}</td><td>${f(r.sym_equity)}</td><td>${P(r.dd)}</td><td>${P(r.ret)}</td><td>${P(r.bh_ret)}</td><td>${P(r.r4_ret)}</td><td class=f>${r.flags||''}${s.ledger[r.inst]?.halted?' 정지':''}</td></tr>`).join('')+'</table>';
  document.getElementById('daily').innerHTML='<tr><th>날짜</th><th>종목</th><th>레짐</th><th>실제</th><th>체결가</th><th>슬리피지bp</th><th>수수료누적</th><th>펀딩누적</th><th>자산</th><th>플래그</th><th>메모</th></tr>'+
    s.daily.slice().reverse().map(r=>`<tr><td>${r.date}</td><td>${r.inst.split('-')[0]}</td><td>${S[r.regime]}</td><td>${S[r.actual]}</td><td>${f(r.fill_px)}</td><td>${f(r.slip_bps,1)}</td><td>${f(r.fee_cum)}</td><td>${f(r.funding_cum)}</td><td>${f(r.equity)}</td><td class=f>${r.flags||''}</td><td style="text-align:left">${r.note||''}</td></tr>`).join('');
  document.getElementById('orders').innerHTML='<tr><th>시각</th><th>clOrdId</th><th>동작</th><th>방향</th><th>수량</th><th>평단</th><th>수수료</th><th>상태</th></tr>'+
    s.orders.map(o=>`<tr><td>${o.ts.slice(0,16)}</td><td>${o.cid}</td><td>${o.action}</td><td>${S[o.side]}</td><td>${o.sz}</td><td>${f(o.avg_px)}</td><td>${f(o.fee,4)}</td><td>${o.state}</td></tr>`).join('');
  const dates=[...new Set(s.daily.map(r=>r.date))],sets=[];
  const C=['#3182f6','#f04452','#8b95a1','#b0b8c1','#00c471','#ffb300'];
  Object.keys(last).forEach((inst,i)=>[['ret','RLS'],['bh_ret','매수보유'],['r4_ret','R4']].forEach(([k,n],j)=>sets.push({label:inst.split('-')[0]+' '+n,
    data:dates.map(d=>{const r=s.daily.find(x=>x.date===d&&x.inst===inst);return r?r[k]*100:null}),borderColor:C[(i*3+j)%6],borderDash:j?[4,3]:[],pointRadius:0,borderWidth:2})));
  const data={labels:dates,datasets:sets};
  if(chart){chart.data=data;chart.update()}else chart=new Chart(document.getElementById('chart'),{type:'line',data,options:{animation:false,spanGaps:true}});
}
load();setInterval(load,60000);
</script></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    if SCHEDULE:
        threading.Thread(target=A.schedule_forever, daemon=True).start()
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"), port=int(os.environ.get("DASHBOARD_PORT", 8000)))
