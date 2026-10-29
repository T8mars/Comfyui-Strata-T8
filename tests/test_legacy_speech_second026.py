"""Legacy sampled-frame video keeps speech and visuals on one profile identity."""
import unittest
from unittest import mock

import test_speech as speech_fixture
import test_speech_profile_postrelease026 as lease_fixture
import test_video_nodes as fixture

core, nodes, video = fixture.core, fixture.nodes, fixture.video
speech = speech_fixture.speech


class LegacySpeechProfile(unittest.TestCase):
    setUp = fixture.VideoLease.setUp
    assert_profile_save_waits_for_speech = lease_fixture.SpeechProfileLease.assert_profile_save_waits_for_speech

    def frames(self):
        sampled = video.sample_input(images=fixture.images(24), fps=24,
                                     start_s=0, end_s=1, sampling_fps=2, max_frames=8)
        return video.tensor_result(sampled)

    def run_legacy(self, **kwargs):
        frames, manifest = self.frames()
        return nodes.StrataVideoAnalyze().run(self.connection, frames, manifest,
                                             max_tokens=512, **kwargs)

    def test_legacy_video_holds_profile_during_automatic_speech(self):
        client = mock.Mock()
        client.request.side_effect = lambda path, body=None, **kwargs: (self.status if path == '/v1/status'
            else fixture.completion({**fixture.result(), 'dialogue':[]}))
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup'):
            self.assert_profile_save_waits_for_speech(lambda: self.run_legacy(enable_asr=True))

    def test_profile_change_between_speech_and_visual_rejects_before_client(self):
        generate = core.generate_pipeline

        def changed(*args, **kwargs):
            profile = core.read_profile(self.connection.profile)
            profile['api_key'] = 'new-secret-do-not-print'
            core.save_profile(self.connection.profile, profile)
            return generate(*args, **kwargs)

        with mock.patch.object(speech, 'prepare', return_value=None), \
                mock.patch.object(core, 'generate_pipeline', side_effect=changed), \
                mock.patch.object(core, 'Client') as client, mock.patch.object(core, 'Managed') as managed:
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed') as failure:
                self.run_legacy(enable_asr=True)
        self.assertNotIn('new-secret', str(failure.exception))
        client.assert_not_called()
        managed.assert_not_called()

    def test_invalid_image_settings_do_not_start_speech(self):
        for kind in ('pixels', 'dimensions'):
            frames, manifest = self.frames()
            if kind == 'dimensions':
                frames = frames[:, :-1]
            with self.subTest(kind=kind), \
                    mock.patch.object(speech, 'prepare', side_effect=AssertionError('ASR before image validation')) as prepare:
                with self.assertRaises(core.StrataError):
                    nodes.StrataVideoAnalyze().run(self.connection, frames, manifest,
                        enable_asr=True, max_pixels=1 if kind == 'pixels' else 589824)
            prepare.assert_not_called()

    def test_disabled_and_imported_speech_keep_only_the_existing_visual_lease(self):
        for enabled, transcript in ((False, ''), (True, '1\n00:00:00,000 --> 00:00:01,000\n你好\n')):
            client, _ = speech_fixture.SpeechEvidence.audio_client(self)
            # This fixture expects speech evidence; disabled mode needs only an empty valid result.
            if not transcript:
                client.request.side_effect = lambda path, body=None, **kwargs: (self.status if path == '/v1/status'
                    else fixture.completion(fixture.result()))
            with self.subTest(enabled=enabled, transcript=bool(transcript)), \
                    mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup'), \
                    mock.patch.object(core, 'profile_lock', wraps=core.profile_lock) as lock:
                self.run_legacy(enable_asr=enabled, timed_transcript=transcript)
            self.assertEqual(lock.call_count, 1, 'manual/disabled speech took an extra ASR profile lease')

    def test_automatic_audio_remains_correlated_in_legacy_analysis(self):
        visual, _ = speech_fixture.SpeechEvidence.audio_client(self)
        audio = speech_fixture.SpeechMediaAndTransport.client(self)
        response = visual.request.side_effect
        visual.request.side_effect = lambda path, *args, **kwargs: (audio.request(path, *args, **kwargs)
            if path in ('/v1/asr/settings', '/v1/audio/transcribe') else response(path, *args, **kwargs))
        with mock.patch.object(core, 'Client', return_value=visual), mock.patch.object(core, 'cleanup') as cleanup:
            result = self.run_legacy(enable_asr=True, audio=speech_fixture.SpeechMediaAndTransport.audio())
        import json
        value = json.loads(result['result'][1])
        self.assertEqual(value['speech']['cues'][0]['text'], '向右移动')
        self.assertEqual(value['analysis']['dialogue'][0]['cue_ids'], ['u1'])
        cleanup.assert_called_once()


class NegotiatedSpeechDevice(unittest.TestCase):
    setUp = fixture.VideoLease.setUp

    def test_guarded_runtime_receives_the_device_that_was_authorized(self):
        for device in ('cpu', 'cuda'):
            client = speech_fixture.SpeechMediaAndTransport.client(self, settings={'device':device})
            response = client.request.side_effect
            def guarded(path, *args, **kwargs):
                result = response(path, *args, **kwargs)
                if path == '/v1/asr/settings':
                    result['capabilities']['expected_device_guard'] = True
                return result
            client.request.side_effect = guarded
            with self.subTest(device=device), mock.patch.object(core, 'Client', return_value=client), \
                    mock.patch.object(speech, 'authorize_cuda') as authorize:
                speech.prepare(self.connection, audio=speech_fixture.SpeechMediaAndTransport.audio(),
                               start_s=0, end_s=1, enable_asr=True)
            self.assertEqual(client.request.call_args.args[1]['expected_device'], device)
            self.assertEqual(authorize.call_count, int(device == 'cuda'))

    def test_legacy_runtime_keeps_the_original_upload_contract(self):
        client = speech_fixture.SpeechMediaAndTransport.client(self)
        with mock.patch.object(core, 'Client', return_value=client):
            speech.prepare(self.connection, audio=speech_fixture.SpeechMediaAndTransport.audio(),
                           start_s=0, end_s=1, enable_asr=True)
        self.assertNotIn('expected_device', client.request.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
