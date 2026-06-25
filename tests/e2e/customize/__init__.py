"""End-to-end matrix harness for the Customize ``custom_rules`` surface.

PR-F-QA1 lays the foundation: matrix enumeration, payload factory, per-slot
trigger drivers (pre_final / before_tool_use / after_tool_use), assertion
helpers, and conftest fixtures. F-QA2-5 extend coverage to the remaining
14 lifecycle slots.

These tests author rules via the customize storage API (no wizard / no React
layer), drive a synthetic turn through the appropriate runtime chokepoint,
and assert the verdict matches the ``_LEGAL`` matrix's declared action for
the ``(kind, slot, action)`` combo. Each test cleans up after itself.
"""
