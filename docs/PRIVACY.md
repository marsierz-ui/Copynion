# Privacy design

Copynion watches what you do all day. That is either a useful assistant or the
most invasive piece of software on your machine, and the difference is entirely
in the engineering. This document states what it collects, what it guarantees,
how those guarantees are enforced, and — importantly — what it does **not**
protect you against.

## The one-sentence version

Copynion records *which application and window you were in, and for how long*.
It does not record what you typed, what you saw, or what was on your screen, and
it has no code capable of sending anything anywhere.

## Data inventory

Everything Copynion stores, and how sensitive it is:

| Data | Stored as | Sensitivity | Retention |
|------|-----------|-------------|-----------|
| Timestamps, durations | plaintext columns | low | 90 days (configurable) |
| Application name | plaintext column | low–medium | 90 days |
| Window title | AES-GCM ciphertext, **redacted first** | **high** | 30 days |
| Title fingerprint | keyed HMAC-SHA256, truncated | low (not reversible) | 90 days |
| Category, rule id, confidence | plaintext columns | low | 90 days |
| Daily rollups (per day/app/category seconds) | plaintext, no free text | low | **kept indefinitely** |
| App-to-app transition counts | plaintext, no free text | low | kept indefinitely |
| Your category corrections | plaintext | low | kept until you delete them |
| Audit log of privacy actions | plaintext | low | kept |

Explicitly **never collected**, at any stage or setting:

- keystrokes, or any keyboard content
- clipboard contents
- screenshots, screen recording, or OCR of the screen
- file contents
- accessibility-tree or DOM scraping
- microphone or camera
- network traffic
- anything about other users on the machine

Stage 4 (execution) will eventually need to *act*, which is a different
capability with a different consent model. It is not implemented, and it will
not be bolted onto the observer.

## The five layers

Every observation passes through these, in this order, before it can reach disk:

```
    active window
         |
    [1] Policy      -- may we record this at all, and at what fidelity?
         |
    [2] Redaction   -- strip anything that looks personal or secret
         |
    [3] Fingerprint -- keyed HMAC, so repetition is countable without the text
         |
    [4] Vault       -- AES-GCM seal the title
         |
    [5] Store       -- 0600 SQLite file, two-tier retention
```

The order is load-bearing and is enforced in `Sampler.ingest()`:

- Policy runs **first**, so a window you excluded is never even scrubbed, let
  alone stored.
- Redaction runs **before** fingerprinting, so the fingerprint covers the
  cleaned text — otherwise two titles differing only in a redacted email would
  fingerprint differently and break repetition counting.
- Fingerprinting is **keyed**, so a stolen database cannot be attacked by
  hashing a dictionary of likely window titles and matching.

### 1. Policy — four fidelity levels

| Level | Records |
|-------|---------|
| `full` | application name + redacted window title |
| `app_only` | application name only — **the default** |
| `opaque` | only that you were active (keeps totals honest) |
| `drop` | nothing at all |

Unlisted applications get `app_only`. You opt *into* title capture per
application; you never have to remember to opt out.

Protected by default, regardless of your settings:

- password managers, banking and wallet apps (`1password`, `bitwarden`,
  `keepass`, `*banking*`, `*wallet*`, …) — downgraded to `app_only`
- browser private/incognito windows — downgraded to `app_only`, because you
  already told your browser to forget this
- a `title_denylist` you control, matched by regex, which drops matching windows
  entirely — this is how you exclude one client or project without excluding the
  whole application

`copynion pause` stops recording within one poll interval, from any terminal,
and it is logged.

### 2. Redaction

Window titles are startlingly revealing. A single title can contain a customer's
full name, an invoice total, a password-reset URL with a live token, or the
subject line of a confidential email. The redactor replaces anything matching:

emails · IBANs · card numbers · phone numbers · `password:`/`token=`/`api_key=`
assignments · JWTs · API-key-shaped strings · long hex strings · URL query
strings · home-directory usernames · long digit runs

...and truncates to 180 characters. It is deliberately aggressive: a false
positive costs a little categorisation accuracy, a false negative writes
someone's bank details to disk. You can add your own patterns.

### 3 & 4. Fingerprinting and encryption

Titles are sealed individually with AES-GCM (random 96-bit nonce per value, so
the same title never produces the same ciphertext twice — a deterministic
ciphertext would itself leak which windows repeat).

