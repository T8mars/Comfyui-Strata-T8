"""Imported speech timestamps fail as input errors before any service access."""
import json
import unittest
from unittest import mock

import test_speech as fixture

core, nodes = fixture.core, fixture.nodes


class ImportedSpeechNumbers(unittest.TestCase):
    setUp = fixture.SpeechEvidence.setUp

    def imported(self, value):
        with mock.patch.object(core, 'read_profile', side_effect=AssertionError('manual transcript profile I/O')), \
                mock.patch.object(core, 'Client', side_effect=AssertionError('manual transcript service I/O')):
            return nodes.StrataSpeechTranscribe().run(self.connection,
                timed_transcript=json.dumps(value), enable_asr=True)

    def test_unrepresentable_imported_cue_time_is_a_user_error(self):
        for field in ('start_s', 'end_s'):
            for value in (10**400, -10**400):
                data = fixture.timed()
                data['cues'][0][field] = value
                with self.subTest(field=field, sign=value > 0), \
                        self.assertRaisesRegex(core.StrataError, 'finite number'):
                    self.imported(data)

    def test_finite_but_unbounded_confucius_duration_is_a_user_error(self):
        for value in (1e308, -1e308, 3600.00004, -.00004):
            with self.subTest(value=value), self.assertRaisesRegex(core.StrataError, 'audio duration'):
                self.imported({'status':'complete', 'text':'', 'segments':[], 'audio_seconds':value})

    def test_valid_import_preserves_cues_srt_and_service_independence(self):
        result = self.imported(fixture.timed())
        self.assertEqual(result[0], '向右移动')
        self.assertEqual(json.loads(result[1]), fixture.timed())
        self.assertIn('00:00:00,000 --> 00:00:01,000', result[2])
        # Keep the source's existing nearest-sample rounding at the one-hour/zero edges.
        for value in (3600, 3600.00001, -.00001):
            with self.subTest(audio_seconds=value):
                empty = self.imported({'status':'complete', 'text':'', 'segments':[], 'audio_seconds':value})
                self.assertEqual(json.loads(empty[1])['status'], 'no_speech')


if __name__ == '__main__':
    unittest.main()
