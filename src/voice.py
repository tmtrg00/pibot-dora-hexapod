"""
Shared voice interaction utilities for PiBot.
Extracted from pi_chat.py and src/main.py to eliminate duplication.
"""

import json
import logging
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple, Any

from src.actions import ACTION_EMOTIONS, execute as run_action

logger = logging.getLogger("voice")

DEFAULT_MEMORY_PROMPT = """You are extracting long-term memory for a robot assistant.
Summarize the user's message and the assistant response into ONE short, factual memory.
Also extract any stable user preferences or personal facts.

Rules:
- Do NOT include secrets, passwords, API keys, or sensitive data.
- Keep the summary under 200 characters.
- Preferences should be stable (name, language, response style, ongoing project).
- If nothing is worth saving, return an empty summary and empty preferences.

Return ONLY valid JSON with keys:
- summary (string)
- preferences (object)
- tags (array)
- should_store (boolean, optional)

User: {user_text}
Assistant: {assistant_text}
Tool results (if any): {tool_results}
"""

MIN_MEMORY_SUMMARY_CHARS = 16
DEFAULT_DEDUPE_LOOKBACK = 60
GENERIC_MEMORY_MARKERS = (
    "nothing to store",
    "no memory",
    "general conversation",
    "small talk",
    "casual greeting",
    "greeting exchange",
    "assistant greeted",
    "user greeted",
    "n/a",
    "none",
)
TOKEN_RE = re.compile(r"[a-z0-9']+")
PROACTIVE_ALLOWED_INSTRUCTION = (
    "Turn policy: You may include at most one short proactive follow-up question, "
    "only if it is clearly relevant to the user's remembered goals/preferences or "
    "the current topic. Avoid generic filler questions."
)
PROACTIVE_BLOCKED_INSTRUCTION = (
    "Turn policy: Do not add a proactive follow-up question in this turn. "
    "Answer or act on the user's request directly and concisely."
)
ACTION_REQUEST_WORDS = {
    "walk",
    "turn",
    "dance",
    "stand",
    "step",
    "move",
    "photo",
    "picture",
    "look",
    "camera",
    "distance",
    "ultrasonic",
    "battery",
    "balance",
    "led",
    "light",
    "buzzer",
    "head",
    "attitude",
    "position",
    "relax",
}
ACTION_REQUEST_PREFIXES = (
    "walk ",
    "turn ",
    "dance",
    "stand",
    "look",
    "move ",
    "go ",
    "relax",
    "check battery",
    "battery",
    "how far",
    "distance",
    "set lights",
    "set led",
    "light ",
    "enable balance",
    "disable balance",
    "take a photo",
    "take photo",
    "take picture",
    "take a picture",
    "image ",
    "what do you see",
)
ACTION_REQUEST_MARKERS = (
    "can you",
    "could you",
    "please",
)


def _parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _tokenize(value: Any) -> List[str]:
    return TOKEN_RE.findall(_normalize_text(value))


def _coerce_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(tag).strip() for tag in parsed if str(tag).strip()]
        return []
    return []


def _format_pref_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 120:
        return text[:119].rstrip() + "..."
    return text


def _summary_relevance_score(query_tokens: List[str], summary: str, tags: List[str]) -> float:
    if not query_tokens:
        return 0.0
    query_set = set(query_tokens)
    memory_set = set(_tokenize(summary))
    for tag in tags:
        memory_set.update(_tokenize(tag))
    if not memory_set:
        return 0.0
    overlap = query_set & memory_set
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(1, len(query_set))
    jaccard = len(overlap) / max(1, len(query_set | memory_set))
    return (0.7 * coverage) + (0.3 * jaccard)


def _is_direct_action_request(user_text: str) -> bool:
    normalized = _normalize_text(user_text)
    if not normalized:
        return True
    if any(normalized.startswith(prefix) for prefix in ACTION_REQUEST_PREFIXES):
        return True

    tokens = set(_tokenize(normalized))
    has_action_word = any(word in tokens for word in ACTION_REQUEST_WORDS)
    if not has_action_word:
        return False

    if normalized.startswith(("walk", "turn", "dance", "stand", "look", "move", "go", "take")):
        return True
    if any(marker in normalized for marker in ACTION_REQUEST_MARKERS):
        return True
    return False


