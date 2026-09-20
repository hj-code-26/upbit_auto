"""미결제약정(OI) 적재 — **지금은 백테스트할 수 없어서** 데이터만 모은다 (2026-09-21).

왜 백테스트를 못 하나 (주장이 아니라 실측이다. 2026-09-21 에 직접 찔러 본 결과)
  바이낸스 fapiDataGetOpenInterestHist : 25일 전 요청 OK · **35일 전은 -1130 parameter invalid** → 약 30일치뿐
  OKX rubik 오픈이자                   : period=4H 는 51000 오류 · **1D 만 되고 180일까지** (400일은 50030)
  우리 봇 백테스트는 2021-03~ 이고 탐색/검증 분할이 2024-01 이다. 30일·180일로는 아무것도 판정할 수 없다.
  유료 데이터(Coinglass 등)를 사지 않는 한 이 가설은 **지금 잴 수 없다.**

그래서 하는 일: 지금부터 쌓는다.
  이 스크립트를 주기적으로 돌리면 data_cache/oi_btc.pkl 에 4h OI 가 누적된다.
  요청은 매번 최근 30일을 통째로 가져와 타임스탬프로 중복을 제거한다 → 주 1회만 돌려도 구멍이 안 난다
  (30일 창에 7일 간격이면 여유가 23일이다. 몇 주 걸러도 복구된다).
  1년쯤 쌓이면 탐색/검증 분할이 가능해지고, 그때 research_funding.txt 와 같은 방식으로 사전등록하고 재면 된다.

  ponytail: pickle 한 장 + 중복 제거. DB·스케줄러 통합은 안 넣는다.
            파일이 수십 MB 가 되거나 여러 심볼이 필요해지면 그때 sqlite 로 옮기면 된다.

**검증할 가설 (그때 가서 — 지금 적어 두는 것은 사후 각색을 막기 위해서다)**
  "OI 가 오르는데 가격이 내리면(OI↑ price↓) 추세에 역행하는 레버리지가 쌓인 것이고 청산 캐스케이드 위험이 높다"
  → 펀딩 게이트와 같은 축(포지셔닝 과밀)이므로, 같은 방식으로 **진입 게이트 / 레버리지 축소로만** 쓴다.
  주의: 펀딩 게이트(research_funding.txt)는 조건부 정보량이 있었는데도 REJECT 났다 —
  우리 MDD 가 과밀 구간 뒤에 오지 않아서다. OI 도 같은 이유로 떨어질 가능성이 높다. 기대치를 낮게 잡을 것.

사용: python oi_log.py          한 번 적재 (make oi)
      python oi_log.py --show   쌓인 범위만 보기
"""
import sys
import time

import ccxt
import pandas as pd

import model as M

F = M.CACHE / "oi_btc.pkl"


def load():
    return pd.read_pickle(F) if F.exists() else pd.Series(dtype=float)


def fetch_30d(period="4h"):
    """바이낸스 최근 30일 OI (계약 수). 30일보다 이전 startTime 은 거래소가 거부한다."""
    ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    out, since = {}, ex.milliseconds() - 29 * 86400 * 1000
    while since < ex.milliseconds():
        r = ex.fapiDataGetOpenInterestHist({"symbol": "BTCUSDT", "period": period,
                                            "startTime": since, "limit": 500})
        if not r:
            break
        for x in r:
            out[pd.to_datetime(int(x["timestamp"]), unit="ms", utc=True)] = float(x["sumOpenInterest"])
        nxt = int(r[-1]["timestamp"]) + 1
        if nxt <= since:
            break
        since = nxt
        time.sleep(0.05)
    return pd.Series(out).sort_index()


def append():
    old = load()
    new = fetch_30d()
    both = pd.concat([old, new[~new.index.isin(old.index)]]).sort_index()
    M.CACHE.mkdir(exist_ok=True)
    both.to_pickle(F)
    return old, new, both


def show(s, tag=""):
    if not len(s):
        print(f"{tag}비어 있음")
        return
    gaps = s.index.to_series().diff().dropna()
    big = (gaps > pd.Timedelta(hours=8)).sum()
    print(f"{tag}{len(s):,}행 · {s.index[0]:%Y-%m-%d %H:%M} ~ {s.index[-1]:%Y-%m-%d %H:%M} "
          f"({(s.index[-1]-s.index[0]).days}일) · 4h 초과 구멍 {big}개")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--show" in sys.argv:
        show(load(), "쌓인 OI: ")
        sys.exit()
    old, new, both = append()
    show(old, "이전:   ")
    show(new, "받음:   ")
    show(both, "누적:   ")
    print(f"새로 추가된 행 {len(both) - len(old):,}개 → {F}")
    if (both.index[-1] - both.index[0]).days < 400:
        print(f"※ 아직 {(both.index[-1]-both.index[0]).days}일치다. 탐색/검증 분할에는 최소 2년이 필요하다 — "
              f"주 1회씩 계속 돌릴 것 (30일 창이라 몇 주 걸러도 복구된다).")
