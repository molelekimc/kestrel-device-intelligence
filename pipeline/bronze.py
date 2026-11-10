"""Bronze: raw files as landed. Every column is VARCHAR; nothing is parsed or
fixed here, only source file name, row number and load timestamp are added."""

from pipeline import config, dq

_META = "regexp_extract(filename, '[^/]+$') AS _source_file, current_timestamp AS _loaded_at"


def build(con) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_intune_devices AS
        SELECT * EXCLUDE (filename), row_number() OVER () AS _row_no, {_META}
        FROM read_csv('{config.INTUNE_GLOB}', header = true, all_varchar = true,
                      filename = true)""")

    # The tracker has a title block above the header and a footer below the data,
    # so it lands positionally (c0..c6) and Silver finds the header row.
    cols = ", ".join(f"'c{i}': 'VARCHAR'" for i in range(7))
    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_tracker AS
        SELECT *  EXCLUDE (filename), row_number() OVER () AS _row_no, {_META}
        FROM read_csv('{config.TRACKER_PATH}', header = false, auto_detect = false,
                      columns = {{{cols}}}, filename = true)""")

    # One row per raw line. chr(31) never appears in the JSON, so each line stays
    # a single unparsed value and malformed lines survive to be quarantined.
    con.execute(f"""
        CREATE OR REPLACE TABLE bronze_telemetry_lines AS
        SELECT raw_line,
               row_number() OVER (PARTITION BY filename) AS _row_no,
               {_META}
        FROM read_csv('{config.TELEMETRY_GLOB}', columns = {{'raw_line': 'VARCHAR'}},
                      delim = chr(31), quote = '', escape = '', header = false,
                      auto_detect = false, filename = true)""")

    for table in ("bronze_intune_devices", "bronze_tracker", "bronze_telemetry_lines"):
        dq.log_count(con, "bronze_load", table, "true", "loaded as-is")