class InitiativePolicy:
    """Lightweight policy for proactive follow-up frequency and timing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.cooldown_seconds = float(cfg.get("cooldown_seconds", 180))
        self.max_proactive_per_session = int(cfg.get("max_proactive_per_session", 6))
        self.min_user_words = int(cfg.get("min_user_words", 3))
        self.disable_during_interrupts = bool(cfg.get("disable_during_interrupts", True))
        self.require_memory_context = bool(cfg.get("require_memory_context", False))

        self.last_proactive_at = 0.0
        self.proactive_count = 0

    def prepare_turn(
        self,
        user_text: str,
        *,
        force_disable: bool = False,
        has_memory_context: bool = False,
    ) -> Tuple[bool, str]:
        now = time.time()
        allow_proactive = self._should_allow(
            user_text,
            now=now,
            force_disable=force_disable,
            has_memory_context=has_memory_context,
        )
        if allow_proactive:
            return True, PROACTIVE_ALLOWED_INSTRUCTION
        return False, PROACTIVE_BLOCKED_INSTRUCTION

    def register_assistant_turn(
        self,
        assistant_text: str,
        *,
        allowed: bool,
        had_tool_calls: bool = False,
    ) -> None:
        if not allowed or had_tool_calls:
            return
        if not assistant_text or "?" not in assistant_text:
            return
        self.last_proactive_at = time.time()
        self.proactive_count += 1

    def _should_allow(
        self,
        user_text: str,
        *,
        now: float,
        force_disable: bool,
        has_memory_context: bool,
    ) -> bool:
        if not self.enabled or force_disable:
            return False
        if self.require_memory_context and not has_memory_context:
            return False
        if self.max_proactive_per_session >= 0 and self.proactive_count >= self.max_proactive_per_session:
            return False
        if self.last_proactive_at > 0 and (now - self.last_proactive_at) < self.cooldown_seconds:
            return False

        word_count = len(_tokenize(user_text))
        if word_count < self.min_user_words:
            return False
        if _is_direct_action_request(user_text):
            return False
        return True


def _rank_memories(memories: List[Dict[str, Any]], query_text: str, max_items: int) -> List[Dict[str, Any]]:
    if not memories or max_items <= 0:
        return []

    query_tokens = _tokenize(query_text)
    total = len(memories)
    scored: List[Tuple[float, float, int, Dict[str, Any]]] = []

    for index, mem in enumerate(memories):
        summary = str(mem.get("summary", "")).strip()
        if not summary:
            continue
        tags = _coerce_tags(mem.get("tags"))
        relevance = _summary_relevance_score(query_tokens, summary, tags)
        recency = 1.0 if total <= 1 else 1.0 - (index / (total - 1))
        score = recency if not query_tokens else (relevance * 3.0) + (recency * 0.35)
        scored.append((score, relevance, -index, mem))

    if not scored:
        return []

    scored.sort(key=lambda item: (item[1] > 0.0, item[0], item[2]), reverse=True)

    selected: List[Dict[str, Any]] = []
    seen = set()
    for _score, _relevance, _idx, mem in scored:
        norm = _normalize_text(mem.get("summary", ""))
        if not norm or norm in seen:
            continue
        selected.append(mem)
        seen.add(norm)
        if len(selected) >= max_items:
            break
    return selected


def build_memory_context(
    memory_db,
    query_text: str = "",
    max_items: int = 5,
    max_chars: int = 1200,
) -> str:
    """Build compact memory context string from preferences + ranked memories."""
    try:
        prefs = memory_db.get_preferences()
        candidate_limit = max(max_items * 4, max_items, 20)
        memories = memory_db.get_recent_memories(limit=candidate_limit)
    except Exception as exc:
        logger.warning(f"Memory context unavailable: {exc}")
        return ""

    selected_memories = _rank_memories(memories, query_text=query_text, max_items=max_items)

    lines: List[str] = []
    if prefs:
        lines.append("Preferences:")
        for key, value in prefs.items():
            value_str = _format_pref_value(value)
            if value_str:
                lines.append(f"- {key}: {value_str}")
    if selected_memories:
        section_title = "Relevant memories:" if query_text.strip() else "Recent memories:"
        lines.append(section_title)
        for mem in selected_memories:
            summary = mem.get("summary", "")
            if summary:
                lines.append(f"- {summary}")

    context = "\n".join(lines).strip()
    if len(context) > max_chars:
        context = context[: max_chars - 1].rstrip() + "…"
    return context


def extract_memory_payload(
    llm,
    user_text: str,
    assistant_text: str,
    tool_results: Optional[Dict[str, str]] = None,
    prompt_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Use LLM to extract memory summary + preferences."""
    if not user_text:
        return {"summary": "", "preferences": {}, "tags": []}

    prompt = prompt_override or DEFAULT_MEMORY_PROMPT
    tool_blob = json.dumps(tool_results) if tool_results else ""
    filled = prompt.format(
        user_text=user_text,
        assistant_text=assistant_text or "",
        tool_results=tool_blob,
    )

    try:
        response = llm.query(filled)
    except Exception as exc:
        logger.warning(f"Memory extraction failed: {exc}")
        response = ""

    if isinstance(response, dict):
        response = response.get("text", "")

    data = _parse_json_from_text(str(response)) or {}
    summary = str(data.get("summary", "")).strip()
    if len(summary) > 200:
        summary = summary[:199].rstrip() + "…"

    preferences = data.get("preferences", {})
    if not isinstance(preferences, dict):
        preferences = {}

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(tag) for tag in tags][:8]

    should_store = data.get("should_store")
    if not isinstance(should_store, bool):
        should_store = None

    return {
        "summary": summary,
        "preferences": preferences,
        "tags": tags,
        "should_store": should_store,
    }


