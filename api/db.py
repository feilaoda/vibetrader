import os
import re
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
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

AITRADER_RULE_MARKETS = ("ashare", "etf", "us", "crypto")
DEFAULT_RULE_INDICATOR_WEIGHTS: Dict[str, float] = {
    "大盘": 0.5,
    "个股基础": 0.4,
    "趋势动量": 0.8,
    "量能": 0.7,
    "板块相对": 0.6,
    "左侧潜伏": 0.9,
    "右侧突破": 1.0,
    "风险观察": 0.7,
    "止盈止损": 0.6,
    "时间周期": 0.3,
}
DEFAULT_AITRADER_RULE_PROFILES: Dict[str, Dict[str, Any]] = {
    "ashare": {
        "rr_buy_downtrend": 1.5,
        "rr_buy_uptrend": 1.3,
        "require_close_above_ma20_downtrend": True,
        "buy_position_downtrend": "试错小仓(≤10%)，仅右侧确认后执行",
        "watch_position_downtrend": "空仓或轻仓(≤10%)，防守优先",
        "buy_position_uptrend": "分批建仓(10%-25%)",
        "watch_position_uptrend": "等待更优盈亏比后再进场",
        "watch_position_range": "控制仓位，等待方向确认",
    },
    "etf": {
        "rr_buy_downtrend": 1.4,
        "rr_buy_uptrend": 1.2,
        "require_close_above_ma20_downtrend": True,
        "buy_position_downtrend": "试错小仓(≤12%)，仅右侧确认后执行",
        "watch_position_downtrend": "空仓或轻仓(≤12%)，防守优先",
        "buy_position_uptrend": "分批建仓(10%-30%)",
        "watch_position_uptrend": "等待更优盈亏比后再进场",
        "watch_position_range": "控制仓位，等待方向确认",
    },
    "us": {
        "rr_buy_downtrend": 1.4,
        "rr_buy_uptrend": 1.2,
        "require_close_above_ma20_downtrend": False,
        "buy_position_downtrend": "试错小仓(≤12%)，仅右侧确认后执行",
        "watch_position_downtrend": "空仓或轻仓(≤12%)，防守优先",
        "buy_position_uptrend": "分批建仓(10%-30%)",
        "watch_position_uptrend": "等待更优盈亏比后再进场",
        "watch_position_range": "控制仓位，等待方向确认",
    },
    "crypto": {
        "rr_buy_downtrend": 1.6,
        "rr_buy_uptrend": 1.4,
        "require_close_above_ma20_downtrend": True,
        "buy_position_downtrend": "试错小仓(≤8%)，仅右侧确认后执行",
        "watch_position_downtrend": "空仓或轻仓(≤8%)，防守优先",
        "buy_position_uptrend": "分批建仓(8%-20%)",
        "watch_position_uptrend": "等待更优盈亏比后再进场",
        "watch_position_range": "控制仓位，等待方向确认",
    },
}


def _normalize_query(sql: str) -> str:
    if not sql:
        return sql
    text = re.sub(r"(?i)INSERT\\s+OR\\s+REPLACE", "REPLACE", sql)
    text = text.replace("BEGIN TRANSACTION", "START TRANSACTION")
    if "?" in text:
        text = text.replace("?", "%s")
    return text


