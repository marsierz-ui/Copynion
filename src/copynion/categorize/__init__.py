"""Stage 2a: deciding what kind of work an observation represents."""

from copynion.categorize.engine import Categoriser
from copynion.categorize.rules import Rule, load_rules
from copynion.categorize.taxonomy import TAXONOMY, CategoryDef, affinity, is_focus_work

__all__ = [
    "Categoriser",
    "Rule",
    "load_rules",
    "TAXONOMY",
    "CategoryDef",
    "affinity",
    "is_focus_work",
]
