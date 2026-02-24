import os
import re
from typing import List, Dict, Optional
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MEMORY_PAIRS_LIMIT, PROVIDERS, AVAILABLE_MODELS
from prompts import DEFAULT_SYSTEM_PROMPT_NAME, SYSTEM_PROMPT_TEMPLATES

class LLMService:
    def __init__(self):
        # Default client (for backward compatibility or default model)
        self.default_api_key = LLM_API_KEY
        self.default_base_url = LLM_BASE_URL
        self.default_model = LLM_MODEL
        self.clients = {} # Cache clients by provider
        
    def _get_provider_for_model(self, model_id: str) -> str:
        """Find provider for a given model ID"""
        for m in AVAILABLE_MODELS:
            if m["id"] == model_id:
                return m.get("provider", "default")
        return "default"

    def _get_client(self, model_id: str) -> Optional[OpenAI]:
        """Get or create a client for the specific model/provider"""
        provider = self._get_provider_for_model(model_id)
        
        if provider in self.clients:
            return self.clients[provider]
            
        config = PROVIDERS.get(provider)
        if not config:
            # Fallback to default if provider not found (shouldn't happen if config is correct)
            config = PROVIDERS.get("default")
            
        api_key = config.get("api_key")
        base_url = config.get("base_url")

        if not api_key:
            return None
            
        print(f"[LLM] Creating client for provider: {provider} (URL: {base_url})")
        client = OpenAI(api_key=api_key, base_url=base_url)
        self.clients[provider] = client
        return client

    def is_configured(self) -> bool:
        # Check if at least default or current model's provider is configured
        # Simple check: do we have ANY api key?
        return bool(self.default_api_key) or any(p.get("api_key") for p in PROVIDERS.values())

    def _format_kline_data(self, klines: List[Dict], max_rows: Optional[int] = 100) -> str:
        # 简化 K 线数据
        if max_rows is None or max_rows <= 0:
            recent_klines = klines
        else:
            recent_klines = klines[-max_rows:]
        
        data_str = "Date | Open | High | Low | Close | Volume\n"
        data_str += "--- | --- | --- | --- | --- | ---\n"
        
        for k in recent_klines:
            date = k.get("date", "")
            if not date and k.get('closeTime'):
                import datetime
                date = datetime.datetime.fromtimestamp(k['closeTime'] / 1000).strftime('%Y-%m-%d')
                
            data_str += f"{date} | {k['open']} | {k['high']} | {k['low']} | {k['close']} | {k['volume']}\n"
        return data_str

    def get_default_system_prompt_template(self) -> str:
        try:
            from db import get_prompt_template_by_name
            template = get_prompt_template_by_name(DEFAULT_SYSTEM_PROMPT_NAME)
            if template and template.get("prompt"):
                return template.get("prompt")
        except Exception:
            pass
        if SYSTEM_PROMPT_TEMPLATES:
            return SYSTEM_PROMPT_TEMPLATES[0][1]
        return ""

    def build_system_prompt(self, symbol: str, custom_prompt: Optional[str] = None) -> str:
        if custom_prompt:
            text = custom_prompt.strip()
            if "{symbol}" in text:
                return text.replace("{symbol}", symbol)
            if symbol and symbol in text:
                return text
            return f"{text}\n\n当前分析的目标股票是: {symbol}。"
        return self.get_default_system_prompt_template().replace("{symbol}", symbol)

    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text.lower())

    def _score_message(self, query_tokens: List[str], msg_text: str) -> int:
        if not query_tokens or not msg_text:
            return 0
        msg_tokens = set(self._tokenize(msg_text))
        score = 0
        for tok in query_tokens:
            if tok in msg_tokens:
                score += 1
        return score

    def _select_relevant_messages(self, query: str, messages: List[Dict], top_k: int = 4) -> List[Dict]:
        if not query or not messages:
            return []
        query_tokens = self._tokenize(query)
        scored = []
        for msg in messages:
            score = self._score_message(query_tokens, msg.get("content", ""))
            if score > 0:
                scored.append((score, msg))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def _summarize_memory(self, client: OpenAI, model_id: str, symbol: str, existing_summary: str, new_messages: List[Dict], include_assistant: bool = False) -> Optional[str]:
        if not client or not new_messages:
            return existing_summary
        new_lines = []
        for msg in new_messages:
            role = (msg.get("role") or "user").lower()
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and not include_assistant:
                continue
            content = msg.get("content") or ""
            if not content:
                continue
            content = re.sub(r"\s+", " ", content).strip()
            new_lines.append(f"{role}: {content}")
        if not new_lines:
            return existing_summary
        system_prompt = "你是交易助理的记忆整理器。请基于用户问题与AI结论摘要提炼记忆要点。"
        include_hint = (
            "必须包含一行以“用户:”开头的要点，以及一行以“AI:”开头的要点。\n"
            if include_assistant
            else "必须包含一行以“用户:”开头的要点。\n"
        )
        user_prompt = (
            f"股票: {symbol}\n"
            f"已有记忆:\n{existing_summary or '(无)'}\n\n"
            "新增对话内容:\n" + "\n".join(new_lines) + "\n\n"
            "请输出更新后的记忆摘要，要求：\n"
            "- 只输出要点（不超过 8 条）\n"
            "- 包含用户问题与AI结论的摘要要点\n"
            "- 包含偏好、风险、仓位、关注标的、关键结论\n"
            "- 不要输出解释或多余文字\n"
            + include_hint
        )
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2
            )
            content = resp.choices[0].message.content if resp.choices else ""
            return content.strip() if content else existing_summary
        except Exception:
            return existing_summary

    def _fallback_memory_summary(self, existing_summary: str, new_messages: List[Dict]) -> str:
        lines: List[str] = []
        if existing_summary:
            lines.append(existing_summary.strip())
        for msg in new_messages:
            role = (msg.get("role") or "user").lower()
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            content = re.sub(r"\s+", " ", content)
            if len(content) > 200:
                content = content[:200] + "..."
            prefix = "用户" if role == "user" else "AI"
            lines.append(f"{prefix}: {content}")
        if len(lines) > 8:
            lines = lines[-8:]
        return "\n".join([ln for ln in lines if ln]).strip()

    def _extract_ai_summary(self, content: str) -> str:
        if not content:
            return ""
        text = content.strip()
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        keywords = ("总结", "结论", "最终建议", "操作建议", "建议", "综上", "简短总结")
        start_idx = None
        for idx, ln in enumerate(lines):
            for kw in keywords:
                if kw in ln:
                    start_idx = idx
                    break
            if start_idx is not None:
                break
        if start_idx is not None:
            summary_lines = lines[start_idx:]
            return "\n".join(summary_lines[:20])
        # fallback: use tail
        tail = lines[-12:]
        return "\n".join(tail)

    def _summary_to_messages(self, summary: str, max_chars: int = 1200) -> List[Dict]:
        if not summary:
            return []
        lines = [ln.strip() for ln in summary.splitlines() if ln and ln.strip()]
        if not lines:
            return []
        messages: List[Dict] = []
        current_role: Optional[str] = None
        buffer: List[str] = []

        def _flush():
            nonlocal buffer, current_role
            if current_role and buffer:
                content = " ".join(buffer).strip()
                if max_chars and len(content) > max_chars:
                    content = content[-max_chars:]
                if content:
                    messages.append({"role": current_role, "content": content})
            buffer = []

        def _strip_prefix(text: str) -> str:
            return re.sub(r"^[\-\*\d\.\)\s]+", "", text).strip()

        for raw in lines:
            clean = _strip_prefix(raw)
            lowered = clean.lower()
            role = None
            payload = None
            if clean.startswith("用户:") or clean.startswith("用户："):
                role = "user"
            elif lowered.startswith("ai:") or lowered.startswith("assistant:") or clean.startswith("助手:") or lowered.startswith("ai：") or lowered.startswith("assistant：") or clean.startswith("助手："):
                role = "assistant"
            if role:
                parts = re.split(r"[:：]", clean, 1)
                payload = parts[1].strip() if len(parts) > 1 else ""
                _flush()
                current_role = role
                if payload:
                    buffer.append(payload)
                continue
            if current_role:
                buffer.append(clean)
        _flush()
        return messages

    def _build_memory_pairs(self, history: List, max_pairs: int = 5, max_user_chars: int = 600, max_ai_chars: int = 800) -> List[Dict]:
        if not history:
            return []
        pairs = []
        pending_user = None
        for msg in history:
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", "") or ""
            content = content.strip()
            if role == "user" and content:
                pending_user = content
                continue
            if role == "assistant" and pending_user and content:
                ai_summary = self._extract_ai_summary(content) or content
                pairs.append((pending_user, ai_summary))
                pending_user = None
        if not pairs:
            return []
        pairs = pairs[-max_pairs:]
        messages: List[Dict] = []
        for user_text, ai_text in pairs:
            user_text = re.sub(r"\s+", " ", user_text).strip()
            ai_text = re.sub(r"\s+", " ", ai_text).strip()
            if max_user_chars and len(user_text) > max_user_chars:
                user_text = user_text[-max_user_chars:]
            if max_ai_chars and len(ai_text) > max_ai_chars:
                ai_text = ai_text[-max_ai_chars:]
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if ai_text:
                messages.append({"role": "assistant", "content": ai_text})
        return messages

    def analyze_stock(
        self,
        symbol: str,
        klines: List[Dict],
        model: str = None,
        user_input: str = None,
        mode: str = "assistant",
        context_config: Optional[Dict] = None,
        transient_context: Optional[str] = None
    ) -> object:
        """调用 LLM 分析股票 (支持对话模式)"""
        target_model = model or self.default_model
        client = self._get_client(target_model)

        debug_ctx = os.getenv("LLM_DEBUG_CONTEXT", "").lower() in ("1", "true", "yes", "on")
        debug_verbose = os.getenv("LLM_DEBUG_CONTEXT_VERBOSE", "").lower() in ("1", "true", "yes", "on")
        debug_full = os.getenv("LLM_DEBUG_CONTEXT_FULL", "").lower() in ("1", "true", "yes", "on")
        
        if not client:
            yield f"Error: LLM API Key not configured for model {target_model}. Please check your .env configuration."
            return

        import db # Import here to avoid circular dependency if any
        
        # 1. 准备上下文数据
        cfg = context_config or {}
        def _to_int(key: str, default: int, min_val: int, max_val: int) -> int:
            val = cfg.get(key, default)
            try:
                v = int(val)
            except Exception:
                return default
            if v < min_val:
                return min_val
            if v > max_val:
                return max_val
            return v

        def _to_bool(key: str, default: bool) -> bool:
            val = cfg.get(key)
            if val is None:
                return default
            if isinstance(val, str):
                return val.strip().lower() in ("1", "true", "yes", "on")
            return bool(val)

        HISTORY_LIMIT = _to_int("history_limit", 60, 0, 200)
        RECENT_LIMIT = _to_int("recent_limit", 8, 0, 50)
        SUMMARY_MIN = _to_int("summary_min", 10, 0, 200)
        SUMMARY_STEP = _to_int("summary_step", 6, 1, 50)
        MAX_MESSAGE_CHARS = _to_int("max_message_chars", 1200, 200, 4000)
        MEMORY_PAIRS_LIMIT = _to_int("memory_pairs_limit", LLM_MEMORY_PAIRS_LIMIT, 1, 50)
        RELEVANT_TOP_K = _to_int("relevant_top_k", 4, 0, 10)
        ENABLE_MEMORY = _to_bool("enable_memory", True)
        MEMORY_INCLUDE_ASSISTANT = _to_bool("memory_include_assistant", False)
        ENABLE_RETRIEVAL = _to_bool("enable_retrieval", True)
        RETRIEVAL_INCLUDE_ASSISTANT = _to_bool("retrieval_include_assistant", False)
        HISTORY_INCLUDE_ASSISTANT = _to_bool("history_include_assistant", False)
        DISABLE_HISTORY = _to_bool("disable_history", False)
        DISABLE_INDICATOR_CONTEXT = _to_bool("disable_indicator_context", False)
        SAVE_HISTORY = _to_bool("save_history", True)
        KLINE_ROWS_CHAT = _to_int("kline_rows_chat", 100, 0, 365)
        KLINE_ROWS_ASSISTANT = _to_int("kline_rows_assistant", 365, 0, 365)

        active_prompt = db.get_active_system_prompt(symbol) if symbol else None
        system_prompt = self.build_system_prompt(symbol, active_prompt.get("prompt") if active_prompt else None)
        short_query = False
        if user_input:
            try:
                short_query = len(user_input.strip()) <= 6
            except Exception:
                short_query = False

        effective_summary_min = SUMMARY_MIN
        effective_summary_step = SUMMARY_STEP
        if MEMORY_INCLUDE_ASSISTANT:
            effective_summary_min = min(SUMMARY_MIN, 2)
            effective_summary_step = 1

        raw_history = []
        if not DISABLE_HISTORY:
            raw_history = db.get_history(symbol, HISTORY_LIMIT)

        if DISABLE_HISTORY or (short_query and not MEMORY_INCLUDE_ASSISTANT):
            ENABLE_MEMORY = False
            ENABLE_RETRIEVAL = False
            HISTORY_LIMIT = 0
            RECENT_LIMIT = 0
            history = []
            summary = ""
            last_summary_id = 0
        else:
            history = list(raw_history)
            memory = db.get_memory(symbol) if symbol else None
            summary = memory.get("summary") if memory else ""
            last_summary_id = memory.get("last_message_id") if memory else 0
            if memory is not None and last_summary_id:
                history = [msg for msg in history if msg.id > last_summary_id]

        if ENABLE_MEMORY and history and len(history) >= effective_summary_min:
            last_id = history[-1].id
            if not summary or (last_summary_id and last_id - last_summary_id >= effective_summary_step) or (not last_summary_id and last_id >= effective_summary_step):
                new_msgs = []
                for msg in history:
                    if last_summary_id and msg.id <= last_summary_id:
                        continue
                    if msg.role == "assistant" and not MEMORY_INCLUDE_ASSISTANT:
                        continue
                    if msg.role not in ("user", "assistant"):
                        continue
                    new_msgs.append({
                        "role": msg.role,
                        "content": msg.content
                    })
                if len(new_msgs) > 20:
                    new_msgs = new_msgs[-20:]
                updated = self._summarize_memory(client, target_model, symbol, summary or "", new_msgs, include_assistant=MEMORY_INCLUDE_ASSISTANT)
                if updated:
                    summary = updated
                    if SAVE_HISTORY:
                        db.save_memory(symbol, summary, last_id)
                    if debug_ctx:
                        print(f"[LLM][Memory] summary_updated=history len={len(summary)} last_id={last_id}")

        context_history = history
        if history and not HISTORY_INCLUDE_ASSISTANT:
            context_history = [msg for msg in history if msg.role == "user"]

        recent_msgs = context_history[-RECENT_LIMIT:] if context_history and RECENT_LIMIT > 0 else []
        older_msgs = context_history[:-RECENT_LIMIT] if context_history and RECENT_LIMIT > 0 else context_history

        def _trim(content: str) -> str:
            if not content:
                return ""
            text = content.strip()
            if len(text) > MAX_MESSAGE_CHARS:
                return text[-MAX_MESSAGE_CHARS:]
            return text

        relevant_msgs = []
        if ENABLE_RETRIEVAL and user_input and history:
            retrieval_history = history if RETRIEVAL_INCLUDE_ASSISTANT else [m for m in history if m.role == "user"]
            if RECENT_LIMIT > 0:
                retrieval_older = retrieval_history[:-RECENT_LIMIT]
            else:
                retrieval_older = retrieval_history
            older_dicts = [{"role": m.role, "content": m.content} for m in retrieval_older]
            relevant_msgs = self._select_relevant_messages(user_input, older_dicts, top_k=RELEVANT_TOP_K)

        messages = [{"role": "system", "content": system_prompt}]
        memory_messages: List[Dict] = []
        if ENABLE_MEMORY:
            if MEMORY_INCLUDE_ASSISTANT:
                memory_messages = self._build_memory_pairs(raw_history, max_pairs=MEMORY_PAIRS_LIMIT, max_user_chars=MAX_MESSAGE_CHARS, max_ai_chars=MAX_MESSAGE_CHARS)
            elif summary:
                memory_messages = self._summary_to_messages(summary, MAX_MESSAGE_CHARS)
                if not memory_messages:
                    memory_messages = [{"role": "assistant", "content": f"对话记忆（仅供参考，不要逐字复述）:\n{summary}"}]
        suppress_recent = MEMORY_INCLUDE_ASSISTANT and bool(memory_messages)
        if memory_messages:
            messages.extend(memory_messages)
        if relevant_msgs and ENABLE_RETRIEVAL and not suppress_recent:
            for m in relevant_msgs:
                role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
                content = _trim(m.get("content") or "")
                if content:
                    messages.append({"role": role, "content": content})
        if not suppress_recent:
            for msg in recent_msgs:
                role = msg.role if msg.role in ("user", "assistant") else "user"
                content = _trim(msg.content)
                if content:
                    messages.append({"role": role, "content": content})

        kline_limit = KLINE_ROWS_ASSISTANT if mode == "assistant" else KLINE_ROWS_CHAT
        data_context = self._format_kline_data(klines, max_rows=kline_limit) if klines else ""
        context_block = f"以下是 {symbol} 最近的交易数据:\n{data_context}\n\n" if data_context else ""
        extra_parts: List[str] = []
        if transient_context:
            extra_parts.append(transient_context.strip())
        if not DISABLE_INDICATOR_CONTEXT:
            try:
                from industry import build_indicator_context
                indicator_ctx = build_indicator_context(symbol) if symbol else ""
                if indicator_ctx:
                    extra_parts.append(indicator_ctx.strip())
            except Exception:
                pass
        extra_context = "\n\n".join([p for p in extra_parts if p])
        extra_block = f"{extra_context}\n\n" if extra_context else ""

        if user_input:
            full_prompt = (
                f"用户问题: \"{user_input}\"\n"
                "请结合对话上下文与数据进行回答。\n\n"
                f"{extra_block}{context_block}"
            )
            user_msg_content = user_input
            if MEMORY_INCLUDE_ASSISTANT:
                full_prompt = (
                    f"用户问题: \"{user_input}\"\n"
                    "请先给出完整分析，最后必须包含“总结/结论/最终建议”小节。\n\n"
                    f"{extra_block}{context_block}"
                )
        else:
            full_prompt = (
                f"{context_block}{extra_block}"
                "请对该股票进行全面的技术面分析，包括：\n"
                "1. 当前趋势判断\n"
                "2. 关键支撑与阻力位\n"
                "3. 量价分析\n"
                "4. 短期与中长期操作建议\n"
            )
            user_msg_content = "请分析该股票 (Default Analysis)"

        # 3. Save User Message to DB (optional)
        if SAVE_HISTORY:
            db.add_message(symbol, "user", user_msg_content, target_model)

        try:
            messages.append({"role": "user", "content": full_prompt})
            if debug_ctx:
                print(
                    "[LLM][Context] "
                    f"symbol={symbol} mode={mode} model={target_model} "
                    f"history={len(history)} recent={len(recent_msgs)} "
                    f"summary={'on' if ENABLE_MEMORY else 'off'} "
                    f"retrieval={'on' if ENABLE_RETRIEVAL else 'off'} "
                    f"relevant={len(relevant_msgs)} "
                    f"kline_rows={kline_limit}"
                )
                print(
                    "[LLM][Context] "
                    f"memory_ai={'on' if MEMORY_INCLUDE_ASSISTANT else 'off'} "
                    f"summary_min={effective_summary_min} summary_step={effective_summary_step} "
                    f"memory_pairs_limit={MEMORY_PAIRS_LIMIT}"
                )
                if memory_messages:
                    print(f"[LLM][Context] memory_pairs={len(memory_messages)}")
                if active_prompt:
                    print(
                        "[LLM][Context] "
                        f"prompt_id={active_prompt.get('id')} "
                        f"prompt_name={active_prompt.get('name')}"
                    )
                if summary and ENABLE_MEMORY:
                    print(f"[LLM][Context] summary_len={len(summary)}")
            if debug_verbose:
                print("[LLM][Context][Messages]")
                for idx, msg in enumerate(messages):
                    content = (msg.get("content") or "")
                    if not debug_full:
                        content = content.replace("\n", "\\n")
                        if len(content) > 400:
                            content = content[:400] + "...(truncated)"
                    print(f"  {idx+1}. {msg.get('role')}: {content}")
            response = None
            full_response = ""
            try:
                response = client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    stream=True
                )
                
                # 4. Stream Response & Accumulate for History
                for chunk in response:
                    if chunk.choices and len(chunk.choices) > 0:
                        content = chunk.choices[0].delta.content
                        if content:
                            full_response += content
                            yield content
                
                # 5. Save Assistant Response to DB
                if full_response and SAVE_HISTORY:
                    db.add_message(symbol, "assistant", full_response, target_model)
                    if ENABLE_MEMORY and MEMORY_INCLUDE_ASSISTANT:
                        try:
                            memory = db.get_memory(symbol) if symbol else None
                            existing_summary = memory.get("summary") if memory else ""
                            ai_summary = self._extract_ai_summary(full_response)
                            if ai_summary:
                                new_msgs = []
                                if user_msg_content:
                                    new_msgs.append({"role": "user", "content": user_msg_content})
                                new_msgs.append({"role": "assistant", "content": ai_summary})
                                updated = self._summarize_memory(
                                    client,
                                    target_model,
                                    symbol,
                                    existing_summary or "",
                                    new_msgs,
                                    include_assistant=True
                                )
                                if not updated:
                                    updated = self._fallback_memory_summary(existing_summary or "", new_msgs)
                                if updated:
                                    last_id = db.get_last_history_id(symbol) if symbol else 0
                                    db.save_memory(symbol, updated, last_id)
                                    if debug_ctx:
                                        print(f"[LLM][Memory] summary_updated=assistant len={len(updated)} last_id={last_id}")
                        except Exception:
                            pass
            finally:
                try:
                    if response is not None and hasattr(response, "close"):
                        response.close()
                except Exception:
                    pass
            
        except Exception as e:
            err_msg = f"Error analyzing stock: {str(e)}"
            if SAVE_HISTORY:
                db.add_message(symbol, "assistant", err_msg, target_model) # Log errors too?
            yield err_msg

    def extract_action_plan(self, symbol: str, text: str, model: str = None) -> dict:
        """从文本中提取行动计划 (Structured Output)"""
        target_model = model or self.default_model
        client = self._get_client(target_model)
        
        if not client:
             return {
                "symbol": symbol,
                "action": "Watch",
                "time_range": "Unknown",
                "description": "LLM not configured",
                "reasoning": f"Please configure LLM API key for {target_model}.",
                "stock_name": symbol
            }

        prompt = f"""
你是一个专业的股票交易助手。请根据下面的分析文本，提取出一个结构化的交易行动计划。
分析文本:
"{text}"

请提取以下信息并以 JSON 格式返回:
1. `action`: 具体操作建议 (Buy, Sell, 或 Watch)。
2. `time_range`: 建议的操作时间范围 (例如: "短期 (1-3天)", "中长期", "立刻")。
3. `description`: 行动描述 (2-3句话，具体明确)。
4. `reasoning`: 支持该行动的详细理由 (包括技术面和基本面因素)。
5. `stock_name`: 股票名称 (如果文本中有提到，否则使用 "{symbol}")。

如果文本中没有明确建议，默认为 "Watch"。
返回格式示例:
{{
    "action": "Buy",
    "time_range": "Next week",
    "description": "Buy at support level",
    "reasoning": "Strong support at 20 day MA",
    "stock_name": "Moutai"
}}
只返回 JSON 字符串，不要包含 Markdown 格式。
"""
        try:
            response = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts structured data from text."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1 # Low temperature for consistent formatting
            )
            
            content = response.choices[0].message.content
            # Clean up potential markdown code blocks
            content = content.replace("```json", "").replace("```", "").strip()
            
            import json
            data = json.loads(content)
            data["symbol"] = symbol # Ensure symbol is correct
            data["original_response"] = text
            data["model"] = target_model # Add model info
            if "stock_name" not in data:
                data["stock_name"] = symbol
            return data

        except Exception as e:
            print(f"Extraction error: {e}")
            return {
                "symbol": symbol,
                "action": "Watch",
                "time_range": "Unknown",
                "description": "Failed to extract plan",
                "reasoning": str(e),
                "stock_name": symbol,
                "original_response": text
            }

# 单例实例
llm_service = LLMService()
