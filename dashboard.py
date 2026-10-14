"""읽기 전용 대시보드 + 스케줄러 (몫 작업 UTC 00·08·16:02 + 매시 07분 점검). 수동 주문은 없다 (리스크 규칙 우회 방지).
사용: python dashboard.py   → http://localhost:8000   (SCHEDULE=0 이면 화면만)"""
import json
import os
import pathlib
import sqlite3
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import autotrade as A

app = FastAPI()
SCHEDULE = os.environ.get("SCHEDULE", "1") != "0"
RUNS = pathlib.Path(__file__).resolve().parent / "reports" / "rlsc_runs"
STATE_TXT = {"RUNNING": "정상", "HALTING": "진입 금지 기록 (청산 전)", "FLATTENING": "전량 청산 진행 중 (잔여 수량 있음)",
             "HALTED": "전량 정리 완료 (봇 수량 0 확인)", "DEGRADED": "조회·주문 실패 (재시도 중, 노출 확인 필요)"}


def backtest_for(h, root=RUNS):
    """전략 설정 해시가 같은 백테스트 manifest 만 (다른 구성의 수치를 이 계정 성과처럼 보이지 않게)."""
    for f in sorted(root.glob("*/manifest.json")) if root.exists() else []:
        m = json.loads(f.read_text())
        if m.get("strategy_hash") == h:
            return m
    return None


def rows(sql):
    with A.db() as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql)]


@app.get("/api/state")
def api_state():
    cfg = A.kv_get("config") or A.effective_config()
    r = A.risk_get()
    return {"mode": A.MODE, "schedule": SCHEDULE, "config": cfg, "backtest": backtest_for(cfg["strategy_hash"]),
            "risk": {**r, "label": STATE_TXT.get(r["state"], r["state"]),
                     "entry_blocks": A.entry_blocks(r, A.dt.datetime.now(A.dt.UTC), A.kv_get("acct", {}))},
            "daily": rows("SELECT * FROM daily ORDER BY date"),
            "trades": rows("SELECT * FROM trades ORDER BY exit_ts DESC LIMIT 200"),
            "orders": rows("SELECT * FROM orders ORDER BY ts DESC LIMIT 100"),
            "acct": A.kv_get("acct", {}), "regimes": A.kv_get("regimes", {}), "okx": A.kv_get("okx_balance"), "tranches": A.TRANCHES,
            "ledger": {A.tkey(i, k): A.ledger(i, k) for i in A.COINS for k in range(A.N)}, "forward": forward_json()}


def forward_json():
    """forward.py 결과 (실거래 vs 리플레이). 없거나 깨졌으면 None."""
    try:
        return json.loads((A.ROOT / "forward.json").read_text())
    except (OSError, ValueError):
        return None


