"""The category vocabulary.

Each category carries an ``automation_affinity``: a rough prior for how likely
work of this kind is to be automatable at all. Data entry and file shuffling
score high; design and meetings score low. Stage 2 only reports these numbers;
stage 3 will combine them with observed repetition to rank real candidates,
which is why the vocabulary is defined here rather than invented later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CategoryDef:
    name: str
    label: str
    description: str
    automation_affinity: float
    """0.0 = essentially never automatable, 1.0 = archetypal automation target."""

    focus_work: bool = True
    """Whether time here counts towards 'focused work' in the statistics."""


TAXONOMY: tuple[CategoryDef, ...] = (
    CategoryDef("development", "Development", "Writing and running code", 0.55),
    CategoryDef("terminal", "Terminal", "Shell and command-line work", 0.80),
    CategoryDef("data_entry", "Data & spreadsheets", "Tabular data, forms, records", 0.90),
    CategoryDef("documents", "Documents", "Writing and editing prose", 0.45),
    CategoryDef("communication", "Communication", "Email and chat", 0.60, focus_work=False),
    CategoryDef("meetings", "Meetings", "Calls and video conferences", 0.10, focus_work=False),
    CategoryDef("research", "Research", "Reading docs, searching, reference", 0.30),
    CategoryDef("project_mgmt", "Project management", "Tickets, boards, planning", 0.70),
    CategoryDef("design", "Design", "Visual and UI design work", 0.15),
    CategoryDef("file_mgmt", "File management", "Moving, renaming, organising files", 0.95),
    CategoryDef("sysadmin", "System administration", "Configuring and maintaining systems", 0.75),
    CategoryDef("media", "Media", "Video, music, images", 0.05, focus_work=False),
    CategoryDef("leisure", "Leisure", "Social, entertainment, browsing", 0.0, focus_work=False),
    CategoryDef("idle", "Idle", "Away from the machine", 0.0, focus_work=False),
    CategoryDef("uncategorised", "Uncategorised", "Not yet classified", 0.0, focus_work=False),
)

BY_NAME: dict[str, CategoryDef] = {c.name: c for c in TAXONOMY}

VALID_CATEGORIES: frozenset[str] = frozenset(BY_NAME)


def get(name: str) -> CategoryDef:
    return BY_NAME.get(name, BY_NAME["uncategorised"])


def affinity(name: str) -> float:
    return get(name).automation_affinity


def is_focus_work(name: str) -> bool:
    return get(name).focus_work
