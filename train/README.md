# Training (walk-forward)

This folder contains offline training tools for strategy parameter optimization.

## Batch screening (no memory / no history)

Run LLM screening over a universe without touching chat memory:

```
python train/screen_symbols.py --universe all --limit 200
```

Common options:
- `--universe all|watchlist`
- `--model <model_id>`
- `--kline-limit 365`
- `--cache-only` (skip remote data)
- `--force-refresh`
- `--sleep 0.5` (throttle)

Output is saved to `train/screen_results_YYYYMMDD_HHMMSS.json`.

## Optimize parameters (daily walk-forward)

```
python train/optimize_params.py --strategy-id 2 --train-days 126 --mode rolling --max-days 30
```

Use `--apply` to write the best parameters back into the strategy record:

```
python train/optimize_params.py --strategy-id 2 --train-days 126 --mode expanding --apply
```

Notes:
- Uses cached daily data and will auto-sync missing data unless `--no-sync` is set.
- `--mode rolling` uses a fixed-size rolling window instead of expanding.
- Results are saved to `train/results_*.json`.
- Use `--progress-every 10` to see more frequent progress output.
- Default training window is 1 year unless `--start-date` is provided.
- If some symbols start later (new listings), training start_date will be advanced to the latest first_date.
- LLM prompt optimization is enabled by default; logs are saved as `train/logs/llm_summary_*.log` and `train/logs/llm_detail_*.jsonl`.
- Use `--no-llm` to disable LLM prompt optimization.
- LLM raw outputs are recorded in `llm_detail_*.jsonl` along with system/user prompts for each call.
- Use `--no-log-prompts` to skip logging prompts (keeps logs smaller).
- Use `--llm-progress-every 20` to see more frequent LLM progress output.
- Training resumes from the last checkpoint automatically if parameters are unchanged.
- Use `--reset` to discard checkpoints and start fresh.
- Use `--checkpoint-every 5` to control checkpoint frequency.

List strategies:

```
python train/optimize_params.py --list
```
