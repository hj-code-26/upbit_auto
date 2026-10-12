"""미장(quant_nasq100) + 코인(RLS-C) 통합 조회 화면. 읽기 전용, 표준 라이브러리만 쓴다.

두 봇의 기존 대시보드 JSON API 에 GET 만 보낸다. 매매 코드를 import 하지 않고 DB·.env·로그도 열지 않는다
→ 봇과 서로 간섭할 수 없고, 매매 알고리즘이 바뀌어도 그 대시보드 API 가 살아 있으면 이 화면은 뜬다.
필드가 없어지거나 이름이 바뀌면 그 칸만 '-' 가 되고, 표는 행에 실제로 있는 키를 보여 준다. 한쪽이 죽어도 다른 쪽은 뜬다.
실행: python3 hub.py → http://127.0.0.1:8899   (STOCK_URL, COIN_URL, PORT 로 바꿀 수 있다)
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STOCK = os.environ.get("STOCK_URL", "http://127.0.0.1:8877")   # quant_nasq100 (quant-dashboard)
COIN = os.environ.get("COIN_URL", "http://127.0.0.1:8000")     # RLS-C (rlsc-dashboard)
_cache = {}


def get(url, ttl=15, timeout=10):
    """(json, 오류). 예외를 올리지 않는다. 실패하면 직전 성공값(있으면)과 오류를 준다."""
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1], None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.load(r, parse_constant=lambda _: None)   # NaN/Infinity → null (브라우저 JSON 은 NaN 을 못 읽는다)
        _cache[url] = (time.time(), data)
        return data, None
    except Exception as e:  # noqa: BLE001
        return (hit[1] if hit else None), f"{url} 조회 실패 ({type(e).__name__}){' — 이전 값 표시' if hit else ''}"


def d(x):
    return x if isinstance(x, dict) else {}


def lst(x):
    return [r for r in x if isinstance(r, dict)] if isinstance(x, list) else []


def pct(rows, *keys):
    """비율(0.01) → %(1.0). 코인 쪽은 비율로 저장한다."""
    for r in rows:
        for k in keys:
            if isinstance(r.get(k), (int, float)):
                r[k] = r[k] * 100
    return rows


def stock():
    st, e1 = get(STOCK + "/api/state")
    ac, e2 = get(STOCK + "/api/account", ttl=60, timeout=30)   # 토스 실시간 조회라 길게 캐시한다
    od, e3 = get(STOCK + "/api/orders?limit=50")
    st, ac = d(st), d(ac)
    h = ac.get("holdings")
    holdings = [{"symbol": k, **d(v)} for k, v in h.items()] if isinstance(h, dict) else lst(h)
    errors = [e1, e2, e3, ac.get("error")]
    if ac and ac.get("live") is False:
        errors.append("토스 실시간 조회 실패 — 자산은 마지막 사이클 기록, 보유 종목은 비어 보일 수 있음")
    return {
        "mode": ("모의" if st["dry_run"] else "실주문") if "dry_run" in st else None,
        "account": {"총자산 (USD)": ac.get("total_value"), "현금 (USD)": ac.get("cash"),
                    "수익률 %": ac.get("return_pct"), "손익 (USD)": ac.get("pnl"),
                    "미체결 주문": len(ac["open_orders"]) if isinstance(ac.get("open_orders"), list) else None,
                    "자동 실행": st.get("auto"), "마지막 실행": st.get("last_run"), "다음 실행": st.get("next_run")},
        "tables": [
            {"title": "보유 종목", "rows": holdings,
             "cols": ["symbol", "name", "quantity", "avg_price", "last_price", "market_value", "pnl_pct"]},
            {"title": "최근 주문", "rows": lst(od),
             "cols": ["timestamp", "symbol", "side", "quantity", "price", "amount_usd", "status", "reason"]}],
        "errors": [e for e in errors if e]}


def coin_state(r, a, s):
    """위험 상태(R2: risk.state/label·진입 차단 사유), 옛 대시보드면 acct.halted/hold."""
    if r.get("state"):
        blocks = lst(r.get("entry_blocks"))
        return f"{r.get('label') or r['state']}" + (f" · 진입 차단: {', '.join(map(str, blocks))[:160]}" if blocks else "")
    return "정지" if a.get("halted") else (a.get("hold") or ("정상" if s else None))


def coin():
    s, err = get(COIN + "/api/state")
    s = d(s)
    o, a, daily = d(s.get("okx")), d(s.get("acct")), lst(s.get("daily"))
    last = daily[-1] if daily else {}
    held = [{"pos": k, **v} for k, v in d(s.get("ledger")).items() if d(v).get("side")]
    return {
        "mode": s.get("mode"),
        "account": {f"OKX{' 데모' if o.get('demo') else ''} 자산 (USDT)": o.get("eq"), "가용 (USDT)": o.get("avail"),
                    "실현 잔고 (USDT)": o.get("realized"), "잔액 조회 (UTC)": o.get("ts"),
                    "장부 자산": last.get("equity"), "낙폭 %": last["dd"] * 100 if isinstance(last.get("dd"), (int, float)) else None,
                    "연패": a.get("streak"), "상태": coin_state(d(s.get("risk")), a, s)},
        "tables": [
            {"title": "보유 포지션 (상품#몫)", "rows": held, "cols": ["pos", "side", "sz", "entry", "stop", "be_ts"]},
            {"title": "최근 거래 (청산 완료, UTC)", "rows": pct(lst(s.get("trades"))[:50], "pnl_pct"),
             "cols": ["inst", "side", "entry_ts", "entry_px", "exit_ts", "exit_px", "why", "pnl", "pnl_pct"]},
            {"title": "최근 주문 (UTC)", "rows": lst(s.get("orders"))[:50],
             "cols": ["ts", "inst", "action", "side", "sz", "avg_px", "state"]}],
        "errors": [err] if err else []}


API = {"/api/stock": stock, "/api/coin": coin}

PAGE = r"""<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Trade Hub</title>
<style>
:root{--bg:#f2f4f6;--card:#fff;--t1:#191f28;--t3:#6b7684;--line:#e5e8eb;--red:#f04452;--blue:#3182f6;--warn:#c25e00}
@media (prefers-color-scheme:dark){:root{--bg:#101318;--card:#1b1f26;--t1:#e8ebed;--t3:#8b95a1;--line:#2a2f37;--warn:#f0a050}}
body{margin:0;background:var(--bg);color:var(--t1);font-family:-apple-system,system-ui,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}h1{font-size:20px;margin:0;flex:1}
.tabs{display:flex;background:var(--card);border-radius:12px;padding:4px}
.tabs button{border:0;background:none;color:var(--t3);font:inherit;font-weight:600;padding:8px 20px;border-radius:9px;cursor:pointer}
.tabs button[aria-pressed=true]{background:var(--blue);color:#fff}
.card{background:var(--card);border-radius:16px;padding:20px;margin-bottom:12px;overflow-x:auto}h2{font-size:16px;margin:0 0 12px}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.kv div{font-size:13px;color:var(--t3)}.kv b{display:block;font-size:17px;color:var(--t1);margin-top:3px}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:6px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{color:var(--t3);font-weight:500}td:first-child,th:first-child{text-align:left}
.up{color:var(--red)}.dn{color:var(--blue)}.err{color:var(--warn);font-size:13px;margin:4px 0}.mut{color:var(--t3);font-size:13px}
</style>
<div class=wrap><header><h1>Trade Hub <span id=mode class=mut></span></h1>
<div class=tabs role=group><button data-k=stock>미장</button><button data-k=coin>코인</button></div></header>
<div id=body><p class=mut>불러오는 중…</p></div><p class=mut id=foot></p></div>
<script>
const LABEL={symbol:'종목',name:'이름',quantity:'수량',avg_price:'평단',last_price:'현재가',market_value:'평가액',pnl_pct:'수익률 %',
 timestamp:'시각',side:'방향',price:'가격',amount_usd:'금액 (USD)',status:'상태',reason:'사유',pos:'상품#몫',sz:'수량',entry:'진입가',
 stop:'손절가',be_ts:'본전',inst:'상품',entry_ts:'진입',entry_px:'진입가',exit_ts:'청산',exit_px:'청산가',why:'사유',pnl:'손익',
 ts:'시각',action:'동작',avg_px:'평단',state:'상태'};
const SIDE={'1':'<span class=up>롱</span>','-1':'<span class=dn>숏</span>',BUY:'<span class=up>매수</span>',SELL:'<span class=dn>매도</span>'};
const esc=s=>String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
function fmt(k,v){
  if(v==null||v==='')return '-';
  if(k==='side'&&SIDE[String(v).toUpperCase()])return SIDE[String(v).toUpperCase()];
  if(typeof v==='boolean')return v?'켜짐':'꺼짐';
  if(typeof v==='number'){const s=v.toLocaleString('ko-KR',{maximumFractionDigits:Math.abs(v)<10?4:2});
    return /pnl|수익|손익/.test(k)&&v?`<span class=${v>0?'up':'dn'}>${s}</span>`:s}
  if(typeof v==='object')v=JSON.stringify(v);
  v=String(v);if(/^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(v))v=v.slice(0,16).replace('T',' ');
  return v.length>60?`<span title="${esc(v)}">${esc(v.slice(0,60))}…</span>`:esc(v);
}
function table(rows,cols){
  if(!rows.length)return '<p class=mut>없음</p>';
  const all=[...new Set(rows.flatMap(Object.keys))],known=cols.filter(k=>all.includes(k)),use=known.length?known:all;
  return '<table><tr>'+use.map(k=>`<th>${esc(LABEL[k]||k)}</th>`).join('')+'</tr>'+
    rows.map(r=>'<tr>'+use.map(k=>`<td>${fmt(k,r[k])}</td>`).join('')+'</tr>').join('')+'</table>';
}
let cur=location.hash.slice(1)==='coin'?'coin':'stock',seq=0;
async function load(){
  const k=cur,n=++seq;
  document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.k===k));
  let s;try{s=await (await fetch('api/'+k)).json()}catch(e){s={errors:['허브 응답 없음: '+e]}}
  if(n!==seq)return;
  document.getElementById('mode').textContent=s.mode?'· '+s.mode:'';
  document.getElementById('body').innerHTML=(s.errors||[]).map(e=>`<p class=err>⚠ ${esc(e)}</p>`).join('')+
    `<div class=card><h2>계좌</h2><div class=kv>${Object.entries(s.account||{}).map(([l,v])=>`<div>${esc(l)}<b>${fmt(l,v)}</b></div>`).join('')}</div></div>`+
    (s.tables||[]).map(t=>`<div class=card><h2>${esc(t.title)}</h2>${table(t.rows||[],t.cols||[])}</div>`).join('');
  document.getElementById('foot').textContent='갱신 '+new Date().toLocaleTimeString('ko-KR')+' · 1분마다 자동 · 조회 전용';
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{cur=b.dataset.k;location.hash=cur;load()});
load();setInterval(load,60000);
</script></html>"""


class Handler(BaseHTTPRequestHandler):   # GET 만 있다: POST 등은 501
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif path in API:
            try:
                data = API[path]()
            except Exception as e:  # noqa: BLE001  응답 모양이 크게 바뀌어도 화면은 오류 문구로 뜬다
                data = {"errors": [f"데이터 정리 실패 ({type(e).__name__}: {e})"]}
            data["fetched"] = datetime.now(timezone.utc).isoformat()
            body, ctype = json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8"
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    print(f"Trade Hub → http://127.0.0.1:{port}  (미장 {STOCK}, 코인 {COIN}, GET 만)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
