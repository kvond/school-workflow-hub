"""Descriptive, paired Biology evidence across lessons; no assessment or writes.

The caller supplies a verified roster and immutable, already-reviewed source
observations. Names, filenames, ordinal averages and changing class totals are
never used to manufacture a longitudinal comparison.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations
from zoneinfo import ZoneInfo

from school_workflow_hub.workflows.understanding_scale import aspect_key

LEVELS = ('Beginning', 'Developing', 'Proficient', 'Extending')
DATE_BASES = ('form_receipt', 'external_submission', 'schoology_submission')
WRITING = {
    'Uses lesson terms accurately to explain an idea': 'vocabulary',
    'Constructs a conceptual answer that explains a relationship or mechanism': 'conceptual',
    'Supports the claim with specific evidence': 'evidence',
    'Explains how the evidence supports the claim': 'reasoning',
}
CLASS_HEADER = ['Section', 'Evidence family', 'Measure', 'Rubric version', 'Field',
                'Earlier lesson', 'Later lesson', 'Current roster n', 'Paired n',
                'Earlier P+ n', 'Later P+ n', 'Earlier P+ / paired', 'Later P+ / paired',
                'Change (percentage points)', 'Higher level n', 'Same level n', 'Lower level n',
                'Not in paired cohort n', 'Earlier original work dates', 'Later original work dates',
                'Assessment dates', 'Interpretation']
STUDENT_HEADER = ['Student', 'Section', 'Evidence family', 'Measure', 'Rubric version', 'Field',
                  'Earlier lesson', 'Earlier work date', 'Earlier level', 'Later lesson',
                  'Later work date', 'Later level', 'Observed change', 'Earlier work', 'Later work',
                  'Earlier excerpt', 'Later excerpt', 'Assessment dates', 'Evidence limit']
FIGURE_HEADER = ['Section / measure / lessons', 'Earlier P+ / paired', 'Later P+ / paired', 'Paired n']
LIMIT_HEADER = ['Evidence family', 'Evidence limit', 'Observation n']
CRITICAL_HEADER = ['Section', 'Lesson', 'Saved assessment dates', 'Current roster n',
                   'Distinct lesson critical aspects', 'Expected student × aspect slots',
                   'Beginning n', 'Developing n', 'Proficient n', 'Extending n',
                   'Evidence unavailable n', 'Not assessed n', 'Rated aspect observations n',
                   'P+ aspect observations n', 'P+ / rated aspects', 'Rated / expected slots',
                   'Interpretation']
CRITICAL_PAIR_HEADER = ['Section', 'Earlier assessed lesson', 'Later assessed lesson',
                        'Paired students n', 'Earlier rated aspect observations n',
                        'Later rated aspect observations n', 'Earlier P+ aspect observations n',
                        'Later P+ aspect observations n', 'Earlier P+ / rated aspects',
                        'Later P+ / rated aspects', 'Change (percentage points)',
                        'Higher attainment fraction n', 'Same attainment fraction n',
                        'Lower attainment fraction n', 'Earlier assessment dates',
                        'Later assessment dates', 'Hub roster n', 'Evidence limit']


class Held(ValueError):
    pass


def _day(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise Held('A comparison needs a verified ISO original-work and assessment date.') from None


def _moment(row):
    """A file timestamp never establishes when a student submitted an answer."""
    if row.get('source_date_basis') not in DATE_BASES:
        return None
    if not row.get('submitted_at'):
        return (_day(row['date']), None)
    try:
        moment = datetime.fromisoformat(row['submitted_at'].replace('Z', '+00:00'))
        if moment.tzinfo is None or moment.astimezone(ZoneInfo('America/New_York')).date() != _day(row['date']):
            raise ValueError()
        return (_day(row['date']), moment.timestamp())
    except (ValueError, TypeError, AttributeError):
        raise Held('The original submission timestamp disagrees with its source date.') from None


def _ordered(left, right):
    a, b = _moment(left), _moment(right)
    return bool(a and b and (a[0] < b[0] or (a[0] == b[0] and a[1] is not None
                                           and b[1] is not None and a[1] < b[1])))


def _metric(row):
    if row.get('critical_aspect'):
        if not aspect_key(row['critical_aspect']):
            raise Held('A conceptual observation needs its exact teacher critical-aspect name.')
        return ('Critical aspect', aspect_key(row['critical_aspect']), row['critical_aspect'])
    dimension = row.get('dimension')
    if dimension not in WRITING:
        raise Held('A writing observation has no canonical science-writing dimension.')
    return ('Science writing', WRITING[dimension], dimension)


def _selected(observations, people):
    chosen, limits = {}, Counter()
    for original in observations:
        row = dict(original)
        family, metric, label = _metric(row)
        person = people.get(row.get('student_key'))
        if person is None:
            limits[family, 'Outside the supplied current roster; historical evidence retained'] += 1
            continue
        if row.get('class_key', 'biology') != 'biology' or (row.get('section') and row['section'] != person['section']):
            raise Held('An observation belongs to a different class or section; do not combine it.')
        if row.get('identity_verified') is False or row.get('status') in ('held', 'identity_unverified'):
            limits[family, 'Identity or source verification unavailable; no longitudinal join'] += 1
            continue
        if not row.get('section') or not row.get('rubric_version') or not row.get('source_file_id') or not row.get('source_revision'):
            limits[family, 'Section, rubric version or original source revision unavailable; no longitudinal join'] += 1
            continue
        if family == 'Critical aspect' and row['rubric_version'] != 'critical-aspect-4/v1':
            limits[family, 'Legacy critical-aspect rubric retained separately; no four-level growth line'] += 1
            continue
        if row.get('stage') not in ('NOTES', 'DRAFT', 'BANK', 'revised', 'first'):
            limits[family, 'Answer field / stage unavailable; no longitudinal join'] += 1
            continue
        if not row.get('lesson_key') or not row.get('lesson'):
            raise Held('A comparison needs the verified lesson identity and teacher lesson title.')
        _day(row.get('date')); assessed = _day(row.get('assessed_at'))
        if _day(row['date']) > assessed:
            raise Held('A report assessment precedes its claimed original work date.')
        if row.get('level') not in LEVELS + ('Evidence unavailable', 'Not assessed'):
            raise Held('A comparison observation has an unsupported rubric level.')
        if row['level'] in LEVELS and (not row.get('quote') or not row.get('source_url')):
            raise Held('A rated comparison needs the actual authored excerpt and original source link.')
        _moment(row)  # Validate original timestamp even if the point is unassessed.
        if row.get('source_date_basis') not in DATE_BASES:
            limits[family, 'Source date is not a verified submission event; descriptive point only'] += 1
        identity = (row['student_key'], row['lesson_key'], family, metric, row['rubric_version'], row['stage'])
        row.update(family=family, metric=metric, label=label)
        prior = chosen.get(identity)
        if prior is None or _ordered(prior, row):
            chosen[identity] = row
        elif _ordered(row, prior):
            continue
        elif row != prior:
            same = row['source_file_id'] == prior['source_file_id'] and row['date'] == prior['date']
            versions = str(row['source_revision']), str(prior['source_revision'])
            if same and all(v.isdecimal() for v in versions) and versions[0] != versions[1]:
                chosen[identity] = max((row, prior), key=lambda x: int(x['source_revision']))
            else:
                raise Held('Two source observations cannot be ordered for one learner, lesson, rubric and field.')
    return list(chosen.values()), limits


def _range(rows, field):
    values = sorted({r[field] for r in rows})
    return values[0] if len(values) == 1 else values[0] + ' to ' + values[-1] if values else ''


def build(observations, active_students):
    """Compare identical measures, rubric versions and fields within each section.

    Every percentage uses the same paired students. Actual event order, rather
    than lesson-title order, determines earlier/later. Missing evidence, dates
    without Form receipts, equal-day ties and unversioned legacy records remain
    explicit N/A. Levels are ordinal: only transitions and P+ counts are shown.
    """
    if (not active_students or len(active_students) > 200 or len(observations) > 20000
            or any(not p.get('student_key') or not p.get('section') or not p.get('name') for p in active_students)
            or len({p['student_key'] for p in active_students}) != len(active_students)):
        raise Held('Growth reporting needs one bounded, uniquely verified Biology roster.')
    people = {p['student_key']: p for p in active_students}
    selected, limits = _selected(observations, people)
    grouped = defaultdict(list)
    for row in selected:
        grouped[row['section'], row['family'], row['metric'], row['rubric_version'], row['stage']].append(row)
    class_rows, student_rows, figures = [], [], []
    for group, rows in sorted(grouped.items()):
        section, family, metric, rubric, stage = group
        cohort = sorted((p for p in active_students if p['section'] == section), key=lambda p: p['name'])
        labels = {r['lesson_key']: r['lesson'] for r in rows}
        for lesson_a, lesson_b in combinations(sorted(labels), 2):
            lookup = {(r['student_key'], r['lesson_key']): r for r in rows}
            # Both source-event directions are possible for late submissions.
            pairs = defaultdict(list)
            unpaired = []
            for student in cohort:
                left, right = lookup.get((student['student_key'], lesson_a)), lookup.get((student['student_key'], lesson_b))
                reason = ''
                if not left or not right:
                    reason = 'Insufficient evidence: one or both lessons not assessed'
                elif left['level'] not in LEVELS or right['level'] not in LEVELS:
                    reason = 'Insufficient evidence: one or both observations unrated'
                elif left['source_file_id'] == right['source_file_id']:
                    reason = 'Insufficient evidence: same original source is not another lesson observation'
                elif _ordered(left, right):
                    pairs[lesson_a, lesson_b].append((student, left, right))
                elif _ordered(right, left):
                    pairs[lesson_b, lesson_a].append((student, right, left))
                else:
                    reason = 'Insufficient evidence: original submission order unverified'
                if reason:
                    unpaired.append((student, left, right, reason))
            if not pairs:
                pairs[lesson_a, lesson_b] = []
            for (earlier, later), paired in sorted(pairs.items()):
                before = [a for _, a, _ in paired]; after = [b for _, _, b in paired]
                n = len(paired); aplus = sum(r['level'] in LEVELS[2:] for r in before)
                bplus = sum(r['level'] in LEVELS[2:] for r in after)
                changes = Counter('Higher' if LEVELS.index(b['level']) > LEVELS.index(a['level']) else
                                  'Lower' if LEVELS.index(b['level']) < LEVELS.index(a['level']) else 'Same'
                                  for _, a, b in paired)
                label = rows[0]['label']
                interpretation = ('Observed change across different lesson tasks; same rubric, field and paired students. '
                                  'Teacher review; no causal or completion-grade claim.') if n else 'N/A: insufficient comparable dated evidence.'
                values = [section, family, label, rubric, stage, labels[earlier], labels[later], len(cohort), n,
                          aplus if n else 'N/A', bplus if n else 'N/A', aplus/n if n else 'N/A',
                          bplus/n if n else 'N/A', (bplus-aplus)*100/n if n else 'N/A',
                          changes['Higher'], changes['Same'], changes['Lower'], len(cohort)-n,
                          _range(before, 'date'), _range(after, 'date'), _range(before+after, 'assessed_at'), interpretation]
                class_rows.append(values)
                if n:
                    figures.append([section + ' | ' + metric + ' | ' + stage + ' | ' + labels[earlier] + ' → ' + labels[later] + ' | paired n=' + str(n), aplus/n, bplus/n, n])
                for student, a, b in paired:
                    change = 'Higher' if LEVELS.index(b['level']) > LEVELS.index(a['level']) else 'Lower' if LEVELS.index(b['level']) < LEVELS.index(a['level']) else 'Same'
                    student_rows.append([student['name'], section, family, label, rubric, stage,
                                         a['lesson'], a['date'], a['level'], b['lesson'], b['date'], b['level'], change,
                                         a['source_url'], b['source_url'], a['quote'], b['quote'],
                                         _range([a,b], 'assessed_at'), 'Observed change in submitted work; different lesson tasks.'])
            for student, a, b, reason in unpaired:
                student_rows.append([student['name'], section, family, rows[0]['label'], rubric, stage,
                                     labels[lesson_a], a['date'] if a else '', a['level'] if a else 'Not assessed',
                                     labels[lesson_b], b['date'] if b else '', b['level'] if b else 'Not assessed', 'N/A',
                                     a.get('source_url', '') if a else '', b.get('source_url', '') if b else '',
                                     a.get('quote', '') if a else '', b.get('quote', '') if b else '',
                                     _range([r for r in (a,b) if r], 'assessed_at'), reason])
    if not any(r['family'] == 'Critical aspect' for r in selected):
        limits['Critical aspect', 'N/A: no verified identity / rubric / original-date critical-aspect history supplied'] += 0
    return {'schema': 'biology-longitudinal/v1', 'class_key': 'biology',
            'tables': {'Across lessons': [CLASS_HEADER] + class_rows,
                       'Student growth': [STUDENT_HEADER] + student_rows,
                       'Growth figure data': [FIGURE_HEADER] + figures,
                       'Growth evidence limits': [LIMIT_HEADER] + [[family, reason, n] for (family, reason), n in sorted(limits.items())]},
            'paired_comparisons': sum(r[8] > 0 for r in class_rows), 'model_calls': 0,
            'grade_writes': 0, 'source_writes': 0,
            'interpretation': 'Descriptive observed change. Different lesson tasks; no ordinal mean or inferred missing score.'}


def critical_attainment(observations, active_students):
    """Preserve saved per-lesson categories without pretending aspects recur.

    These are discrete curriculum comparisons, not a same-concept growth line.
    Older category counts can be displayed as saved without recoding legacy
    rubrics. A separately verified student key and section are mandatory.
    """
    people = {p['student_key']: p for p in active_students}
    if not people or len(people) != len(active_students):
        raise Held('Critical-aspect attainment needs a unique verified roster.')
    grouped, seen = defaultdict(list), set()
    for row in observations:
        student = people.get(row.get('student_key'))
        if student is None:
            continue
        if row.get('class_key', 'biology') != 'biology' or row.get('section') != student['section']:
            raise Held('A saved conceptual category belongs to another class or section.')
        if not row.get('lesson_key') or not row.get('lesson') or not row.get('critical_aspect'):
            raise Held('Saved conceptual categories need exact teacher lesson and aspect identities.')
        if row.get('level') not in LEVELS + ('Evidence unavailable', 'Not assessed'):
            raise Held('A saved category cannot be converted to the current four-level scale.')
        if row.get('assessed_at'):
            _day(row['assessed_at'])
        elif row['level'] in LEVELS:
            raise Held('A rated conceptual category needs its actual saved assessment date.')
        ident = (row['student_key'], row['lesson_key'], aspect_key(row['critical_aspect']))
        if ident in seen:
            raise Held('Repeated saved critical-aspect categories require a verified latest-source selection.')
        seen.add(ident); grouped[row['section'], row['lesson_key'], row['lesson']].append(row)
    output = [CRITICAL_HEADER]
    for (section, _, lesson), rows in sorted(grouped.items()):
        n = sum(p['section'] == section for p in active_students)
        aspects = {aspect_key(r['critical_aspect']) for r in rows}
        expected = n * len(aspects); counts = Counter(r['level'] for r in rows)
        rated = sum(counts[level] for level in LEVELS); pplus = counts['Proficient'] + counts['Extending']
        not_assessed = expected - rated - counts['Evidence unavailable']
        if not_assessed < 0:
            raise Held('Conceptual category counts exceed the verified student × aspect denominator.')
        output.append([section, lesson, _range([r for r in rows if r.get('assessed_at')], 'assessed_at'),
                       n, len(aspects), expected, *[counts[level] for level in LEVELS],
                       counts['Evidence unavailable'], not_assessed, rated, pplus,
                       pplus/rated if rated else 'N/A', rated/expected if expected else 'N/A',
                       'Saved category attainment across different lesson aspects. Aspect counts and assessed students vary; '
                       'not a paired same-concept growth score. Unversioned categories remain saved categories; '
                       'historic missing/unrated categories do not certify located source coverage.'])
    return output


def critical_pairs(observations, active_students):
    """Same verified students, different concepts; order by saved assessment.

    The comparison deliberately reports both aspect-observation denominators.
    It does not equate new concepts, backfill original submission times or claim
    that a later assessment means later-authored work.
    """
    critical_attainment(observations, active_students)  # Identity and duplicate proof.
    people = {p['student_key']: p for p in active_students}
    by_person = defaultdict(lambda: defaultdict(list))
    for row in observations:
        if row.get('student_key') in people:
            by_person[row['student_key']][row['lesson_key']].append(row)
    groups = defaultdict(list)
    individual = [['Student', 'Section', 'Earlier assessed lesson', 'Later assessed lesson',
                   'Earlier P+ n', 'Earlier rated aspects n', 'Later P+ n', 'Later rated aspects n',
                   'Earlier P+ / rated aspects', 'Later P+ / rated aspects',
                   'Change (percentage points)', 'Earlier assessment date', 'Later assessment date', 'Evidence limit']]
    for student_key, lessons in by_person.items():
        person = people[student_key]
        for akey, bkey in combinations(sorted(lessons), 2):
            a, b = lessons[akey], lessons[bkey]
            ar, br = [r for r in a if r['level'] in LEVELS], [r for r in b if r['level'] in LEVELS]
            ad, bd = {r['assessed_at'] for r in ar}, {r['assessed_at'] for r in br}
            if not ar or not br or len(ad) != 1 or len(bd) != 1 or ad == bd:
                continue
            if next(iter(ad)) > next(iter(bd)):
                a, b, ar, br, ad, bd = b, a, br, ar, bd, ad
            ap = sum(r['level'] in LEVELS[2:] for r in ar); bp = sum(r['level'] in LEVELS[2:] for r in br)
            group = (person['section'], a[0]['lesson'], b[0]['lesson'])
            record = (person, ar, br, ap, bp, next(iter(ad)), next(iter(bd)))
            groups[group].append(record)
            individual.append([person['name'], person['section'], a[0]['lesson'], b[0]['lesson'],
                               ap, len(ar), bp, len(br), ap/len(ar), bp/len(br),
                               (bp/len(br)-ap/len(ar))*100, next(iter(ad)), next(iter(bd)),
                               'Different lesson concepts and aspect counts. Dates are saved assessments, '
                               'not verified authorship order; descriptive attainment, not same-concept mastery growth.'])
    summary = [CRITICAL_PAIR_HEADER]
    for (section, earlier, later), pairs in sorted(groups.items()):
        na = sum(len(a) for _,a,_,_,_,_,_ in pairs); nb = sum(len(b) for _,_,b,_,_,_,_ in pairs)
        ap = sum(p for _,_,_,p,_,_,_ in pairs); bp = sum(p for _,_,_,_,p,_,_ in pairs)
        transitions = Counter('Higher' if q/len(b) > p/len(a) else 'Lower' if q/len(b) < p/len(a) else 'Same'
                              for _,a,b,p,q,_,_ in pairs)
        datesa = sorted({ad for *_, ad, bd in pairs}); datesb = sorted({bd for *_, ad, bd in pairs})
        summary.append([section, earlier, later, len(pairs), na, nb, ap, bp, ap/na, bp/nb,
                        (bp/nb-ap/na)*100, transitions['Higher'], transitions['Same'], transitions['Lower'],
                        datesa[0] + (' to '+datesa[-1] if len(datesa)>1 else ''),
                        datesb[0] + (' to '+datesb[-1] if len(datesb)>1 else ''),
                        sum(p['section']==section for p in active_students),
                        'Same students; different concepts and number of rated aspects. Saved assessment order only; '
                        'observed attainment comparison, not repeated mastery of identical concepts.'])
    return summary, individual


def student_series(observations, active_students):
    """Figure blocks for each student, scale and field with real submitted dates.

    Empty cells preserve unassessed gaps. Equal-day lessons without exact Form
    timestamps stay in evidence tables and never enter a connected figure.
    """
    people = {p['student_key']: p for p in active_students}
    selected, _ = _selected(observations, people)
    groups = defaultdict(list)
    for r in selected:
        if r['family'] == 'Science writing' and _moment(r):
            groups[r['student_key'], r['rubric_version'], r['stage']].append(r)
    table = [['Student', 'Vocabulary', 'Conceptual answer', 'Evidence', 'Reasoning', 'Original work date / lesson']]
    blocks = []
    for (person_key, rubric, stage), rows in sorted(groups.items()):
        samples = defaultdict(dict)
        # Include other fields as unassessed gaps, never connect across a
        # different answer field simply because this field has no sample there.
        for r in selected:
            if r['student_key'] == person_key and r['rubric_version'] == rubric and _moment(r):
                samples[r['lesson_key'], r['date'], r.get('submitted_at')]
        for r in rows:
            samples[r['lesson_key'], r['date'], r.get('submitted_at')][r['metric']] = r
        events = [r for r in selected if r['student_key'] == person_key and r['rubric_version'] == rubric and _moment(r)]
        event_map = {(r['lesson_key'], r['date'], r.get('submitted_at')): r for r in events}
        values = list(samples.items())
        ambiguous = {r['date'] for r in events if len({r2['lesson_key'] for r2 in events if r2['date'] == r['date']}) > 1
                     and any(not r2.get('submitted_at') for r2 in events if r2['date'] == r['date'])}
        values = [v for v in values if v[0][1] not in ambiguous]
        values.sort(key=lambda v: (_moment(event_map[v[0]])[0], _moment(event_map[v[0]])[1] or 0))
        if len(values) < 2:
            continue
        start = len(table)
        # Sheets requires every chart source block to be contiguous. Keep the
        # four series headers directly above each student's dated samples.
        table.append(list(table[0]))
        person = people[person_key]
        for event, sample in values:
            row = event_map[event]
            table.append([person['name'],
                          *[LEVELS.index(sample[k]['level'])+1 if k in sample and sample[k]['level'] in LEVELS else ''
                            for k in ('vocabulary', 'conceptual', 'evidence', 'reasoning')],
                          row['date'] + ' | ' + row['lesson']])
        blocks.append({'student_key': person_key, 'title': person['name'] + ' · ' + person['section'] + ' · ' + stage,
                       'rubric_version': rubric, 'start_row': start, 'end_row': len(table)})
    return table, blocks
