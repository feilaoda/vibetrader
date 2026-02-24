import sys
import os
import argparse
import pandas as pd
from datetime import datetime, timedelta

# Add api directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir)) # .../api
if parent_dir not in sys.path:
    sys.path.append(os.path.join(parent_dir, 'api'))

try:
    from db import get_connection
    from cache import get_klines_with_cache
    from llm import llm_service
except ImportError:
    sys.path.append(os.getcwd())
    from db import get_connection
    from cache import get_klines_with_cache
    from llm import llm_service

def scan_with_ai(symbols_with_reasons: list, prompt_type: str = "custom", model: str = None):
    """
    Args:
        symbols_with_reasons: List of dicts, e.g. [{'symbol': '000001.SZ', 'reason': 'turtle_breakout'}]
        prompt_type: "custom" (embedded) or "db_default" (from llm service defaults)
    """
    if not llm_service.is_configured():
        print("Error: LLM is not configured. Please check .env file.")
        return []

    print(f"AI Screening {len(symbols_with_reasons)} stocks...")
    print("-" * 60)
    
    results = [] # return this
    
    target_model = model or llm_service.default_model
    
    for item in symbols_with_reasons:
        # Compatibility with different key names
        symbol = item.get('symbol') or item.get('Symbol')
        reason = item.get('reason') or item.get('Reason') or ''
        price = item.get('price') or item.get('Price') or 0
        
        if not symbol:
            continue
        
        print(f"Analyzing {symbol} ({reason})...", end="", flush=True)
        
        # 1. Fetch Data (Recent 60 days)
        klines, _ = get_klines_with_cache(symbol, "daily", limit=60)
        if not klines:
            print(" [No Data]")
            continue
            
        # 2. Construct Prompt
        full_msg = ""
        system_msg = "You are a specialized stock screening assistant."
        
        if prompt_type == "db_default":
            # Use the registered system prompt (e.g. from prompts.py or DB)
            # This is "stable" as user requested
            system_msg = llm_service.build_system_prompt(symbol)
            # We append the specific signal info to user message
            user_query = f"""
            我正在根据以下技术指标信号筛选股票：
            触发原因: {reason}
            当前价格: {price}
            
            请结合最近的 K 线数据，分析该信号的有效性。
            
            【重要指令】
            请忽略客套话，直接输出结果。格式严格如下：
            DECISION: [BUY] 或 [WATCH] 或 [PASS]
            REASON: [50字以内的理由]
            """
            kline_str = llm_service._format_kline_data(klines, max_rows=30)
            full_msg = f"{user_query}\n\n最近30天数据:\n{kline_str}"
            
        else:
            # Legacy "custom" hardcoded prompt
            prompt = f"""
            你是一个严谨的量化交易员。我正在筛选股票，以下是生成的买入信号：
            股票代码: {symbol}
            触发原因: {reason}
            当前价格: {price}
            
            请结合最近的 K 线数据（附在后面），分析：
            1. 这个突破/信号是否有效？（是否存在假突破风险？上方是否有强阻力？）
            2. 量能配合情况如何？
            3. 给出明确的操作建议：BUY (买入), WATCH (观察), PASS (放弃).
            
            输出格式要求：
            DECISION: [BUY] 或 [WATCH] 或 [PASS]
            REASON: [简述理由]
            """
            kline_str = llm_service._format_kline_data(klines, max_rows=30)
            full_msg = f"{prompt}\n\n最近30天数据:\n{kline_str}"
        
        try:
            client = llm_service._get_client(target_model)
            response = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": full_msg}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            content = response.choices[0].message.content.strip()
            
            # Parse decision
            decision = "UNKNOWN"
            upper_content = content.upper()
            
            # Find decision line
            decision_line = ""
            for line in upper_content.split('\n'):
                if "DECISION:" in line:
                    decision_line = line
                    break
            
            # Check decision in that specific line (prioritized)
            if decision_line:
                if "BUY" in decision_line:
                    decision = "BUY"
                elif "WATCH" in decision_line:
                    decision = "WATCH"
                elif "PASS" in decision_line:
                    decision = "PASS"
            
            # Fallback if no decision found in line, or no decision line found
            if decision == "UNKNOWN":
                if "DECISION: BUY" in upper_content or "DECISION:BUY" in upper_content or "DECISION: [BUY]" in upper_content:
                    decision = "BUY"
                elif "DECISION: WATCH" in upper_content or "DECISION:WATCH" in upper_content or "DECISION: [WATCH]" in upper_content:
                    decision = "WATCH"
                elif "DECISION: PASS" in upper_content or "DECISION:PASS" in upper_content or "DECISION: [PASS]" in upper_content:
                     decision = "PASS"
                # Fuzzy fallback
                elif "RECOMMEND" in upper_content and "BUY" in upper_content:
                    decision = "BUY"
                elif "建议买入" in content:
                    decision = "BUY"
                elif "RECOMMEND" in upper_content and "WATCH" in upper_content:
                    decision = "WATCH"
                elif "建议观察" in content:
                    decision = "WATCH"
                elif "建议放弃" in content or "不建议" in content:
                    decision = "PASS"

            if decision == "UNKNOWN":
                print(f"\n[DEBUG] Raw AI Response for {symbol}:\n{content.encode('utf-8', errors='replace').decode('utf-8')}\n[DEBUG] End Raw Response")
                sys.stdout.flush()

            # Extract summary
            summary = content
            if "REASON:" in content:
                summary = content.split("REASON:")[-1].strip()
            elif "DECISION:" in content:
                # Remove the decision line
                lines = content.split('\n')
                summary = " ".join([l for l in lines if "DECISION:" not in l]).strip()
            
            results.append({
                "Symbol": symbol,
                "Reason": reason,
                "AI_Decision": decision,
                "AI_Comment": summary[:150].replace('\n', ' ')
            })
            print(f" -> {decision}")
            
        except Exception as e:
            print(f" [Error: {e}]")
            
    # Output Table
    if results:
        df = pd.DataFrame(results)
        # Sort: BUY first, then WATCH, then PASS
        df['rank'] = df['AI_Decision'].map({'BUY': 1, 'WATCH': 2, 'PASS': 3, 'UNKNOWN': 4})
        df = df.sort_values('rank')
        
        # Save to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ai_scan_results_{timestamp}.csv"
        output_path = os.path.join(parent_dir, 'api', 'quant', filename)
        df.to_csv(output_path, index=False)
        print(f"\n[Saved] Results saved to: {output_path}")
        
        print("\n" + "="*100)
        print("AI SCREENING RESULTS")
        print("="*100)
        print(f"{'Symbol':<12} {'Reason':<30} {'Decision':<10} {'Comment'}")
        print("-" * 100)
        for _, row in df.iterrows():
            print(f"{row['Symbol']:<12} {row['Reason']:<30} {row['AI_Decision']:<10} {row['AI_Comment']}")
        print("-" * 100)
        
    return results

