import sys
import os
import argparse
from datetime import datetime

# Add api directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir)) # .../api
if parent_dir not in sys.path:
    sys.path.append(os.path.join(parent_dir, 'api'))

try:
    from quant.scan_opportunities import scan_opportunities
    from quant.scan_with_ai import scan_with_ai
    from quant.log_utils import setup_logging
except ImportError:
    # If run from api root
    sys.path.append(os.getcwd())
    from quant.scan_opportunities import scan_opportunities
    from quant.scan_with_ai import scan_with_ai
    from quant.log_utils import setup_logging

# Database imports
try:
    from db import create_screening_run, add_screening_result, update_screening_run
except ImportError:
    # Fallback if path issues
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from db import create_screening_run, add_screening_result, update_screening_run

def run_daily_scan_workflow(strategy_name="turtle", source="all", limit=None, prompt_type="db_default"):
    setup_logging("run_daily_scan")
    
    print("=" * 100)
    print(f"STARTING DAILY SCAN WORKFLOW")
    print(f"Strategy: {strategy_name}")
    print(f"Source:   {source}")
    print(f"AI Prompt: {prompt_type}")
    print("=" * 100)
    
    # Create Run in DB
    run_id = 0
    try:
        # We don't know total yet, estimate or update later
        run_id = create_screening_run(
            model_id=prompt_type,
            universe=source,
            params={"strategy": strategy_name},
            total=0 # Update later
        )
        print(f"[DB] Created Screening Run ID: {run_id}")
    except Exception as e:
        print(f"[DB Error] Failed to create run: {e}")

    # Step 1: Scan for Technical Signals
    print("\n>>> STEP 1: Scanning for Technical BUY Signals...")
    # This calls the refactored function which returns a list of results
    tech_results = scan_opportunities(
        strategy_name=strategy_name, 
        lookback_days=365,
        source=source,
        limit=limit
    )
    
    # Update total count
    if run_id:
        # Here 'total' in screening_run usually means how many stocks were AI scanned? 
        # Or how many in universe? 
        # Let's say it's how many candidates passed to AI.
        pass
    
    if not tech_results:
        print("\n[Stop] No technical BUY signals found. Workflow finished.")
        if run_id:
            update_screening_run(run_id, status="completed", processed=0, total=0, finished=True)
        return

    # Filter only meaningful results (already filtered for BUY in scan_opportunities, but double check)
    candidates = [r for r in tech_results if r.get('Symbol')]
    
    if run_id:
        update_screening_run(run_id, total=len(candidates))

    print(f"\n>>> Found {len(candidates)} candidates. Proceeding to AI Analysis...")
    
    # Step 2: AI Analysis
    print("\n>>> STEP 2: AI Screening with LLM...")
    
    # Convert keys to match scan_with_ai expectation (it handles Symbol/Reason/Price capitalizations)
    ai_results = scan_with_ai(candidates, prompt_type=prompt_type)
    
    # Step 3: Final Report & DB Save
    if ai_results:
        # Filter for AI BUY
        final_buys = [r for r in ai_results if r.get('AI_Decision') == 'BUY']
        
        # Save ALL results to DB (including WATCH and PASS)
        if run_id:
            print(f"\n[DB] Saving {len(ai_results)} results to database...")
            for res in ai_results:
                symbol = res.get('Symbol')
                decision = res.get('AI_Decision', 'UNKNOWN')
                reason = res.get('AI_Comment', '')
                # Score: map decision to score? 
                score = 0.0
                if decision == 'BUY': score = 90.0
                elif decision == 'WATCH': score = 60.0
                elif decision == 'PASS': score = 30.0
                
                # Raw json can store technical reason
                raw_data = {
                    "tech_reason": res.get('Reason'),
                    "ai_full_comment": res.get('AI_Comment')
                }
                
                try:
                    add_screening_result(
                        run_id=run_id,
                        symbol=symbol,
                        action=decision,
                        score=score,
                        reason=reason[:255], # Truncate for DB column if needed (usually text)
                        raw_json=raw_data,
                        model_id=prompt_type
                    )
                except Exception as e:
                    print(f"Error saving {symbol}: {e}")
            
            # Mark finished
            update_screening_run(run_id, status="completed", processed=len(ai_results), total=len(candidates), finished=True)
            print(f"[DB] Run {run_id} completed.")

        print("\n" + "="*100)
        
        print("\n" + "="*100)
        print(f"FINAL DAILY REPORT: {len(final_buys)} STOCK(S) SELECTED")
        print("="*100)
        
        if final_buys:
            print("TOP PICKS (Tech BUY + AI BUY):")
            for item in final_buys:
                print(f"★ {item['Symbol']} | {item['Reason']} | {item['AI_Comment']}")
        else:
            print("No stocks passed the AI strict filter (all were WATCH/PASS).")
            
        print("\n[Info] Detailed results saved to CSV in api/quant/")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Full Daily Scan Workflow")
    parser.add_argument("--strategy", type=str, default="turtle", help="Strategy to use (turtle, bollinger, etc)")
    parser.add_argument("--source", type=str, default="all", choices=["watchlist", "all"], help="Source of symbols")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols to scan (for testing)")
    parser.add_argument("--prompt", type=str, default="db_default", choices=["db_default", "custom"], help="Prompt type")
    
    args = parser.parse_args()
    
    run_daily_scan_workflow(args.strategy, args.source, args.limit, args.prompt)
