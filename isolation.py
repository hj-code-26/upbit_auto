"""연구 스크립트 격리 — 운영 자산을 **실제로 못 건드리게** 막고 사후에 검증한다.
(quant_nasq100 research/isolation.py 이식, 2026-09-19. 저쪽 것의 필요한 부분만 가져왔다)

    연구/백테스트 파일 맨 위에서:  from isolation import guard; guard()

막는 것
  1. 비밀값 격리 — OKX_*/UPBIT_*/ANTHROPIC_* 환경변수를 **이 프로세스 안에서만** 지운다.
     dotenv.load_dotenv 도 감싼다 — autotrade 가 import 때 다시 불러 키를 되살리기 때문이다.
     autotrade.HAVE_KEYS 가 False 가 되어 실계좌 조회·주문 경로가 애초에 열리지 않는다.
     .env 파일 자체는 읽기 허용 — backtest_current.py 가 설정을 봇과 맞추는 근거다 (비밀값은 안 쓴다).
  2. 운영 파일 쓰기 차단 — trading.db(+wal/shm)·.env·autotrade.log·autorun.off·entry.off 를
     쓰기 모드로 여는 모든 경로(open / sqlite3.connect)를 예외로 막는다.
  3. 변경 감시 — 보호 대상의 (존재, 크기, sha256) 을 실행 전후로 비교한다 (atexit).

왜 필요한가: 2026-09-08 에 dashboard 와 autotrade 두 프로세스가 같은 trading.db 에 판단을 쓰고 있었다.
백테스트가 autotrade 를 import 하는 순간(test_parity.py 가 그렇다) 같은 사고가 난다 — import 만으로
DB 가 생성되고 state 행이 들어간다. 격리는 그 경로를 물리적으로 막는다.

한계: 시세 API(공개 엔드포인트)는 막지 않는다. 비밀값이 없으므로 주문은 못 나가고, 공개 캔들 조회는
캐시가 없을 때 필요하다. 다른 프로세스가 DB 를 여는 것도 막지 못한다 (그건 autotrade.single_instance 의 일).
"""
import atexit
import builtins
import hashlib
import os
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent
PROTECTED = [ROOT / n for n in ("trading.db", "trading.db-wal", "trading.db-shm", ".env",
                                "autotrade.log", "autorun.off", "entry.off")]
SECRETS = ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE", "OKX_DEMO_API_KEY", "OKX_DEMO_SECRET",
           "OKX_DEMO_PASSPHRASE", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "ANTHROPIC_API_KEY")
_ON = False


def _fp(p):
    if not p.exists():
        return None
    b = p.read_bytes()
    return len(b), hashlib.sha256(b).hexdigest()


def _blocked(path):
    try:
        p = pathlib.Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return None
    return p if p in {q.resolve() for q in PROTECTED} else None


def _scrub():
    for k in SECRETS:
        os.environ.pop(k, None)


def guard(allow_read_env=True):
    """호출한 프로세스를 격리 모드로 만든다. 두 번 불러도 한 번만 걸린다."""
    global _ON
    if _ON:
        return
    _ON = True
    _scrub()
    import dotenv                              # **결정적**: autotrade·model 이 import 때 load_dotenv() 를 다시 부른다.
    for name in ("load_dotenv", "dotenv_values"):   # 감싸지 않으면 지운 키가 그대로 되살아난다 (2026-09-19 실측).
        real = getattr(dotenv, name)

        def wrap(*a, _real=real, **kw):
            out = _real(*a, **kw)
            _scrub()
            return out
        setattr(dotenv, name, wrap)
    before = {p: _fp(p) for p in PROTECTED}
    real_open, real_connect = builtins.open, sqlite3.connect

    def open_(file, mode="r", *a, **kw):
        p = _blocked(file)
        if p and not (set(mode) <= set("rbt") and allow_read_env):
            raise PermissionError(f"격리 모드: 운영 파일 쓰기 금지 — {p.name} (mode={mode!r})")
        return real_open(file, mode, *a, **kw)

    def connect_(database, *a, **kw):
        if _blocked(database):
            raise PermissionError(f"격리 모드: 운영 DB 연결 금지 — {database}")
        return real_connect(database, *a, **kw)

    builtins.open, sqlite3.connect = open_, connect_
    atexit.register(_verify, before)


def _verify(before):
    bad = [p.name for p in PROTECTED if _fp(p) != before[p]]
    if bad:                                   # 여기 걸리면 위의 차단을 우회한 경로가 있다는 뜻이다
        raise RuntimeError(f"격리 위반: 운영 파일이 바뀌었다 — {', '.join(bad)}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    guard()
    assert not os.environ.get("OKX_API_KEY"), "비밀값이 지워지지 않았다"
    for f, why in ((lambda: open(ROOT / "trading.db", "wb"), "DB 쓰기"),
                   (lambda: open(ROOT / ".env", "a"), ".env 추가쓰기"),
                   (lambda: sqlite3.connect(ROOT / "trading.db"), "DB 연결")):
        try:
            f()
            raise AssertionError(f"{why} 가 막히지 않았다")
        except PermissionError:
            pass
    open(ROOT / "data_cache" / ".isolation_ok", "w").close()      # 보호 대상 밖은 그대로 써진다
    (ROOT / "data_cache" / ".isolation_ok").unlink()
    if (ROOT / ".env").exists():
        assert len(open(ROOT / ".env", encoding="utf-8").read()) > 0, ".env 읽기는 허용이다"
    import dotenv
    dotenv.load_dotenv(ROOT / ".env", override=True)
    assert not os.environ.get("OKX_API_KEY"), "load_dotenv 가 비밀값을 되살렸다"
    print("ok  격리 점검 통과 (비밀값 제거 · 운영 파일 쓰기/연결 차단 · 그 밖은 정상)")
