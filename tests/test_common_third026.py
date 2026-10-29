"""Third audit: exceptional numeric schemas and final cancellation boundaries."""
import json
import sys
import threading
import unittest
from unittest import mock

import test_nodes as fixture
import test_video_nodes as video_fixture

core, nodes = fixture.core, fixture.nodes


class NumericSchemaFailures(unittest.TestCase):
    def test_float_constraint_overflow_has_the_structured_error_contract(self):
        # This invokes the real jsonschema numeric keyword. It attempts to
        # coerce this valid JSON integer to float for the 0.1 divisor.
        with self.assertRaises(nodes.StructuredOutputError):
            nodes.validated(str(10**1000), {'type':'integer', 'multipleOf':0.1})

    def test_numeric_validation_overflow_can_use_opt_in_repair(self):
        schema = {'type':'object', 'required':['value'],
                  'properties':{'value':{'type':'integer', 'multipleOf':0.1}}}
        replies = [[(json.dumps({'value':10**1000}), '', '{}')], [('{"value":1}', '', '{}')]]
        with mock.patch.object(core, 'generate', side_effect=replies) as generate:
            result = nodes.StrataStructured().run(core.Connection('test'), 'Return a value',
                schema=json.dumps(schema), repair_attempts=1)
        self.assertEqual(json.loads(result[0]), {'value':1})
        self.assertEqual(generate.call_count, 2)

    def test_unconstrained_large_integer_is_preserved(self):
        value = 10**1000
        self.assertEqual(nodes.validated(str(value), {'type':'integer'}), value)
        self.assertEqual(nodes.StrataExtract().run(str(value))[0], str(value))


class SamplingLateCancellation(unittest.TestCase):
    setUp = video_fixture.CPUVideo.setUp

    def test_cancel_during_final_tensor_stack_does_not_publish_sample_output(self):
        cancel = threading.Event()
        torch = sys.modules['torch']
        original = torch.stack
        def stack(values):
            result = original(values)
            cancel.set()
            return result
        def check():
            if cancel.is_set():
                raise InterruptedError('cancelled during final sample conversion')
        with mock.patch.object(torch, 'stack', side_effect=stack), mock.patch.object(core, 'interrupted', side_effect=check):
            with self.assertRaisesRegex(InterruptedError, 'final sample'):
                nodes.StrataVideoSample().run(images=video_fixture.images(2), fps=1)

    def test_cancel_during_manifest_serialization_does_not_publish_sample_output(self):
        video = nodes.video_tools
        sampled = video.sample_input(images=video_fixture.images(2), fps=1)
        cancel = threading.Event()
        original = video.json.dumps
        def dumps(*args, **kwargs):
            result = original(*args, **kwargs)
            cancel.set()
            return result
        def check():
            if cancel.is_set():
                raise InterruptedError('cancelled during manifest serialization')
        with mock.patch.object(video.json, 'dumps', side_effect=dumps), mock.patch.object(core, 'interrupted', side_effect=check):
            with self.assertRaisesRegex(InterruptedError, 'manifest serialization'):
                video.tensor_result(sampled)


class SubtitleLineEndings(unittest.TestCase):
    def check_subtitles(self, header, clock):
        lines = [header, '', '1', f'00:00:01{clock}000 --> 00:00:02{clock}000', '第一句', '',
                 '2', f'00:00:02{clock}500 --> 00:00:03{clock}500', '第二句', '']
        baseline = None
        with mock.patch.object(core, 'read_profile', side_effect=AssertionError('manual subtitles read profile')), \
                mock.patch.object(core, 'Client', side_effect=AssertionError('manual subtitles opened service')):
            for newline in ('\n', '\r\n', '\r'):
                with self.subTest(newline=repr(newline)):
                    result = nodes.StrataSpeechTranscribe().run(core.Connection('test'),
                        timed_transcript=newline.join(lines))
                    value = json.loads(result[1])
                    self.assertEqual([cue['text'] for cue in value['cues']], ['第一句','第二句'])
                    self.assertEqual([(cue['start_s'],cue['end_s']) for cue in value['cues']], [(1,2),(2.5,3.5)])
                    if baseline is None:
                        baseline = result
                    else:
                        self.assertEqual(result, baseline)

    def test_srt_lf_crlf_and_cr_preserve_both_cues_in_the_node(self):
        self.check_subtitles('', ',')

    def test_vtt_lf_crlf_and_cr_preserve_both_cues_in_the_node(self):
        self.check_subtitles('WEBVTT', '.')


if __name__ == '__main__':
    unittest.main()
