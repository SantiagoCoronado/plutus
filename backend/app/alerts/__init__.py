"""Per-asset price alerts: the crossing-edge evaluator (M4).

The evaluator runs as a per-minute beat task over the live quote cache and fires
once when a quote actually crosses a rule's threshold, then waits for an explicit
re-arm. See app/alerts/evaluate.py.
"""
