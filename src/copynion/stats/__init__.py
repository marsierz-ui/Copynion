"""Stage 2b: statistics over observed activity."""

from copynion.stats.aggregate import (
    AppStat,
    CategoryStat,
    FocusBlock,
    RecurringWindow,
    SequencePattern,
    Summary,
    summarise,
)
from copynion.stats.report import human_duration, render, render_compact

__all__ = [
    "AppStat",
    "CategoryStat",
    "FocusBlock",
    "RecurringWindow",
    "SequencePattern",
    "Summary",
    "human_duration",
    "render",
    "render_compact",
    "summarise",
]