def _should_store_summary(
    summary: str,
    preferences: Dict[str, Any],
    tags: List[str],
    explicit_decision: Optional[bool],
) -> bool:
    if explicit_decision is False:
        return False
    normalized = _normalize_text(summary)
    if not normalized:
        return False
    if explicit_decision is True:
        return True
    if len(normalized) < MIN_MEMORY_SUMMARY_CHARS and not preferences:
        return False
    if summary.strip().endswith("?") and not preferences:
        return False
    for marker in GENERIC_MEMORY_MARKERS:
        if marker in normalized:
            return False
    if len(_tokenize(normalized)) < 3 and not preferences and not tags:
        return False
    return True


def _is_duplicate_summary(memory_db, summary: str, lookback: int = DEFAULT_DEDUPE_LOOKBACK) -> bool:
    norm_summary = _normalize_text(summary)
    if not norm_summary:
        return True
    try:
        recent = memory_db.get_recent_memories(limit=lookback)
    except Exception:
        return False

    for mem in recent:
        existing = _normalize_text(mem.get("summary", ""))
        if not existing:
            continue
        if norm_summary == existing:
            return True
        if norm_summary in existing or existing in norm_summary:
            return True
        if SequenceMatcher(None, norm_summary, existing).ratio() >= 0.92:
            return True
    return False


def store_memory_payload(
    memory_db,
    payload: Dict[str, Any],
    source: str = "voice",
) -> None:
    """Persist memory payload into SQLite with gating + deduplication."""
    try:
        summary = payload.get("summary", "").strip()
        preferences = payload.get("preferences", {})
        if not isinstance(preferences, dict):
            preferences = {}

        tags = _coerce_tags(payload.get("tags"))
        explicit_decision = payload.get("should_store")
        if not isinstance(explicit_decision, bool):
            explicit_decision = None

        if _should_store_summary(summary, preferences, tags, explicit_decision):
            if not _is_duplicate_summary(memory_db, summary):
                memory_db.add_memory(summary=summary, source=source, tags=tags)
            else:
                logger.debug("Skipping duplicate memory summary")

        if isinstance(preferences, dict):
            for key, value in list(preferences.items())[:10]:
                key_str = str(key).strip()
                if not key_str:
                    continue
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                memory_db.set_preference(key_str, value)
    except Exception as exc:
        logger.warning(f"Failed to store memory: {exc}")


def store_memory_async(
    llm,
    memory_db,
    user_text: str,
    assistant_text: str,
    tool_results: Optional[Dict[str, str]] = None,
    prompt_override: Optional[str] = None,
    source: str = "voice",
) -> None:
    """Run memory extraction + storage in a background thread."""
    def _task():
        payload = extract_memory_payload(
            llm,
            user_text,
            assistant_text,
            tool_results=tool_results,
            prompt_override=prompt_override,
        )
        store_memory_payload(memory_db, payload, source=source)

    thread = threading.Thread(target=_task, daemon=True)
    thread.start()


def record_and_transcribe(
    audio,
    display,
    max_duration: float = 10,
    silence_threshold: float = 1.0,
    min_speech: float = 0.3,
    filepath: str = "data/voice_input.wav"
) -> Optional[str]:
    """Record audio with VAD and return transcribed text, or None on failure."""
    display.show_emotion("curious")
    recording = audio.record_vad(
        filepath=filepath,
        max_duration=max_duration,
        silence_threshold=silence_threshold,
        min_speech=min_speech
    )

    if not recording:
        display.show_emotion("neutral")
        return None

    display.show_emotion("thinking")
    text = audio.transcribe(recording)

    if not text or not text.strip():
        return None

    return text.strip()


