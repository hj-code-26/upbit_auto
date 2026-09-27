"""aoa(워뇨띠) BitMEX 체결 CSV → parquet (Trade/Funding/Settlement 만, 필요한 열만)."""
import pathlib
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "COIN_CHANGE"
OUT = ROOT / "data_cache" / "aoa_exec.parquet"
COLS = ["symbol", "side", "lastqty", "lastpx", "lastliquidityind", "exectype", "ordtype", "execinst",
        "orderqty", "price", "stoppx", "text", "execcomm", "homenotional", "foreignnotional", "transacttime", "orderid"]

if __name__ == "__main__":
    parts = []
    for f in sorted(SRC.glob("aoa-execution-*.csv")):
        for ch in pd.read_csv(f, usecols=COLS, chunksize=500_000, low_memory=False, encoding="utf-8-sig"):
            parts.append(ch[ch.exectype.isin(["Trade", "Funding", "Settlement"])])
        print(f.name, sum(len(p) for p in parts))
    df = pd.concat(parts, ignore_index=True)
    df["transacttime"] = pd.to_datetime(df.transacttime, format="mixed")
    df.sort_values("transacttime", kind="stable").to_parquet(OUT)
    print(df.exectype.value_counts(), df.symbol.value_counts().head(20), sep="\n")
