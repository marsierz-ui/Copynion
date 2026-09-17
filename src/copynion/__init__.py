"""Copynion - a local-first companion that learns how you use your computer.

The project is built in four stages, each one refining the last:

1. Observation           -- what happened (implemented)
2. Categorisation/stats  -- what kind of work it was (implemented)
3. Analysis/suggestions  -- what is worth automating (planned)
4. Execution             -- doing it for you (planned)

Everything in stages 1 and 2 runs offline. The package deliberately contains no
network client of any kind; see ``docs/PRIVACY.md`` and ``tests/test_no_network.py``.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
