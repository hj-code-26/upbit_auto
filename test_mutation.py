"""결함 주입 — 고쳐 둔 버그를 되살리면 그 테스트가 **정말 실패하는지** 본다 (2026-09-19).
(quant_nasq100 research/test_invariance.py §2 이식)

통과만 보는 테스트는 아무것도 증명하지 않는다. 2026-09-10 감사에서 고친 결함 네 개에 회귀 테스트를
붙였지만, 그 테스트가 결함을 실제로 잡는지는 아무도 확인하지 않았다. 여기서는 모듈 소스를 문자열로
바꿔 메모리에서 다시 실행(exec)해 결함을 되살리고, 짝이 되는 검사가 AssertionError 를 내는지 본다.
**디스크의 파일은 건드리지 않는다.** 하나라도 '주입했는데 통과' 가 나오면 그 테스트는 가짜다.

사용: python test_mutation.py     (약 1분 — test_signal 리플레이가 대부분)
"""
import contextlib
import io
import pathlib
import sys
import types

from isolation import guard

guard()

ROOT = pathlib.Path(__file__).resolve().parent
PASS, FAIL = [], []


def mutate(name, *subs):
    """모듈 소스에서 문자열을 바꿔 메모리에서 실행 → 결함이 든 모듈 객체. 파일은 그대로다."""
    src = (ROOT / f"{name}.py").read_text(encoding="utf-8")
    for old, new in subs:
        assert old in src, f"주입 지점을 찾지 못했다 ({name}): {old[:60]}"
        src = src.replace(old, new, 1)
    mod = types.ModuleType(f"{name}__mut")
    mod.__file__ = str(ROOT / f"{name}.py")
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)         # noqa: S102 — 결함 주입이 목적이다
    return mod


class _Sink(io.StringIO):
    def reconfigure(self, **kw):      # autotrade 가 import 때 sys.stdout.reconfigure 를 부른다
        pass


def expect_fail(label, fn):
    """fn 이 AssertionError 를 내야 한다 (= 그 검사가 결함을 잡는다)."""
    try:
        with contextlib.redirect_stdout(_Sink()):
            fn()
    except AssertionError as e:
        PASS.append(f"{label} → 검사가 잡는다 ({(str(e).splitlines() or ['assert 실패'])[0][:70]})")
        return
    except Exception as e:                                          # noqa: BLE001
        PASS.append(f"{label} → 검사가 예외로 잡는다 ({type(e).__name__}: {str(e)[:50]})")
        return
    FAIL.append(f"{label} → **주입했는데 통과했다. 이 테스트는 결함을 잡지 못한다**")


# ── 1) engine: 청산 신호보다 현재 봉 저가를 먼저 본다 (감사 전 동작) ──
expect_fail("engine 청산 우선순위 뒤집기", lambda: mutate(
    "engine", ("if want_exit and not breached(o[i], e, lev, side):",
               "if want_exit and not breached(lo[i] if side > 0 else hi[i], e, lev, side):"))._selfcheck())

# ── 2) engine: 진입한 그 봉 안의 강제청산을 검사하지 않는다 ──
expect_fail("engine 진입봉 청산 검사 제거", lambda: mutate(
    "engine", ("if breached(lo_a if side > 0 else hi_a, px, lev, side):", "if False:"))._selfcheck())

# ── 3) engine: 청산선을 진입가 × (1 − 1/lev) 로 (정수 올림·유지증거금 무시) ──
expect_fail("engine 청산선에서 정수레버리지·MMR 제거", lambda: mutate(
    "engine", ("d = 1.0 / exchange_lev(lev) - MMR", "d = 1.0 / lev"))._selfcheck())

# ── 4) engine: 비용 상식 검사 제거 → 편도 300 이 조용히 통과하는가 (2026-09-10 에 실제로 일어난 일) ──
COST_ASSERT = ('assert 0 <= B.COST < 0.05, f"편도 비용 {B.COST} 가 상식 밖이다'
               ' (backtest_okx.arg_cost 주석 참고)"')


def cost_check(mod):
    """편도 300(=30,000%) 으로 시뮬레이터를 돌리면 **소리 내어** 멈춰야 한다."""
    import numpy as np
    import pandas as pd
    import backtest_okx as B
    d = pd.DataFrame([[100, 101, 99, 100]] * 3, columns=["open", "high", "low", "close"],
                     index=pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")).assign(volume=1.0)
    z = np.zeros(3, bool)
    en = z.copy(); en[0] = True
    old, B.COST = B.COST, 300.0
    try:
        mod.run(d, en, z, z, z, lambda i, c: 1)
    except AssertionError:
        return                                                      # 제대로 멈췄다
    finally:
        B.COST = old
    raise AssertionError("편도 300 이 조용히 통과했다 — 성적이 소리 없이 −100% 가 된다")


import engine as _E                                                 # noqa: E402 — 먼저 진짜 엔진이 막는지 본다
cost_check(_E)
PASS.append("engine 비용 상식 검사 → 편도 300 을 실제로 막는다 (양성 확인)")
expect_fail("engine 비용 assert 제거", lambda: cost_check(mutate("engine", (COST_ASSERT, "pass"))))

# ── 5) model: 감사 전 일봉→4h 정렬 (20:00 봉이 하루 묵은 일봉을 본다) ──
def align_legacy():
    import model as M
    import test_signal as TS
    M.ALIGN_LEGACY = True
    try:
        TS.main()
    finally:
        M.ALIGN_LEGACY = False
expect_fail("model 일봉 정렬을 감사 전으로", align_legacy)

# ── 6) autotrade: approve 가 Boolean 인지 안 본다 → bool("false") == True 로 거부가 승인이 된다 ──
def approve_string():
    import logging
    logging.FileHandler = lambda *a, **kw: logging.NullHandler()
    m = mutate("autotrade", ('if not isinstance(out.get("approve"), bool):', "if False:"),
               ('return {"approve": out["approve"]', 'return {"approve": bool(out["approve"])'))
    m.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **k: types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text='{"approve": "false", "reason": "안 된다"}')],
            model="t", usage=types.SimpleNamespace(input_tokens=1, output_tokens=1)))))
    assert m.claude_gate({})["approve"] is False, 'approve="false" 가 승인으로 읽혔다 (test_bot.py:248 이 잡는 결함)'
expect_fail("autotrade approve Boolean 검사 제거", approve_string)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for p in PASS:
        print(f"  ok   {p}")
    for f in FAIL:
        print(f"  FAIL {f}")
    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} 결함 주입이 해당 검사에 잡혔다")
    assert not FAIL, "결함을 잡지 못하는 테스트가 있다"
    print("ok  회귀 테스트가 실제로 결함을 잡는다")
