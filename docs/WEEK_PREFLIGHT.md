# Weekly teaching preflight

`school_workflow_hub.workflows.week_preflight.run_preflight` checks one
upcoming week before it begins. It outputs only:

1. **Fix before Monday**: actionable exceptions. Each names the lesson, the
   system to change and the smallest action. `?` lines (severity `decide`) are
   teacher choices, not errors.
2. **Monday print/lab packet**: every printed item for the week, ordered by
   the day it is first needed, with copy counts. Copies cover students plus
   10% spares (minimum 2), groups, one per class or one for the teacher.
3. **Supply / grocery list**: merged across lessons by item and unit, scaled
   by students, groups or classes, net of `on_hand`. Grocery items come first.
4. **Ready digital alternatives**: printed worksheets whose items are all
   text responses (`short_text`, `long_text`, `calculation`, `choice`), with
   the original prompts carried over unchanged. The target is the lesson's
   linked Turn-in Form, or slide text boxes when the lesson has no Form.
   Drawings, tables, graphs, diagram labels and manipulatives stay on paper.
   The paper copy stays in the packet until the teacher chooses.

A clean week prints one line, `ready to run`, followed by the packet and list.

```
python3 scripts/week_preflight.py path/to/week.json         # teacher view
python3 scripts/week_preflight.py path/to/week.json --json  # full result
```

Exit codes: 0 means ready, 1 means there are exceptions, 2 means the snapshot
is unusable and nothing was checked.

## What is checked

| Area | Checks |
| --- | --- |
| Sync | The weekly folder, the Agenda (one row per section) and Schoology (one row per section, published) all carry the lesson under the same title. Student-facing links point to the student copy, never the editable deck. Folder deck IDs match the lesson record. Materials are in the folder and posted to Schoology. Entries for lessons outside this week's plan are flagged. |
| Student work | The lesson has a Turn-in Form, the Form accepts responses, and the Agenda and Schoology link that same Form. A Form built for an older deck revision is flagged for a question recheck. |
| Variation Theory | Intended critical aspects must still appear in the deck. Deck aspects the plan never intended are flagged for a decision. Each aspect's question, contrast, rule, pattern break and mastery steps come from `deck_release.validate_release`. A `builds_on` aspect must be introduced by an earlier lesson in `sequence` or listed in `prior_critical_aspects`. |
| 90-minute flow | Every segment is timed. The total may not exceed `class_minutes` or fall more than 10 minutes short. Passive segments (`direct`, `listen`, `lecture`, `video`) may not run past 20 minutes. All five whole-to-whole phases must appear in order. |
| Print/supplies | Printed items need a final `file_revision` and a known per-basis. Supplies need a positive quantity. |

## Snapshot (`week-preflight-input/v1`)

See `tests/fixtures/week_preflight_synthetic.json` for a complete example.
Top level: `week_id`, `week_start` (ISO date), `class_minutes` (default 90),
`sections[]` (`section_id`, `course`, `enrollment`), optional
`prior_critical_aspects[]`, `lessons[]` and
`systems.{folder,agenda,schoology}[]`.

Each lesson carries `lesson_id`, `course`, `sequence`, `title`,
`intended_critical_aspects[]`, `builds_on[]`,
`deck{editable_id, student_copy_id, revision, slides?[]}`,
`flow[]{segment, phase, minutes, mode}`,
`materials[]{material_id, title, kind, print, per, group_size, pages, double_sided, color, file_revision, items[]{prompt, response}}`,
`supplies[]{item, quantity, per, group_size, unit, grocery, on_hand}` and
`turn_in{form_id, accepting_responses, deck_revision}`.

`deck.slides` holds the exported native slide text from
`deck_release.pptx_slide_texts`. Without it, the deck-level Variation Theory
checks are skipped and the sequence check falls back to the intended aspects.

## Boundaries and status

- The engine is pure. It does not read sources, write to any system, edit
  decks, submit Forms or call a model (`external_writes` and
  `live_model_calls` stay 0). It prepares conversions as data. It does not
  create slides or Forms.
- The snapshot must be built by an authorized read-only host reader, or by
  the teacher's supervised browser session. Those readers are not part of
  this module. No real week has been run yet. That run, repairs to whatever it
  misses, and adoption as the standard weekly step remain open (see the
  `week_preflight` capability in `config/capabilities.json`).
