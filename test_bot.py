"""상태기계 자체 점검 (모의 장부만 사용, 거래소·Claude 호출 없음).
본다: 매 판단이 open/close/hold 중 하나인지, 롱·숏 자리에만 진입하는지, 같은 자리로 두 번 진입하지 않는지,
청산 조건이 즉시 청산인지, ALLOW_SHORT 게이트, 모의 장부 수수료, 사고 차단기, 변동성 타겟팅, 진입 대기.
용어: 롱 = 상승에 거는 것, 숏 = 하락에 거는 것. 청산은 방향이 아니라 행동이다.
사용: python test_bot.py
"""
import datetime as dt
import os
import pathlib
import statistics
import sys

os.environ.update(MODE="paper", PAPER_CASH="1000", SYMBOL="BTC/USDT:USDT", LEVERAGE="1")
ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "test_bot.db"
DB.unlink(missing_ok=True)

import autotrade as A                                     # noqa: E402

A.DB_PATH = DB
A.USE_CLAUDE = False
A.ENTRY_FLOOR = False          # 진입 대기는 아래에서 따로 본다 (분봉·거래소 호출이 필요하므로)
A.ALLOW_SHORT = True           # 상태기계는 양방향으로 점검하고, 게이트 자체도 아래에서 따로 본다
A.VOL_TARGET_PCT = 0           # 레버리지 고정 — 타겟팅은 아래에서 따로 본다
A.AUTORUN_OFF = ROOT / "test_autorun.off"
A.ENTRY_OFF = ROOT / "test_entry.off"
REAL_ASK = A.ask_claude          # 아래 점검들이 ask_claude 를 갈아 끼우므로 원본을 먼저 잡아 둔다
A._LOCK = object()               # 프로세스간 락은 이미 잡은 것으로 (실제 봇이 돌고 있어도 테스트는 돌아야 한다)
PX = 80_000
CONF = A.M.CONF


def sig(zone="wait", bar="b0", exit_long=False, exit_short=False, bull=None, bear=0, ext=7, **over):
    """합성 신호. zone = 이번 봉이 가리키는 진입 방향, exit_* = 그 방향을 들고 있을 때의 청산 여부.
    over 로 개별 값을 덮어쓴다 (숏 자리는 close < lo_n 이어야 앞뒤가 맞는다)."""
    bull = CONF + 1 if bull is None else bull
    return {**{"zone": zone, "bar": bar, "bull": bull, "bear": bear,
            "bull_regime": bull >= CONF, "bear_regime": bear >= CONF, "regime": bull >= CONF,
            "exit_long": exit_long, "exit_short": exit_short, "conf": CONF,
            "h4_n": A.M.H4_N, "h4_m": A.M.H4_M, "close": PX,
            "hi": PX - 500, "lo": PX - 1500, "lo_n": PX - 2000, "hi_m": PX + 500,
            "extreme_hi": ext, "extreme_lo": ext, "extreme_rules": 7}, **over}


def cycle(zone="wait", **kw):
    """run_cycle 의 판단·주문 부분(act)을 그대로 탄다. 거래소·Claude 만 뺀다."""
    action, _, _ = A.act(None, None, 0, sig(zone, **kw), (None, None), PX, A.account(None, PX))
    assert action in ("open", "close", "hold")
    return action