PAGE = r"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>RLS-C v1</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#f2f4f6;--card:#fff;--t1:#191f28;--t3:#8b95a1;--red:#f04452;--blue:#3182f6}
@media (prefers-color-scheme:dark){:root{--bg:#101318;--card:#1b1f26;--t1:#e8ebed;--t3:#8b95a1}}
body{margin:0;background:var(--bg);color:var(--t1);font-family:-apple-system,system-ui,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}.card{background:var(--card);border-radius:16px;padding:20px;margin-bottom:12px;overflow-x:auto}
h1{font-size:20px}h2{font-size:16px;margin:0 0 12px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:6px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--bg)}th{color:var(--t3);font-weight:500}td:first-child,th:first-child{text-align:left}
.L{color:var(--red)}.S{color:var(--blue)}.f{color:#e67e22}
</style><div class=wrap><h1>RLS-C v1 <span id=mode style="font-size:14px;color:var(--t3)"></span></h1>
<div class=card id=now></div><div class=card><h2>가격 범위와 진입 기준 <span style="font-size:13px;color:var(--t3);font-weight:400">예측 아님 · 최근 60일 변동성이 이어진다고 볼 때의 범위</span></h2><canvas id=ol height=110></canvas><div id=olt style="margin-top:12px"></div></div><div class=card><h2>자산 vs 같은 기간 기준선 (시작 대비 %)</h2><canvas id=chart height=90></canvas></div>
<div class=card><h2>거래</h2><table id=trades></table></div><div class=card><h2>일별 기록</h2><table id=daily></table></div><div class=card><h2>주문</h2><table id=orders></table></div></div>
<script>
const f=(x,d=2)=>x==null?'-':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d}),P=x=>x==null?'-':(x*100).toFixed(2)+'%';
const S={1:'<span class=L>롱/BULL</span>','-1':'<span class=S>숏/BEAR</span>',0:'-',null:'-',undefined:'-'};
let chart,olc;
const kst=t=>{if(!t)return '-';const d=new Date(Date.parse(t+'Z')+9*3600e3);return `${d.getUTCMonth()+1}/${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2,'0')}:02`};
function drawOutlook(fw){
  const el=document.getElementById('olt'),O=fw&&fw.outlook&&Object.entries(fw.outlook)[0];
  if(!O){el.textContent=fw&&fw.outlook_error?'계산 실패: '+fw.outlook_error:'아직 없음 (실거래 검증이 하루 3번 계산)';return}
  const [inst,o]=O,h=o.hist,u=o.fut,n=h.date.length,pad=a=>Array(n).fill(null).concat(a),sb=o.tranches.map(t=>t.short_below).filter(v=>v);
  const ds=[['95% 상단',pad(u.hi2),'rgba(49,130,246,.25)','+1','rgba(49,130,246,.08)'],['95% 하단',pad(u.lo2),'rgba(49,130,246,.25)',false],
    ['68% 상단',pad(u.hi1),'rgba(49,130,246,.45)','+1','rgba(49,130,246,.2)'],['68% 하단',pad(u.lo1),'rgba(49,130,246,.45)',false]]
    .map(([l,d,c,fl,bg])=>({label:l,data:d,borderColor:c,borderWidth:1,pointRadius:0,fill:fl,backgroundColor:bg}));
  ds.push({label:inst.split('-')[0]+' 종가',data:h.close,borderColor:'#3182f6',borderWidth:2,pointRadius:0},
    {label:'SMA50 = 롱 기준 (오른쪽은 가격 유지 가정)',data:h.sma.concat(u.sma_flat),borderColor:'#8b95a1',borderWidth:2,borderDash:[6,4],pointRadius:0},
    {label:'숏 기준 (이 아래 유지)',data:pad(u.date.map(()=>sb.length?Math.max(...sb):null)),borderColor:'#e67e22',borderWidth:2,borderDash:[2,3],pointRadius:0});
  const data={labels:h.date.concat(u.date),datasets:ds},opt={animation:false,spanGaps:true,interaction:{mode:'index',intersect:false},
    plugins:{legend:{labels:{filter:i=>!/하단|상단/.test(i.text)||/68% 상단|95% 상단/.test(i.text)}},tooltip:{filter:i=>i.raw!=null}}};
  if(olc){olc.data=data;olc.update()}else olc=new Chart(document.getElementById('ol'),{type:'line',data,options:opt});
  const lv=o.levels.map(x=>x?(x>0?'+':'')+(x*100).toFixed(1)+'%':'현재'),px=o.levels.map(x=>f(o.price*(1+x),0));
  el.innerHTML=`<p style="font-size:13px;color:var(--t3);margin:0 0 8px">${inst.split('-')[0]} ${f(o.price)} · 하루 변동성 ${o['sigma_d%']}% · 10일 뒤 68% 범위 ${f(u.lo1[9],0)} ~ ${f(u.hi1[9],0)} · 아래 표 = 가격이 그 수준에 머물 때 몫별 첫 진입 (KST, 재진입 제한·변동성 필터 제외, ${o.horizon_d}일 안)</p>`+
    '<table><tr><th>몫 (판단 KST)</th><th>롱: 종가 ≥ (3일)</th><th>숏: 종가 ≤ 유지</th>'+lv.map((l,j)=>`<th>${l}<br>${px[j]}</th>`).join('')+'</tr>'+
    o.tranches.map((t,k)=>`<tr><td>#${k} (${String((t.off+9)%24).padStart(2,'0')}:02)</td><td>${f(t.long_above,0)}</td><td>${f(t.short_below,0)}</td>`+
      t.scen.map(c=>`<td>${c.side?`<span class=${c.side>0?'L':'S'}>${c.side>0?'롱':'숏'}</span> ${kst(c.at)}`:'-'}</td>`).join('')+'</tr>').join('')+'</table>';
}
async function load(){
  const s=await (await fetch('/api/state')).json(),a=s.acct;
  const R=s.risk,c=s.config,bt=s.backtest,st=c.strategy;
  document.getElementById('mode').textContent=`MODE=${s.mode} · 위험 ${R.state} · 연패 ${a.streak||0}`;
  const o=s.okx;
  document.getElementById('now').innerHTML=`<p><b>위험 상태: ${R.label}</b>${R.err?` · 오류: ${R.err}`:''}<br>신규 진입: ${R.entry_blocks.length?'<span class=f>차단</span> — '+R.entry_blocks.join(' · '):'가능'}`+
    `<br>청산 사유: ${Object.entries(R.flatten).map(([k,v])=>k+' '+v).join(' · ')||'없음'} · 일 기준 ${R.day?f(R.day.eq)+' ('+R.day.date+')':'-'} · 고점 ${f(R.hwm)}`+
    `<br>실거래 검증: ${s.forward?`${s.forward.summary} (${s.forward.ts.slice(0,16)} UTC 기준)`+(s.forward.flags.length?'<br><span class=f>'+s.forward.flags.join('<br>')+'</span>':''):'아직 없음 (forward.py)'}`+
    `<br><span style="color:var(--t3)">마지막 조회 ${R.t.last_ok||'-'} · 감지 ${R.t.detected||'-'} · 청산 요청 ${R.t.flatten_req||'-'} · flat 확인 ${R.t.flat_ok||'-'}</span></p>`+
    `<p>설정 ${c.strategy_hash} · 몫 ${st.tranches_utc.length} · ${st.lever}x · 종목당 증거금 ${(st.margin_per_symbol*100).toFixed(1)}% (몫당 ${(st.margin_per_tranche*100).toFixed(2)}%) · 신호 ${st.signal.ver} · 위험 ${st.risk.ver} · 코드 ${c.code.git}${c.code.dirty?' (미커밋 변경 있음)':''} / ${c.code.files_sha}<br>`+
    (bt?`같은 설정 백테스트 [${bt.run}] ${bt.period}: CAGR ${bt.stats['cagr%'].toFixed(1)}% · MDD ${bt.stats['mdd%'].toFixed(1)}% · 거래 ${bt.stats.trades} (과거 리플레이, 수익 보장 아님)`:'<span class=f>이 설정과 해시가 같은 백테스트가 없음 — 다른 구성(예: 3몫) 수치를 이 계정 성과로 보지 말 것</span>')+'</p>'+`<p><b>OKX${o?.demo?' 데모':''} 잔액: ${o?f(o.eq)+' USDT':'키 없음/조회 전'}</b>${o?` (가용 ${f(o.avail)}, 실현 ${f(o.realized)}, ${o.ts.slice(0,16).replace('T',' ')} UTC)`:''}${s.mode==='dry'?' · 매매 장부는 dry 가상 자본':''}</p><p>시각 분할 ${s.tranches.length}몫: ${s.tranches.map((h,k)=>`#${k} UTC ${String(h).padStart(2,'0')} (KST ${String((h+9)%24).padStart(2,'0')})`).join(' · ')}</p>`+
    '<table><tr><th>상품#몫</th><th>레짐</th><th>확정</th><th>보유</th><th>수량</th><th>진입가</th><th>손절가</th><th>본전</th><th>edge</th></tr>'+
    Object.entries(s.ledger).map(([i,l])=>{const r=s.regimes[i]||s.regimes[i.split('-')[0]]||[];return `<tr><td>${i}</td><td>${S[r[0]]}</td><td>${S[r[1]]}</td><td>${S[l?.side]}</td><td>${l?.side?l.sz:'-'}</td><td>${f(l?.side?l.entry:null,4)}</td><td>${f(l?.side?l.stop:null,4)}</td><td>${l?.be_ts||'-'}</td><td>${l?.blk||'-'}</td></tr>`}).join('')+'</table>';
  document.getElementById('trades').innerHTML='<tr><th>슬리브</th><th>상품</th><th>방향</th><th>진입</th><th>진입가</th><th>청산</th><th>청산가</th><th>사유</th><th>증거금</th><th>손절거리</th><th>청산가(거래소)</th><th>본전</th><th>수수료</th><th>펀딩</th><th>괴리</th><th>자산 대비</th></tr>'+
    s.trades.map(t=>`<tr><td>${t.sleeve||'-'}</td><td>${t.inst.split('-')[0]}</td><td>${S[t.side]}</td><td>${t.entry_ts||'-'}</td><td>${f(t.entry_px,4)}</td><td>${t.exit_ts}</td><td>${f(t.exit_px,4)}</td><td>${t.why}</td><td>${f(t.margin)}</td><td>${P(t.stop_dist)}</td><td>${f(t.liq_px,4)}</td><td>${t.be_ts||'-'}</td><td>${f(t.fee,4)}</td><td>${f(t.funding,4)}</td><td>${P(t.basis)}</td><td class=${t.pnl>0?'L':'S'}>${P(t.pnl_pct)}</td></tr>`).join('');
  document.getElementById('daily').innerHTML='<tr><th>날짜</th><th>자산</th><th>낙폭</th><th>증거금</th><th>한도 막힘</th><th>연패</th><th>주식만</th><th>코인만</th><th>BTC 1x</th><th>플래그</th><th>메모</th></tr>'+
    s.daily.slice().reverse().map(r=>`<tr><td>${r.date}</td><td>${f(r.equity)}</td><td>${P(r.dd)}</td><td>${P(r.margin_pct)}</td><td>${r.blocked||0}</td><td>${r.streak}</td><td>${P(r.stock_only)}</td><td>${P(r.coin_only)}</td><td>${P(r.btc_hold)}</td><td class=f>${r.flags||''}</td><td style="text-align:left">${r.note||''}</td></tr>`).join('');
  document.getElementById('orders').innerHTML='<tr><th>시각</th><th>clOrdId</th><th>동작</th><th>방향</th><th>수량</th><th>평단</th><th>수수료</th><th>상태</th></tr>'+
    s.orders.map(o=>`<tr><td>${o.ts.slice(0,16)}</td><td>${o.cid}</td><td>${o.action}</td><td>${S[o.side]}</td><td>${o.sz}</td><td>${f(o.avg_px,4)}</td><td>${f(o.fee,4)}</td><td>${o.state}</td></tr>`).join('');
  const d=s.daily,e0=d.length?d[0].equity:1,C=['#191f28','#3182f6','#f04452','#8b95a1'];
  const K=[['equity','계좌',r=>(r.equity/e0-1)*100],['stock_only','주식만',r=>r.stock_only*100],['coin_only','코인만',r=>r.coin_only*100],['btc_hold','BTC 1x',r=>r.btc_hold*100]];
  const data={labels:d.map(r=>r.date),datasets:K.map(([k,n,g],j)=>({label:n,data:d.map(r=>r[k]==null?null:g(r)),borderColor:C[j],pointRadius:0,borderWidth:2}))};
  if(chart){chart.data=data;chart.update()}else chart=new Chart(document.getElementById('chart'),{type:'line',data,options:{animation:false,spanGaps:true}});
  drawOutlook(s.forward);
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
