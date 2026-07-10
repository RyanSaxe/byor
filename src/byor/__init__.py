"""Expose the BYOR package namespace.

The package root intentionally keeps its runtime surface small while submodules own concrete
command, scan, and scaffold behavior. Its module contract still records that choice explicitly so
public API drift is visible during dogfood checks.
"""

__version__ = "0.4.0"

__all__ = ("__version__",)