try:
    # ── 롱 (상승 베팅) ──
    assert cycle() == "hold", "포지션 없고 자리도 없으면 관망"
    assert cycle(exit_long=True) == "hold", "포지션이 없으면 청산 조건은 아무것도 안 한다"
    assert cycle("long", bar="b1") == "open", "롱 자리면 진입"
    assert A.state()["side"] == "long"

    assert cycle("long", bar="b1") == "hold", "보유 중 같은 자리는 유지"
    assert cycle() == "hold", "보유 중 자리 없음은 유지"
    assert cycle(exit_short=True) == "hold", "롱 보유 중 숏 청산 조건은 상관없다"
    assert cycle(exit_long=True, bull=1, bear=CONF + 1) == "close", "롱 국면 붕괴는 즉시 청산"
    assert A.state()["side"] is None

    assert cycle("long", bar="b2") == "open"
    assert cycle(exit_long=True) == "close", "이탈선 이탈은 즉시 청산"
    assert cycle("long", bar="b2") == "hold", "같은 자리로는 다시 진입하지 않는다"
    assert cycle("long", bar="b3") == "open", "새 자리면 진입"
    A.execute(None, 0, "close", "long", A.state()["qty"], A.state()["qty"] * PX, PX, "test")

    # ── 숏 (하락 베팅) ──
    short = {"bull": 1, "bear": CONF + 1}
    assert cycle("short", bar="s1", **short) == "open", "숏 자리면 숏 진입"
    assert A.state()["side"] == "short"
    assert cycle(exit_long=True, **short) == "hold", "숏 보유 중 롱 청산 조건은 상관없다"
    assert cycle("short", bar="s1", **short) == "hold", "보유 중 같은 자리는 유지"
    assert cycle(exit_short=True, **short) == "close", "숏 청산 조건이면 즉시 청산"
    assert A.state()["side"] is None

    A.ALLOW_SHORT = False
    assert cycle("short", bar="s2", **short) == "hold", "ALLOW_SHORT=0 이면 숏 자리를 봐도 진입하지 않는다"
    assert A.state()["side"] is None
    A.ALLOW_SHORT = True
    assert cycle("short", bar="s3", **short) == "open", "다시 켜면 진입"
    A.execute(None, 0, "close", "short", A.state()["qty"], A.state()["qty"] * PX, PX, "test")
    A.ALLOW_SHORT = False

    cash = A.state()["cash"]
    assert cash < 1_000, f"같은 가격에 사고팔면 수수료만큼 줄어야 한다: {cash:,.2f}"
    assert cash > 1_000 * (1 - 12 * A.X.TAKER_FEE), f"수수료가 과다 차감됐다: {cash:,.2f}"
    assert A.paper_trades_done() == 5, f"완료된 모의 거래 5회: {A.paper_trades_done()}"

    # 데모 주문(대시보드)은 별도 계좌 → 모의 장부를 건드리면 안 된다
    before = A.state()
    A.execute(None, 0, "open", "long", 0.01, 0.01 * PX, PX, "demo test", demo=True)
    assert A.state() == before, "데모 주문이 모의 장부를 바꿨다"

    # ── 사고 차단기 (24h 고점 대비 MAX_DAY_LOSS% 아래면 True) ──
    A.MAX_DAY_LOSS = 30
    with A.db() as c:
        c.execute("INSERT INTO runs (timestamp, equity, paper_equity, real_equity) VALUES (?, 1000, 1000, 7)", (A.now(),))
    assert not A.day_loss_hit(800), "고점 대비 −20% 는 아직 아니다"
    assert A.day_loss_hit(700), "고점 1000 대비 −30% 면 걸린다"
    assert not A.day_loss_hit(7, live=True), "모의 1000 과 실계좌 7 을 섞어 재면 안 된다 (2026-09-08 오작동)"
    assert A.day_loss_hit(4, live=True), "실계좌 고점 7 대비 −30% 면 걸린다"
    A.MAX_DAY_LOSS = 0
    assert not A.day_loss_hit(1), "0 이면 차단기를 끈 것"

    # ── 변동성 타겟팅: lev = min(LEVERAGE, 목표 / 계좌 일별 실현변동성) ──
    A.VOL_TARGET_PCT, A.VOL_WINDOW = 40, 60
    assert A.target_leverage(False)[0] == A.LEVERAGE, "이력 0일이면 대기 (LEVERAGE 그대로)"
    day0 = dt.datetime.now(A.KST) - dt.timedelta(days=40)
    eqs = [1000 * (1.03 if i % 2 else 0.98) ** (i // 2 + 1) for i in range(40)]   # 하루 걸러 +3%/−2%
    with A.db() as c:
        c.execute("DELETE FROM runs")                  # 차단기 점검이 넣은 행을 빼고 이 계열만 본다
        c.executemany("INSERT INTO runs (timestamp, paper_equity) VALUES (?, ?)",
                      [((day0 + dt.timedelta(days=i)).isoformat(timespec="seconds"), e) for i, e in enumerate(eqs)])
    rets = [b / a - 1 for a, b in zip(eqs, eqs[1:])]
    want = min(A.LEVERAGE, 40 / (statistics.stdev(rets) * (365 ** 0.5) * 100))
    got, why = A.target_leverage(False)
    assert abs(got - want) < 1e-9, f"타겟 레버리지가 공식과 다르다: {got} vs {want} ({why})"
    assert got < A.LEVERAGE, "변동성이 목표보다 크면 레버리지를 깎아야 한다"
    A.VOL_TARGET_PCT = 0
    assert A.target_leverage(False)[0] == A.LEVERAGE, "0 이면 타겟팅을 끈 것"
    A.VOL_TARGET_PCT = 40
    with A.db() as c:                                  # 자산이 그대로면 실현 변동성 0 → 대기
        c.execute("DELETE FROM runs")
        c.executemany("INSERT INTO runs (timestamp, paper_equity) VALUES (?, ?)",
                      [((day0 + dt.timedelta(days=i)).isoformat(timespec="seconds"), 1000.0) for i in range(40)])
    assert A.target_leverage(False)[0] == A.LEVERAGE, "실현 변동성 0 이면 대기"
    with A.db() as c:
        c.execute("DELETE FROM runs")
    A.VOL_TARGET_PCT = 0                               # 이후 점검은 타겟팅 없이 (레버리지 고정)

    # ── 진입 대기: 돌파당한 선을 분봉이 지키는지. 롱은 하한, 숏은 상한 (거울) ──
    import pandas as pd
    def m1(lows, closes, highs=None):
        i = pd.date_range("2026-09-07 09:00", periods=len(lows), freq="min", tz="Asia/Seoul")
        d = pd.DataFrame({"low": lows, "close": closes, "open": closes,
                          "high": closes if highs is None else highs, "volume": 1.0}, index=i)
        A.X.candles = lambda pub, n, tf: pd.concat([d, d.tail(1)])      # 마지막 봉은 진행 중 → 잘린다
        return d
    since = pd.Timestamp("2026-09-07 09:00", tz="Asia/Seoul").to_pydatetime()
    m1([100, 101, 102], [101, 102, 103])
    assert A.floor_check(None, 99, since, "long")[0], "롱: 하한을 한 번도 잃지 않았으면 진입"
    assert not A.floor_check(None, 105, since, "long")[0], "롱: 하한 아래면 대기"
    m1([100, 95, 102], [101, 96, 103])
    assert A.floor_check(None, 99, since, "long")[0], "롱: 잃었다가 되찾으면 진입"
    m1([100, 95, 96], [101, 96, 97])
    assert not A.floor_check(None, 99, since, "long")[0], "롱: 되찾지 못하면 대기"
    m1([98, 97, 96], [99, 98, 97], [100, 99, 98])
    assert A.floor_check(None, 101, since, "short")[0], "숏: 상한을 한 번도 넘지 않았으면 진입"
    assert not A.floor_check(None, 95, since, "short")[0], "숏: 상한 위면 대기"
    m1([98, 97, 96], [99, 102, 97], [100, 103, 98])
    assert A.floor_check(None, 101, since, "short")[0], "숏: 넘었다가 다시 눌리면 진입"
    m1([98, 102, 103], [99, 103, 104], [100, 104, 105])
    assert not A.floor_check(None, 101, since, "short")[0], "숏: 다시 못 누르면 대기"

    # ── 진입 대기 상태기계: 승인 → 선 확인될 때까지 보류 → 확인되면 진입 · 기한 지나면 자리 포기 ──
    A.ENTRY_FLOOR = True
    A.watch_stop()
    bar = (dt.datetime.now(A.KST) - dt.timedelta(hours=4, minutes=30)).strftime("%Y-%m-%d %H:%M")   # 기한(자리+8h) 안쪽
    bar2 = (dt.datetime.now(A.KST) - dt.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M")
    ok, seen = [False], []
    A.floor_check = lambda pub, level, since, side="long": (seen.append((level, side)), (ok[0], "테스트"))[1]
    assert cycle("long", bar=bar) == "hold", "자리 직후엔 선 확인 전이라 사지 않는다"
    assert A.state()["watch_bar"] == bar and A.state()["watch_hi"] == PX - 500 and A.state()["watch_side"] == "long"
    assert cycle() == "hold", "대기 중 새 자리가 없어도 선만 보면 된다"
    ok[0] = True
    assert cycle() == "open", "선이 지켜지면 그때 진입"
    assert A.state()["watch_bar"] is None and A.state()["side"] == "long"
    assert seen[-1] == (PX - 500, "long"), "롱 대기는 돌파당한 고가를 본다"
    A.execute(None, 0, "close", "long", A.state()["qty"], A.state()["qty"] * PX, PX, "test")

    ok[0] = False
    assert cycle("long", bar=bar) == "hold"
    A.set_state(watch_since="2020-01-01T00:00:00+09:00")            # 기한 지난 것으로 위조
    assert cycle() == "hold" and A.state()["watch_bar"] is None, "기한 내 미회복이면 자리 포기"
    assert cycle("long", bar=bar2) == "hold"
    assert cycle(exit_long=True) == "hold" and A.state()["watch_bar"] is None, "그 방향 청산 조건이 켜지면 대기 취소"

    A.ALLOW_SHORT = True                                            # 숏 대기는 이탈당한 저가를 본다
    ok[0] = True                                                    # 숏 자리는 현재가가 이탈선 **아래**여야 앞뒤가 맞는다
    assert cycle("short", bar=bar, lo_n=PX + 2000, **short) == "open", "숏 자리도 선 확인 뒤 진입"
    assert seen[-1] == (PX + 2000, "short"), "숏 대기는 이탈당한 저가를 본다"
    assert A.state()["side"] == "short"
    A.execute(None, 0, "close", "short", A.state()["qty"], A.state()["qty"] * PX, PX, "test")
    A.ALLOW_SHORT, A.ENTRY_FLOOR = False, False

    # 극단 점수 필터 (EXTREME_MIN): 점수가 모자라면 자리를 봐도 진입하지 않는다
    A.EXTREME_MIN = 2
    assert cycle("long", bar="e1", ext=1) == "hold", "극단 점수 1 < 2 면 진입하지 않는다"
    assert A.state()["side"] is None
    assert cycle("long", bar="e2", ext=2) == "open", "극단 점수 2 면 진입"
    A.execute(None, 0, "close", "long", A.state()["qty"], A.state()["qty"] * PX, PX, "test")
    A.ALLOW_SHORT = True
    assert cycle("short", bar="e3", ext=0, **short) == "hold", "숏도 같은 문턱을 본다 (저점 점수)"
    A.ALLOW_SHORT = False
    A.EXTREME_MIN = 0
    assert cycle("long", bar="e4", ext=0) == "open", "0 이면 필터를 끈 것"
    A.execute(None, 0, "close", "long", A.state()["qty"], A.state()["qty"] * PX, PX, "test")

    A.ask_claude = lambda p: (_ for _ in ()).throw(RuntimeError("omniroute down"))
    assert A.claude_gate({})["approve"] is False, "Claude 호출 실패는 거부 (확인 못 한 진입은 내지 않는다)"
    A.ask_claude = lambda p: {"approve": True, "reason": "ok", "model": "t", "input_tokens": 1, "output_tokens": 1}
    assert A.claude_gate({})["approve"] is True

    # ── Claude 검토는 '자리가 났을 때' 가 아니라 '체결 직전' 에 부른다 (2026-09-10 이동) ──
    A.USE_CLAUDE, A.ENTRY_FLOOR, A.ALLOW_SHORT = True, True, False
    A.watch_stop()
    A.set_state(seen_bar=None)
    A.review_payload = lambda *a, **k: {}
    calls = []
    A.ask_claude = lambda p: (calls.append(1), {"approve": True, "reason": "ok", "model": "t",
                                                "input_tokens": 1, "output_tokens": 1})[1]
    ok[0] = False
    assert cycle("long", bar=bar) == "hold", "자리가 나도 아직 사는 게 아니다"
    assert A.state()["watch_bar"] == bar
    assert not calls, "자리만 났을 때는 Claude 를 부르지 않는다 (예전 동작)"
    ok[0] = True
    assert cycle() == "open", "선이 지켜지면 그때 검토하고 산다"
    assert len(calls) == 1, f"체결 직전에 정확히 한 번 불러야 한다: {len(calls)}"
    A.execute(None, 0, "close", "long", A.state()["qty"], A.state()["qty"] * PX, PX, "test")

    A.watch_stop(); A.set_state(seen_bar=None); calls.clear()
    A.ask_claude = lambda p: (calls.append(1), {"approve": False, "reason": "거부 사유", "model": "t",
                                                "input_tokens": 1, "output_tokens": 1})[1]
    ok[0] = False
    assert cycle("long", bar=bar) == "hold"
    ok[0] = True
    assert cycle() == "hold", "Claude 가 거부하면 사지 않는다"
    assert len(calls) == 1 and A.state()["side"] is None
    assert A.state()["watch_bar"] is None, "거부되면 그 자리는 접는다 (대기 해제)"
    A.USE_CLAUDE, A.ENTRY_FLOOR = False, False

    # ══════ 2026-09-10 감사 회귀 테스트 — 아래는 전부 수정 전 코드에서 실패한다 ══════

    # ── E① LLM: bool("false") 는 True 다. 문자열이 오면 거부가 승인으로 뒤집힌다 ──
    class FakeResp:                       # anthropic 응답 흉내
        def __init__(self, text):
            self.content = [type("B", (), {"type": "text", "text": text})()]
            self.model, self.usage = "t", type("U", (), {"input_tokens": 1, "output_tokens": 1})()
    def fake_client(text):
        A.anthropic.Anthropic = lambda **kw: type("C", (), {
            "messages": type("M", (), {"create": staticmethod(lambda **k: FakeResp(text))})()})()
    A.ask_claude = REAL_ASK
    fake_client('{"approve": "false", "reason": "안 된다"}')
    assert A.claude_gate({})["approve"] is False, 'approve="false" 가 승인으로 읽혔다 (bool("false") == True)'
    fake_client('{"approve": 1, "reason": "숫자"}')
    assert A.claude_gate({})["approve"] is False, "approve=1 도 Boolean 이 아니므로 거부여야 한다"
    fake_client('{"approve": true, "reason": "진짜 승인"}')
    assert A.claude_gate({})["approve"] is True, "진짜 JSON true 는 승인"

    # ── D① 주문 실패 시 장부를 건드리지 않는다 (예전엔 set_state 가 먼저였다) ──
    class FakeEx:
        pass
    orders_seen = []
    A.X.setup = lambda ex, lev: int(lev)
    A.X.explain = lambda e: str(e)[:80]
    A.watch_stop(); A.set_state(side=None, qty=0, entry=0, entered_at=None, seen_bar=None)
    before = A.state()
    A.X.open_position = lambda ex, side, notional, px, cid=None: (_ for _ in ()).throw(TimeoutError("gateway timeout"))
    A.X.find_order = lambda ex, cid: None                       # 재조회에도 안 잡힌다 → 상태 불명
    assert A.execute(FakeEx(), 0, "open", "long", None, 1000, PX, "실패 테스트") is False
    assert A.state()["side"] is None, "주문이 실패했는데 장부에 포지션이 생겼다"
    with A.db() as c:
        st_row = c.execute("SELECT status, client_id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    assert st_row[0].startswith("unknown") and st_row[1], "의도(clOrdId)와 불명 상태가 기록돼야 한다"

    # ── D② 타임아웃 뒤 재조회에서 체결이 확인되면 그 값으로 장부를 확정한다 ──
    A.X.find_order = lambda ex, cid: {"status": "closed", "filled": 0.02, "avg": 79_000.0, "id": "X1"}
    assert A.execute(FakeEx(), 0, "open", "long", None, 1580, PX, "재조회 테스트") is True
    st = A.state()
    assert st["side"] == "long" and abs(st["qty"] - 0.02) < 1e-9 and st["entry"] == 79_000.0,         f"거래소가 알려준 실제 체결(수량·평단)으로 장부를 맞춰야 한다: {st}"

    # ── D③ 청산 주문이 불명이면 장부의 포지션을 지우지 않는다 (지우면 다시 청산을 시도하지 않는다) ──
    A.X.close_position = lambda ex, pos, cid=None: (_ for _ in ()).throw(TimeoutError("gateway timeout"))
    A.X.find_order = lambda ex, cid: None
    assert A.execute(FakeEx(), 0, "close", "long", 0.02, 1580, PX, "청산 실패 테스트") is False
    assert A.state()["side"] == "long", "청산이 확인되지 않았는데 장부에서 포지션을 지웠다"
    A.X.close_position = lambda ex, pos, cid=None: "OK1"
    A.X.find_order = lambda ex, cid: {"status": "closed", "filled": 0.02, "avg": 79_000.0, "id": "OK1"}
    assert A.execute(FakeEx(), 0, "close", "long", 0.02, 1580, 79_000.0, "청산 성공") is True
    assert A.state()["side"] is None

    # ── D④ 신규 진입 차단(entry.off)은 진입만 막고 청산은 막지 않는다 ──
    A.set_entry_block(True, "테스트")
    assert A.entry_blocked()
    assert cycle("long", bar="blk1") == "hold", "신규 진입 차단 중에는 진입하지 않는다"
    assert A.state()["side"] is None
    A.execute(None, 0, "open", "long", 0.01, 0.01 * PX, PX, "강제 보유")   # 포지션이 있는 상태를 만든다
    assert cycle(exit_long=True) == "close", "차단 중에도 청산은 나가야 한다"
    A.set_entry_block(False)
    assert not A.entry_blocked()

    # ── D⑤ 격리 청산선: 거래소는 정수 레버리지로 걸고 유지증거금이 남을 때 청산한다 ──
    # 2.1배 → 거래소 3배 → 청산선 −33%+MMR. 예전 식(1/2.1 = −48%)이면 아직 생존이라고 봤다
    pos = {"side": "long", "qty": 1.0, "entry": 100.0, "lev": 2.1, "pnl": -35.0}   # 현재가 65
    assert A.liquidated(pos), "2.1배를 3배로 올려 걸면 −35% 에서 이미 청산이다"
    assert not A.liquidated({**pos, "pnl": -30.0}), "−30% 는 아직 청산선(−32.8%) 위"
    assert A.liquidated({"side": "short", "qty": 1.0, "entry": 100.0, "lev": 5, "pnl": -20.0}), "숏도 거울로 판정"

    # ── D⑥ 주문 직전 재확인: 미끄러짐과 선 이탈 ──
    A.X.price = lambda pub: 80_800.0
    assert not A.recheck(object(), 80_000, "long", None)[0], "1% 미끄러졌으면 진입을 접는다 (MAX_SLIP 0.5%)"
    A.X.price = lambda pub: 80_200.0
    assert A.recheck(object(), 80_000, "long", 79_500)[0], "0.25% 는 허용, 하한 위"
    assert not A.recheck(object(), 80_000, "long", 80_500)[0], "하한 아래로 내려갔으면 진입 취소"
    assert not A.recheck(object(), 80_000, "short", 79_900)[0], "숏은 상한 위로 올라갔으면 취소"
    A.X.price = lambda pub: (_ for _ in ()).throw(RuntimeError("api down"))
    assert not A.recheck(object(), 80_000, "long", None)[0], "시세를 못 읽으면 주문하지 않는다"

    # ── D⑦ 여유 현금: 명목은 자산 × 비중 × 레버리지의 100% 가 아니다 ──
    assert A.CASH_RESERVE > 0, "왕복 수수료·펀딩을 낼 현금이 남아야 한다"

    # ── D⑧ 사이클 락: 겹쳐 들어온 호출은 건너뛴다 ──
    A._CYCLE.acquire()
    ran = []
    real = A._run_cycle
    A._run_cycle = lambda src="자동": ran.append(src)
    A.run_cycle("겹침")
    assert not ran, "사이클이 돌고 있는데 또 들어갔다"
    A._CYCLE.release()
    A.run_cycle("정상")
    assert ran == ["정상"]
    A._run_cycle = real

    A.ENTRY_OFF.unlink(missing_ok=True)
    print("ok  감사 회귀 테스트 통과 (LLM Boolean · 주문/장부 순서 · 재조회 · 진입차단 · 청산선 · 재확인 · 락)")
    print(f"ok  모의 잔고 {cash:,.2f} USDT (수수료 {1_000 - cash:,.2f}), 모의 거래 {A.paper_trades_done()}회")
finally:
    DB.unlink(missing_ok=True)
    A.AUTORUN_OFF.unlink(missing_ok=True)
    A.ENTRY_OFF.unlink(missing_ok=True)
    sys.stdout.flush()
