"""
后端配置模块
管理环境变量和应用配置
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
start_path = Path(__file__)
env_path = start_path.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# LLM 配置 (Default / Fallback)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL")
# Provider Specific Configs
PROVIDERS = {
    "default": {
        "api_key": LLM_API_KEY,
        "base_url": LLM_BASE_URL,
    },
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY", LLM_API_KEY),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    },
    "gemini": {
        "api_key": os.getenv("GEMINI_API_KEY", LLM_API_KEY),
        "base_url": os.getenv("GEMINI_BASE_URL", "http://127.0.0.1:8045/v1") # "https://generativelanguage.googleapis.com/v1beta/openai/"),
    },
    "claude": {
        "api_key": os.getenv("CLAUDE_API_KEY", LLM_API_KEY),
        "base_url": os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com/v1"), # Note: anthropic needs different client usually, but if proxied via openai compatible...
    }
}

# 预设模型列表
AVAILABLE_MODELS = [
    {"id": "deepseek-chat", "name": "DeepSeek V3", "provider": "deepseek"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (推理版)", "provider": "deepseek"},
    
    # {"id": "gemini-2.0-flash-exp", "name": "Gemini 2.0 Flash", "provider": "gemini"},
    # {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "provider": "gemini"},
    {"id": "gemini-3-pro-high", "name": "Gemini 3.0 Pro (高配)", "provider": "gemini"},
    {"id": "gemini-3-flash", "name": "Gemini 3.0 Flash", "provider": "gemini"},

    {"id": "claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet", "provider": "claude"},
]


def get_llm_config():
    return {
        "api_key": LLM_API_KEY, # Backwards compatibility
        "base_url": LLM_BASE_URL,
        "model": LLM_MODEL,
        "available_models": AVAILABLE_MODELS,
        "providers": {k: {"base_url": v["base_url"], "configured": bool(v["api_key"])} for k, v in PROVIDERS.items()}
    }
