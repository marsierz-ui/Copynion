"""Local SQLite persistence. No data leaves this machine."""

from copynion.storage.schema import SCHEMA_VERSION
from copynion.storage.store import Store

__all__ = ["Store", "SCHEMA_VERSION"]
