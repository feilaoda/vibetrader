import os
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional
import pymysql
from strategy_defaults import (
    DEFAULT_OBJECTIVES,
    DEFAULT_CONSTRAINTS,
    DEFAULT_PARAMS,
    DEFAULT_OPTIMIZATION,
    dumps_config
)
import time
from datetime import datetime
from pydantic import BaseModel
from config import LLM_MODEL
from prompts import SYSTEM_PROMPT_TEMPLATES, DEFAULT_SYSTEM_PROMPT_NAME

DB_PATH = str(Path(__file__).resolve().parent / "history.duckdb")
DB_BACKEND = os.getenv("DB_BACKEND", "mysql").lower()
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306") or 3306)
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "vibetrader")


def _normalize_query(sql: str) -> str:
    if not sql:
        return sql
    text = re.sub(r"(?i)INSERT\\s+OR\\s+REPLACE", "REPLACE", sql)
    text = text.replace("BEGIN TRANSACTION", "START TRANSACTION")
    if "?" in text:
        text = text.replace("?", "%s")
    return text


class _MySQLResult:
    def __init__(self, cursor):
        self._rows: List[Any] = []
        self._idx = 0
        self._desc = cursor.description
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount
        if cursor.description:
            self._rows = list(cursor.fetchall())

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        if self._idx == 0:
            self._idx = len(self._rows)
            return self._rows
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rows

    def df(self):
        try:
            import pandas as pd
        except Exception:
            return None
        if not self._desc:
            return pd.DataFrame([])
        columns = [d[0] for d in self._desc]
        return pd.DataFrame(self._rows, columns=columns)


class _MySQLConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: Optional[Iterable] = None):
        cursor = self._conn.cursor()
        cursor.execute(_normalize_query(sql), params or ())
        result = _MySQLResult(cursor)
        cursor.close()
        return result

    def executemany(self, sql: str, params: Iterable[Iterable]):
        cursor = self._conn.cursor()
        cursor.executemany(_normalize_query(sql), params)
        result = _MySQLResult(cursor)
        cursor.close()
        return result

    def close(self):
        self._conn.close()

class ChatMessage(BaseModel):
    id: int
    symbol: str
    timestamp: float
    role: str
    content: str
    model: str
    is_favorite: bool

class PromptTemplate(BaseModel):
    id: int
    name: str
    prompt: str
    is_builtin: bool
    created_at: str | None = None
    updated_at: str | None = None

class SymbolPromptSetting(BaseModel):
    symbol: str
    template_id: int | None = None
    updated_at: str | None = None

def get_connection():
    if DB_BACKEND == "duckdb":
        import duckdb
        return duckdb.connect(DB_PATH)
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True
    )
    return _MySQLConnection(conn)

