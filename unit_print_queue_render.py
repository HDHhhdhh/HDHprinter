"""Geometry for rendering only the queue cards visible in a Canvas viewport."""

from __future__ import annotations

from math import ceil, floor


def queue_content_height(
    count: int, *, card_height: float, gap: float, padding: float,
) -> float:
    """Full model extent, independent of which cards currently have Canvas items."""
    count = max(0, count)
    return padding * 2 + count * card_height + max(0, count - 1) * gap


def visible_queue_indices(
    count: int,
    viewport_top: float,
    viewport_bottom: float,
    *,
    card_height: float,
    gap: float,
    padding: float,
    outline_margin: float = 2.0,
) -> range:
    """Return ordinary row indices intersecting the viewport, including outlines.

    Row positions and the full scroll region remain independent of rendering.
    This avoids visiting every row when a long queue is idle or only scrolling.
    """
    slot = card_height + gap
    if count <= 0 or card_height <= 0 or slot <= 0 or viewport_bottom < viewport_top:
        return range(0)
    margin = max(0.0, outline_margin)
    first = ceil((viewport_top - padding - card_height - margin) / slot)
    last = floor((viewport_bottom - padding + margin) / slot)
    return range(max(0, first), min(count, last + 1))


def queue_card_intersects_viewport(
    y0: float,
    card_height: float,
    viewport_top: float,
    viewport_bottom: float,
    *,
    lift_progress: float = 0.0,
    lifted: bool = False,
    outline_margin: float = 2.0,
) -> bool:
    """Test actual animated card bounds, including the lifted drag shadow."""
    if card_height <= 0 or viewport_bottom < viewport_top:
        return False
    margin = max(0.0, outline_margin)
    lift = min(1.0, max(0.0, lift_progress)) if lifted else 0.0
    top = y0 - 2 * lift
    bottom = top + card_height + (5 + 5 * lift if lifted else 0)
    return top - margin <= viewport_bottom and bottom + margin >= viewport_top
