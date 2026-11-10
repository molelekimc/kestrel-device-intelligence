"""python -m pipeline.run  ->  kestrel.duckdb (Bronze, Silver, Gold, dq_log, quarantine)."""

import time

import duckdb

from pipeline import bronze, config, dq, gold, silver


def main() -> None:
    started = time.time()
    tmp = config.DB_PATH.with_suffix(".building.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    dq.create_tables(con)
    for name, layer in (("bronze", bronze), ("silver", silver), ("gold", gold)):
        t0 = time.time()
        layer.build(con)
        print(f"{name:<7} built in {time.time() - t0:5.1f}s")
    con.close()
    # Build into a temp file and swap, so readers never see a half-built DB.
    tmp.replace(config.DB_PATH)
    print(f"wrote {config.DB_PATH.name} in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
