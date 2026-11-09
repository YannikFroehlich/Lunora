"""Atomic counters in the shared cache, for limits that parallel requests must not slip past."""

from django.core.cache import cache


def count_hit(key, window_seconds):
    """Count one event under ``key`` and return the total; the window starts at the first event."""
    if cache.add(key, 1, window_seconds):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # Expired between add and incr, so this event starts over.
        return 1
