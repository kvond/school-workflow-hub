# Weekly lesson preflight

The goal is for each week of teaching to arrive ready to run, with the lesson design intact and the
operational pieces already checked.

Every Thursday the preflight reads next week's Agenda rows, week folders, lesson decks, worksheets,
turn-in routing and Schoology. It writes one short page: **only what needs fixing**, followed by the
Monday print packet, the supply list, and paper that could move into the deck.
A clean week produces an almost empty page.

## What it checks

| Area | What it looks for |
|---|---|
| **Calendar** | A lesson on every day each course meets (including the A/B Friday), nothing planned on days it doesn't meet or on closures, plans shifted for a closure that isn't on the calendar, and leftover `NEEDS CONTENT` / `VERIFY DAY` / `Check the day` markers |
| **Links** | Agenda deck, folder, form and worksheet links that don't open, links that point at another week's deck, form or folder, one deck linked for two different weeks, a student link that opens in edit mode, turn-in routing that won't file the work, and leftover or duplicate files in the week folder |
| **Sync** | Schoology items that link a different copy than the Agenda, give students an edit link, are unpublished or due on a closed day, or are missing the week's deck or form. Also deck wording that sends students to another LMS |
| **Variation Theory** | For each `Critical aspect:` block: contrast set → Build a Rule → Pattern break → Getting Started → response slide, present and in order. Also checks for a rule shown before students build it, a Concept Bank that only asks for definitions, a missing teacher note, editing notes visible to students, unfinished edit requests in the speaker notes, a deck still dated for another week, and any VT step that disappeared since the last run |
| **Feedback markers** | Slide-1 inventory vs. the actual `[[NOTES/DRAFT/OPTIONAL/BANK]]` labels (counts and IDs both ways), DRAFT without NOTES, typographic characters inside labels, and lost revision prompts |
| **90-minute flow** | Minutes of student work per `Day N of M` segment (or per deck when it has no day headers) against the block, and days with too many separate activities |
| **Print packet** | Everything that needs paper (deck `TO PRINT` / `PRINT / HAND OUT` sections, `Print …` lines, Worksheets-tab rows, printable files in the week folder, and slides that assume paper), with copy counts, merged into one PDF |
| **Supplies** | Agenda `BUY:` / `PULL:` / `on order` and deck `BUY AHEAD` / `PUT OUT`, grouped into buy, confirm the order arrived, and pull from the lab |
| **Paper → digital** | Worksheets whose questions already exist as typed response boxes, tables that belong on a slide, and slide-ready text with ASCII feedback labels for the rest. Hands-on and lab work stays on paper |

The checks follow the rules in *05 Deck Adaptation and Technical Guide* and *08 AI Guide: Transform a Lesson Deck*.
With `llm_review: true`, Claude on Vertex AI also reads each deck and reports a contrast that now varies more than one
thing, a pattern break that doesn't strain the rule, or a sequence that no longer builds.

The preflight only reads. The only files it writes are its own report and print packet, in the output folder.

## Run it

```bash
pip install -r requirements-dev.txt
python -m pytest                                   # synthetic fixtures, no school data
python -m preflight.cli run                         # next week, live (needs config.yaml + secrets/token.json)
python -m preflight.cli run --week 8 --out out/     # a specific week; also writes preflight.md/.json
python -m preflight.cli run --publish               # also writes the Doc + print packet to Drive
python -m preflight.cli run --snapshot snapshots/wk08 --week 8   # offline replay of a saved week
PYTHONPATH=. python scripts/snapshot_week.py --week 8 --out snapshots/wk08
```

Deployment to Google Cloud (Cloud Run Job, Scheduler, Cloud Build, Secret Manager) is covered in
[deploy/README.md](deploy/README.md).

## The weekly routine

1. **Thursday, 3:47 PM**: the job publishes `Preflight — Wk NN` and `Print packet — Wk NN.pdf`.
2. Fix the **Fix before class** items and send the packet to print.
3. **Sunday, 5:47 PM**: the job runs again and replaces both files, so Monday's page reflects weekend edits.

## Layout

```
preflight/
  sources/   agenda.py (Sheets/xlsx), gdrive.py (Drive + offline snapshot), slides.py (deck text + notes), schoology.py
  checks/    calendar, links, sync, vt, markers, flow, printing, supplies, convert
  baseline.py   VT skeleton per deck between runs (GCS)
  llm.py        optional Claude review on Vertex AI
  report.py     the short page (Markdown / HTML for Docs / JSON)
  output/publish.py   report Doc + merged print-packet PDF
deploy/      setup.sh, README.md
cloudbuild.yaml, Dockerfile
```