def speak_response(
    text: str,
    audio,
    display,
    interruptible: bool = True
) -> bool:
    """Speak response with happy emotion. Returns True if interrupted."""
    if not text:
        return False
    display.show_emotion("happy")
    return audio.speak(text, interruptible=interruptible)


def execute_tool_calls(
    tool_calls,
    llm,
    hardware,
    display,
    camera_available: bool
) -> Tuple[str, Dict[str, str]]:
    """Execute tool calls and return (extra_text, tool_results dict)."""
    extra_text = ""
    tool_results = {}
    hardware = hardware or {}
    camera = hardware.get("camera")

    for tc in tool_calls:
        fn_name = tc.function.name
        try:
            fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except json.JSONDecodeError:
            fn_args = {}

        logger.info(f"Executing action: {fn_name}({fn_args})")

        emotion = ACTION_EMOTIONS.get(fn_name, "neutral")
        display.show_emotion(emotion)

        if fn_name == "take_photo":
            if camera_available and camera is not None:
                image = camera.capture("data/voice_photo.jpg")
                if image:
                    display.show_emotion("thinking")
                    desc = llm.query("Describe what you see briefly.", image_path=image)
                    extra_text = desc if desc else "I took a photo but couldn't describe it."
                    tool_results[tc.id] = extra_text
                else:
                    tool_results[tc.id] = "Photo capture failed"
            else:
                tool_results[tc.id] = "Camera not available"
        else:
            action_result = run_action(fn_name, fn_args, hardware)
            if action_result is not None:
                tool_results[tc.id] = str(action_result)
            else:
                tool_results[tc.id] = f"{fn_name} not available"

    return extra_text, tool_results


def build_tool_history(result: Dict, tool_results: Dict[str, str]) -> List[Dict]:
    """Build history messages for a tool-call response.

    Returns list of messages: [assistant_with_tool_calls, tool_result_1, tool_result_2, ...]
    """
    messages = []
    tool_calls = result.get("tool_calls", [])

    if not tool_calls:
        return messages

    # Assistant message with tool_calls
    messages.append({
        "role": "assistant",
        "content": result.get("text") or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in tool_calls
        ],
    })

    # Tool result messages
    for tc in tool_calls:
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": tool_results.get(tc.id, "done"),
        })

    return messages


class VoiceHistory:
    """Manages conversation history with automatic trimming."""

    def __init__(self, max_turns: int = 10):
        self.messages: List[Dict] = []
        self.max_turns = max_turns

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        if text:
            self.messages.append({"role": "assistant", "content": text})
            self._trim()

    def add_tool_response(self, result: Dict, tool_results: Dict[str, str]) -> None:
        """Add assistant + tool messages for a tool-call response."""
        tool_messages = build_tool_history(result, tool_results)
        self.messages.extend(tool_messages)
        self._trim()

    def get_context(self) -> List[Dict]:
        """Return messages for LLM context (excludes most recent user message)."""
        if not self.messages:
            return []
        if self.messages[-1].get("role") == "user":
            base = self.messages[:-1]
        else:
            base = self.messages.copy()
        return self._sanitize_tool_history(base)

    def _trim(self) -> None:
        """Keep only the last max_turns user turns."""
        if not self.messages:
            return
        user_count = 0
        start_index = 0
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                user_count += 1
                if user_count >= self.max_turns:
                    start_index = i
                    break
        if user_count >= self.max_turns:
            self.messages = self.messages[start_index:]

    def to_list(self) -> List[Dict]:
        """Return all messages as a list."""
        return self.messages.copy()

    def load(self, messages: List[Dict]) -> None:
        """Load messages from a list."""
        self.messages = messages.copy()
        self._trim()

    def _sanitize_tool_history(self, messages: List[Dict]) -> List[Dict]:
        """Drop orphaned tool messages that lack preceding tool_calls."""
        if not messages:
            return []
        output: List[Dict] = []
        pending = set()
        for msg in messages:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                output.append(msg)
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id")
                    if tc_id:
                        pending.add(tc_id)
                continue
            if role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id and tc_id in pending:
                    output.append(msg)
                    pending.remove(tc_id)
                continue
            output.append(msg)
        return output