if __name__ == "__main__":
    try:
        from quant.log_utils import setup_logging
    except ImportError:
        parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent not in sys.path: sys.path.append(parent)
        from quant.log_utils import setup_logging
        
    setup_logging("scan_with_ai")

    # Example hardcoded list from user request
    # Ideally this would accept JSON input or file
    list_str = """
000590.SZ    12.40      0.80       turtle_breakout (12.4 > 12.02)          
000692.SZ    3.95       0.80       turtle_breakout (3.95 > 3.92)           
000700.SZ    15.87      0.80       turtle_breakout (15.87 > 15.65)         
000802.SZ    5.15       0.80       turtle_breakout (5.15 > 4.95)           
000818.SZ    23.62      0.80       turtle_breakout (23.62 > 22.85)         
000999.SZ    29.20      0.80       turtle_breakout (29.2 > 29.16)          
001217.SZ    15.10      0.80       turtle_breakout (15.1 > 13.89)          
001336.SZ    28.35      0.80       turtle_breakout (28.35 > 28.12)         
002015.SZ    12.83      0.80       turtle_breakout (12.83 > 11.53)         
002069.SZ    4.11       0.80       turtle_breakout (4.11 > 4.03)           
002082.SZ    18.59      0.80       turtle_breakout (18.59 > 18.28)         
002099.SZ    8.37       0.80       turtle_breakout (8.37 > 7.71)           
002215.SZ    12.33      0.80       turtle_breakout (12.33 > 11.84)         
002292.SZ    10.88      0.80       turtle_breakout (10.88 > 10.87)         
002293.SZ    10.67      0.80       turtle_breakout (10.67 > 10.55)         
002309.SZ    3.46       0.80       turtle_breakout (3.46 > 3.3)            
002323.SZ    2.16       0.80       turtle_breakout (2.16 > 1.89)           
002343.SZ    8.40       0.80       turtle_breakout (8.4 > 8.31)            
002412.SZ    8.09       0.80       turtle_breakout (8.09 > 7.17)           
002445.SZ    2.94       0.80       turtle_breakout (2.94 > 2.87)           
002471.SZ    10.09      0.80       turtle_breakout (10.09 > 9.66)          
002482.SZ    1.85       0.80       turtle_breakout (1.85 > 1.78)           
002485.SZ    4.81       0.80       turtle_breakout (4.81 > 4.63)           
002595.SZ    93.33      0.80       turtle_breakout (93.33 > 92.5)          
002624.SZ    22.52      0.80       turtle_breakout (22.52 > 19.3)          
002634.SZ    6.99       0.80       turtle_breakout (6.99 > 6.33)           
002655.SZ    14.08      0.80       turtle_breakout (14.08 > 13.65)         
002687.SZ    5.28       0.80       turtle_breakout (5.28 > 5.22)           
002723.SZ    10.56      0.80       turtle_breakout (10.56 > 9.25)          
002739.SZ    13.50      0.80       turtle_breakout (13.5 > 12.3)           
002793.SZ    5.23       0.80       turtle_breakout (5.23 > 5.14)           
002800.SZ    19.71      0.80       turtle_breakout (19.71 > 18.68)         
002812.SZ    57.51      0.80       turtle_breakout (57.51 > 57.18)         
002872.SZ    5.16       0.80       turtle_breakout (5.16 > 5.14)           
003015.SZ    16.83      0.80       turtle_breakout (16.83 > 16.63)         
003018.SZ    21.29      0.80       turtle_breakout (21.29 > 18.7)          
300013.SZ    4.15       0.80       turtle_breakout (4.15 > 4.14)           
300055.SZ    8.44       0.80       turtle_breakout (8.44 > 8.4)            
300067.SZ    5.80       0.80       turtle_breakout (5.8 > 5.67)            
300165.SZ    5.46       0.80       turtle_breakout (5.46 > 5.37)           
300166.SZ    12.86      0.80       turtle_breakout (12.86 > 12.79)         
300264.SZ    8.86       0.80       turtle_breakout (8.86 > 7.88)           
300345.SZ    8.02       0.80       turtle_breakout (8.02 > 8.0)            
300347.SZ    66.91      0.80       turtle_breakout (66.91 > 66.47)         
300384.SZ    19.12      0.80       turtle_breakout (19.12 > 18.12)         
300408.SZ    56.97      0.80       turtle_breakout (56.97 > 54.07)         
300417.SZ    14.44      0.80       turtle_breakout (14.44 > 14.4)          
300426.SZ    10.36      0.80       turtle_breakout (10.36 > 9.83)          
300449.SZ    8.04       0.80       turtle_breakout (8.04 > 8.0)            
300464.SZ    7.10       0.80       turtle_breakout (7.1 > 7.01)            
300511.SZ    6.88       0.80       turtle_breakout (6.88 > 6.76)           
300512.SZ    10.57      0.80       turtle_breakout (10.57 > 10.48)         
300527.SZ    8.62       0.80       turtle_breakout (8.62 > 8.52)           
300571.SZ    32.36      0.80       turtle_breakout (32.36 > 31.87)         
300603.SZ    10.96      0.80       turtle_breakout (10.96 > 10.92)         
300736.SZ    20.11      0.80       turtle_breakout (20.11 > 19.08)         
300770.SZ    49.71      0.80       turtle_breakout (49.71 > 49.03)         
300814.SZ    82.09      0.80       turtle_breakout (82.09 > 80.3)          
300839.SZ    14.72      0.80       turtle_breakout (14.72 > 12.8)          
300915.SZ    26.20      0.80       turtle_breakout (26.2 > 26.1)           
300931.SZ    12.71      0.80       turtle_breakout (12.71 > 11.66)         
300960.SZ    27.82      0.80       turtle_breakout (27.82 > 26.47)         
300982.SZ    24.21      0.80       turtle_breakout (24.21 > 24.2)          
300991.SZ    48.67      0.80       turtle_breakout (48.67 > 46.46)         
301004.SZ    59.65      0.80       turtle_breakout (59.65 > 54.37)         
301018.SZ    77.27      0.80       turtle_breakout (77.27 > 75.18)         
301025.SZ    13.90      0.80       turtle_breakout (13.9 > 11.58)          
301029.SZ    29.59      0.80       turtle_breakout (29.59 > 29.35)         
301030.SZ    15.75      0.80       turtle_breakout (15.75 > 14.99)         
301122.SZ    32.04      0.80       turtle_breakout (32.04 > 31.49)         
301228.SZ    44.35      0.80       turtle_breakout (44.35 > 43.64)         
301268.SZ    24.72      0.80       turtle_breakout (24.72 > 21.71)         
301322.SZ    30.68      0.80       turtle_breakout (30.68 > 30.0)          
600016.SH    3.97       0.80       turtle_breakout (3.97 > 3.89)           
600031.SH    23.77      0.80       turtle_breakout (23.77 > 23.42)         
600185.SH    7.99       0.80       turtle_breakout (7.99 > 7.45)           
600207.SH    5.61       0.80       turtle_breakout (5.61 > 5.22)           
600229.SH    7.17       0.80       turtle_breakout (7.17 > 7.12)           
600234.SH    15.20      0.80       turtle_breakout (15.2 > 14.88)          
600236.SH    8.58       0.80       turtle_breakout (8.58 > 8.54)           
600241.SH    8.77       0.80       turtle_breakout (8.77 > 8.74)           
600303.SH    3.47       0.80       turtle_breakout (3.47 > 3.38)           
600345.SH    44.00      0.80       turtle_breakout (44.0 > 43.88)          
600448.SH    3.46       0.80       turtle_breakout (3.46 > 3.44)           
600533.SH    2.76       0.80       turtle_breakout (2.76 > 2.73)           
600668.SH    11.85      0.80       turtle_breakout (11.85 > 11.81)         
600715.SH    2.61       0.80       turtle_breakout (2.61 > 2.38)           
600751.SH    4.36       0.80       turtle_breakout (4.36 > 4.32)           
600757.SH    9.49       0.80       turtle_breakout (9.49 > 9.46)           
600875.SH    31.21      0.80       turtle_breakout (31.21 > 28.13)         
600884.SH    15.19      0.80       turtle_breakout (15.19 > 14.69)         
601009.SH    11.35      0.80       turtle_breakout (11.35 > 11.25)         
601198.SH    14.33      0.80       turtle_breakout (14.33 > 14.21)         
601225.SH    23.12      0.80       turtle_breakout (23.12 > 23.05)         
601515.SH    4.68       0.80       turtle_breakout (4.68 > 4.55)           
601528.SH    5.58       0.80       turtle_breakout (5.58 > 5.55)           
601595.SH    35.78      0.80       turtle_breakout (35.78 > 33.46)         
601838.SH    16.53      0.80       turtle_breakout (16.53 > 16.41)         
601996.SH    2.53       0.80       turtle_breakout (2.53 > 2.49)           
603048.SH    23.07      0.80       turtle_breakout (23.07 > 22.9)          
603076.SH    25.50      0.80       turtle_breakout (25.5 > 25.16)          
603182.SH    16.42      0.80       turtle_breakout (16.42 > 16.4)          
603301.SH    88.76      0.80       turtle_breakout (88.76 > 72.5)          
603323.SH    5.17       0.80       turtle_breakout (5.17 > 5.11)           
603335.SH    6.41       0.80       turtle_breakout (6.41 > 6.25)           
603687.SH    11.70      0.80       turtle_breakout (11.7 > 11.64)          
603707.SH    9.98       0.80       turtle_breakout (9.98 > 9.86)           
603726.SH    27.60      0.80       turtle_breakout (27.6 > 27.3)           
603912.SH    9.29       0.80       turtle_breakout (9.29 > 9.12)           
603950.SH    34.94      0.80       turtle_breakout (34.94 > 33.89)         
603959.SH    7.07       0.80       turtle_breakout (7.07 > 6.29)           
605318.SH    69.54      0.80       turtle_breakout (69.54 > 68.79)         
605500.SH    9.30       0.80       turtle_breakout (9.3 > 9.16)            
688090.SH    55.94      0.80       turtle_breakout (55.94 > 52.52)         
688275.SH    83.99      0.80       turtle_breakout (83.99 > 80.88)         
688428.SH    24.88      0.80       turtle_breakout (24.88 > 23.2)          
688450.SH    31.55      0.80       turtle_breakout (31.55 > 31.0)          
688466.SH    18.33      0.80       turtle_breakout (18.33 > 18.18)         
688529.SH    21.15      0.80       turtle_breakout (21.15 > 20.82)         
688613.SH    25.23      0.80       turtle_breakout (25.23 > 24.9)
    """
    
    tasks = []
    lines = list_str.strip().split('\n')
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            symbol = parts[0]
            price = parts[1]
            reason = " ".join(parts[2:]) # e.g. "0.80 turtle_breakout (25.23 > 24.9)"
            tasks.append({
                "symbol": symbol,
                "price": price,
                "reason": reason
            })
            
    # Run scan
    scan_with_ai(tasks)
