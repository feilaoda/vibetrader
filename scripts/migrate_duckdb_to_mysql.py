#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from dotenv import load_dotenv
import pymysql

try:
    import duckdb
except Exception as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(f"duckdb is required for migration: {exc}")

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "api" / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306") or 3306)
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "vibetrader")


def _mysql_connect(database: str | None):
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def _ensure_database():
    conn = _mysql_connect(None)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` DEFAULT CHARSET utf8mb4")
    finally:
        conn.close()


def _list_tables(conn) -> List[str]:
    rows = conn.execute("SHOW TABLES").fetchall()
    return [row[0] for row in rows]


def _table_columns(conn, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [row[1] for row in rows]

def _table_pk_columns(conn, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [row[1] for row in rows if row[5]]


def _fetch_batches(conn, table: str, batch_size: int) -> Iterable[Sequence]:
    cursor = conn.execute(f"SELECT * FROM {table}")
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        yield batch


def _migrate_table(duck_conn, mysql_conn, table: str, mode: str, batch_size: int):
    columns = _table_columns(duck_conn, table)
    if not columns:
        print(f"[migrate] skip empty table schema: {table}")
        return
    extra_cols: List[str] = []
    extra_defaults: List = []
    if table == "paper_positions" and "strategy_id" not in columns:
        extra_cols.append("strategy_id")
        extra_defaults.append(1)
    if table == "paper_orders" and "strategy_id" not in columns:
        extra_cols.append("strategy_id")
        extra_defaults.append(1)
    insert_cols = columns + extra_cols
    col_list = ", ".join([f"`{c}`" for c in insert_cols])
    placeholders = ", ".join(["%s"] * len(insert_cols))
    verb = "REPLACE" if mode == "replace" else "INSERT"
    sql = f"{verb} INTO `{table}` ({col_list}) VALUES ({placeholders})"

    total = 0
    for batch in _fetch_batches(duck_conn, table, batch_size):
        if extra_cols:
            batch = [tuple(row) + tuple(extra_defaults) for row in batch]
        with mysql_conn.cursor() as cur:
            cur.executemany(sql, batch)
        total += len(batch)
    print(f"[migrate] {table}: {total} rows")


def _count_rows_duckdb(conn, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _count_rows_mysql(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _verify_samples(duck_conn, mysql_conn, table: str, sample_size: int, extra_defaults: Tuple[List[str], List]) -> Tuple[int, int]:
    columns = _table_columns(duck_conn, table)
    extra_cols, extra_vals = extra_defaults
    pk_cols = _table_pk_columns(duck_conn, table)
    missing = 0
    checked = 0
    if pk_cols:
        duck_pk_list = ", ".join([f"\"{c}\"" for c in pk_cols])
        rows = duck_conn.execute(f"SELECT {duck_pk_list} FROM {table} ORDER BY random() LIMIT {sample_size}").fetchall()
        for row in rows:
            checked += 1
            where = " AND ".join([f"`{c}`=%s" for c in pk_cols])
            params = list(row)
            if table == "paper_positions" and "strategy_id" not in pk_cols and extra_cols:
                where += " AND `strategy_id`=%s"
                params.append(extra_vals[0])
            if table == "paper_orders" and "strategy_id" not in pk_cols and extra_cols:
                where += " AND `strategy_id`=%s"
                params.append(extra_vals[0])
            with mysql_conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM `{table}` WHERE {where} LIMIT 1", params)
                found = cur.fetchone()
            if not found:
                missing += 1
        return checked, missing

    # Fallback: compare full row equality (may be strict for float)
    duck_col_list = ", ".join([f"\"{c}\"" for c in columns])
    rows = duck_conn.execute(f"SELECT {duck_col_list} FROM {table} ORDER BY random() LIMIT {sample_size}").fetchall()
    for row in rows:
        checked += 1
        where_parts = []
        params = []
        for col, val in zip(columns, row):
            if val is None:
                where_parts.append(f"`{col}` IS NULL")
            else:
                where_parts.append(f"`{col}`=%s")
                params.append(val)
        if extra_cols:
            for col, val in zip(extra_cols, extra_vals):
                where_parts.append(f"`{col}`=%s")
                params.append(val)
        where = " AND ".join(where_parts) if where_parts else "1=1"
        with mysql_conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM `{table}` WHERE {where} LIMIT 1", params)
            found = cur.fetchone()
        if not found:
            missing += 1
    return checked, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate DuckDB data to MySQL.")
    parser.add_argument("--duckdb-path", default=str(ROOT / "api" / "history.duckdb"), help="DuckDB file path")
    parser.add_argument("--tables", default="", help="Comma-separated table names to migrate (default: all)")
    parser.add_argument("--exclude", default="", help="Comma-separated table names to skip")
    parser.add_argument("--mode", choices=["insert", "replace"], default="replace", help="Insert mode")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size")
    parser.add_argument("--create-db", action="store_true", help="Create database if missing")
    parser.add_argument("--init-schema", action="store_true", help="Initialize MySQL schema before migrating")
    parser.add_argument("--verify", action="store_true", help="Verify row counts after migration")
    parser.add_argument("--verify-sample", type=int, default=0, help="Verify sample rows per table")
    args = parser.parse_args()

    duck_path = Path(args.duckdb_path)
    if not duck_path.exists():
        print(f"[migrate] duckdb file not found: {duck_path}", file=sys.stderr)
        return 1

    if args.create_db:
        _ensure_database()
    if args.init_schema:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "api"))
        from api.db import init_db  # noqa: E402
        init_db()

    duck_conn = duckdb.connect(str(duck_path))
    mysql_conn = _mysql_connect(MYSQL_DATABASE)

    try:
        tables = _list_tables(duck_conn)
        if args.tables:
            include = {t.strip() for t in args.tables.split(",") if t.strip()}
            tables = [t for t in tables if t in include]
        if args.exclude:
            exclude = {t.strip() for t in args.exclude.split(",") if t.strip()}
            tables = [t for t in tables if t not in exclude]
        if not tables:
            print("[migrate] no tables to migrate", file=sys.stderr)
            return 1

        for table in tables:
            extra_cols = []
            extra_vals = []
            columns = _table_columns(duck_conn, table)
            if table == "paper_positions" and "strategy_id" not in columns:
                extra_cols.append("strategy_id")
                extra_vals.append(1)
            if table == "paper_orders" and "strategy_id" not in columns:
                extra_cols.append("strategy_id")
                extra_vals.append(1)
            _migrate_table(duck_conn, mysql_conn, table, args.mode, args.batch_size)
            if args.verify or args.verify_sample > 0:
                duck_count = _count_rows_duckdb(duck_conn, table)
                mysql_count = _count_rows_mysql(mysql_conn, table)
                status = "OK" if duck_count == mysql_count else "DIFF"
                print(f"[verify] {table}: duckdb={duck_count} mysql={mysql_count} -> {status}")
                if args.verify_sample > 0:
                    checked, missing = _verify_samples(
                        duck_conn, mysql_conn, table, args.verify_sample, (extra_cols, extra_vals)
                    )
                    sample_status = "OK" if missing == 0 else "MISSING"
                    print(f"[verify] {table}: samples={checked} missing={missing} -> {sample_status}")
        return 0
    finally:
        duck_conn.close()
        mysql_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
