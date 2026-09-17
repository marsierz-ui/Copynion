# Does this already exist?

Short answer: **every individual stage exists, in mature products. The
combination — all four stages, for one person, entirely on their own machine —
does not.** Whether that gap is a real opportunity or a warning is discussed at
the end.

Researched September 2026. Sources are linked throughout.

## The landscape, by stage

### Stages 1–2: passive tracking and statistics — a solved problem

This is a crowded, mature category. If all you want is stage 1 and 2, you should
probably use one of these rather than Copynion.

| Tool | Local? | Open source? | Notes |
|------|--------|--------------|-------|
| [**ActivityWatch**](https://activitywatch.net/) | **yes** | **yes** | The closest existing thing. Cross-platform, no cloud, modular "watcher" architecture, local REST API. |
| [RescueTime](https://www.rescuetime.com/) | no | no | Cloud-based, the long-standing commercial incumbent. |
| ManicTime | mostly | no | Strong Windows tool, local storage, optional server. |
| [Rize](https://rize.io/) | no | no | AI categorisation, explicitly markets "no screenshots". |
| Timing (macOS) | mostly | no | Polished, macOS-only. |
| WakaTime | no | partly | Developer-specific (editor plugins, not OS-wide). |
| [Super Productivity](https://super-productivity.com/) | yes | yes | Task-centric rather than passive observation. |

**ActivityWatch is the serious prior art.** It already does local-first automatic
tracking, cross-platform, with data staying on the device. Its documented limits
are the interesting part: categorisation offers [only "Regex" or "No rule"](https://docs.activitywatch.net/en/latest/features/categorization.html),
matching on `app` and `title`; URL matching from the browser watcher is
[not yet supported](https://github.com/ActivityWatch/activitywatch/issues/352);
and it deliberately stops at raw data — no automation analysis, no suggestions.
As one comparison put it, you get the raw activity data and process it yourself.

Its security model is also worth noting as a contrast: the local REST API's
protection is essentially "only listens on localhost", which is fine for a
personal tracker and thinner than what Copynion's threat model assumes.

### Stage 1 at maximum fidelity: screen-recording memory tools

A different bet: record *everything* on screen, OCR it, make it searchable.

- **[Rewind AI](https://www.rewind.ai/)** — was the flagship. **Acquired by Meta
  in December 2025; screen and audio capture was disabled on 19 December 2025.**
  Its original pitch was that your data stays on your device. This is the single
  most instructive data point in this document: a local-first promise backed by
  a company, rather than by an architecture and a licence, lasted until the
  acquisition.
- **Microsoft Recall** — shipped, then withdrawn, then re-shipped. Screenshots
  were initially stored unencrypted and readable by any app; Microsoft fixed
  that, but the code is closed, and researchers extracted encrypted Recall data
  in early 2026.
- **[Screenpipe](https://screenpipe.com/)** — open-source, local, continuous
  screen + audio capture with AI search. The credible successor in this niche.

These make the **opposite privacy trade-off to Copynion**: maximum fidelity
(pixels, OCR, audio) in exchange for maximum capability. They mostly serve
*recall* ("what was that thing I saw on Tuesday?") rather than automation.

### Stages 1–3: enterprise task mining — the real prior art for the idea

This is where "observe someone working, then tell them what to automate" is a
funded, mature product category, and it is worth being clear that **Copynion's
core premise is not novel here**.

- **[Mimica](https://www.mimica.ai/)** — closest to Copynion's stated ambition.
  Its AI observes desktop work for 1–2 weeks, categorises tasks into named
  processes, measures time spent, **determines automatability**, and produces
  process maps with decision points, variants and exceptions. It captures every
  click and keystroke.
- **[UiPath Task Mining / Task Capture](https://www.uipath.com/product/task-capture)** —
  assisted (record a known task) and unassisted (discover tasks from observed
  activity) modes, feeding directly into RPA.
- **Celonis** — process mining from system logs rather than desktop observation;
  complements task mining.
- **Microsoft Power Automate Process Advisor** — deliberately lightweight:
  record tasks, generate a flow map.
- **KYP.ai**, **Nintex/Kryon** — similar space.

**The thing to understand about all of these: the observed person is not the
customer.** These are sold to enterprises, priced accordingly, run against a
central server, and the resulting data belongs to the employer. They capture
keystrokes and screenshots because nobody in that relationship is optimising for
the observed person's privacy.

### Stage 4: execution — exists, but disconnected from observation

- **RPA**: UiPath, Power Automate Desktop, AutoHotkey, Automa — execute reliably,
  but you must author the automation.
- **Computer-use agents**: Claude's computer use, OpenAI's Operator, UI-TARS and
  similar — can drive a GUI from a natural-language instruction. But you *tell*
  them what to do; they carry no history of watching you work.

So stage 4 technology is genuinely available now. What is missing is the link
from "I observed you do this 40 times" to "shall I do it for you?".

## Where the actual gap is

Nobody currently offers **all four stages, to an individual, with the data
staying under that individual's control.**

The category splits cleanly along who the customer is:

- **Consumer local trackers** (ActivityWatch) — your data, your machine, but they
  stop at statistics. No automation analysis by design.
- **Enterprise task mining** (Mimica, UiPath) — does the automation analysis, but
  it is your employer's tool, your employer's server, and it records your
  keystrokes.
- **Screen-memory tools** (Screenpipe, Recall) — maximum observation fidelity,
  aimed at search rather than automation.

Copynion aims at the empty cell: **the analytical ambition of task mining, with
the ownership model of ActivityWatch.**

## The bet Copynion is making — and how it could be wrong

Task-mining vendors capture every click, keystroke and screenshot. Copynion
records only which application and window you were in, for how long. The bet is
that **window-level metadata plus repetition structure is enough to find
automatable work**, without ever recording content.

The argument for: a task you repeat 40 times a week shows up as a repeated
*path* between applications (mail → spreadsheet → mail → ticket). You do not
need to know the invoice number to notice that the routine happens every
morning. This is why stage 2 already computes recurring windows and repeated
app-sequences — see the demo output in the README.

The argument against, stated honestly:

1. **It may not be enough.** Detecting *that* a routine exists is much easier
   than learning *how* to execute it. Stage 4 may genuinely require the
   fidelity Copynion refuses to collect, in which case the honest outcome is a
   tool that suggests automations and hands them to something else — still
   useful, but less than the original ambition.
2. **Titles are doing a lot of work.** Much of the signal lives in window titles,
   which are exactly what the privacy design withholds by default. A user who
   never opts any app into `full` fidelity will get thin repetition data.
3. **The category has a body count.** Rewind was the best-funded attempt at
   consumer-owned always-on observation, and it ended with the capture feature
   switched off by an acquirer. Recall shipped, was withdrawn over security, and
   came back contested. Users are, reasonably, suspicious of this whole idea.
4. **Stages 1–2 alone are not a product.** ActivityWatch already does them well
   and is free. Copynion is only worth building if stages 3 and 4 arrive.

## What to borrow rather than reinvent

- **ActivityWatch's watcher architecture** — separating observation from storage
  is the right decomposition, and it is why it has browser and editor watchers.
  Copynion's `WindowBackend` interface is a deliberately smaller version of the
  same idea.
- **Its export format** — being able to import an existing ActivityWatch history
  would let someone arrive with months of data already collected. Worth doing.
- **Task mining's vocabulary** — "automatability", process variants, exceptions,
  decision points. Stage 3 should not invent its own terms for these.
- **Nothing from Recall.** Its architecture is the cautionary tale.

## Honest recommendation

If you want stages 1 and 2 today, **install ActivityWatch** — it is mature, free
and does that job well. Copynion's stage 1 and 2 exist to be a *correct
foundation for stages 3 and 4* under a stricter privacy model, not to compete on
time-tracking features.

The project is worth continuing if, and only if, stage 3 can demonstrate that
metadata-only observation finds real automation candidates. That is the
experiment, and it should be run before building anything more.
