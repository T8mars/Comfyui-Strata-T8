"""Twenty independent duration, sparse-frame and speech-clock regression cases."""
import io
import json
import types
import unittest
from unittest import mock

import numpy as np
import test_video_nodes as fixture
import test_speech as speech_fixture

core, nodes, video = fixture.core, fixture.nodes, fixture.video
speech = speech_fixture.speech


class DurationSpeechAudit20(unittest.TestCase):
    setUp = fixture.VideoLease.setUp

    def client(self, *, forged_audio_visual=False):
        client, seen = mock.Mock(), []
        status = dict(self.status)
        status['capabilities'] = {'video': {'contract_version': 1, 'modes': ['frames', 'native']}}
        def response(path, body=None, **kwargs):
            if path == '/v1/status':
                return status
            seen.append(body)
            content = body['messages'][-1]['content']
            value = fixture.result('checked selected window')
            if isinstance(content, list):
                info = json.loads(content[0]['text'])
                frames = info['frames']
                if frames:
                    value['events'] = [{'start_s': frames[0]['timestamp_s'], 'end_s': frames[-1]['timestamp_s'],
                        'description': 'sampled visual change',
                        'evidence_frames': list(dict.fromkeys([frames[0]['index'], frames[-1]['index']]))}]
                parts = [part['text'] for part in content if part.get('text', '').startswith('Timed dialogue evidence:\n')]
                cues = json.loads(parts[0].split('\n', 1)[1])['cues'] if parts else None
            elif '\n\nTimed dialogue evidence:\n' in content:
                info, dialogue = content.split('\n\nTimed dialogue evidence:\n', 1)
                info = json.loads(info)
                self.assertEqual(info['visual_status'], 'no_source_frames')
                self.assertEqual(info['frames'], [])
                cues = json.loads(dialogue)['cues']
                if forged_audio_visual:
                    value['shots'] = ['invented empty-window camera pan']
            else:
                info = json.loads(content)
                frames = info['evidence_frames']
                if frames:
                    value['events'] = [{'start_s': frames[0]['timestamp_s'], 'end_s': frames[-1]['timestamp_s'],
                        'description': 'merged real frames',
                        'evidence_frames': list(dict.fromkeys([frames[0]['index'], frames[-1]['index']]))}]
                cues = info.get('speech_evidence', {}).get('cues')
            if 'dialogue' in body['response_format']['json_schema']['schema']['properties']:
                value['dialogue'] = [{'cue_ids': [cue['id'] for cue in cues], 'observation': 'correlated timed speech'}] if cues else []
            return fixture.completion(value)
        client.request.side_effect = response
        return client, seen

    def dense(self, *, count=288, fps=24, **options):
        client, seen = self.client()
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            output = nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(count), fps=fps,
                max_tokens=512, **options)
        cleanup.assert_called_once()
        return json.loads(output['result'][1]), json.loads(output['result'][2]), seen

    @staticmethod
    def timed(start, end):
        value = speech_fixture.timed()
        value['cues'][0].update(start_s=start, end_s=end)
        return json.dumps(value)

    def test_round01_twelve_seconds_preserves_all_288_source_frames(self):
        result, evidence, _ = self.dense(sampling_fps=24)
        self.assertEqual((result['coverage']['window_end_s'], len(evidence)), (12, 288))
        self.assertAlmostEqual(evidence[-1]['timestamp_s'], 12 - 1 / 24)
        self.assertTrue(result['coverage']['analysis_completed'])

    def test_round02_sampling_density_does_not_redefine_source_duration(self):
        result, evidence, _ = self.dense(sampling_fps=12)
        self.assertEqual((result['coverage']['window_end_s'], len(evidence)), (12, 144))
        self.assertAlmostEqual(evidence[-1]['source_timestamp_s'], 12 - 1 / 12)

    def test_round03_144_frames_at_actual_12_fps_preserves_twelve_seconds(self):
        result, evidence, _ = self.dense(count=144, fps=12, sampling_fps=24)
        self.assertEqual((result['coverage']['window_end_s'], len(evidence)), (12, 144))
        self.assertAlmostEqual(evidence[-1]['source_timestamp_s'], 12 - 1 / 12)

    def test_round04_explicit_six_second_selection_remains_six_seconds(self):
        result, evidence, _ = self.dense(duration_s=6, sampling_fps=24)
        self.assertEqual((result['coverage']['window_end_s'], len(evidence)), (6, 144))

    def test_round05_second_half_has_relative_and_original_clocks(self):
        result, evidence, _ = self.dense(start_s=6, sampling_fps=24)
        self.assertEqual((result['coverage']['window_start_s'], result['coverage']['window_end_s']), (6, 12))
        self.assertEqual((evidence[0]['timestamp_s'], evidence[0]['source_timestamp_s']), (0, 6))
        self.assertAlmostEqual(evidence[-1]['timestamp_s'], 6 - 1 / 24)

    def test_round06_image_source_fps_explains_six_seconds_without_guessing(self):
        result, evidence, _ = self.dense(count=144, fps=24, sampling_fps=24)
        self.assertEqual((result['source']['duration_s'], len(evidence)), (6, 144))
        tooltip = nodes.StrataVideoSample.INPUT_TYPES()['required']['fps'][1]['tooltip']
        self.assertIn('Source IMAGE frame rate', tooltip)

    def test_round07_one_fps_source_scans_all_empty_windows_to_twelve_seconds(self):
        result, evidence, _ = self.dense(count=12, fps=1, sampling_fps=24)
        coverage = result['coverage']
        self.assertEqual((coverage['window_end_s'], len(evidence)), (12, 12))
        self.assertEqual(coverage['completed_windows'], 36)
        self.assertEqual(coverage['empty_segments'], 24)
        self.assertTrue(coverage['analysis_completed'])

    def test_round08_empty_first_window_preserves_selection_and_next_real_frame(self):
        result, evidence, _ = self.dense(count=3, fps=1, sampling_fps=24, start_s=.2, duration_s=1.6)
        self.assertEqual([frame['source_timestamp_s'] for frame in evidence], [1])
        self.assertAlmostEqual(evidence[0]['timestamp_s'], .8)
        self.assertAlmostEqual(result['coverage']['window_end_s'], 1.8)
        self.assertTrue(result['coverage']['analysis_completed'])

    def test_round09_empty_tail_is_recorded_without_reusing_last_picture(self):
        result, evidence, _ = self.dense(count=2, fps=1, sampling_fps=24, duration_s=1.6)
        self.assertEqual([frame['source_timestamp_s'] for frame in evidence], [0, 1])
        self.assertAlmostEqual(result['coverage']['empty_windows'][-1]['end_s'], 1.6)

    def test_round10_all_empty_visual_selection_fails_before_service_lease(self):
        with mock.patch.object(core, 'Client') as client:
            with self.assertRaisesRegex(core.StrataError, 'no source frames'):
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(2), fps=1,
                    start_s=.1, duration_s=.1, sampling_fps=24)
        client.assert_not_called()

    def test_round11_middle_audio_only_gap_preserves_speech_and_empty_visual_scope(self):
        result, _, seen = self.dense(count=2, fps=1, duration_s=2, sampling_fps=24,
            timed_transcript=self.timed(.4, .5))
        pure = [request for request in seen if isinstance(request['messages'][-1]['content'], str)
            and '\n\nTimed dialogue evidence:\n' in request['messages'][-1]['content']]
        self.assertEqual(len(pure), 1)
        self.assertIn('NO source presentation frames', pure[0]['messages'][0]['content'])
        self.assertEqual(result['analysis']['dialogue'][0]['cue_ids'], ['u1'])

    def test_round12_first_audio_only_gap_keeps_the_real_input_clock(self):
        result, _, _ = self.dense(count=2, fps=1, start_s=.2, duration_s=1, sampling_fps=24,
            timed_transcript=self.timed(.25, .3))
        self.assertEqual(result['analysis']['dialogue'][0]['cue_ids'], ['u1'])
        self.assertEqual(result['segments'][0]['events'], [])

    def test_round13_last_audio_only_gap_survives_final_merge(self):
        result, _, seen = self.dense(count=2, fps=1, duration_s=2, sampling_fps=24,
            timed_transcript=self.timed(1.7, 1.8))
        self.assertEqual(result['segments'][-1]['events'], [])
        self.assertEqual(result['analysis']['dialogue'][0]['cue_ids'], ['u1'])
        merges = [json.loads(request['messages'][-1]['content']) for request in seen
            if isinstance(request['messages'][-1]['content'], str)
            and '\n\nTimed dialogue evidence:\n' not in request['messages'][-1]['content']]
        self.assertTrue(any(info['speech_evidence']['cues'] for info in merges))

    def test_round14_empty_window_cannot_fabricate_a_visual_shot(self):
        client, _ = self.client(forged_audio_visual=True)
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(nodes.StructuredOutputError, 'audio-only'):
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(2), fps=1,
                    duration_s=2, sampling_fps=24, timed_transcript=self.timed(.4, .5))
        cleanup.assert_called_once()

    def test_round15_decoder_fault_is_not_misclassified_as_an_empty_window(self):
        client, _ = self.client()
        original, calls = video.sample_input, []
        def sample(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ValueError('corrupted decoder state')
            return original(*args, **kwargs)
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup, \
                mock.patch.object(video, 'sample_input', side_effect=sample):
            with self.assertRaisesRegex(ValueError, 'corrupted decoder'):
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(24), fps=24)
        cleanup.assert_called_once()

    def test_round16_cancel_during_empty_scan_stops_followup_requests(self):
        client, seen = self.client()
        original, calls = video.sample_input, []
        def sample(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 3:
                raise InterruptedError('cancel sparse scan')
            return original(*args, **kwargs)
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup, \
                mock.patch.object(video, 'sample_input', side_effect=sample):
            with self.assertRaisesRegex(InterruptedError, 'cancel sparse'):
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(2), fps=1)
        self.assertEqual(len(seen), 1)
        cleanup.assert_called_once()

    def asr_result(self, offset_ms, start=0, end=1):
        helper = speech_fixture.SpeechMediaAndTransport()
        helper.status = self.status
        client = helper.client(settings={'offset_ms': offset_ms})
        audio = helper.audio(samples=6 * 16000)
        with mock.patch.object(core, 'Client', return_value=client):
            result = speech.prepare(self.connection, audio=audio, enable_asr=True, start_s=start, end_s=end)
        payload = client.request.call_args.args[1]
        self.assertEqual(payload['offset_ms'], 0)
        return result

    def test_round17_positive_manual_offset_is_applied_once_after_wire_bounds_check(self):
        result = self.asr_result(1000)
        self.assertEqual((result['cues'][0]['start_s'], result['cues'][0]['end_s']), (1, 2))

    def test_round18_negative_offset_preserves_preselection_timing_until_fusion(self):
        result = self.asr_result(-500, start=4, end=5)
        self.assertEqual((result['cues'][0]['start_s'], result['cues'][0]['end_s']), (3.5, 4.5))
        overlap = speech.contract.relevant(result, 4, 5)['cues'][0]
        self.assertEqual((overlap['overlap_start_s'], overlap['overlap_end_s']), (4, 4.5))

    def test_round19_negative_offset_clips_actual_input_zero_not_upload_zero(self):
        result = self.asr_result(-500)
        self.assertEqual((result['cues'][0]['start_s'], result['cues'][0]['end_s']), (0, .5))
        self.assertEqual(self.asr_result(-1000)['cues'], [])

    def test_round20_disabled_asr_and_manual_subtitle_still_avoid_native_io(self):
        with mock.patch.object(core, 'read_profile', side_effect=AssertionError('disabled ASR profile access')), \
                mock.patch.object(speech, 'extract_video_audio', side_effect=AssertionError('disabled ASR media decode')):
            self.assertIsNone(speech.prepare(self.connection, enable_asr=False))
            result = speech.prepare(self.connection, timed_transcript=self.timed(11, 12))
        self.assertEqual(result['cues'][0]['end_s'], 12)

    def test_audio_only_selection_with_timed_speech_does_not_require_vision(self):
        self.status['vision']['enabled'] = False
        result, evidence, seen = self.dense(count=2, fps=1, start_s=.1, duration_s=.1, sampling_fps=24,
            timed_transcript=self.timed(.12, .18))
        self.assertEqual(evidence, [])
        self.assertTrue(result['coverage']['analysis_completed'])
        self.assertEqual(result['coverage']['audio_only_segments'], 1)
        self.assertEqual(result['analysis']['events'], [])
        self.assertTrue(all(isinstance(request['messages'][-1]['content'], str) for request in seen))


if __name__ == '__main__':
    unittest.main()
