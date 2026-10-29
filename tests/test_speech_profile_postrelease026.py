"""ASR and visual stages must not silently mix independently edited profiles."""
import json
import hashlib
import threading
import time
import unittest
from unittest import mock

import test_speech as speech_fixture
import test_video_nodes as fixture
import test_video_stream as dense
import test_nodes as context

core, nodes, video = fixture.core, fixture.nodes, fixture.video
speech = speech_fixture.speech


class SpeechProfileLease(unittest.TestCase):
    setUp = fixture.VideoLease.setUp
    client = dense.DenseVideo.client

    def assert_profile_save_waits_for_speech(self, run):
        entered, release, saved, saving = (threading.Event() for _ in range(4))
        errors = []

        def prepare(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('speech fixture was not released')
            return None

        def execute():
            try:
                run()
            except BaseException as error:
                errors.append(error)

        def save():
            try:
                changed = core.read_profile(self.connection.profile)
                changed['timeout_s'] = 120
                saving.set()
                core.save_profile(self.connection.profile, changed)
                saved.set()
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(speech, 'prepare', side_effect=prepare):
            worker = threading.Thread(target=execute)
            writer = threading.Thread(target=save)
            worker.start()
            try:
                self.assertTrue(entered.wait(3), errors)
                writer.start()
                self.assertTrue(saving.wait(3), errors)
                self.assertFalse(saved.wait(.25), 'profile changed while ASR was using it')
            finally:
                release.set()
                worker.join(5)
                if writer.ident is not None:
                    writer.join(5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(writer.is_alive())
            # A writer can win the gap before the visual stage. The stage must
            # reject that change rather than use two different configurations.
            self.assertTrue(all(isinstance(error, core.StrataError) and 'profile' in str(error)
                                and 'changed' in str(error) for error in errors), errors)
            self.assertTrue(saved.is_set(), errors)

    def test_speech_node_holds_profile_until_asr_finishes(self):
        self.assert_profile_save_waits_for_speech(lambda: nodes.StrataSpeechTranscribe().run(
            self.connection, enable_asr=True, duration_s=1, audio={}))

    def test_full_video_holds_profile_during_asr_without_journal(self):
        client, _ = self.client()
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup'):
            self.assert_profile_save_waits_for_speech(lambda: nodes.StrataVideoAnalyzeFull().run(
                self.connection, images=fixture.images(24), enable_asr=True, duration_s=1,
                sampling_fps=2, max_tokens=512))

    def test_profile_change_between_speech_and_visual_stops_before_network(self):
        generate = core.generate_video_pipeline

        def changed_before_visual(*args, **kwargs):
            changed = core.read_profile(self.connection.profile)
            changed['url'] = 'http://127.0.0.1:19876'
            core.save_profile(self.connection.profile, changed)
            return generate(*args, **kwargs)

        client, _ = self.client()
        with mock.patch.object(speech, 'prepare', return_value=None), \
                mock.patch.object(core, 'Client', return_value=client), \
                mock.patch.object(core, 'cleanup'), \
                mock.patch.object(core, 'generate_video_pipeline', side_effect=changed_before_visual):
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed'):
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(24),
                    enable_asr=True, duration_s=1, sampling_fps=2, max_tokens=512)
        client.request.assert_not_called()

    def test_disabled_and_manual_speech_do_not_take_a_service_profile_lease(self):
        with mock.patch.object(core, 'profile_lock', side_effect=AssertionError('unnecessary service lease')):
            self.assertEqual(nodes.StrataSpeechTranscribe().run(self.connection), ('', '', ''))
            result = nodes.StrataSpeechTranscribe().run(self.connection,
                enable_asr=True, timed_transcript=json.dumps(speech_fixture.timed()))
        self.assertEqual(result[0], '向右移动')

    def test_credentials_change_after_speech_rejects_before_client_or_managed_start(self):
        generate = core.generate_video_pipeline
        profile = core.read_profile('test')
        profile.update(mode='managed', runtime=self.directory.name, data_dir=self.directory.name)
        core.save_profile('test', profile)

        def changed_before_visual(*args, **kwargs):
            changed = core.read_profile('test')
            changed['api_key'] = 'new-secret-must-not-appear-in-error'
            core.save_profile('test', changed)
            return generate(*args, **kwargs)

        with mock.patch.object(speech, 'prepare', return_value=None), \
                mock.patch.object(core, 'Client') as client, mock.patch.object(core, 'Managed') as manager, \
                mock.patch.object(core, 'generate_video_pipeline', side_effect=changed_before_visual):
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed') as failure:
                nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(24),
                    enable_asr=True, duration_s=1, sampling_fps=2, max_tokens=512)
        self.assertNotIn('new-secret', str(failure.exception))
        client.assert_not_called()
        manager.assert_not_called()

    def test_cancellation_releases_profile_and_endpoint_leases(self):
        for full_video in (False, True):
            client = speech_fixture.SpeechMediaAndTransport.client(self)
            response = client.request.side_effect

            def cancelled(path, *args, **kwargs):
                if path == '/v1/audio/transcribe':
                    raise InterruptedError('cancelled during ASR')
                return response(path, *args, **kwargs)

            client.request.side_effect = cancelled
            with self.subTest(full_video=full_video), mock.patch.object(core, 'Client', return_value=client):
                with self.assertRaisesRegex(InterruptedError, 'during ASR'):
                    kwargs = dict(enable_asr=True, duration_s=1,
                                  audio=speech_fixture.SpeechMediaAndTransport.audio())
                    if full_video:
                        nodes.StrataVideoAnalyzeFull().run(self.connection, images=fixture.images(24),
                            sampling_fps=2, max_tokens=512, **kwargs)
                    else:
                        nodes.StrataSpeechTranscribe().run(self.connection, **kwargs)
            deadline = time.monotonic()+1

            def check_unlocked():
                if time.monotonic() >= deadline:
                    raise AssertionError('ASR cancellation left a transaction locked')

            profile = core.read_profile('test')
            with core.profile_lock('test', check=check_unlocked), core.service_lock(profile, check=check_unlocked):
                pass


