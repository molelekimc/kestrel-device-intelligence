"""dq_log and quarantine: every rule that changes, nulls or removes data writes here."""

import duckdb


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE dq_log (
            check_name    VARCHAR,
            table_name    VARCHAR,
            rows_affected BIGINT,
            action_taken  VARCHAR,
            severity      VARCHAR,   -- info | warning | error
            run_at        TIMESTAMP DEFAULT current_timestamp
        )""")
    # retained = true means the row is still in Silver with the bad field nulled;
    # false means the row was held back from Silver entirely.
    con.execute("""
        CREATE OR REPLACE TABLE quarantine (
            source       VARCHAR,
            source_file  VARCHAR,
            source_row   BIGINT,
            reason       VARCHAR,
            retained     BOOLEAN,
            raw_record   VARCHAR,
            quarantined_at TIMESTAMP DEFAULT current_timestamp
        )""")


def log(con, check_name: str, table_name: str, rows: int, action: str,
        severity: str = "info") -> None:
    con.execute(
        "INSERT INTO dq_log (check_name, table_name, rows_affected, action_taken, severity) "
        "VALUES (?, ?, ?, ?, ?)",
        [check_name, table_name, int(rows), action, severity],
    )


def log_count(con, check_name: str, table_name: str, where_sql: str, action: str,
              severity: str = "info") -> int:
    """Count rows in table_name matching where_sql and log them."""
    n = con.execute(f"SELECT count(*) FROM {table_name} WHERE {where_sql}").fetchone()[0]
    log(con, check_name, table_name, n, action, severity)
    return n


def quarantine(con, source: str, from_sql: str, reason: str, retained: bool) -> int:
    """from_sql must yield (source_file, source_row, raw_record)."""
    n = con.execute(
        f"INSERT INTO quarantine (source, source_file, source_row, reason, retained, raw_record) "
        f"SELECT ?, source_file, source_row, ?, ?, raw_record FROM ({from_sql})",
        [source, reason, retained],
    ).fetchone()[0]
    return n