def _parse_json_field(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {}
    return {}


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

            CREATE TABLE IF NOT EXISTS daily_klines (
                symbol VARCHAR,
                period VARCHAR,
                date VARCHAR,
                open_time BIGINT,
                close_time BIGINT,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                source VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, period, date)
            );

            CREATE TABLE IF NOT EXISTS daily_kline_sync_state (
                symbol VARCHAR,
                period VARCHAR,
                last_update_date VARCHAR,
                last_update_time DOUBLE,
                source VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, period)
            );

            CREATE TABLE IF NOT EXISTS technical_indicators (
                symbol VARCHAR,
                date VARCHAR,
                close DOUBLE,
                ma30 DOUBLE,
                ma60 DOUBLE,
                kdj_period INTEGER,
                daily_k DOUBLE,
                daily_d DOUBLE,
                daily_j DOUBLE,
                weekly_k DOUBLE,
                weekly_d DOUBLE,
                weekly_j DOUBLE,
                source VARCHAR,
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
                market_broad_index VARCHAR,
                market_style_index VARCHAR,
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

            CREATE SEQUENCE IF NOT EXISTS seq_symbol_note_id;
            CREATE TABLE IF NOT EXISTS symbol_notes (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_symbol_note_id'),
                symbol VARCHAR,
                note_date VARCHAR,
                content TEXT,
                is_global BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_symbol_notes_symbol_date
                ON symbol_notes(symbol, note_date);

            CREATE SEQUENCE IF NOT EXISTS seq_prompt_template_id;
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_prompt_template_id'),
                name VARCHAR,
                prompt TEXT,
                params TEXT,
                is_builtin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rule_indicators (
                indicator_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                category VARCHAR,
                description TEXT,
                formula TEXT,
                data_source TEXT,
                params_json TEXT,
                default_enabled BOOLEAN DEFAULT TRUE,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rule_indicator_settings (
                indicator_id VARCHAR,
                market VARCHAR,
                enabled BOOLEAN DEFAULT TRUE,
                params_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (indicator_id, market)
            );

            CREATE TABLE IF NOT EXISTS rule_indicator_bucket_mapping (
                category VARCHAR PRIMARY KEY,
                bucket VARCHAR,
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
                rule_action VARCHAR,
                rule_stage VARCHAR,
                rule_rr DOUBLE,
                rule_confidence DOUBLE,
                rule_trend_score DOUBLE,
                rule_structure_score DOUBLE,
                rule_volume_score DOUBLE,
                rule_rr_score DOUBLE,
                rule_total_score DOUBLE,
                rule_risk_gates TEXT,
                ai_action VARCHAR,
                ai_reason TEXT,
                ai_risk TEXT,
                ai_model VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id, symbol)
            );

            CREATE SEQUENCE IF NOT EXISTS seq_aitrader_history_id;
            CREATE TABLE IF NOT EXISTS aitrader_analysis_history (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_aitrader_history_id'),
                symbol VARCHAR,
                engine VARCHAR,
                model_id VARCHAR,
                bars INTEGER,
                start_date VARCHAR,
                end_date VARCHAR,
                source VARCHAR,
                analysis TEXT,
                analysis_meta_json TEXT,
                snapshot_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS aitrader_rule_profiles (
                market VARCHAR PRIMARY KEY,
                rr_buy_downtrend DOUBLE,
                rr_buy_uptrend DOUBLE,
                require_close_above_ma20_downtrend BOOLEAN DEFAULT TRUE,
                buy_position_downtrend TEXT,
                watch_position_downtrend TEXT,
                buy_position_uptrend TEXT,
                watch_position_uptrend TEXT,
                watch_position_range TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS aitrader_signal_calibration (
                symbol VARCHAR,
                market VARCHAR,
                bucket VARCHAR,
                horizon_days INTEGER,
                sample_size INTEGER,
                hit_rate DOUBLE,
                avg_return DOUBLE,
                mfe DOUBLE,
                mae DOUBLE,
                confidence DOUBLE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, bucket, horizon_days)
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
            CREATE TABLE IF NOT EXISTS daily_klines (
                symbol VARCHAR(32),
                period VARCHAR(16),
                date VARCHAR(10),
                open_time BIGINT,
                close_time BIGINT,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, period, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_kline_sync_state (
                symbol VARCHAR(32),
                period VARCHAR(16),
                last_update_date VARCHAR(8),
                last_update_time DOUBLE,
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, period)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS technical_indicators (
                symbol VARCHAR(32),
                date VARCHAR(10),
                close DOUBLE,
                ma30 DOUBLE,
                ma60 DOUBLE,
                kdj_period INT,
                daily_k DOUBLE,
                daily_d DOUBLE,
                daily_j DOUBLE,
                weekly_k DOUBLE,
                weekly_d DOUBLE,
                weekly_j DOUBLE,
                source VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, date),
                INDEX idx_technical_indicators_date (date)
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
                market_broad_index VARCHAR(32),
                market_style_index VARCHAR(32),
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
            CREATE TABLE IF NOT EXISTS symbol_notes (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                note_date VARCHAR(10),
                content TEXT,
                is_global BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_symbol_notes_symbol_date (symbol, note_date, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(128),
                prompt TEXT,
                params TEXT,
                is_builtin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS rule_indicators (
                indicator_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(128),
                category VARCHAR(64),
                description TEXT,
                formula TEXT,
                data_source TEXT,
                params_json TEXT,
                default_enabled BOOLEAN DEFAULT TRUE,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS rule_indicator_settings (
                indicator_id VARCHAR(64),
                market VARCHAR(16),
                enabled BOOLEAN DEFAULT TRUE,
                params_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (indicator_id, market)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS rule_indicator_bucket_mapping (
                category VARCHAR(64) PRIMARY KEY,
                bucket VARCHAR(16),
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
                rule_action VARCHAR(16),
                rule_stage VARCHAR(64),
                rule_rr DOUBLE,
                rule_rr_up DOUBLE,
                rule_rr_down DOUBLE,
                rule_rr_threshold DOUBLE,
                rule_confidence DOUBLE,
                rule_trend_score DOUBLE,
                rule_structure_score DOUBLE,
                rule_volume_score DOUBLE,
                rule_rr_score DOUBLE,
                rule_total_score DOUBLE,
                rule_risk_gates TEXT,
                ai_action VARCHAR(16),
                ai_reason TEXT,
                ai_risk TEXT,
                ai_model VARCHAR(64),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(run_id, symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS aitrader_analysis_history (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                symbol VARCHAR(32),
                engine VARCHAR(16),
                model_id VARCHAR(64),
                bars INTEGER,
                start_date VARCHAR(10),
                end_date VARCHAR(10),
                source VARCHAR(32),
                analysis LONGTEXT,
                analysis_meta_json LONGTEXT,
                snapshot_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_aitrader_symbol_created (symbol, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS aitrader_rule_profiles (
                market VARCHAR(16) PRIMARY KEY,
                rr_buy_downtrend DOUBLE,
                rr_buy_uptrend DOUBLE,
                require_close_above_ma20_downtrend BOOLEAN DEFAULT TRUE,
                buy_position_downtrend TEXT,
                watch_position_downtrend TEXT,
                buy_position_uptrend TEXT,
                watch_position_uptrend TEXT,
                watch_position_range TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS aitrader_signal_calibration (
                symbol VARCHAR(32),
                market VARCHAR(16),
                bucket VARCHAR(64),
                horizon_days INTEGER,
                sample_size INTEGER,
                hit_rate DOUBLE,
                avg_return DOUBLE,
                mfe DOUBLE,
                mae DOUBLE,
                confidence DOUBLE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(symbol, bucket, horizon_days),
                INDEX idx_signal_calibration_updated (updated_at)
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
    try:
        if not _col_exists("prompt_templates", "params"):
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE prompt_templates ADD COLUMN params TEXT")
            else:
                conn.execute("ALTER TABLE prompt_templates ADD COLUMN params TEXT")
    except Exception:
        pass
    try:
        screening_cols = [
            ("rule_action", "VARCHAR"),
            ("rule_stage", "VARCHAR"),
            ("rule_rr", "DOUBLE"),
            ("rule_rr_up", "DOUBLE"),
            ("rule_rr_down", "DOUBLE"),
            ("rule_rr_threshold", "DOUBLE"),
            ("rule_confidence", "DOUBLE"),
            ("rule_trend_score", "DOUBLE"),
            ("rule_structure_score", "DOUBLE"),
            ("rule_volume_score", "DOUBLE"),
            ("rule_rr_score", "DOUBLE"),
            ("rule_total_score", "DOUBLE"),
            ("rule_risk_gates", "TEXT"),
            ("ai_action", "VARCHAR"),
            ("ai_reason", "TEXT"),
            ("ai_risk", "TEXT"),
            ("ai_model", "VARCHAR"),
        ]
        for col, col_type in screening_cols:
            if _col_exists("screening_results", col):
                continue
            if DB_BACKEND == "duckdb":
                conn.execute(f"ALTER TABLE screening_results ADD COLUMN {col} {col_type}")
            else:
                mysql_type = col_type
                if col_type == "VARCHAR":
                    if col in ("rule_stage",):
                        mysql_type = "VARCHAR(64)"
                    else:
                        mysql_type = "VARCHAR(16)"
                elif col_type == "DOUBLE":
                    mysql_type = "DOUBLE"
                elif col_type == "TEXT":
                    mysql_type = "TEXT"
                conn.execute(f"ALTER TABLE screening_results ADD COLUMN {col} {mysql_type}")
    except Exception:
        pass
    try:
        if not _col_exists("push_settings", "auto_eval_interval_minutes"):
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE push_settings ADD COLUMN auto_eval_interval_minutes INTEGER DEFAULT 5")
            else:
                conn.execute("ALTER TABLE push_settings ADD COLUMN auto_eval_interval_minutes INT DEFAULT 5")
    except Exception:
        pass
    try:
        if not _col_exists("symbol_industry_settings", "market_broad_index"):
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE symbol_industry_settings ADD COLUMN market_broad_index VARCHAR")
            else:
                conn.execute("ALTER TABLE symbol_industry_settings ADD COLUMN market_broad_index VARCHAR(32)")
    except Exception:
        pass
    try:
        if not _col_exists("symbol_industry_settings", "market_style_index"):
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE symbol_industry_settings ADD COLUMN market_style_index VARCHAR")
            else:
                conn.execute("ALTER TABLE symbol_industry_settings ADD COLUMN market_style_index VARCHAR(32)")
    except Exception:
        pass
    try:
        if not _col_exists("symbol_notes", "is_global"):
            if DB_BACKEND == "duckdb":
                conn.execute("ALTER TABLE symbol_notes ADD COLUMN is_global BOOLEAN DEFAULT FALSE")
            else:
                conn.execute("ALTER TABLE symbol_notes ADD COLUMN is_global TINYINT(1) DEFAULT 0")
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

    try:
        ensure_aitrader_rule_profiles(conn)
    except Exception as e:
        print(f"[DB] aitrader rule profile init skipped: {e}")

    try:
        ensure_rule_indicators(conn)
    except Exception as e:
        print(f"[DB] rule indicators init skipped: {e}")

    try:
        ensure_rule_indicator_bucket_mapping(conn)
    except Exception as e:
        print(f"[DB] rule indicator bucket mapping init skipped: {e}")

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
                auto_eval_interval_minutes INTEGER DEFAULT 5,
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
                auto_eval_interval_minutes INT DEFAULT 5,
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


def backfill_symbols_from_daily_klines(limit: Optional[int] = None) -> dict:
    """Backfill symbols table from locally stored daily kline symbols."""
    conn = get_connection()
    try:
        sql = (
            "SELECT k.symbol, COALESCE(MAX(s.name), '') AS name "
            "FROM daily_klines k LEFT JOIN symbols s ON k.symbol = s.symbol "
            "WHERE k.period = ? GROUP BY k.symbol ORDER BY k.symbol ASC"
        )
        rows = conn.execute(sql, ("daily",)).fetchall()
    finally:
        conn.close()

    total = len(rows or [])
    inserted = 0
    skipped = 0
    for row in rows or []:
        if limit and inserted >= int(limit):
            break
        sym = (row[0] or "").upper().strip()
        if not sym:
            skipped += 1
            continue
        if "." in sym:
            code = sym.split(".")[0]
        else:
            code = sym
        if sym.endswith(".US"):
            market = "US"
        elif sym.endswith(".SH"):
            market = "SH"
        elif sym.endswith(".SZ"):
            market = "SZ"
        else:
            market = "BJ"
        sym_type = "etf" if market in ("SH", "SZ") and code.startswith(("5", "15", "16")) else "stock"
        if upsert_symbol_db(sym, code=code, name=row[1] or "", market=market, sym_type=sym_type):
            inserted += 1
        else:
            skipped += 1
    return {"source_total": total, "upserted": inserted, "skipped": skipped}


def _normalize_kline_period(period: str) -> str:
    text = (period or "daily").strip().lower()
    if text in ("1d", "d"):
        return "daily"
    if text in ("1w", "w"):
        return "weekly"
    if text in ("1m", "m"):
        return "monthly"
    return text or "daily"


def _safe_float_value(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text in ("", "--", "None", "nan"):
                return 0.0
            return float(text)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return 0.0
        return num
    except Exception:
        return 0.0


def _safe_int_value(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except Exception:
        return 0


def _normalize_ymd(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if " " in text:
        text = text.split(" ")[0]
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _to_ymd_compact(value: Any) -> str:
    text = _normalize_ymd(value)
    return text.replace("-", "") if text else ""


def normalize_aitrader_rule_market(market: str | None) -> str:
    text = (market or "").strip().lower()
    if text in AITRADER_RULE_MARKETS:
        return text
    if text in ("a", "cn", "ash", "stock"):
        return "ashare"
    return "ashare"


def detect_aitrader_rule_market(symbol: str | None) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        return "ashare"
    if sym.endswith(".US"):
        return "us"
    if sym.endswith((".SH", ".SZ", ".BJ")):
        code = sym.split(".")[0]
        if code.startswith(("15", "16", "5")):
            return "etf"
        return "ashare"
    return "crypto"


def _rule_profile_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _rule_profile_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return float(default)
        return num
    except Exception:
        return float(default)


def _normalize_rule_profile_row(row: Dict[str, Any]) -> Dict[str, Any]:
    market = normalize_aitrader_rule_market(str(row.get("market") or "ashare"))
    defaults = DEFAULT_AITRADER_RULE_PROFILES.get(market, DEFAULT_AITRADER_RULE_PROFILES["ashare"])
    return {
        "market": market,
        "rr_buy_downtrend": _rule_profile_float(row.get("rr_buy_downtrend"), float(defaults["rr_buy_downtrend"])),
        "rr_buy_uptrend": _rule_profile_float(row.get("rr_buy_uptrend"), float(defaults["rr_buy_uptrend"])),
        "require_close_above_ma20_downtrend": _rule_profile_bool(
            row.get("require_close_above_ma20_downtrend"),
            bool(defaults["require_close_above_ma20_downtrend"]),
        ),
        "buy_position_downtrend": str(
            row.get("buy_position_downtrend") or defaults["buy_position_downtrend"]
        ),
        "watch_position_downtrend": str(
            row.get("watch_position_downtrend") or defaults["watch_position_downtrend"]
        ),
        "buy_position_uptrend": str(
            row.get("buy_position_uptrend") or defaults["buy_position_uptrend"]
        ),
        "watch_position_uptrend": str(
            row.get("watch_position_uptrend") or defaults["watch_position_uptrend"]
        ),
        "watch_position_range": str(
            row.get("watch_position_range") or defaults["watch_position_range"]
        ),
    }


def _build_default_rule_profile(market: str) -> Dict[str, Any]:
    market_norm = normalize_aitrader_rule_market(market)
    defaults = DEFAULT_AITRADER_RULE_PROFILES.get(market_norm, DEFAULT_AITRADER_RULE_PROFILES["ashare"])
    return _normalize_rule_profile_row({"market": market_norm, **defaults})


def _decode_rule_profile_row(row: Any) -> Dict[str, Any]:
    return _normalize_rule_profile_row({
        "market": row[0],
        "rr_buy_downtrend": row[1],
        "rr_buy_uptrend": row[2],
        "require_close_above_ma20_downtrend": row[3],
        "buy_position_downtrend": row[4],
        "watch_position_downtrend": row[5],
        "buy_position_uptrend": row[6],
        "watch_position_uptrend": row[7],
        "watch_position_range": row[8],
    })


def ensure_aitrader_rule_profiles(conn=None):
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        for market in AITRADER_RULE_MARKETS:
            defaults = _build_default_rule_profile(market)
            row = conn.execute(
                "SELECT 1 FROM aitrader_rule_profiles WHERE market = ? LIMIT 1",
                (market,),
            ).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO aitrader_rule_profiles "
                "(market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
                "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
                "watch_position_range, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (
                    market,
                    defaults["rr_buy_downtrend"],
                    defaults["rr_buy_uptrend"],
                    int(bool(defaults["require_close_above_ma20_downtrend"])),
                    defaults["buy_position_downtrend"],
                    defaults["watch_position_downtrend"],
                    defaults["buy_position_uptrend"],
                    defaults["watch_position_uptrend"],
                    defaults["watch_position_range"],
                ),
            )
    finally:
        if own_conn:
            conn.close()


def ensure_rule_indicators(conn=None):
    from rule_indicator_catalog import RULE_INDICATOR_CATALOG

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        for item in RULE_INDICATOR_CATALOG:
            indicator_id = str(item.get("indicator_id") or "").strip()
            if not indicator_id:
                continue
            name = item.get("name") or indicator_id
            category = item.get("category") or ""
            description = item.get("description") or ""
            formula = item.get("formula") or ""
            data_source = item.get("data_source") or ""
            params = dict(item.get("params") or {})
            if "weight" not in params:
                params["weight"] = DEFAULT_RULE_INDICATOR_WEIGHTS.get(category, 0.5)
            params_text = json.dumps(params, ensure_ascii=False)
            default_enabled = bool(item.get("default_enabled", True))
            sort_order = int(item.get("sort_order") or 0)
            if DB_BACKEND == "duckdb":
                conn.execute(
                    "INSERT OR REPLACE INTO rule_indicators "
                    "(indicator_id, name, category, description, formula, data_source, params_json, default_enabled, sort_order, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (
                        indicator_id,
                        name,
                        category,
                        description,
                        formula,
                        data_source,
                        params_text,
                        default_enabled,
                        sort_order,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO rule_indicators "
                    "(indicator_id, name, category, description, formula, data_source, params_json, default_enabled, sort_order, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                    "ON DUPLICATE KEY UPDATE "
                    "name = VALUES(name), "
                    "category = VALUES(category), "
                    "description = VALUES(description), "
                    "formula = VALUES(formula), "
                    "data_source = VALUES(data_source), "
                    "params_json = VALUES(params_json), "
                    "default_enabled = VALUES(default_enabled), "
                    "sort_order = VALUES(sort_order), "
                    "updated_at = CURRENT_TIMESTAMP",
                    (
                        indicator_id,
                        name,
                        category,
                        description,
                        formula,
                        data_source,
                        params_text,
                        default_enabled,
                        sort_order,
                    ),
                )
    finally:
        if own_conn:
            conn.close()


def ensure_rule_indicator_bucket_mapping(conn=None):
    from rule_indicator_mapping import get_default_mapping

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        existing_rows = conn.execute(
            "SELECT category FROM rule_indicator_bucket_mapping"
        ).fetchall()
        existing = {str(r[0]) for r in existing_rows if r and r[0] is not None}
        defaults = get_default_mapping()
        for category, bucket in defaults.items():
            if category in existing:
                continue
            if DB_BACKEND == "duckdb":
                conn.execute(
                    "INSERT INTO rule_indicator_bucket_mapping (category, bucket, updated_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (category, bucket),
                )
            else:
                conn.execute(
                    "INSERT INTO rule_indicator_bucket_mapping (category, bucket, updated_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP) "
                    "ON DUPLICATE KEY UPDATE bucket = bucket",
                    (category, bucket),
                )
    finally:
        if own_conn:
            conn.close()


def get_rule_indicator_bucket_mapping() -> Dict[str, str]:
    conn = get_connection()
    try:
        ensure_rule_indicator_bucket_mapping(conn)
        rows = conn.execute(
            "SELECT category, bucket FROM rule_indicator_bucket_mapping"
        ).fetchall()
        mapping: Dict[str, str] = {}
        for row in rows:
            if not row or row[0] is None:
                continue
            category = str(row[0])
            bucket = str(row[1]) if row[1] is not None else ""
            if bucket:
                mapping[category] = bucket
        return mapping
    finally:
        conn.close()


def upsert_rule_indicator_bucket_mapping(category: str, bucket: str) -> bool:
    category_norm = (category or "").strip()
    bucket_norm = (bucket or "").strip().lower()
    if not category_norm or bucket_norm not in {"trend", "structure", "volume", "rr", "total"}:
        return False
    conn = get_connection()
    try:
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO rule_indicator_bucket_mapping (category, bucket, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (category_norm, bucket_norm),
            )
        else:
            conn.execute(
                "INSERT INTO rule_indicator_bucket_mapping (category, bucket, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE bucket = VALUES(bucket), updated_at = CURRENT_TIMESTAMP",
                (category_norm, bucket_norm),
            )
        return True
    finally:
        conn.close()


def list_rule_indicators(market: str = "ashare") -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        ensure_rule_indicators(conn)
        rows = conn.execute(
            "SELECT indicator_id, name, category, description, formula, data_source, params_json, default_enabled, sort_order "
            "FROM rule_indicators ORDER BY sort_order ASC, indicator_id ASC"
        ).fetchall()
        setting_rows = conn.execute(
            "SELECT indicator_id, enabled, params_json FROM rule_indicator_settings WHERE market = ?",
            ((market or "ashare").strip().lower(),),
        ).fetchall()
        settings = {}
        for r in setting_rows:
            settings[str(r[0])] = {
                "enabled": bool(r[1]) if r[1] is not None else None,
                "params": _parse_json_field(r[2]),
            }
        items: List[Dict[str, Any]] = []
        for r in rows:
            indicator_id = str(r[0])
            params = _parse_json_field(r[6])
            if "weight" not in params:
                params["weight"] = DEFAULT_RULE_INDICATOR_WEIGHTS.get(r[2] or "", 0.5)
            if "score_contribution" not in params:
                params["score_contribution"] = 0.0
            setting = settings.get(indicator_id, {})
            if setting.get("params"):
                params = {**params, **setting.get("params")}
            enabled = setting.get("enabled")
            if enabled is None:
                enabled = bool(r[7]) if r[7] is not None else True
            items.append({
                "indicator_id": indicator_id,
                "name": r[1],
                "category": r[2],
                "description": r[3],
                "formula": r[4],
                "data_source": r[5],
                "params": params,
                "default_enabled": bool(r[7]) if r[7] is not None else True,
                "enabled": bool(enabled),
                "sort_order": int(r[8] or 0),
            })
        return items
    finally:
        conn.close()


def upsert_rule_indicator_setting(
    indicator_id: str,
    market: str = "ashare",
    enabled: Optional[bool] = None,
    params: Optional[dict] = None,
) -> bool:
    conn = get_connection()
    try:
        indicator_id = (indicator_id or "").strip()
        if not indicator_id:
            return False
        market_key = (market or "ashare").strip().lower()
        row = conn.execute(
            "SELECT indicator_id FROM rule_indicators WHERE indicator_id = ? LIMIT 1",
            (indicator_id,),
        ).fetchone()
        if not row:
            return False
        existing = conn.execute(
            "SELECT enabled, params_json FROM rule_indicator_settings WHERE indicator_id = ? AND market = ?",
            (indicator_id, market_key),
        ).fetchone()
        current_enabled = enabled
        current_params = params
        if existing:
            if current_enabled is None:
                current_enabled = bool(existing[0]) if existing[0] is not None else None
            if current_params is None:
                current_params = _parse_json_field(existing[1])
        if current_enabled is None:
            current_enabled = True
        params_text = json.dumps(current_params or {}, ensure_ascii=False)
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO rule_indicator_settings "
                "(indicator_id, market, enabled, params_json, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (indicator_id, market_key, bool(current_enabled), params_text),
            )
        else:
            conn.execute(
                "INSERT INTO rule_indicator_settings "
                "(indicator_id, market, enabled, params_json, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE "
                "enabled = VALUES(enabled), "
                "params_json = VALUES(params_json), "
                "updated_at = CURRENT_TIMESTAMP",
                (indicator_id, market_key, bool(current_enabled), params_text),
            )
        return True
    finally:
        conn.close()


def _merge_constraint_defaults(raw: Any) -> Dict[str, Any]:
    merged = dict(DEFAULT_CONSTRAINTS)
    if isinstance(raw, dict):
        merged.update(raw)
        return merged
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                merged.update(loaded)
        except Exception:
            pass
    return merged


def _safe_strategy_numeric(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return float(default)
        return num
    except Exception:
        return float(default)


def _get_effective_paper_strategy_id(conn, strategy_id: int | None = None) -> int:
    if strategy_id:
        row = conn.execute(
            "SELECT id FROM paper_strategies WHERE id = ? LIMIT 1",
            (int(strategy_id),),
        ).fetchone()
        if row:
            return int(row[0])
    row = conn.execute(
        "SELECT id FROM paper_strategies WHERE type = 'manual' ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        return int(row[0])
    row = conn.execute("SELECT id FROM paper_strategies ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row[0])
    return 0


def get_paper_portfolio_context(symbol: str = "", strategy_id: int | None = None):
    conn = get_connection()
    try:
        sid = _get_effective_paper_strategy_id(conn, strategy_id)
        if sid <= 0:
            return {
                "strategy_id": 0,
                "strategy_name": "",
                "initial_capital": 0.0,
                "cash_balance": 0.0,
                "positions_value": 0.0,
                "total_equity": 0.0,
                "max_drawdown_pct": float(DEFAULT_CONSTRAINTS.get("max_drawdown_pct") or 15.0),
                "max_position_pct": float(DEFAULT_CONSTRAINTS.get("max_position_pct") or 20.0),
                "stop_loss_pct": float(DEFAULT_CONSTRAINTS.get("stop_loss_pct") or 5.0),
                "max_drawdown_budget": 0.0,
                "position": {},
                "positions": [],
            }

        strategy_row = conn.execute(
            "SELECT name, initial_capital, constraints_json FROM paper_strategies WHERE id = ?",
            (sid,),
        ).fetchone()
        strategy_name = strategy_row[0] if strategy_row else ""
        initial_capital = _safe_strategy_numeric(strategy_row[1] if strategy_row else None, 100000.0)
        constraints = _merge_constraint_defaults(strategy_row[2] if strategy_row else None)
        max_drawdown_pct = _safe_strategy_numeric(constraints.get("max_drawdown_pct"), 15.0)
        max_position_pct = _safe_strategy_numeric(constraints.get("max_position_pct"), 20.0)
        stop_loss_pct = _safe_strategy_numeric(constraints.get("stop_loss_pct"), 5.0)

        order_rows = conn.execute(
            "SELECT side, price, quantity, fee FROM paper_orders WHERE strategy_id = ?",
            (sid,),
        ).fetchall()
        cash_balance = float(initial_capital)
        for side, price, qty, fee in order_rows:
            amount = _safe_strategy_numeric(price, 0.0) * _safe_strategy_numeric(qty, 0.0)
            fee_val = _safe_strategy_numeric(fee, 0.0)
            if str(side).upper() == "BUY":
                cash_balance -= amount + fee_val
            elif str(side).upper() == "SELL":
                cash_balance += amount - fee_val

        pos_rows = conn.execute(
            "SELECT symbol, quantity, avg_cost FROM paper_positions WHERE strategy_id = ?",
            (sid,),
        ).fetchall()
        positions: List[Dict[str, Any]] = []
        positions_value = 0.0
        target_symbol = (symbol or "").strip().upper()
        target_position: Dict[str, Any] = {}

        for row in pos_rows:
            pos_symbol = str(row[0] or "").strip().upper()
            qty = _safe_int_value(row[1])
            avg_cost = _safe_strategy_numeric(row[2], 0.0)
            close_row = conn.execute(
                "SELECT close FROM daily_klines WHERE symbol = ? AND period = ? ORDER BY date DESC LIMIT 1",
                (pos_symbol, "daily"),
            ).fetchone()
            last_close = _safe_strategy_numeric(close_row[0] if close_row else None, avg_cost)
            market_value = float(last_close) * float(qty)
            positions_value += market_value
            pos_item = {
                "symbol": pos_symbol,
                "quantity": qty,
                "avg_cost": avg_cost,
                "last_close": last_close,
                "market_value": market_value,
            }
            positions.append(pos_item)
            if pos_symbol == target_symbol:
                target_position = pos_item

        total_equity = float(cash_balance) + float(positions_value)
        if total_equity < 0:
            total_equity = 0.0
        max_drawdown_budget = total_equity * max(0.0, max_drawdown_pct) / 100.0

        return {
            "strategy_id": sid,
            "strategy_name": strategy_name or "",
            "initial_capital": float(initial_capital),
            "cash_balance": float(cash_balance),
            "positions_value": float(positions_value),
            "total_equity": float(total_equity),
            "max_drawdown_pct": float(max_drawdown_pct),
            "max_position_pct": float(max_position_pct),
            "stop_loss_pct": float(stop_loss_pct),
            "max_drawdown_budget": float(max_drawdown_budget),
            "position": target_position,
            "positions": positions,
        }
    finally:
        conn.close()


def get_daily_klines_cache(symbol: str, period: str = "daily") -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    per = _normalize_kline_period(period)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, open_time, close_time, open, high, low, close, volume, source "
            "FROM daily_klines WHERE symbol = ? AND period = ? ORDER BY date ASC",
            (sym, per)
        ).fetchall()
        state = conn.execute(
            "SELECT last_update_date, last_update_time, source FROM daily_kline_sync_state "
            "WHERE symbol = ? AND period = ?",
            (sym, per)
        ).fetchone()
        if not rows and not state:
            return None

        klines: List[Dict[str, Any]] = []
        for row in rows:
            date_iso = _normalize_ymd(row[0])
            if not date_iso:
                continue
            open_time = _safe_int_value(row[1])
            close_time = _safe_int_value(row[2])
            if open_time <= 0:
                try:
                    open_time = int(datetime.strptime(date_iso, "%Y-%m-%d").timestamp() * 1000)
                except Exception:
                    open_time = 0
            if close_time <= 0:
                close_time = open_time + 86400000 - 1 if open_time > 0 else 0
            klines.append({
                "date": date_iso,
                "time": None,
                "openTime": open_time,
                "open": _safe_float_value(row[3]),
                "high": _safe_float_value(row[4]),
                "low": _safe_float_value(row[5]),
                "close": _safe_float_value(row[6]),
                "volume": _safe_float_value(row[7]),
                "closeTime": close_time,
                "source": (row[8] or "") if len(row) > 8 else "",
            })

        last_update_date = _to_ymd_compact(state[0]) if state else ""
        if not last_update_date and klines:
            last_update_date = _to_ymd_compact(klines[-1].get("date"))
        last_update_time = _safe_float_value(state[1]) if state else 0.0
        state_source = (state[2] or "") if state and len(state) > 2 else ""

        return {
            "symbol": sym,
            "period": per,
            "last_update_date": last_update_date,
            "last_update_time": last_update_time,
            "source": state_source,
            "klines": klines,
        }
    except Exception as e:
        print(f"[DB] Error loading daily klines cache {sym}/{per}: {e}")
        return None
    finally:
        conn.close()


def save_daily_klines_cache(symbol: str, period: str, data: Dict[str, Any]) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym:
        return False
    per = _normalize_kline_period(period)
    payload = data or {}
    raw_klines = payload.get("klines") if isinstance(payload, dict) else []
    if not isinstance(raw_klines, list):
        raw_klines = []
    state_source = (payload.get("source") or "").strip() if isinstance(payload, dict) else ""

    rows: List[tuple] = []
    for item in raw_klines:
        if not isinstance(item, dict):
            continue
        date_iso = _normalize_ymd(item.get("date"))
        if not date_iso:
            continue
        source = (item.get("source") or state_source or "").strip()
        rows.append((
            sym,
            per,
            date_iso,
            _safe_int_value(item.get("openTime")),
            _safe_int_value(item.get("closeTime")),
            _safe_float_value(item.get("open")),
            _safe_float_value(item.get("high")),
            _safe_float_value(item.get("low")),
            _safe_float_value(item.get("close")),
            _safe_float_value(item.get("volume")),
            source,
        ))

    rows.sort(key=lambda x: x[2])

    last_update_date = ""
    if isinstance(payload, dict):
        last_update_date = _to_ymd_compact(payload.get("last_update_date"))
    if not last_update_date and rows:
        last_update_date = _to_ymd_compact(rows[-1][2])
    last_update_time = _safe_float_value(payload.get("last_update_time")) if isinstance(payload, dict) else 0.0
    if not last_update_time:
        last_update_time = time.time()

    conn = get_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM daily_klines WHERE symbol = ? AND period = ?", (sym, per))
        if rows:
            conn.executemany(
                "INSERT INTO daily_klines "
                "(symbol, period, date, open_time, close_time, open, high, low, close, volume, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                rows
            )
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO daily_kline_sync_state "
                "(symbol, period, last_update_date, last_update_time, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (sym, per, last_update_date, float(last_update_time), state_source)
            )
        else:
            conn.execute(
                "INSERT INTO daily_kline_sync_state "
                "(symbol, period, last_update_date, last_update_time, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE last_update_date=VALUES(last_update_date), "
                "last_update_time=VALUES(last_update_time), source=VALUES(source), "
                "updated_at=CURRENT_TIMESTAMP",
                (sym, per, last_update_date, float(last_update_time), state_source)
            )
        conn.execute("COMMIT")
        return True
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        print(f"[DB] Error saving daily klines cache {sym}/{per}: {e}")
        return False
    finally:
        conn.close()


def save_technical_indicators(symbol: str, rows: List[Dict[str, Any]]) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym or not rows:
        return False
    payload = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_iso = _normalize_ymd(row.get("date"))
        if not date_iso:
            continue
        payload.append((
            sym,
            date_iso,
            _safe_float_value(row.get("close")),
            row.get("ma30"),
            row.get("ma60"),
            _safe_int_value(row.get("kdj_period")) or 9,
            row.get("daily_k"),
            row.get("daily_d"),
            row.get("daily_j"),
            row.get("weekly_k"),
            row.get("weekly_d"),
            row.get("weekly_j"),
            row.get("source") or "computed",
        ))
    if not payload:
        return False
    conn = get_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM technical_indicators WHERE symbol = ?", (sym,))
        conn.executemany(
            "INSERT INTO technical_indicators "
            "(symbol, date, close, ma30, ma60, kdj_period, daily_k, daily_d, daily_j, weekly_k, weekly_d, weekly_j, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            payload
        )
        conn.execute("COMMIT")
        return True
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        print(f"[DB] Error saving technical indicators {sym}: {e}")
        return False
    finally:
        conn.close()


def refresh_technical_indicators(symbol: str, klines: Optional[List[Dict[str, Any]]] = None) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym:
        return False
    if klines is None:
        cache = get_daily_klines_cache(sym, "daily")
        klines = cache.get("klines") if isinstance(cache, dict) else []
    if not klines:
        return False
    try:
        from technical_indicators import build_indicator_rows

        rows = build_indicator_rows(sym, klines, kdj_period=9)
        return save_technical_indicators(sym, rows)
    except Exception as e:
        print(f"[DB] Error computing technical indicators {sym}: {e}")
        return False


def get_latest_technical_indicator(symbol: str) -> Optional[Dict[str, Any]]:
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT symbol, date, close, ma30, ma60, kdj_period, daily_k, daily_d, daily_j, weekly_k, weekly_d, weekly_j, source "
            "FROM technical_indicators WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (sym,)
        ).fetchone()
        if not row:
            return None
        return {
            "symbol": row[0],
            "date": row[1],
            "close": _safe_float_value(row[2]),
            "ma30": _safe_float_value(row[3]) if row[3] is not None else None,
            "ma60": _safe_float_value(row[4]) if row[4] is not None else None,
            "kdj_period": _safe_int_value(row[5]),
            "daily_k": _safe_float_value(row[6]) if row[6] is not None else None,
            "daily_d": _safe_float_value(row[7]) if row[7] is not None else None,
            "daily_j": _safe_float_value(row[8]) if row[8] is not None else None,
            "weekly_k": _safe_float_value(row[9]) if row[9] is not None else None,
            "weekly_d": _safe_float_value(row[10]) if row[10] is not None else None,
            "weekly_j": _safe_float_value(row[11]) if row[11] is not None else None,
            "source": row[12],
        }
    except Exception as e:
        print(f"[DB] Error loading technical indicators {sym}: {e}")
        return None
    finally:
        conn.close()


def get_push_settings() -> dict:
    """Get global push settings (auto-create if missing)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token FROM push_settings WHERE id = 1"
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO push_settings (id, enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (0, 5, 5, "", "")
            )
            return {
                "enabled": False,
                "interval_minutes": 5,
                "auto_eval_interval_minutes": 5,
                "chat_id": "",
                "token": ""
            }
        return {
            "enabled": bool(row[0]),
            "interval_minutes": int(row[1] or 5),
            "auto_eval_interval_minutes": int(row[2] or 5),
            "chat_id": row[3] or "",
            "token": row[4] or ""
        }
    finally:
        conn.close()


def update_push_settings(update: dict) -> dict:
    current = get_push_settings()
    merged = {**current, **(update or {})}
    enabled = 1 if merged.get("enabled") else 0
    interval_minutes = int(merged.get("interval_minutes") or 5)
    auto_eval_interval_minutes = int(merged.get("auto_eval_interval_minutes") or 5)
    chat_id = (merged.get("chat_id") or "").strip()
    token = (merged.get("token") or "").strip()
    conn = get_connection()
    try:
        if DB_BACKEND == "duckdb":
            conn.execute(
                "INSERT OR REPLACE INTO push_settings (id, enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token)
            )
        else:
            conn.execute(
                "INSERT INTO push_settings (id, enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), interval_minutes=VALUES(interval_minutes), "
                "auto_eval_interval_minutes=VALUES(auto_eval_interval_minutes), chat_id=VALUES(chat_id), "
                "token=VALUES(token), updated_at=CURRENT_TIMESTAMP",
                (enabled, interval_minutes, auto_eval_interval_minutes, chat_id, token)
            )
    finally:
        conn.close()
    return {
        "enabled": bool(enabled),
        "interval_minutes": interval_minutes,
        "auto_eval_interval_minutes": auto_eval_interval_minutes,
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

def get_last_history_id(symbol: str) -> int:
    conn = get_connection()
    try:
        if not symbol:
            return 0
        row = conn.execute(
            "SELECT MAX(id) FROM chat_history WHERE symbol = ?",
            (symbol,)
        ).fetchone()
        if not row or row[0] is None:
            return 0
        return int(row[0])
    finally:
        conn.close()

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

def clear_memory(symbol: str) -> bool:
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        if not sym:
            return False
        conn.execute("DELETE FROM chat_memory WHERE symbol = ?", (sym,))
        return True
    finally:
        conn.close()


def list_symbol_notes(symbol: str, limit: int = 200):
    conn = get_connection()
    try:
        sym = (symbol or "").upper().strip()
        lim = max(1, min(int(limit or 200), 1000))
        if not sym:
            return []
        rows = conn.execute(
            "SELECT id, symbol, note_date, content, is_global, created_at, updated_at "
            "FROM symbol_notes WHERE symbol = ? OR is_global = TRUE "
            "ORDER BY note_date DESC, updated_at DESC, id DESC LIMIT ?",
            (sym, lim),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "symbol": row[1],
                "note_date": row[2],
                "content": row[3] or "",
                "is_global": bool(row[4]),
                "created_at": row[5],
                "updated_at": row[6],
            }
            for row in rows
        ]
    finally:
        conn.close()


def create_symbol_note(symbol: str, note_date: str, content: str, is_global: bool = False):
    conn = get_connection()
    try:
        sym = (symbol or "").upper().strip()
        txt = (content or "").strip()
        date_text = str(note_date or "").strip()
        if not sym:
            raise ValueError("Invalid symbol")
        if not txt:
            raise ValueError("Note content is empty")
        if not date_text:
            raise ValueError("Invalid note_date")
        res = conn.execute(
            "INSERT INTO symbol_notes (symbol, note_date, content, is_global, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (sym, date_text, txt, bool(is_global)),
        )
        note_id = int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else 0
        if note_id > 0:
            row = conn.execute(
                "SELECT id, symbol, note_date, content, is_global, created_at, updated_at "
                "FROM symbol_notes WHERE id = ? LIMIT 1",
                (note_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, symbol, note_date, content, is_global, created_at, updated_at "
                "FROM symbol_notes WHERE symbol = ? AND note_date = ? AND content = ? AND is_global = ? "
                "ORDER BY id DESC LIMIT 1",
                (sym, date_text, txt, bool(is_global)),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "symbol": row[1],
            "note_date": row[2],
            "content": row[3] or "",
            "is_global": bool(row[4]),
            "created_at": row[5],
            "updated_at": row[6],
        }
    finally:
        conn.close()


def update_symbol_note(note_id: int, note_date: str, content: str, is_global: bool = False):
    conn = get_connection()
    try:
        txt = (content or "").strip()
        date_text = str(note_date or "").strip()
        if not txt:
            raise ValueError("Note content is empty")
        if not date_text:
            raise ValueError("Invalid note_date")
        nid = int(note_id)
        conn.execute(
            "UPDATE symbol_notes SET note_date = ?, content = ?, is_global = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (date_text, txt, bool(is_global), nid),
        )
        row = conn.execute(
            "SELECT id, symbol, note_date, content, is_global, created_at, updated_at "
            "FROM symbol_notes WHERE id = ? LIMIT 1",
            (nid,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "symbol": row[1],
            "note_date": row[2],
            "content": row[3] or "",
            "is_global": bool(row[4]),
            "created_at": row[5],
            "updated_at": row[6],
        }
    finally:
        conn.close()


def delete_symbol_note(note_id: int) -> bool:
    conn = get_connection()
    try:
        res = conn.execute("DELETE FROM symbol_notes WHERE id = ?", (int(note_id),))
        return bool(getattr(res, "rowcount", 0))
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
            "SELECT id, name, prompt, params, is_builtin, created_at, updated_at FROM prompt_templates ORDER BY id"
        ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "prompt": r[2],
                "params": _parse_json_field(r[3]),
                "is_builtin": bool(r[4]),
                "created_at": r[5],
                "updated_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()

def get_prompt_template_by_name(name: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, prompt, params, is_builtin FROM prompt_templates WHERE name = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
            ((name or "").strip(),)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "params": _parse_json_field(row[3]), "is_builtin": bool(row[4])}
    finally:
        conn.close()

def get_prompt_template_by_id(template_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, prompt, params, is_builtin FROM prompt_templates WHERE id = ?",
            (int(template_id),)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "params": _parse_json_field(row[3]), "is_builtin": bool(row[4])}
    finally:
        conn.close()

def create_prompt_template(name: str, prompt: str, is_builtin: bool = False, params: dict | None = None) -> dict:
    conn = get_connection()
    try:
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        title = _normalize_template_name(conn, name)
        params_text = json.dumps(params or {}, ensure_ascii=False)
        res = conn.execute(
            "INSERT INTO prompt_templates (name, prompt, params, is_builtin, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (title, text, params_text, bool(is_builtin))
        )
        return {"id": int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else None}
    finally:
        conn.close()

def update_prompt_template(template_id: int, name: str | None = None, prompt: str | None = None, params: dict | None = None) -> bool:
    conn = get_connection()
    try:
        updates = []
        values: list = []
        if name is not None:
            raw_name = (name or "").strip() or "Untitled"
            row = conn.execute(
                "SELECT id FROM prompt_templates WHERE name = ? LIMIT 1",
                (raw_name,)
            ).fetchone()
            if row and int(row[0]) != int(template_id):
                raw_name = _normalize_template_name(conn, raw_name)
            updates.append("name = ?")
            values.append(raw_name)
        if prompt is not None:
            updates.append("prompt = ?")
            values.append((prompt or "").strip())
        if params is not None:
            updates.append("params = ?")
            values.append(json.dumps(params or {}, ensure_ascii=False))
        if not updates:
            return True
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(int(template_id))
        conn.execute(
            f"UPDATE prompt_templates SET {', '.join(updates)} WHERE id = ?",
            values
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
            "SELECT t.id, t.name, t.prompt, t.params, t.is_builtin "
            "FROM symbol_prompt_settings s JOIN prompt_templates t ON s.template_id = t.id "
            "WHERE s.symbol = ? LIMIT 1",
            (sym,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "prompt": row[2], "params": _parse_json_field(row[3]), "is_builtin": bool(row[4])}
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


def update_screening_run(
    run_id: int,
    status: str = None,
    processed: int = None,
    total: int = None,
    finished: bool = False,
    finished_status: str = None,
):
    conn = get_connection()
    try:
        if status is not None:
            conn.execute("UPDATE screening_runs SET status = ? WHERE id = ?", (status, run_id))
        if processed is not None:
            conn.execute("UPDATE screening_runs SET processed = ? WHERE id = ?", (int(processed), run_id))
        if total is not None:
            conn.execute("UPDATE screening_runs SET total = ? WHERE id = ?", (int(total), run_id))
        if finished:
            final_status = finished_status or status or "completed"
            conn.execute("UPDATE screening_runs SET finished_at = CURRENT_TIMESTAMP, status = ? WHERE id = ?", (final_status, run_id))
    finally:
        conn.close()


def add_screening_result(
    run_id: int,
    symbol: str,
    action: str,
    score: float,
    reason: str,
    raw_json: dict,
    model_id: str = "",
    rule_metrics: dict | None = None,
    ai_metrics: dict | None = None,
):
    conn = get_connection()
    try:
        rule_metrics = rule_metrics or {}
        ai_metrics = ai_metrics or {}
        scores = rule_metrics.get("scores") or {}
        risk_gates = rule_metrics.get("risk_gates")
        if risk_gates is None:
            risk_gates = scores.get("risk_gates")
        risk_text = ""
        if isinstance(risk_gates, (list, tuple)):
            risk_text = json.dumps(list(risk_gates), ensure_ascii=False)
        elif isinstance(risk_gates, str):
            risk_text = risk_gates
        conn.execute(
            "REPLACE INTO screening_results (run_id, symbol, action, score, reason, model_id, raw_json, "
            "rule_action, rule_stage, rule_rr, rule_rr_up, rule_rr_down, rule_rr_threshold, rule_confidence, "
            "rule_trend_score, rule_structure_score, rule_volume_score, rule_rr_score, rule_total_score, rule_risk_gates, "
            "ai_action, ai_reason, ai_risk, ai_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(run_id),
                symbol,
                action,
                float(score or 0),
                reason or "",
                model_id or "",
                dumps_config(raw_json or {}, {}),
                rule_metrics.get("action") or "",
                rule_metrics.get("stage") or "",
                float(rule_metrics.get("rr") or 0) if rule_metrics.get("rr") is not None else None,
                float(rule_metrics.get("rr_up") or 0) if rule_metrics.get("rr_up") is not None else None,
                float(rule_metrics.get("rr_down") or 0) if rule_metrics.get("rr_down") is not None else None,
                float(rule_metrics.get("rr_threshold") or 0) if rule_metrics.get("rr_threshold") is not None else None,
                float(rule_metrics.get("confidence") or 0) if rule_metrics.get("confidence") is not None else None,
                float(scores.get("trend_score") or 0) if scores.get("trend_score") is not None else None,
                float(scores.get("structure_score") or 0) if scores.get("structure_score") is not None else None,
                float(scores.get("volume_score") or 0) if scores.get("volume_score") is not None else None,
                float(scores.get("rr_score") or 0) if scores.get("rr_score") is not None else None,
                float(scores.get("total_score") or 0) if scores.get("total_score") is not None else None,
                risk_text,
                ai_metrics.get("action") or "",
                ai_metrics.get("reason") or "",
                ai_metrics.get("risk") or "",
                ai_metrics.get("model") or "",
            )
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
                "SELECT symbol, action, score, reason, model_id, raw_json, "
                "rule_action, rule_stage, rule_rr, rule_rr_up, rule_rr_down, rule_rr_threshold, rule_confidence, "
                "rule_trend_score, rule_structure_score, rule_volume_score, rule_rr_score, rule_total_score, rule_risk_gates, "
                "ai_action, ai_reason, ai_risk, ai_model "
                "FROM screening_results WHERE run_id = ? AND action = ? "
                "ORDER BY score DESC LIMIT ? OFFSET ?",
                (int(run_id), action, int(limit), int(offset))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbol, action, score, reason, model_id, raw_json, "
                "rule_action, rule_stage, rule_rr, rule_rr_up, rule_rr_down, rule_rr_threshold, rule_confidence, "
                "rule_trend_score, rule_structure_score, rule_volume_score, rule_rr_score, rule_total_score, rule_risk_gates, "
                "ai_action, ai_reason, ai_risk, ai_model "
                "FROM screening_results WHERE run_id = ? "
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
                "rule_action": row[6],
                "rule_stage": row[7],
                "rule_rr": row[8],
                "rule_rr_up": row[9],
                "rule_rr_down": row[10],
                "rule_rr_threshold": row[11],
                "rule_confidence": row[12],
                "rule_trend_score": row[13],
                "rule_structure_score": row[14],
                "rule_volume_score": row[15],
                "rule_rr_score": row[16],
                "rule_total_score": row[17],
                "rule_risk_gates": row[18],
                "ai_action": row[19],
                "ai_reason": row[20],
                "ai_risk": row[21],
                "ai_model": row[22],
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


def get_screening_stats(run_id: int) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT action, rule_action, rule_stage, rule_risk_gates, ai_action, score, rule_total_score "
            "FROM screening_results WHERE run_id = ?",
            (int(run_id),)
        ).fetchall()
    finally:
        conn.close()

    action_counts: dict[str, int] = {}
    rule_action_counts: dict[str, int] = {}
    rule_stage_counts: dict[str, int] = {}
    ai_action_counts: dict[str, int] = {}
    risk_gate_counts: dict[str, int] = {}
    score_vals: list[float] = []
    rule_score_vals: list[float] = []

    for row in rows or []:
        action = row[0] or ""
        rule_action = row[1] or ""
        rule_stage = row[2] or ""
        rule_risk_gates = row[3] or ""
        ai_action = row[4] or ""
        score = row[5]
        rule_score = row[6]

        if action:
            action_counts[action] = action_counts.get(action, 0) + 1
        if rule_action:
            rule_action_counts[rule_action] = rule_action_counts.get(rule_action, 0) + 1
        if rule_stage:
            rule_stage_counts[rule_stage] = rule_stage_counts.get(rule_stage, 0) + 1
        if ai_action:
            ai_action_counts[ai_action] = ai_action_counts.get(ai_action, 0) + 1

        gates = []
        if isinstance(rule_risk_gates, str) and rule_risk_gates:
            try:
                gates = json.loads(rule_risk_gates)
            except Exception:
                gates = [rule_risk_gates]
        if isinstance(gates, (list, tuple)):
            for gate in gates:
                if not gate:
                    continue
                risk_gate_counts[gate] = risk_gate_counts.get(gate, 0) + 1

        if score is not None:
            try:
                score_vals.append(float(score))
            except Exception:
                pass
        if rule_score is not None:
            try:
                rule_score_vals.append(float(rule_score))
            except Exception:
                pass

    def _hist(values: list[float]) -> dict[str, int]:
        buckets = {"<40": 0, "40-60": 0, "60-75": 0, ">=75": 0}
        for v in values:
            if v < 40:
                buckets["<40"] += 1
            elif v < 60:
                buckets["40-60"] += 1
            elif v < 75:
                buckets["60-75"] += 1
            else:
                buckets[">=75"] += 1
        return buckets

    total = len(rows or [])
    avg_score = sum(score_vals) / len(score_vals) if score_vals else 0.0
    avg_rule_score = sum(rule_score_vals) / len(rule_score_vals) if rule_score_vals else 0.0

    return {
        "total": total,
        "action_counts": action_counts,
        "rule_action_counts": rule_action_counts,
        "rule_stage_counts": rule_stage_counts,
        "ai_action_counts": ai_action_counts,
        "risk_gate_counts": risk_gate_counts,
        "score_hist": _hist(score_vals),
        "rule_score_hist": _hist(rule_score_vals),
        "avg_score": round(avg_score, 2),
        "avg_rule_score": round(avg_rule_score, 2),
    }


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


def list_aitrader_rule_profiles():
    conn = get_connection()
    try:
        ensure_aitrader_rule_profiles(conn)
        rows = conn.execute(
            "SELECT market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
            "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
            "watch_position_range, updated_at "
            "FROM aitrader_rule_profiles ORDER BY market ASC"
        ).fetchall()
        return [
            {
                **_decode_rule_profile_row(row),
                "updated_at": row[9],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_aitrader_rule_profile(market: str = "ashare"):
    conn = get_connection()
    try:
        ensure_aitrader_rule_profiles(conn)
        market_norm = normalize_aitrader_rule_market(market)
        row = conn.execute(
            "SELECT market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
            "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
            "watch_position_range, updated_at "
            "FROM aitrader_rule_profiles WHERE market = ? LIMIT 1",
            (market_norm,),
        ).fetchone()
        if not row:
            return {
                **_build_default_rule_profile(market_norm),
                "updated_at": None,
            }
        return {
            **_decode_rule_profile_row(row),
            "updated_at": row[9],
        }
    finally:
        conn.close()


def upsert_aitrader_rule_profile(market: str, patch: dict | None = None):
    market_norm = normalize_aitrader_rule_market(market)
    conn = get_connection()
    try:
        ensure_aitrader_rule_profiles(conn)
        row = conn.execute(
            "SELECT market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
            "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
            "watch_position_range, updated_at "
            "FROM aitrader_rule_profiles WHERE market = ? LIMIT 1",
            (market_norm,),
        ).fetchone()
        current = {
            **_decode_rule_profile_row(row),
            "updated_at": row[9],
        } if row else {
            **_build_default_rule_profile(market_norm),
            "updated_at": None,
        }
        merged = dict(current)
        merged.update(patch or {})
        merged["market"] = market_norm
        normalized = _normalize_rule_profile_row(merged)
        conn.execute(
            "INSERT OR REPLACE INTO aitrader_rule_profiles "
            "(market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
            "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
            "watch_position_range, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                market_norm,
                normalized["rr_buy_downtrend"],
                normalized["rr_buy_uptrend"],
                int(bool(normalized["require_close_above_ma20_downtrend"])),
                normalized["buy_position_downtrend"],
                normalized["watch_position_downtrend"],
                normalized["buy_position_uptrend"],
                normalized["watch_position_uptrend"],
                normalized["watch_position_range"],
            ),
        )
        row = conn.execute(
            "SELECT market, rr_buy_downtrend, rr_buy_uptrend, require_close_above_ma20_downtrend, "
            "buy_position_downtrend, watch_position_downtrend, buy_position_uptrend, watch_position_uptrend, "
            "watch_position_range, updated_at "
            "FROM aitrader_rule_profiles WHERE market = ? LIMIT 1",
            (market_norm,),
        ).fetchone()
        if not row:
            return {
                **normalized,
                "updated_at": None,
            }
        return {
            **_decode_rule_profile_row(row),
            "updated_at": row[9],
        }
    finally:
        conn.close()


def _to_epoch_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.timestamp()
        except Exception:
            continue
    return 0.0


def get_signal_calibration(symbol: str, bucket: str, horizon_days: int = 5, max_age_hours: float = 24.0):
    sym = (symbol or "").strip().upper()
    key = (bucket or "").strip().lower()
    if not sym or not key:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT symbol, market, bucket, horizon_days, sample_size, hit_rate, avg_return, mfe, mae, confidence, updated_at "
            "FROM aitrader_signal_calibration WHERE symbol = ? AND bucket = ? AND horizon_days = ? LIMIT 1",
            (sym, key, int(horizon_days or 5)),
        ).fetchone()
        if not row:
            return None
        updated_ts = _to_epoch_seconds(row[10])
        if max_age_hours > 0 and updated_ts > 0:
            age = time.time() - updated_ts
            if age > max_age_hours * 3600.0:
                return None
        return {
            "symbol": row[0],
            "market": row[1],
            "bucket": row[2],
            "horizon_days": int(row[3] or 0),
            "sample_size": int(row[4] or 0),
            "hit_rate": float(row[5] or 0.0),
            "avg_return": float(row[6] or 0.0),
            "mfe": float(row[7] or 0.0),
            "mae": float(row[8] or 0.0),
            "confidence": float(row[9] or 0.0),
            "updated_at": row[10],
        }
    finally:
        conn.close()


def upsert_signal_calibration(
    symbol: str,
    market: str,
    bucket: str,
    horizon_days: int,
    sample_size: int,
    hit_rate: float,
    avg_return: float,
    mfe: float,
    mae: float,
    confidence: float,
):
    sym = (symbol or "").strip().upper()
    mkt = (market or "").strip().lower()
    key = (bucket or "").strip().lower()
    if not sym or not key:
        return False
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO aitrader_signal_calibration "
            "(symbol, market, bucket, horizon_days, sample_size, hit_rate, avg_return, mfe, mae, confidence, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                sym,
                mkt,
                key,
                int(horizon_days or 5),
                int(sample_size or 0),
                float(hit_rate or 0.0),
                float(avg_return or 0.0),
                float(mfe or 0.0),
                float(mae or 0.0),
                float(confidence or 0.0),
            ),
        )
        return True
    finally:
        conn.close()


def add_aitrader_analysis_history(
    symbol: str,
    engine: str,
    model_id: str,
    bars: int,
    start_date: str,
    end_date: str,
    source: str,
    analysis: str,
    analysis_meta: dict | None = None,
    snapshot: dict | None = None
) -> int:
    conn = get_connection()
    try:
        sym = (symbol or "").upper().strip()
        eng = (engine or "").strip().lower()
        model = (model_id or "").strip()
        res = conn.execute(
            "INSERT INTO aitrader_analysis_history "
            "(symbol, engine, model_id, bars, start_date, end_date, source, analysis, analysis_meta_json, snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sym,
                eng,
                model,
                int(bars or 0),
                _normalize_ymd(start_date),
                _normalize_ymd(end_date),
                (source or "").strip(),
                analysis or "",
                dumps_config(analysis_meta or {}, {}),
                dumps_config(snapshot or {}, {}),
            )
        )
        return int(res.lastrowid) if hasattr(res, "lastrowid") and res.lastrowid else 0
    finally:
        conn.close()


def list_aitrader_analysis_history(symbol: str = "", limit: int = 50):
    conn = get_connection()
    try:
        lim = max(1, min(int(limit or 50), 500))
        sym = (symbol or "").upper().strip()
        if sym:
            rows = conn.execute(
                "SELECT id, symbol, engine, model_id, bars, start_date, end_date, source, analysis, created_at "
                "FROM aitrader_analysis_history WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (sym, lim)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, symbol, engine, model_id, bars, start_date, end_date, source, analysis, created_at "
                "FROM aitrader_analysis_history ORDER BY id DESC LIMIT ?",
                (lim,)
            ).fetchall()
        result = []
        for row in rows:
            analysis_text = row[8] or ""
            preview = analysis_text[:240]
            if len(analysis_text) > 240:
                preview += "..."
            result.append({
                "id": int(row[0]),
                "symbol": row[1],
                "engine": row[2],
                "model_id": row[3],
                "bars": int(row[4] or 0),
                "start_date": row[5],
                "end_date": row[6],
                "source": row[7] or "",
                "analysis_preview": preview,
                "created_at": row[9],
            })
        return result
    finally:
        conn.close()


def get_aitrader_analysis_history(record_id: int):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, symbol, engine, model_id, bars, start_date, end_date, source, analysis, analysis_meta_json, snapshot_json, created_at "
            "FROM aitrader_analysis_history WHERE id = ?",
            (int(record_id),)
        ).fetchone()
        if not row:
            return None
        analysis_meta = {}
        snapshot = {}
        try:
            analysis_meta = json.loads(row[9]) if row[9] else {}
        except Exception:
            analysis_meta = {}
        try:
            snapshot = json.loads(row[10]) if row[10] else {}
        except Exception:
            snapshot = {}
        return {
            "id": int(row[0]),
            "symbol": row[1],
            "engine": row[2],
            "model_id": row[3],
            "bars": int(row[4] or 0),
            "start_date": row[5],
            "end_date": row[6],
            "source": row[7] or "",
            "analysis": row[8] or "",
            "analysis_meta": analysis_meta,
            "snapshot": snapshot,
            "created_at": row[11],
        }
    finally:
        conn.close()

# Initialize on module load (or call explicitly)
init_db()
