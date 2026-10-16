import unittest
from datetime import date
from unittest.mock import patch

from hub_v2 import scheduled, weekly_packet, weekly_preflight


class NextMondayTests(unittest.TestCase):
    def test_thursday_gives_the_coming_monday(self):
        self.assertEqual(scheduled.next_monday(date(2026, 10, 15)), date(2026, 10, 19))

    def test_monday_gives_the_following_monday(self):
        self.assertEqual(scheduled.next_monday(date(2026, 10, 19)), date(2026, 10, 26))

    def test_sunday_gives_the_next_day(self):
        self.assertEqual(scheduled.next_monday(date(2026, 10, 18)), date(2026, 10, 19))


@patch('hub_v2.scheduled.section_counts', return_value={})
@patch('hub_v2.scheduled.load_agenda', return_value={})
@patch('hub_v2.scheduled.google.service_token', return_value='token')
class WeeklyPreflightJobTests(unittest.TestCase):
    today = date(2026, 10, 15)

    def test_checks_then_rebuilds_the_packet_for_next_monday(self, token, agenda, counts):
        with patch.object(weekly_preflight, 'run') as run, patch.object(weekly_packet, 'build') as build:
            result = scheduled.weekly_preflight_job({}, today=self.today)
        self.assertEqual(run.call_args.args[3:5], ('2026-10-19', 1))
        self.assertEqual(build.call_args.args[3:5], ('2026-10-19', 1))
        self.assertEqual(result['status'], 'checked; packet rebuilt')

    def test_a_held_check_skips_the_packet(self, token, agenda, counts):
        with patch.object(weekly_preflight, 'run', side_effect=weekly_preflight.Held('agenda busy')), \
                patch.object(weekly_packet, 'build') as build:
            result = scheduled.weekly_preflight_job({}, today=self.today)
        build.assert_not_called()
        self.assertEqual(result['status'], 'held')

    def test_unknown_job_is_refused(self, token, agenda, counts):
        self.assertEqual(scheduled.main(['nope']), 64)


if __name__ == '__main__':
    unittest.main()
