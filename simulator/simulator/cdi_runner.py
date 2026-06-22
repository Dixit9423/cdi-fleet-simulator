from __future__ import annotations

import logging


def run_cdi_simulation_placeholder(device_id: str) -> None:
    """Placeholder entrypoint for future CDI runner wiring inside dual-mode package."""
    logging.getLogger("simulator.cdi_runner").info(
        "CDI runner placeholder invoked for device %s", device_id
    )
