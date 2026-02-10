export LLM_DEBUG_CONTEXT_VERBOSE=1
export LLM_DEBUG_CONTEXT=1

uv run uvicorn main:app --reload --workers 5