def init_db():
    conn = get_connection()
    if DB_BACKEND == "duckdb":
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_chat_id;
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_chat_id'),
                symbol VARCHAR,
                timestamp DOUBLE,
                role VARCHAR,
                content TEXT,
                model VARCHAR,
                is_favorite BOOLEAN DEFAULT FALSE
            );

            CREATE SEQUENCE IF NOT EXISTS seq_action_id;
            CREATE TABLE IF NOT EXISTS action_plans (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_action_id'),
                symbol VARCHAR,
                stock_name VARCHAR,
                action VARCHAR, -- Buy, Sell, Watch
                time_range VARCHAR,
                description TEXT,
                reasoning TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                original_response TEXT,
                status VARCHAR DEFAULT 'pending', -- pending, completed
                model VARCHAR
            );
        """)

        # Paper Trading
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_paper_strategy_id;
            CREATE TABLE IF NOT EXISTS paper_strategies (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_paper_strategy_id'),
                name VARCHAR,
                type VARCHAR, -- manual | aggressive | conservative | custom
                prompt TEXT,
                is_ai BOOLEAN DEFAULT FALSE,
                is_builtin BOOLEAN DEFAULT FALSE,
                model_id VARCHAR,
                run_interval_minutes INTEGER DEFAULT 1440,
                auto_run_enabled BOOLEAN DEFAULT FALSE,
                universe_type VARCHAR,
                universe_symbols TEXT,
                objectives_json TEXT,
                constraints_json TEXT,
                params_json TEXT,
                optimization_json TEXT,
                initial_capital DOUBLE DEFAULT 100000,
                last_run_at TIMESTAMP,
                last_optimized_at TIMESTAMP,
                last_optimization_score DOUBLE,
                last_optimization_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE SEQUENCE IF NOT EXISTS seq_paper_order_id;
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_paper_order_id'),
                symbol VARCHAR,
                side VARCHAR, -- 'BUY' or 'SELL'
                price DOUBLE,
                quantity INTEGER,
                fee DOUBLE,
                strategy_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS paper_positions (
                symbol VARCHAR PRIMARY KEY,
                quantity INTEGER,
                avg_cost DOUBLE
            );

            CREATE SEQUENCE IF NOT EXISTS seq_paper_run_id;
            CREATE TABLE IF NOT EXISTS paper_strategy_runs (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_paper_run_id'),
                strategy_id INTEGER,
                status VARCHAR,
                symbols TEXT,
                symbol_count INTEGER,
                action_count INTEGER,
                model_id VARCHAR,
                run_interval_minutes INTEGER,
                details TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS symbol_fundamentals_daily (
                symbol VARCHAR,
                date VARCHAR,
                market_cap DOUBLE,
                float_market_cap DOUBLE,
                pe_ttm DOUBLE,
                pb DOUBLE,
                source VARCHAR,
                metrics_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, date)
            );

            CREATE TABLE IF NOT EXISTS symbol_industry (
                symbol VARCHAR PRIMARY KEY,
                industry VARCHAR,
                source VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS industry_profiles (
                profile_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                keywords_json TEXT,
                config_json TEXT,
                priority INTEGER DEFAULT 0,
                enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS symbol_profile_override (
                symbol VARCHAR PRIMARY KEY,
                profile_id VARCHAR,
                source VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS symbol_industry_settings (
                symbol VARCHAR PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS industry_indicator_cache (
                indicator_key VARCHAR PRIMARY KEY,
                payload_json TEXT,
                source VARCHAR,
                error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chat_memory (
                symbol VARCHAR PRIMARY KEY,
                summary TEXT,
                last_message_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE SEQUENCE IF NOT EXISTS seq_prompt_template_id;
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_prompt_template_id'),
                name VARCHAR,
                prompt TEXT,
                is_builtin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS symbol_prompt_settings (
                symbol VARCHAR PRIMARY KEY,
                template_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE SEQUENCE IF NOT EXISTS seq_symbol_prompt_id;
            CREATE TABLE IF NOT EXISTS symbol_prompts (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_symbol_prompt_id'),
                symbol VARCHAR,
                name VARCHAR,
                prompt TEXT,
                is_active BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE SEQUENCE IF NOT EXISTS seq_screening_run_id;
            CREATE TABLE IF NOT EXISTS screening_runs (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_screening_run_id'),
                status VARCHAR,
                model_id VARCHAR,
                universe VARCHAR,
                params_json TEXT,
                total INTEGER,
                processed INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS screening_results (
                run_id INTEGER,
                symbol VARCHAR,
                action VARCHAR,
                score DOUBLE,
                reason TEXT,
                model_id VARCHAR,
                raw_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id, symbol)
            );
        """)
    else:
        schema_statements = [
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                timestamp DOUBLE,
                role VARCHAR(32),
                content TEXT,
                model VARCHAR(64),
                is_favorite BOOLEAN DEFAULT FALSE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS action_plans (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                stock_name VARCHAR(128),
                action VARCHAR(16),
                time_range VARCHAR(32),
                description TEXT,
                reasoning TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                original_response TEXT,
                status VARCHAR(32) DEFAULT 'pending',
                model VARCHAR(64)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS paper_strategies (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(128),
                type VARCHAR(32),
                prompt TEXT,
                is_ai BOOLEAN DEFAULT FALSE,
                is_builtin BOOLEAN DEFAULT FALSE,
                model_id VARCHAR(64),
                run_interval_minutes INTEGER DEFAULT 1440,
                auto_run_enabled BOOLEAN DEFAULT FALSE,
                universe_type VARCHAR(32),
                universe_symbols TEXT,
                objectives_json TEXT,
                constraints_json TEXT,
                params_json TEXT,
                optimization_json TEXT,
                initial_capital DOUBLE DEFAULT 100000,
                last_run_at TIMESTAMP NULL,
                last_optimized_at TIMESTAMP NULL,
                last_optimization_score DOUBLE,
                last_optimization_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                side VARCHAR(8),
                price DOUBLE,
                quantity INTEGER,
                fee DOUBLE,
                strategy_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS paper_positions (
                strategy_id INTEGER,
                symbol VARCHAR(32),
                quantity INTEGER,
                avg_cost DOUBLE,
                PRIMARY KEY (strategy_id, symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS paper_strategy_runs (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                strategy_id INTEGER,
                status VARCHAR(32),
                symbols TEXT,
                symbol_count INTEGER,
                action_count INTEGER,
                model_id VARCHAR(64),
                run_interval_minutes INTEGER,
                details TEXT,
                error TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_fundamentals_daily (
                symbol VARCHAR(32),
                date VARCHAR(10),
                market_cap DOUBLE,
                float_market_cap DOUBLE,
                pe_ttm DOUBLE,
                pb DOUBLE,
                source VARCHAR(32),
                metrics_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_industry (
                symbol VARCHAR(32) PRIMARY KEY,
                industry VARCHAR(128),
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS industry_profiles (
                profile_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(128),
                keywords_json TEXT,
                config_json TEXT,
                priority INTEGER DEFAULT 0,
                enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_profile_override (
                symbol VARCHAR(32) PRIMARY KEY,
                profile_id VARCHAR(64),
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_industry_settings (
                symbol VARCHAR(32) PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS industry_indicator_cache (
                indicator_key VARCHAR(128) PRIMARY KEY,
                payload_json TEXT,
                source VARCHAR(32),
                error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS chat_memory (
                symbol VARCHAR(32) PRIMARY KEY,
                summary TEXT,
                last_message_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(128),
                prompt TEXT,
                is_builtin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_prompt_settings (
                symbol VARCHAR(32) PRIMARY KEY,
                template_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_prompts (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                name VARCHAR(128),
                prompt TEXT,
                is_active BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS screening_runs (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                status VARCHAR(32),
                model_id VARCHAR(64),
                universe VARCHAR(32),
                params_json TEXT,
                total INTEGER,
                processed INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS screening_results (
                run_id INTEGER,
                symbol VARCHAR(32),
                action VARCHAR(16),
                score DOUBLE,
                reason TEXT,
                model_id VARCHAR(64),
                raw_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id, symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
        ]
        for stmt in schema_statements:
            conn.execute(stmt)

    # Paper Trading - migration for strategy support
    def _col_exists(table: str, col: str) -> bool:
        try:
            if DB_BACKEND == "duckdb":
                cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                return any(c[1] == col for c in cols)
            row = conn.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                (MYSQL_DATABASE, table, col)
            ).fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    def _col_pk(table: str, col: str) -> int:
        try:
            if DB_BACKEND == "duckdb":
                cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                for c in cols:
                    if c[1] == col:
                        return int(c[5])
            row = conn.execute(
                "SELECT COLUMN_KEY FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
                (MYSQL_DATABASE, table, col)
            ).fetchone()
            if row and row[0] == "PRI":
                return 1
        except Exception:
            pass
        return 0

    try:
        if not _col_exists("screening_results", "model_id"):
            conn.execute("ALTER TABLE screening_results ADD COLUMN model_id VARCHAR")
    except Exception:
        pass

    aggressive_prompt = (
        "你是激进型A股交易员，目标是追求超额收益，接受较高回撤。偏好强势板块、趋势加速、放量突破、"
        "龙头股和流动性好的ETF。可进行短线/波段交易，允许更高换手。\n"
        "仓位：总仓位70%-95%，单标的10%-25%，分批建仓/减仓。\n"
        "风控：单笔止损5%-8%，跌破关键支撑/均线必须止损；止盈15%-30%分批落袋。\n"
        "避免ST、低流动性个股、重大消息不确定期。\n"
        "输出格式要求：只输出JSON对象，不要Markdown。\n"
        "{\n"
        "  \"actions\": [\n"
        "    {\n"
        "      \"symbol\": \"000001.SZ\",\n"
        "      \"side\": \"BUY|SELL|HOLD\",\n"
        "      \"order_type\": \"MKT|LMT\",\n"
        "      \"price\": 0,\n"
        "      \"quantity\": 100,\n"
        "      \"confidence\": 0.0,\n"
        "      \"stop_loss_pct\": 0.05,\n"
        "      \"take_profit_pct\": 0.15,\n"
        "      \"reason\": \"简短理由\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "没有交易机会就输出HOLD，且quantity=0，price=0。"
    )
    conservative_prompt = (
        "你是稳健型A股投资者，目标是控制回撤、稳定收益。偏好高质量蓝筹、低波动、"
        "基本面稳健的行业龙头或宽基ETF。以中期趋势和支撑阻力为主，交易频率低。\n"
        "仓位：总仓位30%-60%，单标的5%-15%，严格分散。\n"
        "风控：单笔止损3%-5%，跌破关键支撑/均线立即止损；止盈8%-15%分批止盈。\n"
        "优先观望，不满足条件时保持现金。\n"
        "输出格式要求：只输出JSON对象，不要Markdown。\n"
        "{\n"
        "  \"actions\": [\n"
        "    {\n"
        "      \"symbol\": \"000001.SZ\",\n"
        "      \"side\": \"BUY|SELL|HOLD\",\n"
        "      \"order_type\": \"MKT|LMT\",\n"
        "      \"price\": 0,\n"
        "      \"quantity\": 100,\n"
        "      \"confidence\": 0.0,\n"
        "      \"stop_loss_pct\": 0.03,\n"
        "      \"take_profit_pct\": 0.08,\n"
        "      \"reason\": \"简短理由\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "没有交易机会就输出HOLD，且quantity=0，price=0。"
    )

    # Ensure default strategies exist
    try:
        count = conn.execute("SELECT COUNT(*) FROM paper_strategies").fetchone()[0]
    except Exception:
        count = 0

    if count == 0:
        conn.execute(
            "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin, model_id, run_interval_minutes, universe_type, universe_symbols, objectives_json, constraints_json, params_json, optimization_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "手工策略",
                "manual",
                "",
                False,
                True,
                None,
                1440,
                "custom",
                "",
                dumps_config(None, DEFAULT_OBJECTIVES),
                dumps_config(None, DEFAULT_CONSTRAINTS),
                dumps_config(None, DEFAULT_PARAMS),
                dumps_config(None, DEFAULT_OPTIMIZATION)
            )
        )
        conn.execute(
            "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin, model_id, run_interval_minutes, universe_type, universe_symbols, objectives_json, constraints_json, params_json, optimization_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "激进投资",
                "aggressive",
                aggressive_prompt,
                True,
                True,
                LLM_MODEL,
                1440,
                "watchlist",
                "",
                dumps_config(None, DEFAULT_OBJECTIVES),
                dumps_config(None, DEFAULT_CONSTRAINTS),
                dumps_config(None, DEFAULT_PARAMS),
                dumps_config(None, DEFAULT_OPTIMIZATION)
            )
        )
        conn.execute(
            "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin, model_id, run_interval_minutes, universe_type, universe_symbols, objectives_json, constraints_json, params_json, optimization_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "稳健投资",
                "conservative",
                conservative_prompt,
                True,
                True,
                LLM_MODEL,
                1440,
                "watchlist",
                "",
                dumps_config(None, DEFAULT_OBJECTIVES),
                dumps_config(None, DEFAULT_CONSTRAINTS),
                dumps_config(None, DEFAULT_PARAMS),
                dumps_config(None, DEFAULT_OPTIMIZATION)
            )
        )
    else:
        # Ensure built-ins exist (idempotent)
        def _ensure_strategy(name: str, typ: str, prompt: str, is_ai: bool):
            row = conn.execute("SELECT id FROM paper_strategies WHERE type = ?", (typ,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO paper_strategies (name, type, prompt, is_ai, is_builtin, model_id, run_interval_minutes, universe_type, universe_symbols, objectives_json, constraints_json, params_json, optimization_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        typ,
                        prompt,
                        is_ai,
                        True,
                        LLM_MODEL if is_ai else None,
                        1440,
                        "watchlist" if is_ai else "custom",
                        "",
                        dumps_config(None, DEFAULT_OBJECTIVES),
                        dumps_config(None, DEFAULT_CONSTRAINTS),
                        dumps_config(None, DEFAULT_PARAMS),
                        dumps_config(None, DEFAULT_OPTIMIZATION)
                    )
                )

        _ensure_strategy("手工策略", "manual", "", False)
        _ensure_strategy("激进投资", "aggressive", aggressive_prompt, True)
        _ensure_strategy("稳健投资", "conservative", conservative_prompt, True)
        # Backfill prompts for existing built-ins if they are empty or too short.
        def _is_blank_prompt(p: str) -> bool:
            if p is None:
                return True
            text = str(p).strip()
            return len(text) < 50

        try:
            row = conn.execute(
                "SELECT prompt FROM paper_strategies WHERE type = 'aggressive'"
            ).fetchone()
            if row and _is_blank_prompt(row[0]):
                conn.execute(
                    "UPDATE paper_strategies SET prompt = ? WHERE type = 'aggressive'",
                    (aggressive_prompt,)
                )
        except Exception:
            pass

        try:
            row = conn.execute(
                "SELECT prompt FROM paper_strategies WHERE type = 'conservative'"
            ).fetchone()
            if row and _is_blank_prompt(row[0]):
                conn.execute(
                    "UPDATE paper_strategies SET prompt = ? WHERE type = 'conservative'",
                    (conservative_prompt,)
                )
        except Exception:
            pass

    # Ensure prompt templates are seeded, and migrate legacy per-symbol prompts if needed.
    try:
        ensure_prompt_templates()
        migrate_symbol_prompts_to_templates()
    except Exception as e:
        print(f"[DB] prompt template init skipped: {e}")

    # Paper orders add strategy_id column if missing
    try:
        if not _col_exists("paper_orders", "strategy_id"):
            conn.execute("ALTER TABLE paper_orders ADD COLUMN strategy_id INTEGER")
    except Exception:
        pass

    # Paper strategies add model_id and run_interval_minutes columns if missing
    try:
        if not _col_exists("paper_strategies", "model_id"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN model_id VARCHAR")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "run_interval_minutes"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN run_interval_minutes INTEGER DEFAULT 1440")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "auto_run_enabled"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN auto_run_enabled BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "universe_type"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN universe_type VARCHAR")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "universe_symbols"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN universe_symbols TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "objectives_json"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN objectives_json TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "constraints_json"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN constraints_json TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "params_json"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN params_json TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "optimization_json"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN optimization_json TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "last_run_at"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN last_run_at TIMESTAMP")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "last_optimized_at"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN last_optimized_at TIMESTAMP")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "last_optimization_score"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN last_optimization_score DOUBLE")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "last_optimization_summary"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN last_optimization_summary TEXT")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategies", "initial_capital"):
            conn.execute("ALTER TABLE paper_strategies ADD COLUMN initial_capital DOUBLE DEFAULT 100000")
    except Exception:
        pass

    # Paper strategy runs columns
    try:
        if not _col_exists("paper_strategy_runs", "symbol_count"):
            conn.execute("ALTER TABLE paper_strategy_runs ADD COLUMN symbol_count INTEGER")
    except Exception:
        pass

    try:
        if not _col_exists("paper_strategy_runs", "action_count"):
            conn.execute("ALTER TABLE paper_strategy_runs ADD COLUMN action_count INTEGER")
    except Exception:
        pass

    # Migrate positions table to include strategy_id and composite primary key
    try:
        needs_migration = (not _col_exists("paper_positions", "strategy_id")) or (_col_pk("paper_positions", "strategy_id") == 0)
        if needs_migration:
            manual_id_row = conn.execute("SELECT id FROM paper_strategies WHERE type = 'manual'").fetchone()
            manual_id = manual_id_row[0] if manual_id_row else 1
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_positions_v2 (
                    strategy_id INTEGER,
                    symbol VARCHAR,
                    quantity INTEGER,
                    avg_cost DOUBLE,
                    PRIMARY KEY (strategy_id, symbol)
                );
            """)
            if _col_exists("paper_positions", "strategy_id"):
                conn.execute("""
                    INSERT INTO paper_positions_v2 (strategy_id, symbol, quantity, avg_cost)
                    SELECT strategy_id, symbol, quantity, avg_cost FROM paper_positions
                """)
            else:
                conn.execute("""
                    INSERT INTO paper_positions_v2 (strategy_id, symbol, quantity, avg_cost)
                    SELECT ?, symbol, quantity, avg_cost FROM paper_positions
                """, (manual_id,))
            conn.execute("DROP TABLE paper_positions")
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE paper_positions_v2 RENAME TO paper_positions")
            else:
                conn.execute("RENAME TABLE paper_positions_v2 TO paper_positions")
    except Exception:
        pass

    # Backfill strategy_id for existing orders if missing
    try:
        manual_id_row = conn.execute("SELECT id FROM paper_strategies WHERE type = 'manual'").fetchone()
        manual_id = manual_id_row[0] if manual_id_row else 1
        conn.execute("UPDATE paper_orders SET strategy_id = ? WHERE strategy_id IS NULL", (manual_id,))
    except Exception:
        pass

    # Backfill strategy settings defaults
    try:
        conn.execute("UPDATE paper_strategies SET run_interval_minutes = 1440 WHERE run_interval_minutes IS NULL")
    except Exception:
        pass

    try:
        conn.execute("UPDATE paper_strategies SET auto_run_enabled = FALSE WHERE auto_run_enabled IS NULL")
    except Exception:
        pass

    try:
        conn.execute("UPDATE paper_strategies SET model_id = ? WHERE model_id IS NULL AND is_ai = TRUE", (LLM_MODEL,))
    except Exception:
        pass

    try:
        conn.execute("UPDATE paper_strategies SET universe_type = CASE WHEN is_ai THEN 'watchlist' ELSE 'custom' END WHERE universe_type IS NULL")
    except Exception:
        pass

    try:
        conn.execute("UPDATE paper_strategies SET universe_symbols = '' WHERE universe_symbols IS NULL")
    except Exception:
        pass

    try:
        conn.execute(
            "UPDATE paper_strategies SET objectives_json = ? WHERE objectives_json IS NULL",
            (dumps_config(None, DEFAULT_OBJECTIVES),)
        )
    except Exception:
        pass

    try:
        conn.execute(
            "UPDATE paper_strategies SET constraints_json = ? WHERE constraints_json IS NULL",
            (dumps_config(None, DEFAULT_CONSTRAINTS),)
        )
    except Exception:
        pass

    try:
        conn.execute(
            "UPDATE paper_strategies SET params_json = ? WHERE params_json IS NULL",
            (dumps_config(None, DEFAULT_PARAMS),)
        )
    except Exception:
        pass

    try:
        conn.execute(
            "UPDATE paper_strategies SET optimization_json = ? WHERE optimization_json IS NULL",
            (dumps_config(None, DEFAULT_OPTIMIZATION),)
        )
    except Exception:
        pass

    try:
        conn.execute("UPDATE paper_strategies SET initial_capital = 100000 WHERE initial_capital IS NULL")
    except Exception:
        pass
    
    # Simple migration: heck if column exists, if not, add it
    try:
        conn.execute("ALTER TABLE action_plans ADD COLUMN status VARCHAR DEFAULT 'pending'")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE action_plans ADD COLUMN model VARCHAR")
    except Exception:
        pass
    
    # Symbols Table
    if DB_BACKEND == "duckdb":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                symbol VARCHAR PRIMARY KEY,
                code VARCHAR,
                name VARCHAR,
                market VARCHAR,
                type VARCHAR, -- 'stock' or 'etf'
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                symbol VARCHAR(32) PRIMARY KEY,
                code VARCHAR(16),
                name VARCHAR(128),
                market VARCHAR(8),
                type VARCHAR(16), -- 'stock' or 'etf'
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    # Watchlist Table
    if DB_BACKEND == "duckdb":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol VARCHAR,
                market VARCHAR,
                name VARCHAR,
                added_at BIGINT,
                sort_order INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, market)
            );
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol VARCHAR(32),
                market VARCHAR(16),
                name VARCHAR(128),
                added_at BIGINT,
                sort_order INT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, market)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

    # Push Settings (Global + per symbol)
    if DB_BACKEND == "duckdb":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_settings (
                id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                interval_minutes INTEGER DEFAULT 5,
                chat_id VARCHAR,
                token VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_push_settings (
                symbol VARCHAR PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_settings (
                id INT PRIMARY KEY,
                enabled TINYINT(1) DEFAULT 0,
                interval_minutes INT DEFAULT 5,
                chat_id VARCHAR(128),
                token VARCHAR(255),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_push_settings (
                symbol VARCHAR(32) PRIMARY KEY,
                enabled TINYINT(1) DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        
    conn.close()

def save_symbols_db(df):
    """Save symbols to DB (Overwrite all)"""
    conn = get_connection()
    try:
        # It's faster to drop/create or just replace logic. 
        # But for simplicity, we can delete all and insert.
        conn.execute("START TRANSACTION")
        conn.execute("DELETE FROM symbols")

        rows = []
        for row in df.itertuples(index=False):
            symbol = getattr(row, "symbol", "")
            code = getattr(row, "code", "")
            name = getattr(row, "name", "")
            if symbol.endswith(".US"):
                market = "US"
            elif symbol.endswith(".SH"):
                market = "SH"
            elif symbol.endswith(".SZ"):
                market = "SZ"
            else:
                market = "BJ"
            sym_type = "index" if market == "US" else "unknown"
            rows.append((symbol, code, name, market, sym_type))
        if rows:
            conn.executemany(
                "INSERT INTO symbols (symbol, code, name, market, type, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                rows
            )
        conn.execute("COMMIT")
        print(f"[DB] Saved {len(df)} symbols")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"[DB] Error saving symbols: {e}")
        raise e
    finally:
        conn.close()

def get_symbols_db():
    """Get all symbols from DB"""
    conn = get_connection()
    try:
        # Check if empty or old
        count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if count == 0:
            return None
            
        # Get timestamp of one row
        ts = conn.execute("SELECT MAX(updated_at) FROM symbols").fetchone()[0]
        if not ts:
            return None
            
        # Check expiry (24h)
        # DB timestamp is datetime object
        if (datetime.now() - ts).total_seconds() > 86400:
            print("[DB] Symbols expired")
            return None
            
        df = conn.execute("SELECT symbol, code, name FROM symbols").df()
        return df
    except Exception as e:
        print(f"[DB] Error loading symbols: {e}")
        return None
    finally:
        conn.close()

def get_symbol_db(symbol: str) -> Optional[dict]:
    """Get single symbol row from DB."""
    if not symbol:
        return None
    conn = get_connection()
    try:
        res = conn.execute("SELECT symbol, code, name, market, type FROM symbols WHERE symbol = ?", (symbol.upper(),))
        df = res.df()
        if df is None or df.empty:
            return None
        return df.iloc[0].to_dict()
    except Exception as e:
        print(f"[DB] Error loading symbol {symbol}: {e}")
        return None
    finally:
        conn.close()

def upsert_symbol_db(symbol: str, code: Optional[str] = None, name: Optional[str] = None,
                     market: Optional[str] = None, sym_type: Optional[str] = None) -> bool:
    """Insert or update a single symbol row (best-effort)."""
    if not symbol:
        return False
    symbol = symbol.upper()
    if not code:
        if "." in symbol:
            code = symbol.split(".")[0]
        elif symbol.startswith(("SH", "SZ", "US")):
            code = symbol[2:]
        else:
            code = symbol
    if not market:
        if symbol.endswith(".US"):
            market = "US"
        elif symbol.endswith(".SH"):
            market = "SH"
        elif symbol.endswith(".SZ"):
            market = "SZ"
        else:
            market = "BJ"
    if not sym_type:
        if market in ("SH", "SZ", "BJ"):
            sym_type = "etf" if code and code.startswith(("5", "15", "16")) else "stock"
        elif market == "US":
            sym_type = "index"
        else:
            sym_type = "unknown"
    name = name or ""
    conn = get_connection()
    try:
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO symbols (symbol, code, name, market, type, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (symbol, code, name, market, sym_type)
            )
        else:
            conn.execute(
                "INSERT INTO symbols (symbol, code, name, market, type, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE code=VALUES(code), name=VALUES(name), market=VALUES(market), "
                "type=VALUES(type), updated_at=CURRENT_TIMESTAMP",
                (symbol, code, name, market, sym_type)
            )
        return True
    except Exception as e:
        print(f"[DB] Error upsert symbol {symbol}: {e}")
        return False
    finally:
        conn.close()


def get_push_settings() -> dict:
    """Get global push settings (auto-create if missing)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT enabled, interval_minutes, chat_id, token FROM push_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO push_settings (id, enabled, interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (0, 5, "", "")
            )
            return {"enabled": False, "interval_minutes": 5, "chat_id": "", "token": ""}
        return {
            "enabled": bool(row[0]),
            "interval_minutes": int(row[1] or 5),
            "chat_id": row[2] or "",
            "token": row[3] or ""
        }
    finally:
        conn.close()


def update_push_settings(update: dict) -> dict:
    current = get_push_settings()
    merged = {**current, **(update or {})}
    enabled = 1 if merged.get("enabled") else 0
    interval_minutes = int(merged.get("interval_minutes") or 5)
    chat_id = (merged.get("chat_id") or "").strip()
    token = (merged.get("token") or "").strip()
    conn = get_connection()
    try:
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO push_settings (id, enabled, interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (enabled, interval_minutes, chat_id, token)
            )
        else:
            conn.execute(
                "INSERT INTO push_settings (id, enabled, interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), interval_minutes=VALUES(interval_minutes), "
                "chat_id=VALUES(chat_id), token=VALUES(token), updated_at=CURRENT_TIMESTAMP",
                (enabled, interval_minutes, chat_id, token)
            )
    finally:
        conn.close()
    return {
        "enabled": bool(enabled),
        "interval_minutes": interval_minutes,
        "chat_id": chat_id,
        "token": token
    }


def get_symbol_push_setting(symbol: str) -> dict:
    sym = (symbol or "").upper()
    if not sym:
        return {"symbol": "", "enabled": False}
    conn = get_connection()
    try:
        row = conn.execute("SELECT enabled FROM symbol_push_settings WHERE symbol = ?", (sym,)).fetchone()
        if not row:
            return {"symbol": sym, "enabled": False}
        return {"symbol": sym, "enabled": bool(row[0])}
    finally:
        conn.close()


def set_symbol_push_setting(symbol: str, enabled: bool) -> bool:
    sym = (symbol or "").upper()
    if not sym:
        return False
    conn = get_connection()
    try:
        val = 1 if enabled else 0
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO symbol_push_settings (symbol, enabled, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (sym, val)
            )
        else:
            conn.execute(
                "INSERT INTO symbol_push_settings (symbol, enabled, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), updated_at=CURRENT_TIMESTAMP",
                (sym, val)
            )
        return True
    finally:
        conn.close()

def add_message(symbol: str, role: str, content: str, model: str) -> int:
    conn = get_connection()
    timestamp = time.time()
    # Insert and return ID
    res = conn.execute("""
        INSERT INTO chat_history (symbol, timestamp, role, content, model)
        VALUES (?, ?, ?, ?, ?)
    """, (symbol, timestamp, role, content, model))
    conn.close()
    return res.lastrowid if hasattr(res, "lastrowid") and res.lastrowid else -1

def get_history(symbol: str = None, limit: int = 50):
    conn = get_connection()
    base_query = "SELECT id, symbol, timestamp, role, content, model, is_favorite FROM chat_history"
    params = []
    
    if symbol:
        base_query += " WHERE symbol = ?"
        params.append(symbol)
    
    if limit:
        query = f"SELECT * FROM ({base_query} ORDER BY timestamp DESC LIMIT {limit}) AS recent ORDER BY timestamp ASC"
    else:
        query = base_query + " ORDER BY timestamp ASC"
    
    result = conn.execute(query, params).fetchall()
    conn.close()
    
    messages = []
    for row in result:
        messages.append(ChatMessage(
            id=row[0],
            symbol=row[1],
            timestamp=row[2],
            role=row[3],
            content=row[4],
            model=row[5],
            is_favorite=row[6]
        ))
    return messages

def toggle_favorite(message_id: int) -> bool:
    conn = get_connection()
    curr = conn.execute("SELECT is_favorite FROM chat_history WHERE id = ?", (message_id,)).fetchone()
    if not curr:
        conn.close()
        return False
        
    new_status = not curr[0]
    conn.execute("UPDATE chat_history SET is_favorite = ? WHERE id = ?", (new_status, message_id))
    conn.close()
    return new_status

def get_memory(symbol: str):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT symbol, summary, last_message_id, updated_at FROM chat_memory WHERE symbol = ?",
            (symbol,)
        ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "summary": row[1],
            "last_message_id": row[2],
            "updated_at": row[3],
        }
    finally:
        conn.close()

def save_memory(symbol: str, summary: str, last_message_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM chat_memory WHERE symbol = ?", (symbol,))
        conn.execute(
            "INSERT INTO chat_memory (symbol, summary, last_message_id, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (symbol, summary, int(last_message_id))
        )
    finally:
        conn.close()

def _normalize_template_name(conn, base_name: str) -> str:
    name = (base_name or "").strip() or "Untitled"
    existing = conn.execute(
        "SELECT 1 FROM prompt_templates WHERE name = ? LIMIT 1",
        (name,)
    ).fetchone()
    if not existing:
        return name
    idx = 2
    while True:
        candidate = f"{name} ({idx})"
        row = conn.execute(
            "SELECT 1 FROM prompt_templates WHERE name = ? LIMIT 1",
            (candidate,)
        ).fetchone()
        if not row:
            return candidate
        idx += 1

def ensure_prompt_templates():
    conn = get_connection()
    try:
        for name, prompt in SYSTEM_PROMPT_TEMPLATES:
            row = conn.execute(
                "SELECT id FROM prompt_templates WHERE name = ? LIMIT 1",
                (name,)
            ).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO prompt_templates (name, prompt, is_builtin, updated_at) VALUES (?, ?, TRUE, CURRENT_TIMESTAMP)",
                (name, prompt)
            )
    finally:
        conn.close()

def migrate_symbol_prompts_to_templates():
    """将旧的 per-symbol prompt（symbol_prompts）迁移为模板并绑定到 symbol。"""
    conn = get_connection()
    try:
        # Only run if legacy table has data and new settings empty
        try:
            legacy_count = conn.execute("SELECT COUNT(*) FROM symbol_prompts").fetchone()[0]
        except Exception:
            legacy_count = 0
        try:
            settings_count = conn.execute("SELECT COUNT(*) FROM symbol_prompt_settings").fetchone()[0]
        except Exception:
            settings_count = 0
        if not legacy_count or settings_count:
            return

        rows = conn.execute(
            "SELECT symbol, name, prompt FROM symbol_prompts WHERE is_active = TRUE"
        ).fetchall()
        for symbol, name, prompt in rows:
            if not prompt:
                continue
            # reuse existing template by exact prompt if possible
            tmpl = conn.execute(
                "SELECT id FROM prompt_templates WHERE prompt = ? LIMIT 1",
                (prompt,)
            ).fetchone()
            if tmpl:
                template_id = tmpl[0]
            else:
                tmpl_name = _normalize_template_name(conn, f"{name} - {symbol}")
                res = conn.execute(
                    "INSERT INTO prompt_templates (name, prompt, is_builtin, updated_at) VALUES (?, ?, FALSE, CURRENT_TIMESTAMP)",
                    (tmpl_name, prompt)
                )
                template_id = int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else None
            if template_id:
                conn.execute("DELETE FROM symbol_prompt_settings WHERE symbol = ?", (symbol,))
                conn.execute(
                    "INSERT INTO symbol_prompt_settings (symbol, template_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (symbol, template_id)
                )
    finally:
        conn.close()

def list_prompt_templates() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, prompt, is_builtin, created_at, updated_at FROM prompt_templates ORDER BY id"
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "prompt": r[2],
                "is_builtin": bool(r[3]),
                "created_at": r[4],
                "updated_at": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()

def get_prompt_template_by_name(name: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, prompt, is_builtin FROM prompt_templates WHERE name = ? LIMIT 1",
            ((name or "").strip(),)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "is_builtin": bool(row[3])}
    finally:
        conn.close()

def get_prompt_template_by_id(template_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, prompt, is_builtin FROM prompt_templates WHERE id = ?",
            (int(template_id),)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "is_builtin": bool(row[3])}
    finally:
        conn.close()

def create_prompt_template(name: str, prompt: str, is_builtin: bool = False) -> dict:
    conn = get_connection()
    try:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        title = _normalize_template_name(conn, name)
        res = conn.execute(
            "INSERT INTO prompt_templates (name, prompt, is_builtin, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (title, text, bool(is_builtin))
        )
        return {"id": int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else None}
    finally:
        conn.close()

def update_prompt_template(template_id: int, name: str | None = None, prompt: str | None = None) -> bool:
    conn = get_connection()
    try:
        updates = []
        params: list = []
        if name is not None:
            raw_name = (name or "").strip() or "Untitled"
            row = conn.execute(
                "SELECT id FROM prompt_templates WHERE name = ? LIMIT 1",
                (raw_name,)
            ).fetchone()
            if row and int(row[0]) != int(template_id):
                raw_name = _normalize_template_name(conn, raw_name)
            updates.append("name = ?")
            params.append(raw_name)
        if prompt is not None:
            updates.append("prompt = ?")
            params.append((prompt or "").strip())
        if not updates:
            return True
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(template_id))
        conn.execute(
            f"UPDATE prompt_templates SET {', '.join(updates)} WHERE id = ?",
            params
        )
        return True
    finally:
        conn.close()

def delete_prompt_template(template_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM symbol_prompt_settings WHERE template_id = ?", (int(template_id),))
        conn.execute("DELETE FROM prompt_templates WHERE id = ?", (int(template_id),))
        return True
    finally:
        conn.close()

def set_symbol_prompt_template(symbol: str, template_id: int | None) -> bool:
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        if not sym:
            return False
        if template_id is None:
            conn.execute("DELETE FROM symbol_prompt_settings WHERE symbol = ?", (sym,))
            return True
        conn.execute("DELETE FROM symbol_prompt_settings WHERE symbol = ?", (sym,))
        conn.execute(
            "INSERT INTO symbol_prompt_settings (symbol, template_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (sym, int(template_id))
        )
        return True
    finally:
        conn.close()

def get_symbol_prompt_template(symbol: str) -> dict | None:
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        row = conn.execute(
            "SELECT t.id, t.name, t.prompt, t.is_builtin "
            "FROM symbol_prompt_settings s JOIN prompt_templates t ON s.template_id = t.id "
            "WHERE s.symbol = ? LIMIT 1",
            (sym,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "is_builtin": bool(row[3])}
    finally:
        conn.close()

def get_active_system_prompt(symbol: str) -> dict | None:
    templ = get_symbol_prompt_template(symbol)
    if templ:
        return templ
    default = get_prompt_template_by_name(DEFAULT_SYSTEM_PROMPT_NAME)
    return default


def create_screening_run(model_id: str, universe: str, params: dict, total: int) -> int:
    conn = get_connection()
    try:
        res = conn.execute(
            "INSERT INTO screening_runs (status, model_id, universe, params_json, total, processed) VALUES (?, ?, ?, ?, ?, ?)",
            ("running", model_id, universe, dumps_config(params or {}, {}), int(total), 0)
        )
        return int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else 0
    finally:
        conn.close()


def update_screening_run(run_id: int, status: str = None, processed: int = None, total: int = None, finished: bool = False):
    conn = get_connection()
    try:
        if status is not None:
            conn.execute("UPDATE screening_runs SET status = ? WHERE id = ?", (status, run_id))
        if processed is not None:
            conn.execute("UPDATE screening_runs SET processed = ? WHERE id = ?", (int(processed), run_id))
        if total is not None:
            conn.execute("UPDATE screening_runs SET total = ? WHERE id = ?", (int(total), run_id))
        if finished:
            conn.execute("UPDATE screening_runs SET finished_at = CURRENT_TIMESTAMP, status = ? WHERE id = ?", ("completed", run_id))
    finally:
        conn.close()


def add_screening_result(run_id: int, symbol: str, action: str, score: float, reason: str, raw_json: dict, model_id: str = ""):
    conn = get_connection()
    try:
        conn.execute(
            "REPLACE INTO screening_results (run_id, symbol, action, score, reason, model_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(run_id), symbol, action, float(score or 0), reason or "", model_id or "", dumps_config(raw_json or {}, {}))
        )
    finally:
        conn.close()


def list_screening_runs(limit: int = 20):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, status, model_id, universe, params_json, total, processed, started_at, finished_at "
            "FROM screening_runs ORDER BY started_at DESC LIMIT ?",
            (int(limit),)
        ).fetchall()
        result = []
        for row in rows:
            params = {}
            try:
                params = json.loads(row[4]) if row[4] else {}
            except Exception:
                params = {}
            result.append({
                "id": row[0],
                "status": row[1],
                "model_id": row[2],
                "universe": row[3],
                "params": params,
                "total": row[5],
                "processed": row[6],
                "started_at": row[7],
                "finished_at": row[8],
            })
        return result
    finally:
        conn.close()


def get_screening_run(run_id: int):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, status, model_id, universe, params_json, total, processed, started_at, finished_at "
            "FROM screening_runs WHERE id = ?",
            (int(run_id),)
        ).fetchone()
        if not row:
            return None
        params = {}
        try:
            params = json.loads(row[4]) if row[4] else {}
        except Exception:
            params = {}
        return {
            "id": row[0],
            "status": row[1],
            "model_id": row[2],
            "universe": row[3],
            "params": params,
            "total": row[5],
            "processed": row[6],
            "started_at": row[7],
            "finished_at": row[8],
        }
    finally:
        conn.close()


def list_screening_results(run_id: int, action: str = None, limit: int = 200, offset: int = 0):
    conn = get_connection()
    try:
        if action:
            rows = conn.execute(
                "SELECT symbol, action, score, reason, model_id, raw_json FROM screening_results WHERE run_id = ? AND action = ? "
                "ORDER BY score DESC LIMIT ? OFFSET ?",
                (int(run_id), action, int(limit), int(offset))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbol, action, score, reason, model_id, raw_json FROM screening_results WHERE run_id = ? "
                "ORDER BY score DESC LIMIT ? OFFSET ?",
                (int(run_id), int(limit), int(offset))
            ).fetchall()
        results = []
        for row in rows:
            raw = {}
            try:
                raw = json.loads(row[5]) if row[5] else {}
            except Exception:
                raw = {}
            results.append({
                "symbol": row[0],
                "action": row[1],
                "score": row[2],
                "reason": row[3],
                "model_id": row[4],
                "raw": raw,
            })
        return results
    finally:
        conn.close()


def delete_screening_results(run_id: int, actions: list[str] | None = None) -> int:
    conn = get_connection()
    try:
        if actions:
            placeholders = ", ".join(["?"] * len(actions))
            params = [int(run_id), *actions]
            res = conn.execute(
                f"DELETE FROM screening_results WHERE run_id = ? AND action IN ({placeholders})",
                params
            )
        else:
            res = conn.execute("DELETE FROM screening_results WHERE run_id = ?", (int(run_id),))
        return int(res.rowcount) if hasattr(res, "rowcount") and res.rowcount is not None else 0
    finally:
        conn.close()


def list_screening_result_symbols(run_id: int):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol FROM screening_results WHERE run_id = ?",
            (int(run_id),)
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def get_screening_summary(run_id: int):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT action, COUNT(*) FROM screening_results WHERE run_id = ? GROUP BY action",
            (int(run_id),)
        ).fetchall()
        return {row[0]: int(row[1]) for row in rows}
    finally:
        conn.close()


def get_latest_screening_run():
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM screening_runs WHERE status != 'completed' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0])
        return None
    finally:
        conn.close()


def delete_screening_run(run_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM screening_results WHERE run_id = ?", (int(run_id),))
        conn.execute("DELETE FROM screening_runs WHERE id = ?", (int(run_id),))
        return True
    finally:
        conn.close()

# Initialize on module load (or call explicitly)
init_db()