The key is 256 bits, stored in your OS keychain when one is available, otherwise
in a 0600 file. `copynion doctor` reports which — and says so plainly when the
keychain was unavailable and it fell back to a file.

**Fail closed:** if the `cryptography` package is missing or broken, titles are
**not stored at all** rather than stored in plaintext. Changing that requires
setting `allow_unencrypted_titles = true` yourself, and `doctor` flags it as a
problem for as long as it is set.

Why not encrypt the whole database? Because timestamps, app names and categories
have to be fast SQL aggregates for the statistics to work, and encrypting them
would mean decrypting everything on every query. The titles are the revealing
part, so that is what is sealed. This is a deliberate, documented trade-off
rather than an oversight — see *Limits* below.

### 5. Storage and retention

Two tiers, which is what makes aggressive retention painless:

- **Detailed spans** (per window, with fingerprints) — sensitive, expire after
  90 days by default, titles after 30.
- **Daily rollups** (seconds per day/app/category) — no free text at all, kept
  indefinitely.

So you keep years of "how much time did I spend on data entry in Q2?" while the
per-window detail of any given Tuesday has long since been deleted.

## Your data is yours

```bash
copynion export                      # JSON, no titles
copynion export --format csv -o f.csv
copynion export --include-titles     # decrypts; warns you on stderr
copynion purge --titles-only         # forget every title, keep statistics
copynion purge --app slack           # forget one application
copynion purge --before 2026-01-01
copynion purge --all                 # everything, unrecoverably
copynion audit                       # what Copynion did, and when it stopped watching
```

Destructive commands require typed confirmation, and refuse to run at all in a
non-interactive shell unless given `--yes`.

## How the guarantees are enforced

Claims in a README are worth nothing; these are enforced in the test suite.

- `tests/test_no_network.py` parses every source file's AST and fails if any
  networking module is imported, fails if importing the package pulls in a
  socket stack, and fails on any hardcoded URL. **This is what makes "your data
  stays local" a fact rather than a promise.**
- `test_titles_are_not_readable_without_the_key` writes a sensitive title, then
  greps the raw database file for it.
- `test_vault_fails_closed_without_a_key` pins the fail-closed behaviour.
- `test_default_policy_withholds_titles` pins the conservative default.
- `test_titles_are_redacted_before_they_are_sealed` pins the pipeline ordering.
- `test_purge_refuses_without_confirmation_when_not_a_tty` pins the guard rails.

## Limits — what this does *not* protect you against

Stated plainly, because a privacy document that only lists strengths is
marketing.

1. **An attacker with your logged-in user account.** The key is readable by your
   own user — it has to be, for the daemon to work unattended. Copynion protects
   against a stolen disk, a backup leaking, a snooping second user on the
   machine, and casual inspection. It does not protect against malware running
   as you.
2. **Application names are plaintext.** "You used `tinder` for two hours" is
   visible to anyone who can read the database file, even though every title is
   sealed. If an app's *name* is the sensitive part, set it to `drop`.
3. **Fingerprints leak repetition structure.** An attacker with the database but
   not the key can see that you returned to the same unnamed window 40 times.
   They cannot learn which window.
4. **Redaction is pattern-based, so it is not perfect.** It cannot know that
   "Meeting with Jan about the layoffs" is sensitive. Use `title_denylist`, or
   keep the app at `app_only`, for anything that matters.
5. **Timing metadata is inherently revealing.** Even with every title dropped,
   "laptop active 02:00–04:00 most nights" is information about you. There is no
   way to have usage statistics without this; be aware it is the actual cost.
6. **Wayland.** Most Wayland compositors will not tell an ordinary application
   which window has focus. The workarounds involve installing a shell extension
   that can read considerably more than Copynion should ever ask for, so
   Copynion does not do that. It records the time as `opaque` and tells you in
   `doctor`. This is an honest limitation, not a missing feature.
7. **Copynion does not encrypt its own statistics.** Rollups are plaintext.

## If you are considering this for employees

Don't, without their informed consent — and note that this design actively works
against surveillance use: everything is stored locally under the user's own
account and key, there is no export-to-employer path, and no network code. That
is a deliberate architectural choice, not an accident. In many jurisdictions
(GDPR among them) monitoring of this kind carries legal obligations around
notice, proportionality and purpose limitation that a tool cannot discharge for
you.
