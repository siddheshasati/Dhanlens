"""Medallion pipeline:  bronze (raw CSV)  ->  silver (clean typed Parquet)  ->  gold (DuckDB serving layer).
Run:  python backend/pipeline.py   (add 'bronze' | 'silver' | 'gold' to run one stage)"""
import os, sys
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
BRONZE, SILVER, GOLD = (os.path.join(DATA, d) for d in ("bronze", "silver", "gold"))
GOLD_DB = os.path.join(GOLD, "dhanlens.duckdb")
q = lambda p: p.replace("\\", "/")  # DuckDB wants forward slashes, also on Windows


def bronze():
    """Raw landing zone: exactly what the source produced, as CSV."""
    import generate
    os.makedirs(BRONZE, exist_ok=True)
    chain, candles, pos = generate.build()
    chain.to_csv(os.path.join(BRONZE, "chain_raw.csv"), index=False)
    candles.to_csv(os.path.join(BRONZE, "candles_raw.csv"), index=False)
    pos.to_csv(os.path.join(BRONZE, "positions_raw.csv"), index=False)
    print(f"bronze: chain_raw.csv {len(chain):,} rows, candles_raw.csv {len(candles):,}, positions_raw.csv {len(pos)}")


def silver():
    """Typed, de-duplicated, validated, sorted Parquet (zstd)."""
    os.makedirs(SILVER, exist_ok=True)
    con = duckdb.connect()
    b, s = q(BRONZE), q(SILVER)
    con.execute(f"""copy (
        select cast(ts as timestamp) ts, cast(ts as date) trade_date, underlying, cast(strike as integer) strike,
               opt_type, cast(ltp as double) ltp, cast(iv as double) iv, cast(oi as bigint) oi,
               cast(volume as bigint) volume, cast(spot as double) spot
        from (select *, row_number() over (partition by ts, underlying, strike, opt_type) rn from read_csv_auto('{b}/chain_raw.csv'))
        where rn = 1 and oi > 0 and ltp >= 0 and iv > 0 and opt_type in ('CE','PE')
        order by underlying, ts, strike, opt_type) to '{s}/chain.parquet' (format parquet, compression zstd)""")
    con.execute(f"""copy (select cast(ts as timestamp) ts, underlying, open, high, low, close, cast(volume as bigint) volume
        from read_csv_auto('{b}/candles_raw.csv') where high >= low order by underlying, ts)
        to '{s}/candles.parquet' (format parquet, compression zstd)""")
    con.execute(f"""copy (select * from read_csv_auto('{b}/positions_raw.csv')) to '{s}/positions.parquet' (format parquet)""")
    n = con.execute(f"select count(*) from read_parquet('{s}/chain.parquet')").fetchone()[0]
    print(f"silver: chain.parquet {n:,} rows, candles.parquet, positions.parquet")


def gold():
    """Serving layer the app reads: curated tables + pre-aggregated PCR per minute."""
    os.makedirs(GOLD, exist_ok=True)
    if os.path.exists(GOLD_DB): os.remove(GOLD_DB)
    con, s = duckdb.connect(GOLD_DB), q(SILVER)
    for t in ("chain", "candles", "positions"):
        con.execute(f"create table {t} as select * from read_parquet('{s}/{t}.parquet')")
    con.execute("""create table pcr_minute as select ts, underlying,
        sum(case when opt_type='PE' then oi end) pe_oi, sum(case when opt_type='CE' then oi end) ce_oi,
        sum(case when opt_type='PE' then oi end)*1.0/sum(case when opt_type='CE' then oi end) pcr, any_value(spot) spot
        from chain group by ts, underlying""")
    con.close()
    print("gold: dhanlens.duckdb (chain, candles, positions, pcr_minute)")


def run_all():
    bronze(); silver(); gold()


if __name__ == "__main__":
    stages = {"bronze": bronze, "silver": silver, "gold": gold}
    for st in (sys.argv[1:] or ["all"]):
        run_all() if st == "all" else stages[st]()
