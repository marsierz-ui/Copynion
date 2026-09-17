"""The privacy core.

Every observation passes through this package before it can reach disk:

    raw observation -> Policy.decide() -> Redactor.scrub() -> Vault.seal() -> SQLite

Each layer is independently useful. Even if encryption is disabled the stored
titles are already redacted, and even if a redaction pattern misses something
the ciphertext is unreadable without the vault key.
"""

from copynion.privacy.policy import Policy, PolicyDecision
from copynion.privacy.redact import RedactionReport, Redactor
from copynion.privacy.vault import Vault, VaultError

__all__ = [
    "Policy",
    "PolicyDecision",
    "Redactor",
    "RedactionReport",
    "Vault",
    "VaultError",
]
