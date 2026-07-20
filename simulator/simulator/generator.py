from __future__ import annotations

import random
from typing import Any

from simulator.core.models import ParameterState, SimulationCase


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resolve_phase(profile_cfg: dict[str, Any], tick_index: int) -> str:
    sequence = profile_cfg.get("phase_sequence") or ["baseline"]
    phase_seconds = int(profile_cfg.get("phase_seconds", 45))
    idx = (tick_index // max(phase_seconds, 1)) % len(sequence)
    return str(sequence[idx])


def _target_for_param(param_name: str, state: ParameterState, profile_cfg: dict[str, Any], phase_name: str) -> float:
    targets = profile_cfg.get("targets") or {}
    phase_targets = targets.get(phase_name) or {}
    baseline_targets = targets.get("baseline") or {}

    target = float(
        phase_targets.get(
            param_name,
            baseline_targets.get(param_name, state.current_value),
        )
    )

    return target


def _param_seed(param_name: str) -> int:
    return sum(ord(ch) for ch in param_name)


def _resolve_case_variant(case_name: SimulationCase, param_name: str, tick_index: int) -> SimulationCase:
    if case_name != "mixed":
        return case_name
    variants: tuple[SimulationCase, ...] = ("within_range", "outside_range", "low_limit", "high_limit")
    idx = (_param_seed(param_name) + (tick_index // 10)) % len(variants)
    return variants[idx]


def update_parameter(
    state: ParameterState,
    case_name: SimulationCase,
    param_name: str,
    tick_index: int,
    target_value: float,
    volatility: float,
) -> float:
    definition = state.definition
    base_min = definition.min_value
    base_max = definition.max_value
    span = base_max - base_min

    variant = _resolve_case_variant(case_name, param_name, tick_index)

    if variant == "outside_range":
        margin = span * 0.2
        allowed_min = base_min - margin
        allowed_max = base_max + margin
        direction = -1 if ((_param_seed(param_name) + (tick_index // 8)) % 2 == 0) else 1
        target_value = (base_min - (0.12 * span)) if direction < 0 else (base_max + (0.12 * span))
        max_step = max(0.6, span * 0.08 * volatility)
    elif variant == "low_limit":
        margin = span * 0.2
        allowed_min = base_min - margin
        allowed_max = base_max
        target_value = base_min - (0.08 * span)
        max_step = max(0.25, span * 0.03 * volatility)
    elif variant == "high_limit":
        margin = span * 0.2
        allowed_min = base_min
        allowed_max = base_max + margin
        target_value = base_max + (0.08 * span)
        max_step = max(0.25, span * 0.03 * volatility)
    else:
        allowed_min = base_min
        allowed_max = base_max
        max_step = max(0.25, span * 0.03 * volatility)

    toward_target = (target_value - state.current_value) * 0.25
    delta = toward_target + random.uniform(-max_step, max_step)
    next_value = _clamp(state.current_value + delta, allowed_min, allowed_max)
    state.current_value = next_value
    return next_value


def update_all(
    parameters: list[ParameterState],
    case_name: SimulationCase,
    profile_cfg: dict[str, Any] | None = None,
    tick_index: int = 0,
) -> tuple[dict[str, float], str]:
    profile = profile_cfg or {}
    phase_name = _resolve_phase(profile, tick_index)
    volatility = float(profile.get("volatility", 1.0))

    values: dict[str, float] = {}
    for state in parameters:
        param_name = state.definition.name
        target = _target_for_param(param_name, state, profile, phase_name)
        values[param_name] = round(
            update_parameter(state, case_name, param_name, tick_index, target, volatility),
            2,
        )
    return values, phase_name
