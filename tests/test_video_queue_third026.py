"""Clear/profile changes take effect at the lease, after older jobs finish."""
import json
import importlib
import unittest
from unittest import mock

import test_video_progress as fixture

core = fixture.core


class QueuedProgress(unittest.TestCase):
    setUp = fixture.Progress.setUp
    client = fixture.Progress.client
    cached_client = fixture.Progress.cached_client
    run_saved = fixture.Progress.run_saved

    def test_clear_waits_for_the_older_job_final_commit(self):
        client, _ = self.cached_client()
        self.run_saved(client)
        path = next((core.HOME/'video-progress').glob('*.json'))
        old = path.read_bytes()
        generate = core.generate_video_pipeline
        def older_job_finishes(*args, **kwargs):
            # A job already holding the profile lease writes its final stage
            # while this run is queued to acquire that same lease.
            path.write_bytes(old)
            return generate(*args, **kwargs)
        next_client, seen = self.cached_client()
        with mock.patch.object(core, 'generate_video_pipeline', side_effect=older_job_finishes):
            result = self.run_saved(next_client, clear_progress=True)
        self.assertEqual(json.loads(result['result'][4])['checkpoint']['reused_requests'], 0)
        self.assertGreater(len(seen), 0)

    def test_profile_changed_while_queued_rejects_old_context(self):
        generate = core.generate_video_pipeline
        def profile_changes(*args, **kwargs):
            changed = core.read_profile(self.connection.profile)
            changed['url'] = 'http://127.0.0.1:19876'
            core.save_profile(self.connection.profile, changed)
            return generate(*args, **kwargs)
        client, seen = self.cached_client()
        with mock.patch.object(core, 'generate_video_pipeline', side_effect=profile_changes):
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed'):
                self.run_saved(client)
        self.assertEqual(seen, [])

    def test_clear_allows_changed_config_with_explicit_progress_key(self):
        first, _ = self.cached_client()
        self.run_saved(first, progress_key='fixed')
        second, seen = self.cached_client()
        self.run_saved(second, progress_key='fixed', question='Changed question', clear_progress=True)
        self.assertGreater(len(seen), 0)

    def test_changed_managed_profile_is_rejected_before_starting_service(self):
        generate = core.generate_video_pipeline
        def profile_changes(*args, **kwargs):
            core.save_profile(self.connection.profile, {
                'mode':'managed', 'runtime':str(core.HOME/'new-runtime'),
                'data_dir':str(core.HOME/'new-model'), 'port':19876,
                'vision':'gpu', 'same_gpu':False})
            return generate(*args, **kwargs)
        client, _ = self.cached_client()
        manager = mock.Mock()
        manager.ensure.return_value = self.status
        with mock.patch.object(core, 'generate_video_pipeline', side_effect=profile_changes), \
                mock.patch.object(core, 'Managed', return_value=manager):
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed'):
                self.run_saved(client)
        manager.ensure.assert_not_called()
        client.request.assert_not_called()

    def test_profile_change_blocks_optional_speech_before_preparation(self):
        sample = fixture.video.sample_input
        speech = importlib.import_module(core.__package__+'.speech')
        def sample_then_change(*args, **kwargs):
            result = sample(*args, **kwargs)
            changed = core.read_profile(self.connection.profile)
            changed['url'] = 'http://127.0.0.1:19876'
            core.save_profile(self.connection.profile, changed)
            return result
        client, _ = self.cached_client()
        with mock.patch.object(fixture.video, 'sample_input', side_effect=sample_then_change), \
                mock.patch.object(speech, 'prepare', return_value=None) as prepare:
            with self.assertRaisesRegex(core.StrataError, 'profile.*changed'):
                self.run_saved(client, enable_asr=True)
        prepare.assert_not_called()
        client.request.assert_not_called()

    def test_unchanged_speech_profile_releases_lease_for_visual_pipeline(self):
        speech = importlib.import_module(core.__package__+'.speech')
        client, seen = self.cached_client()
        with mock.patch.object(speech, 'prepare', return_value=None) as prepare:
            self.run_saved(client, enable_asr=True)
        prepare.assert_called_once()
        self.assertGreater(len(seen), 0)


if __name__ == '__main__':
    unittest.main()