class SpeechManagedPreparation(unittest.TestCase):
    setUp = fixture.VideoLease.setUp

    def manager(self, same_gpu=True):
        self.temp = self.directory
        manager = context.OwnedProcesses.prepared_manager(self)
        profile = dict(manager.profile, mode='managed', same_gpu=same_gpu)
        core.save_profile('test', profile)
        manager.profile = core.read_profile('test')
        return manager

    def run_speech(self):
        return nodes.StrataSpeechTranscribe().run(self.connection, enable_asr=True,
            duration_s=1, audio=speech_fixture.SpeechMediaAndTransport.audio())

    def test_first_managed_speech_handoff_precedes_configuration_process(self):
        manager = self.manager()
        sequence = []

        def spawn(*args, **kwargs):
            sequence.append('spawn')
            raise OSError('fixture stops before real process startup')

        with mock.patch.object(core, 'Managed', return_value=manager), \
                mock.patch.object(core, 'gpu_handoff', side_effect=lambda profile: sequence.append('handoff')), \
                mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(OSError, 'fixture stops'):
                self.run_speech()
        self.assertEqual(sequence, ['handoff', 'spawn'])

    def test_cancelled_preparation_handoff_never_starts_process_or_sends_asr(self):
        manager = self.manager()
        client = speech_fixture.SpeechMediaAndTransport.client(self)
        with mock.patch.object(core, 'Managed', return_value=manager), \
                mock.patch.object(core, 'Client', return_value=client), \
                mock.patch.object(core, 'gpu_handoff', side_effect=InterruptedError('cancelled before setup')), \
                mock.patch.object(core.socket, 'socket'), \
                mock.patch.object(core.subprocess, 'Popen', side_effect=AssertionError('process started after cancellation')) as process:
            with self.assertRaisesRegex(InterruptedError, 'before setup'):
                self.run_speech()
        process.assert_not_called()
        client.request.assert_not_called()

    def test_separate_gpu_preparation_does_not_unload_comfy(self):
        manager = self.manager(same_gpu=False)
        with mock.patch.object(core, 'Managed', return_value=manager), \
                mock.patch.object(core, 'gpu_handoff') as handoff, \
                mock.patch.object(core.socket, 'socket'), \
                mock.patch.object(core.subprocess, 'Popen', side_effect=OSError('fixture stops before real process startup')):
            with self.assertRaisesRegex(OSError, 'fixture stops'):
                self.run_speech()
        handoff.assert_not_called()

    def test_resident_managed_cpu_speech_does_not_repeat_handoff(self):
        manager = self.manager()
        startup = {key: manager.profile.get(key) for key in ('runtime','data_dir','port','context','vision','api_key')}
        startup['runtime_version'] = manager.runtime_version()
        manager.state_path.write_text(json.dumps({
            'instance': 'resident-asr', 'fingerprint': hashlib.sha256(json.dumps(startup, sort_keys=True).encode()).hexdigest()
        }), encoding='utf-8')
        self.status['instance_id'] = 'resident-asr'
        client = speech_fixture.SpeechMediaAndTransport.client(self)
        process = mock.Mock()
        process.is_running.return_value = True
        with mock.patch.object(core, 'Managed', return_value=manager), \
                mock.patch.object(manager, 'owned', return_value=process), \
                mock.patch.object(core, 'Client', return_value=client), \
                mock.patch.object(core, 'gpu_handoff') as handoff, mock.patch.object(core.subprocess, 'Popen') as spawn:
            result = self.run_speech()
        self.assertEqual(result[0], '向右移动')
        handoff.assert_not_called()
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
