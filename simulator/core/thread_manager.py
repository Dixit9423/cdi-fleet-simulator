from __future__ import annotations

import threading
from collections.abc import Callable


def start_device_threads(
    targets: list[tuple[str, Callable[[], None]]],
) -> dict[str, threading.Thread]:
    """Start one daemon thread per device target and return them by device_id."""
    threads: dict[str, threading.Thread] = {}
    for device_id, target in targets:
        thread = threading.Thread(target=target, daemon=True, name=f"emr-{device_id}")
        thread.start()
        threads[device_id] = thread
    return threads
