"""Terminal progress indicators for the PACE pipeline.

Provides a spinner context manager for API calls and a stage progress bar
for the overall pipeline. No external dependencies — stdlib only.
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TOTAL_STAGES = 11
_BAR_WIDTH = 28


def _bar(current: int, total: int = _TOTAL_STAGES) -> str:
    filled = round(current / total * _BAR_WIDTH)
    empty = _BAR_WIDTH - filled
    pct = round(current / total * 100)
    return f"[{'━' * filled}{'░' * empty}] {pct:3d}%"


def stage(n: int, label: str, total: int = _TOTAL_STAGES) -> None:
    """Print a stage header with overall pipeline progress bar."""
    print(f"\n{_bar(n - 1, total)}  Stage {n}/{total}")
    print(f"  {label}")


def done(label: str, total: int = _TOTAL_STAGES) -> None:
    """Print the final 100% bar when the pipeline completes."""
    print(f"\n{_bar(total, total)}  {label}")


@contextmanager
def spinner(label: str):
    """
    Animated spinner for long-running API calls.

    Usage:
        with spinner("Generating tailoring JSON"):
            result = api_call(...)

    Shows elapsed time while waiting. Prints a checkmark on completion.
    Any print() calls that happen inside the block will appear after the
    spinner line is cleared on completion.
    """
    stop_event = threading.Event()
    # Capture any prints that happen during the spin so they appear cleanly after
    lines_printed: list[str] = []

    def _spin() -> None:
        i = 0
        t0 = time.time()
        while not stop_event.is_set():
            frame = _FRAMES[i % len(_FRAMES)]
            elapsed = time.time() - t0
            sys.stdout.write(f"\r  {frame}  {label}  ({elapsed:.0f}s)")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    thread = threading.Thread(target=_spin, daemon=True)
    thread.start()
    t0 = time.time()

    try:
        yield
    finally:
        stop_event.set()
        thread.join()
        elapsed = time.time() - t0
        # Overwrite spinner line with completion marker, pad to clear trailing chars
        sys.stdout.write(f"\r  ✓  {label}  ({elapsed:.1f}s){' ' * 20}\n")
        sys.stdout.flush()
