# Manually reconciled completion evidence

`completion_policy.assess_completion` accepts an optional
`ManualReconciliation` when a reviewer has checked the actual distributed
submission and resolved missing or duplicate marker labels. The default call
retains the existing strict inventory and response checks. This is a pure
Python evidence contract; it does not extract answers, judge prose, connect a
provider, change source decks, or post grades.

The reviewer supplies the requirements for the version the learner received,
the checkpoint, the immutable submission revision, the original readiness
evidence key, and a private review receipt. `requirements_verified` must be
true. Every due question must have exactly one `ManualResponseMapping` with its
own mapping-review reference and a unique inspected response-field reference.
An assessment for a blank field still needs evidence that the field was checked.
Requirements and mappings are retained in the proposed result for later audit.

An explicit map may reconcile legacy marker labels with actual prompt fields.
Several fields carrying the same old label can map to distinct reviewed prompt
IDs. Two mappings for one prompt, one field reused across prompts, a missing
assessment, or a non-unique evidence reference causes a review hold. The code
never chooses between contradictory assessments. The upstream layer must retain
the source evidence and verify that all references belong to the stated revision;
syntactically valid references alone do not establish a real review.

At the initial checkpoint, the manual map covers NOTES only. Missing or
ambiguous future BANK/DRAFT answers do not hold the current score. Their counts
are unassessed (`completed: null`), and their categories are listed as deferred.
At the final checkpoint, the map must cover NOTES, BANK and DRAFT. BANK sequence
evidence is required only for a final checkpoint with one or more required BANK
items. A distributed version with zero BANK items has no invented BANK ordering
requirement. Source structural issues remain in `readiness.original_issues`
even after manual resolution for the due checkpoint.

Writing judgments remain separate. `meaningful_attempt` earns completion credit;
`blank` and `printed_copy` do not; `review` holds the score. Resolving a marker
problem never turns unclear writing into a meaningful attempt. Optional responses
cannot be reclassified as required. Each unique question contributes once.

Scores retain the exact `Fraction` string. For example, six attempts out of
thirteen produce `60/13` out of ten; the floating value is for display and is not
a posting-rounding policy. No prior score, timing, improvement, roster identity,
or destination authorization is inferred. An existing grade cannot be changed
by this function.

Hosts must explicitly pass a reviewed `manual_reconciliation` record. Existing
workspace/due-work/feedback callers continue using their strict path until their
provider integration carries this record through the same source and destination
binding checks. Do not claim that the current deployment automatically performs
manual reconciliation merely because the library tests pass.

Validation uses fictional requirements, responses and evidence keys only:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_completion_policy.py'
```
