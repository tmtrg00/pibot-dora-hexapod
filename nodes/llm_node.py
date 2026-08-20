#!/usr/bin/env python3
"""LLM node — the language and long-term-memory service.

Owns `LLMHandler`, the SQLite memory DB and the `InitiativePolicy`. Bundling
them is deliberate: memory context has to be built *before* the query and the
memory extraction is itself an LLM call, so keeping all three together avoids
two extra round trips per conversational turn and leaves the DB with a single
writer.

The point of separating this from the brain is that an OpenAI call is 1-3
seconds of blocking network I/O. Here that blocks only this process — sensors
keep publishing, LEDs keep animating and the gait thread keeps running while
the robot is "thinking".

Request kinds on `llm_request`:
  turn     — a user utterance; builds memory context, applies the initiative
             policy, queries with the hexapod tool schemas
  vision   — describe an image at a path; plain-text answer
  remember — fire-and-forget: extract and store a memory from a finished turn
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import (
    approach_tool_schema,
    decode,
    encode,
    fight_tool_schema,
    hypno_wave_tool_schema,
    get_logger,
    load_config,
    stance_tool_schema,
    tool_calls_to_dicts,
    turn_tool_schema,
    walk_straight_tool_schema,
)

common.bootstrap()

from dora import Node  # noqa: E402
from src import memory_db  # noqa: E402
from src.actions import TOOLS  # noqa: E402
from src.llm_handler import LLMHandler  # noqa: E402
from src.voice import (  # noqa: E402
    InitiativePolicy,
    build_memory_context,
    extract_memory_payload,
    store_memory_payload,
)

NODE = "llm"
logger = get_logger(NODE)

# The upstream 13 tools plus set_stance, turn_to, walk_straight, approach,
# fight and hypno_wave, which this project adds without touching
# src/actions.py.
TOOL_SCHEMAS = list(TOOLS) + [
    stance_tool_schema(),
    turn_tool_schema(),
    walk_straight_tool_schema(),
    approach_tool_schema(),
    fight_tool_schema(),
    hypno_wave_tool_schema(),
]


def main() -> None:
    node = Node()
    config = load_config()

    llm = LLMHandler()

    memory_cfg = config.get("memory", {}) or {}
    memory_enabled = bool(memory_cfg.get("enabled", False))
    memory_max_items = int(memory_cfg.get("max_items", 5))
    memory_prompt = memory_cfg.get("summary_prompt")

    if memory_enabled:
        try:
            memory_db.ensure_schema()
            memory_db.maintain()
            logger.info("memory DB enabled")
        except Exception as exc:
            logger.warning(f"Memory DB unavailable: {exc}")
            memory_enabled = False

    policy = InitiativePolicy(config.get("initiative", {}) or {})
    logger.info("LLM ready")

    def handle_turn(req: dict) -> dict:
        user_text = str(req.get("user_text") or "")

        memory_context = ""
        if memory_enabled:
            try:
                memory_context = build_memory_context(
                    memory_db, query_text=user_text, max_items=memory_max_items
                )
            except Exception as exc:
                logger.warning(f"Memory context failed: {exc}")

        allow_proactive, turn_instructions = policy.prepare_turn(
            user_text,
            force_disable=bool(req.get("force_disable_proactive")),
            has_memory_context=bool(memory_context.strip()),
        )

        result = llm.query(
            user_text,
            tools=TOOL_SCHEMAS,
            history=req.get("history") or [],
            memory_context=memory_context,
            turn_instructions=turn_instructions,
        )

        if isinstance(result, str):
            text, tool_calls = result, []
        else:
            text = result.get("text", "") or ""
            tool_calls = result.get("tool_calls") or []

        policy.register_assistant_turn(text, allowed=allow_proactive, had_tool_calls=bool(tool_calls))
        return {"text": text, "tool_calls": tool_calls_to_dicts(tool_calls)}

    def handle_vision(req: dict) -> dict:
        image_path = req.get("image_path")
        prompt = str(
            req.get("prompt")
            or "What do you see in this image? Describe it briefly and conversationally."
        )
        if not image_path or not os.path.exists(image_path):
            return {"text": "", "tool_calls": [], "error": "image not found"}
        result = llm.query(prompt, image_path=image_path)
        return {"text": str(result) if result else "", "tool_calls": []}

    def handle_remember(req: dict) -> None:
        """Extract and store a memory off the event loop."""
        if not memory_enabled:
            return

        def task() -> None:
            try:
                payload = extract_memory_payload(
                    llm,
                    str(req.get("user_text") or ""),
                    str(req.get("assistant_text") or ""),
                    tool_results=req.get("tool_results") or {},
                    prompt_override=memory_prompt,
                )
                store_memory_payload(memory_db, payload, source=str(req.get("source") or "voice"))
            except Exception as exc:
                logger.warning(f"Memory store failed: {exc}")

        threading.Thread(target=task, daemon=True).start()

    def handle_observation(req: dict) -> None:
        if not memory_enabled:
            return
        try:
            memory_db.add_observation(
                summary=str(req.get("summary") or ""),
                image_path=req.get("image_path"),
                tags=["autonomous"],
            )
        except Exception as exc:
            logger.warning(f"Observation store failed: {exc}")

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT" or event["id"] != "llm_request":
            continue

        req = decode(event)
        if not req:
            continue

        kind = str(req.get("kind") or "turn")
        request_id = req.get("id")

        try:
            if kind == "remember":
                handle_remember(req)
                continue
            if kind == "observation":
                handle_observation(req)
                continue
            if kind == "vision":
                response = handle_vision(req)
            else:
                response = handle_turn(req)
        except Exception as exc:
            logger.exception(f"{kind} request failed")
            response = {"text": f"Sorry, I hit an error: {exc}", "tool_calls": []}

        response["id"] = request_id
        response["kind"] = kind
        node.send_output("llm_response", encode(response))

    logger.info("stopped")


if __name__ == "__main__":
    main()
