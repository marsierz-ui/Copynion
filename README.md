# Copynion

**Copy**ing comp**anion** — a local-first companion that learns how you use your
computer, and eventually learns to do parts of it for you.

Copynion is built in four stages, each one building on the last:

| Stage | What it does | Status |
|-------|--------------|--------|
| 1. **Observation** | Records which application and window you are working in, and optionally what you type and click | **Implemented** |
| 2. **Categorisation & statistics** | Classifies that activity and reports on it | **Implemented** |
| 3. **Analysis & suggestions** | Ranks what is actually worth automating | Planned |
| 4. **Execution** | Performs the tasks it has learned | Planned |

This repository currently implements stages 1 and 2, and deliberately lays the
groundwork for stage 3 (see [Repetition signals](#repetition-signals)).

## The premise

Everything stays on your machine. Not as a setting you can toggle, but as a
property of the code: **Copynion contains no network client at all.** There is no
account, no sync, no telemetry, no "anonymous usage statistics". A test in the
suite (`tests/test_no_network.py`) fails the build if anyone ever adds one.

## Install

```bash
git clone https://github.com/marsierz-ui/copynion
cd copynion
pip install -e ".[crypto]"      # 'crypto' enables title encryption - strongly recommended
pip install -e ".[input]"       # optional: keystroke and mouse capture (off by default)
```

Python 3.11+. The core has **zero required dependencies**; everything optional is
an explicit extra.

Platform support for reading the active window:

| Platform | Backend | Notes |
|----------|---------|-------|
| Linux (X11) | `xdotool` or `xprop` | install either; `xprintidle` adds idle detection |
| Linux (Wayland) | — | compositors do not expose the active window; see [PRIVACY.md](docs/PRIVACY.md) |
| macOS | AppleScript + `ioreg` | window titles need the Accessibility permission |
| Windows | `user32.dll` via ctypes | no extra install |

## Try it without being watched

```bash
copynion demo
```

This generates a synthetic working week and runs the full pipeline over it, so
you can see exactly what Copynion would produce before it records anything real.

## Use it

```bash
copynion init        # create config, encryption key and database
copynion doctor      # check backends, permissions and privacy settings
copynion watch       # start observing (Ctrl-C to stop)

copynion status      # what's running, and today so far
copynion stats --days 7
copynion replay      # the interaction recorded for a span, step by step
copynion pause       # stop recording instantly, from any terminal
copynion resume
```

### What a report looks like

```
Where the time went
-------------------
  Development               10h 27m   48.1%  #############...............
  Meetings                   3h 53m   17.9%  #####.......................
  Communication              2h 44m   12.6%  ####........................
  Data & spreadsheets        2h 44m   12.6%  ####........................

Shape of the work
-----------------
  Context switches             103
  Switches per active hr       4.7
  Fragmentation               0.16  (long uninterrupted stretches)
  Focus blocks (>=15m)          26   totalling 11h 37m (53% of active time)
  Longest focus block      41m 00s   development starting Wed 09:26

  Paths you walked repeatedly:
    thunderbird -> localc -> thunderbird                    6x  on 5 day(s)
    localc -> thunderbird -> firefox                        7x  on 5 day(s)
```

### Input capture — keystrokes and mouse

Replicating a process needs more than knowing which window was in front of you,
so Copynion can record what you type and click. This is **off by default** and
switched on at a fidelity tier, because keystroke content is the most dangerous
thing a program can record:

| `[input] fidelity` | Records | Replay? |
|--------------------|---------|---------|
| `off` | nothing — **the default** | no |
| `counts` | how much typing and clicking, nothing about what | no |
| `structure` | key *classes*, shortcuts, mouse coordinates, timing | approximately |
| `full` | literal characters, coordinates, timing | yes |

See exactly what gets recorded before enabling it for real:

```bash
copynion demo                             # generates synthetic interaction
copynion --database ~/.local/share/copynion/demo.db replay
```

```
Span 2: localc - daily-report.ods - LibreOffice Calc
  2026-09-15 09:16:00, 14m 00s, category data_entry, fidelity full

  +   0.00s  type       'uiru'  (4 chars)
  +   5.09s  shortcut   ctrl+z
  +   5.37s  click      left at (864, 111)
  +  10.00s  type       '=SUM(B2:B31)'  (12 chars)
  +  10.96s  type       'tab'
  +  11.14s  click      left at (563, 833)
```

Three things make this defensible rather than reckless:

- **Typed text is redacted by default** — `invoice 4455667788 for bob@acme.com`
  is stored as `invoice <number> for <email>`. That is also the *better* default
  for replication: each run of a process has a different invoice number, so what
  generalises is the slot, not one run's value.
- **Interlocks you cannot configure away** — password fields (via macOS secure
  input), any app whose title the policy withholds, private browsing, and pause.
  A half-typed word is discarded, not committed, when suppression begins.
- **Seven-day retention**, shorter than anything else, with content-free counters
  surviving so the statistics keep working.

Read [docs/PRIVACY.md](docs/PRIVACY.md#input-capture) before enabling `full` —
particularly the part explaining that Windows and X11 cannot detect password
fields at all.

### Repetition signals

Stage 2 reports three families of numbers:

- **Composition** — where the time went, by category and application.
- **Shape** — focus blocks, context switches, fragmentation, time-of-day profile.
  Two people with identical totals can have completely different days.
- **Repetition** — which windows and which app-to-app paths recur.

That last family is the bridge to stage 3. It is kept deliberately as *counts,
not recommendations*: "you walked mail → spreadsheet → mail six times this week"
is an observation. Turning that into "here is a script that does it" requires
the causal analysis stage 3 is for, and pretending otherwise would be guessing.

Notably, recurring windows are detected **without ever reading a title** — they
are grouped by a keyed HMAC fingerprint (see below).

## Privacy design

The defaults are the private option. You opt *into* detail, never out of it.

- **App names yes, window titles no** — by default. Titles are recorded only for
  applications you explicitly list as `full` in your config.
- **Redaction before storage.** Titles are scrubbed of emails, card numbers,
  IBANs, phone numbers, tokens, JWTs, URL query strings and home-directory
  usernames *before* they are written. See `src/copynion/privacy/redact.py`.
- **Encryption at rest.** Titles are sealed individually with AES-GCM. The key
  lives in your OS keychain, or in a 0600 file. `copynion doctor` tells you
  which — honestly, including when the keychain wasn't available.
- **Fail closed.** No working encryption means titles are *dropped*, not stored
  in the clear. Overriding that takes an explicit config change.
- **Password managers and private browsing** are protected by default.
- **No screenshots, no clipboard, no OCR, no accessibility-tree scraping** — ever.
- **Keystroke and mouse capture is opt-in**, tiered, redacted by default, and
  expires in 7 days. Off unless you switch it on.
- **Two-tier retention.** Detailed per-window records expire (90 days by
  default); the daily statistics, which contain no free text, are kept.
- **Your data, exportable and deletable**: `copynion export`, `copynion purge`,
  `copynion purge --titles-only`, `copynion purge --inputs-only`, `copynion audit`.

Full detail, including the threat model and what Copynion does *not* protect
against: **[docs/PRIVACY.md](docs/PRIVACY.md)**.

## Does this already exist?

Partly — and it is worth knowing what you would be reinventing. That document
also explains [why Copynion has its own observation layer rather than building
on ActivityWatch](docs/PRIOR_ART.md#should-copynion-use-activitywatch-as-its-observation-layer).
**[docs/PRIOR_ART.md](docs/PRIOR_ART.md)** covers the existing landscape
(ActivityWatch, RescueTime, Rewind/Recall/Screenpipe, and enterprise task-mining
tools like UiPath and Celonis), what each one gets right, and where the gap that
Copynion aims at actually is.

## Documentation

- [docs/PRIVACY.md](docs/PRIVACY.md) — threat model, data inventory, guarantees and limits
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pipeline fits together
- [docs/PRIOR_ART.md](docs/PRIOR_ART.md) — existing tools and where the gap is
- [docs/ROADMAP.md](docs/ROADMAP.md) — stages 3 and 4, and the open questions

## Development

```bash
pip install -e ".[dev,crypto]"
pytest          # 239 tests
ruff check src tests
```

## Licence

MIT.
