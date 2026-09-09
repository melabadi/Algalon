from __future__ import annotations

from collections.abc import Iterable


def union_interval_duration(intervals: Iterable[tuple[float, float]]) -> float:
    sorted_intervals = sorted(
        (start, end) for start, end in intervals if end > start
    )
    total = 0.0
    current_start: float | None = None
    current_end: float | None = None
    for start, end in sorted_intervals:
        if current_start is None or current_end is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        total += current_end - current_start
    return total
