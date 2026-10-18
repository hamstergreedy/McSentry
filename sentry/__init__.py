"""McAccount Sentinel — 麦麦账户哨兵.

An unattended patrol for the assets in a McDonald's China (麦享会) account that
silently evaporate: expiring points, unclaimed coupons, unused lottery chances.

The package is intentionally deterministic. Every number in a report can be
traced back to a tool response or a catalogue row; the LLM only narrates.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
