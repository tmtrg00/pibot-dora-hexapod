#!/usr/bin/env python3
"""Camera test node — drives capture requests through the graph.

Two things are under test, and they are worth separating:

  1. Does the camera actually produce a frame? On this Pi the RP1 CSI-2 receive
     path is defective (pinned upstream), so the honest expectation is no.

  2. Does the *graph* survive a camera that fails? This is the part that is
     genuinely being tested here. A capture that times out must come back as a
     clean `ok: false` with a reason, in bounded time, leaving the other nodes
     running — not hang the brain, not crash the node, not wedge the dataflow.

Reports the wall-clock each capture took, because "failed" and "failed after
blocking for 30 seconds" are very different results for the autonomy loop.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import decode, encode, get_logger

common.bootstrap()

from dora import Node  # noqa: E402

NODE = "camera_test"
logger = get_logger(NODE)

ATTEMPTS = int(os.environ.get("PIBOT_CAMERA_ATTEMPTS", "3"))
# Generous: a defective receive path shows up as a frontend timeout, and we
# want to measure how long that takes rather than cut it short ourselves.
CAPTURE_TIMEOUT_S = float(os.environ.get("PIBOT_CAMERA_TIMEOUT", "45"))


def main() -> None:
    node = Node()
    logger.info(f"camera test: {ATTEMPTS} capture attempt(s), {CAPTURE_TIMEOUT_S:.0f}s budget each")

    attempt = 0
    pending = False
    sent_at = 0.0
    next_at = time.time() + 2.0
    results: list = []

    def send_capture() -> None:
        nonlocal attempt, pending, sent_at
        attempt += 1
        path = f"data/camera_test_{attempt}.jpg"
        pending = True
        sent_at = time.time()
        logger.info(f"[{attempt}/{ATTEMPTS}] requesting capture -> {path}")
        node.send_output("capture", encode({"purpose": "observation", "path": path}))

    for event in node:
        if event["type"] == "STOP":
            break
        if event["type"] != "INPUT":
            continue

        if event["id"] == "image":
            if not pending:
                continue
            payload = decode(event) or {}
            elapsed = time.time() - sent_at
            pending = False
            ok = bool(payload.get("ok"))
            path = payload.get("path")
            size = None
            if ok and path and os.path.exists(path):
                size = os.path.getsize(path)
            if ok:
                logger.info(f"    ok in {elapsed:.1f}s -> {path} ({size} bytes)")
            else:
                logger.warning(f"    FAILED in {elapsed:.1f}s: {payload.get('error')}")
            results.append((attempt, ok, elapsed, payload.get("error"), size))
            next_at = time.time() + 1.5

        elif event["id"] == "tick":
            now = time.time()
            if pending:
                if now - sent_at > CAPTURE_TIMEOUT_S:
                    logger.error(
                        f"    NO RESPONSE after {CAPTURE_TIMEOUT_S:.0f}s — the camera node "
                        f"is blocked, not merely failing"
                    )
                    results.append((attempt, False, now - sent_at, "no response (node blocked)", None))
                    pending = False
                    next_at = now + 1.5
                continue
            if attempt >= ATTEMPTS:
                break
            if now >= next_at:
                send_capture()

    logger.info("=" * 66)
    logger.info("camera test summary")
    logger.info("=" * 66)
    for n, ok, elapsed, error, size in results:
        if ok:
            logger.info(f"  attempt {n}: OK      {elapsed:6.1f}s  {size} bytes")
        else:
            logger.info(f"  attempt {n}: FAILED  {elapsed:6.1f}s  {error}")
    logger.info("=" * 66)
    good = sum(1 for r in results if r[1])
    logger.info(f"{good}/{len(results)} captures produced a frame")
    if good == 0 and results:
        worst = max(r[2] for r in results)
        blocked = any("no response" in (r[3] or "") for r in results)
        logger.info(
            f"No frames — consistent with the known-defective CSI receive path. "
            f"Slowest failure {worst:.1f}s; camera node "
            f"{'BLOCKED (bad — it should fail fast)' if blocked else 'answered cleanly every time (good)'}."
        )


if __name__ == "__main__":
    main()
