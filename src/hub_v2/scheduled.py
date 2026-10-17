"""Hub routines run without a browser, for Cloud Run Jobs started by Cloud Scheduler.

The job runs the same code the Hub's buttons run, under the Hub's own service
identity, so there is no teacher sign-in and no AI step. It prints counts and
status words only; nothing from a student record reaches the job log.

    python -m hub_v2.scheduled weekly-preflight
"""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import google, weekly_packet, weekly_preflight
from .app import load_agenda, section_counts

SCHOOL_TZ = ZoneInfo('America/New_York')


def next_monday(today):
    """The Monday after `today`; a Monday gives the following week's Monday."""
    return today + timedelta(days=7 - today.weekday())


def weekly_preflight_job(config, today=None):
    """Thursday run: check next week and rebuild its Monday packet."""
    today = today or datetime.now(SCHOOL_TZ).date()
    start = next_monday(today).isoformat()
    token = google.service_token(config)
    agenda = load_agenda(token, config, fresh=True)
    try:
        weekly_preflight.run(token, config, agenda, start, 1, section_counts(token, config))
    except weekly_preflight.Held as held:
        return {'job': 'weekly-preflight', 'start': start, 'status': 'held', 'reason': str(held)[:200]}
    try:
        weekly_packet.build(token, google.service_token(config, write=True), config, start, 1)
    except weekly_packet.Held as held:
        return {'job': 'weekly-preflight', 'start': start, 'status': 'checked; packet held', 'reason': str(held)[:200]}
    return {'job': 'weekly-preflight', 'start': start, 'status': 'checked; packet rebuilt'}


JOBS = {'weekly-preflight': weekly_preflight_job}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"usage: python -m hub_v2.scheduled {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 64
    print(json.dumps(JOBS[argv[0]](os.environ)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
