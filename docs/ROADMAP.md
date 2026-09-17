# Roadmap

## Stage 1 — Observation ✅

Active window and idle detection on X11, macOS and Windows, collapsed into
activity spans, filtered through the privacy pipeline before storage.

**Input capture** (keystrokes and mouse) is implemented as an opt-in extension
of stage 1, since replicating a process needs more than window metadata. Four
fidelity tiers, redaction of typed text, interlocks that configuration cannot
switch off, and a 7-day retention. `copynion replay` prints the captured
interaction step by step. See [PRIVACY.md](PRIVACY.md#input-capture).

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

**The open question that decides the project:** how much fidelity does
identifying automatable tasks actually need? Now that the tiers exist, this is
an experiment rather than a guess — run stage 3's detection against the same
week captured at `counts`, `structure` and `full`, and measure what each tier
finds. If `structure` finds essentially what `full` finds, that is a strong
result and `full` should stay reserved for the replay step alone.

That experiment should happen before stage 4 is built, because its outcome
decides how much anyone needs to record day to day.

## Stage 4 — Execution (later)

The raw material now exists: `copynion replay` shows a complete, timestamped,
chronologically ordered interaction trace for any span. What is missing is
everything between reading that trace and safely performing it.

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
- **Replay must be parameterised, not literal.** Redacted slots
  (`type <the invoice number>`) are the right abstraction: a process worth
  automating takes different data each run. Replaying one recorded run verbatim
  would mostly reproduce that run's mistakes.
- **Coordinates are brittle.** A recorded click at (864, 111) breaks when a
  window moves or a screen resolution changes. Turning coordinates into targets
  ("the Save button") is likely the hardest single problem in stage 4, and may
  be where a computer-use model earns its place.

## Smaller things worth doing

- **Import ActivityWatch history**, so users arrive with months of data. See
  [PRIOR_ART.md](PRIOR_ART.md#should-copynion-use-activitywatch-as-its-observation-layer)
  for why importing is the right relationship rather than building on it.
- A **browser watcher** (URL/domain rather than title) — the single biggest
  improvement to categorisation accuracy; ActivityWatch's unsolved URL problem
  is instructive here.
- **Wayland**: revisit if a privacy-respecting focus protocol is standardised.
- **`copynion report --html`** for a shareable weekly summary.
- **Per-project attribution** for freelancers (billable time).
- Packaging: systemd user unit, launchd plist, Windows scheduled task.
