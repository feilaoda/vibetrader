import duckdb
from pathlib import Path
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

DB_PATH = str(Path(__file__).resolve().parent / "history.duckdb")

SYSTEM_PROMPT_TEMPLATES = [
    ("稳健型", """你是一位稳健型A股分析师，目标是控制回撤、提升胜率，优先保护本金。
关注高质量蓝筹、低波动行业龙头或宽基ETF。以中期趋势与关键支撑阻力为主，交易频率低。
当前分析的目标股票是: {symbol}。

请遵循以下原则:
1. 以风险控制优先，强调确认信号，不要追高。
2. 关注趋势强弱、量价配合、支撑/阻力是否有效。
3. 给出保守的仓位建议与风险提示。
4. 输出结构化结论，便于快速阅读。
"""),
    ("中间型", """你是一位平衡型A股分析师，目标是在风险可控前提下追求稳定收益。
兼顾趋势跟随与关键位博弈，允许适度试错但必须有止损逻辑。
当前分析的目标股票是: {symbol}。

请遵循以下原则:
1. 综合趋势强度、关键位与量价关系。
2. 给出多情景判断（强势延续/震荡/转弱）。
3. 结合风险控制给出中性仓位建议。
4. 输出结构清晰、结论明确。
"""),
    ("激进型", """你是一位激进型A股分析师，目标是捕捉趋势加速与放量突破机会，接受更高波动。
关注强势板块、趋势加速、龙头股与流动性好的ETF。
当前分析的目标股票是: {symbol}。

请遵循以下原则:
1. 优先识别强势趋势与突破形态。
2. 强调量能与关键位突破的有效性。
3. 给出进攻性但明确的止损/风控提示。
4. 输出简洁清晰，结论直接。
"""),
]

class ChatMessage(BaseModel):
    id: int
    symbol: str
    timestamp: float
    role: str
    content: str
    model: str
    is_favorite: bool

class SymbolPrompt(BaseModel):
    id: int
    symbol: str
    name: str
    prompt: str
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None

def get_connection():
    conn = duckdb.connect(DB_PATH)
    return conn

def init_db():
    conn = get_connection()
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

    # Paper Trading - migration for strategy support
    def _col_exists(table: str, col: str) -> bool:
        try:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            return any(c[1] == col for c in cols)
        except Exception:
            return False

    def _col_pk(table: str, col: str) -> int:
        try:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            for c in cols:
                if c[1] == col:
                    return int(c[5])
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
            conn.execute("ALTER TABLE paper_positions_v2 RENAME TO paper_positions")
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
        
    conn.close()

def save_symbols_db(df):
    """Save symbols to DuckDB (Overwrite all)"""
    conn = get_connection()
    try:
        # It's faster to drop/create or just replace logic. 
        # But for simplicity, we can delete all and insert.
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM symbols")
        
        # Insert in bulk. Pandas to DuckDB is very fast.
        # df should have cols: symbol, code, name, market, type
        conn.register('df_view', df)
        conn.execute("""
            INSERT INTO symbols (symbol, code, name, market, type, updated_at)
            SELECT symbol, code, name, 
                   CASE 
                       WHEN symbol LIKE '%.SH' THEN 'SH' 
                       WHEN symbol LIKE '%.SZ' THEN 'SZ' 
                       ELSE 'BJ' 
                   END as market,
                   'unknown' as type,
                   CURRENT_TIMESTAMP
            FROM df_view
        """)
        conn.unregister('df_view')
        conn.execute("COMMIT")
        print(f"[DB] Saved {len(df)} symbols")
    except Exception as e:
        conn.execute("ROLLBACK")
        print(f"[DB] Error saving symbols: {e}")
        raise e
    finally:
        conn.close()

def get_symbols_db():
    """Get all symbols from DuckDB"""
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
        # DuckDB timestamp is datetime object
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

def add_message(symbol: str, role: str, content: str, model: str) -> int:
    conn = get_connection()
    timestamp = time.time()
    # Insert and return ID
    conn.execute("""
        INSERT INTO chat_history (symbol, timestamp, role, content, model)
        VALUES (?, ?, ?, ?, ?)
    """, (symbol, timestamp, role, content, model))
    
    res = conn.execute("SELECT currval('seq_chat_id')").fetchone()
    conn.close()
    return res[0] if res else -1

def get_history(symbol: str = None, limit: int = 50):
    conn = get_connection()
    base_query = "SELECT id, symbol, timestamp, role, content, model, is_favorite FROM chat_history"
    params = []
    
    if symbol:
        base_query += " WHERE symbol = ?"
        params.append(symbol)
    
    if limit:
        query = f"SELECT * FROM ({base_query} ORDER BY timestamp DESC LIMIT {limit}) ORDER BY timestamp ASC"
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

