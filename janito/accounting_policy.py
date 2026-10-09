"""Shared turn-accounting policy (issue #31).

Single owner for end-of-turn accounting: token selection (cumulative
turn totals with last-round fallback), monetary cost estimation, and
persistence. Both entry layers (CLI observer, web loop) invoke this
service; observers render results but never own billing policy.

Lives at the package root so every outer layer (``ui``, ``web``,
``llm_clients``, ``shell``, ``cli``) may import it without breaking the
enforced import matrix: root may target ``providers`` and ``tooling``,
and callers pass the turn usage duck-typed (no ``llm_adapters`` import,
which root must not depend on).
"""

from __future__ import annotations

import logging
from typing import Any

from janito.providers.costing import get_provider_cost_value
from janito.tooling.accounting import record_turn

logger = logging.getLogger(__name__)


def select_turn_tokens(turn_stats: Any) -> tuple[Any, Any, Any]:
    """Return ``(input, cached, output)`` turn-wide counters.

    Prefers the cumulative turn totals (tool-call rounds included) and
    falls back to the final round's counters when turn-wide ones were
    not reported. ``None`` means "not reported".
    """
    input_tokens = getattr(turn_stats, "turn_input", None)
    if input_tokens is None:
        input_tokens = getattr(turn_stats, "last_input", None)
    cached_tokens = getattr(turn_stats, "turn_cached", None)
    if cached_tokens is None:
        cached_tokens = getattr(turn_stats, "last_cached", None)
    output_tokens = getattr(turn_stats, "turn_output", None)
    if output_tokens is None:
        output_tokens = getattr(turn_stats, "last_output", None)
    return input_tokens, cached_tokens, output_tokens


def record_turn_accounting(
    turn_stats: Any | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Append one overall-use accounting row for a completed turn.

    Best effort -- never raises, so accounting cannot break the agent
    loop (issue #72). ``turn_stats`` is the turn-level usage object
    (e.g. :class:`janito.llm_adapters.usage.TurnInfo`); only its
    ``turn_*`` / ``last_*`` numeric attributes are read.
    """
    if turn_stats is None:
        return
    try:
        input_tokens, cached_tokens, output_tokens = select_turn_tokens(turn_stats)
        cost = None
        if provider and model:
            cost = get_provider_cost_value(
                provider,
                model,
                input_tokens or 0,
                output_tokens or 0,
                cached_tokens or 0,
            )
        record_turn(
            provider,
            model,
            input_tokens,
            cached_tokens,
            output_tokens,
            cost=cost,
        )
    except Exception as e:  # noqa: BLE001 - accounting must never break execution
        logger.debug(f"Failed to record turn accounting: {e}")


__all__ = [
    "record_turn_accounting",
    "select_turn_tokens",
]
