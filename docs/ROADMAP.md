# Roadmap

## Stage 1 — Observation ✅

Active window and idle detection on X11, macOS and Windows, collapsed into
activity spans, filtered through the privacy pipeline before storage.

## Stage 2 — Categorisation and statistics ✅

30 built-in rules over a 15-category taxonomy; composition, shape and repetition
statistics; user corrections that outrank the rules permanently.

## Stage 3 — Analysis and optimisation suggestions (next)

Stage 2 deliberately stops at *counts*. Stage 3 has to turn counts into ranked,
justified proposals. The work:

1. **Task segmentation.** Group spans into *task instances* rather than fixed
   n-grams — a real task has fuzzy boundaries, variants and interruptions.
   Sequence alignment over the app/fingerprint stream is the obvious first
   approach.
2. **Variant and exception detection.** A routine that runs identically 40 times
   is automatable; one that branches five ways probably is not. Borrow the
   vocabulary from task mining (variants, decision points, exceptions) rather
   than inventing terms — see [PRIOR_ART.md](PRIOR_ART.md).
3. **Automatability scoring.** Combine observed frequency × duration × category
   affinity (already in `taxonomy.py`) × variant entropy. Must produce a
   *defensible* number: "this costs you 3.5 h/week and is 90% identical
   each time".
4. **Suggestions with evidence.** Every proposal must cite the spans it is based
   on, so the user can check the reasoning. No unexplained recommendations.
5. **Rejection is training data.** "No, that's not a task" is the most valuable
   signal the system can receive, and must be stored like `category_overrides`.

**The open question that decides the project:** is window-level metadata enough
to identify automatable tasks, without recording content? This should be
validated before stage 4 is started. If it is not enough, the honest outcome is
a tool that *surfaces* candidates and hands them to a human or another tool —
still useful, and much better than quietly expanding what gets recorded.

## Stage 4 — Execution (later)

Not designed yet, deliberately. Acting on the user's behalf is a different
capability with a different consent model, and it must not be bolted onto the
observer. Constraints it will have to meet:

- **Explicit, per-task consent.** Observation consent is not execution consent.
- **Dry-run first, always.** Show what it would do before it does it.
- **Interruptible and reversible**, with a visible audit trail of every action.
- **Never a silent background actor.**
- Likely built on existing execution technology (RPA, computer-use agents)
  rather than a new one — stage 4's contribution is the *link from observation
  to action*, not a new way of clicking buttons.

## Smaller things worth doing

- **Import ActivityWatch history**, so users arrive with months of data.
- A **browser watcher** (URL/domain rather than title) — the single biggest
  improvement to categorisation accuracy; ActivityWatch's unsolved URL problem
  is instructive here.
- **Wayland**: revisit if a privacy-respecting focus protocol is standardised.
- **`copynion report --html`** for a shareable weekly summary.
- **Per-project attribution** for freelancers (billable time).
- Packaging: systemd user unit, launchd plist, Windows scheduled task.