def ensure_symbol_prompts(symbol: str):
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        if not sym:
            return
        count = conn.execute(
            "SELECT COUNT(*) FROM symbol_prompts WHERE symbol = ?",
            (sym,)
        ).fetchone()[0]
        if count and int(count) > 0:
            return
        active_id = None
        for name, prompt in SYSTEM_PROMPT_TEMPLATES:
            row = conn.execute(
                "INSERT INTO symbol_prompts (symbol, name, prompt, is_active, updated_at) "
                "VALUES (?, ?, ?, FALSE, CURRENT_TIMESTAMP) RETURNING id",
                (sym, name, prompt)
            ).fetchone()
            if name == "中间型" and row:
                active_id = int(row[0])
        if active_id:
            conn.execute("UPDATE symbol_prompts SET is_active = FALSE WHERE symbol = ?", (sym,))
            conn.execute(
                "UPDATE symbol_prompts SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (active_id,)
            )
    finally:
        conn.close()

def list_symbol_prompts(symbol: str) -> list[dict]:
    ensure_symbol_prompts(symbol)
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        rows = conn.execute(
            "SELECT id, symbol, name, prompt, is_active, created_at, updated_at "
            "FROM symbol_prompts WHERE symbol = ? ORDER BY id",
            (sym,)
        ).fetchall()
        return [
            {
                "id": r[0],
                "symbol": r[1],
                "name": r[2],
                "prompt": r[3],
                "is_active": bool(r[4]),
                "created_at": r[5],
                "updated_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()

def get_active_system_prompt(symbol: str) -> dict | None:
    ensure_symbol_prompts(symbol)
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        row = conn.execute(
            "SELECT id, symbol, name, prompt FROM symbol_prompts WHERE symbol = ? AND is_active = TRUE",
            (sym,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "symbol": row[1], "name": row[2], "prompt": row[3]}
    finally:
        conn.close()

def create_symbol_prompt(symbol: str, name: str, prompt: str, set_active: bool = False) -> dict:
    conn = get_connection()
    try:
        sym = (symbol or "").upper()
        title = (name or "").strip() or "Untitled"
        text = (prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        row = conn.execute(
            "INSERT INTO symbol_prompts (symbol, name, prompt, is_active, updated_at) "
            "VALUES (?, ?, ?, FALSE, CURRENT_TIMESTAMP) RETURNING id",
            (sym, title, text)
        ).fetchone()
        prompt_id = int(row[0]) if row else None
        if set_active and prompt_id:
            set_active_symbol_prompt(prompt_id)
        else:
            active = conn.execute(
                "SELECT 1 FROM symbol_prompts WHERE symbol = ? AND is_active = TRUE LIMIT 1",
                (sym,)
            ).fetchone()
            if not active and prompt_id:
                set_active_symbol_prompt(prompt_id)
        return {"id": prompt_id}
    finally:
        conn.close()

def update_symbol_prompt(prompt_id: int, name: str | None = None, prompt: str | None = None, set_active: bool | None = None) -> bool:
    conn = get_connection()
    try:
        updates = []
        params: list = []
        if name is not None:
            updates.append("name = ?")
            params.append((name or "").strip() or "Untitled")
        if prompt is not None:
            updates.append("prompt = ?")
            params.append((prompt or "").strip())
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(prompt_id)
            conn.execute(
                f"UPDATE symbol_prompts SET {', '.join(updates)} WHERE id = ?",
                params
            )
        if set_active is True:
            set_active_symbol_prompt(prompt_id)
        elif set_active is False:
            conn.execute("UPDATE symbol_prompts SET is_active = FALSE WHERE id = ?", (prompt_id,))
        return True
    finally:
        conn.close()

def set_active_symbol_prompt(prompt_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT symbol FROM symbol_prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not row:
            return False
        symbol = row[0]
        conn.execute("UPDATE symbol_prompts SET is_active = FALSE WHERE symbol = ?", (symbol,))
        conn.execute(
            "UPDATE symbol_prompts SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (prompt_id,)
        )
        return True
    finally:
        conn.close()

def delete_symbol_prompt(prompt_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM symbol_prompts WHERE id = ?", (prompt_id,))
        return True
    finally:
        conn.close()


def create_screening_run(model_id: str, universe: str, params: dict, total: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "INSERT INTO screening_runs (status, model_id, universe, params_json, total, processed) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            ("running", model_id, universe, dumps_config(params or {}, {}), int(total), 0)
        ).fetchone()
        return int(row[0]) if row else 0
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
            "INSERT OR REPLACE INTO screening_results (run_id, symbol, action, score, reason, model_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
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

# Initialize on module load (or call explicitly)
init_db()
