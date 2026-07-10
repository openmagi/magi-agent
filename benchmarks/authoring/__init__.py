"""Conversational-authoring QA harness for magi-agent.

This top-level ``benchmarks`` package is CI-excluded by placement (pytest
``testpaths`` covers ``tests`` + ``magi_agent/cli/tests`` only) and dropped from
the published wheel (``setuptools.packages.find`` includes ``magi_agent*``
only). The thin CI-visible tiers live under ``tests/authoring_harness`` and
import from here. See docs (clawy) design 2026-07-09 for the full wire map.
"""
