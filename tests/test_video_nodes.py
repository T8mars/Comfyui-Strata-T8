"""CPU sampling, timestamp evidence, capability gates and single-lease video stages."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2

core, nodes = fixture.core, fixture.nodes
video = nodes.video_tools


class Tensor(fixture.Images.Tensor):
    """CPU tensor contract substitute; the separate Comfy probe uses real torch."""
    device = types.SimpleNamespace(type='cpu')
    def __getitem__(self, index): return Tensor(self.array[index], self.transfers)
    def __len__(self): return len(self.array)


def images(count=8):
    import numpy as np
    return Tensor(np.zeros((count, 24, 32, 3), dtype=np.float32))


def cpu_torch():
    import numpy as np
    return types.SimpleNamespace(from_numpy=Tensor, stack=lambda values: Tensor(np.stack([v.array for v in values])))


def result(report='observed', events=None):
    return {'report': report, 'events': events or [], 'shots': [], 'prompt': '', 'limitations': ['Sampled visual evidence only.']}


def completion(value):
    return {'choices': [{'message': {'content': json.dumps(value), 'reasoning_content': ''}}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20}}


class CPUVideo(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(sys.modules, {'torch': cpu_torch()})
        patch.start()
        self.addCleanup(patch.stop)

    def images(self, count=8):
        return images(count)

    def test_shared_copy_matches_runtime_pin(self):
        proof = json.loads((fixture.NODE_ROOT / 'video-shared-source.json').read_text())
        data = (fixture.NODE_ROOT / 'video_shared.py').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), proof['sha256'])
        if fixture.SOURCE_ROOT:
            self.assertEqual(data, (fixture.SOURCE_ROOT / proof['source_path']).read_bytes().replace(b'\r\n', b'\n'))

    def test_tensor_sampling_retains_original_indices_and_marks_estimated_time(self):
        frames, text = nodes.StrataVideoSample().run(images=self.images(16), fps=4, sampling_fps=2, max_frames=4)
        manifest = video.shared().validate_manifest(json.loads(text), 4)
        self.assertEqual(frames.shape, (4, 24, 32, 3))
        self.assertEqual(frames.device.type, 'cpu')
        self.assertEqual([f['source_index'] for f in manifest['frames']], [0, 4, 8, 12])
        self.assertEqual([f['timestamp_s'] for f in manifest['frames']], [0, 1, 2, 3])
        self.assertEqual(manifest['source']['duration_s'], 4)
        self.assertEqual(manifest['coverage']['timestamp_kind'], 'estimated')
        self.assertTrue(manifest['coverage']['frame_cap_applied'])

    def test_provided_vfr_timestamps_and_clip_window_keep_actual_times(self):
        frames, text = nodes.StrataVideoSample().run(images=self.images(5), fps=10,
            timestamps_json='[0,0.1,0.7,1.2,2.4]', start_s=.5, end_s=2.5, sampling_fps=2, max_frames=4)
        manifest = video.shared().validate_manifest(json.loads(text), len(frames))
        self.assertEqual([f['source_timestamp_s'] for f in manifest['frames']], [.7, 1.2, 2.4])
        self.assertEqual([round(f['timestamp_s'], 2) for f in manifest['frames']], [.2, .7, 1.9])
        self.assertEqual(manifest['coverage']['timestamp_kind'], 'provided')

    def test_invalid_inputs_fail_before_copy_or_network(self):
        for options in ({'fps': 0}, {'fps': True}, {'sampling_fps': float('nan')},
                        {'max_frames': 65}, {'end_s': False}, {'timestamps_json': None},
                        {'timestamps_json': '[0,0,1,2,3,4,5,6]'}):
            with self.subTest(options=options), mock.patch.object(video, 'pil_frame') as copied:
                with self.assertRaises((core.StrataError, ValueError)):
                    nodes.StrataVideoSample().run(images=self.images(), **options)
                copied.assert_not_called()

    def test_unknown_video_is_rejected_without_materialization(self):
        obj = mock.Mock()
        with self.assertRaisesRegex(core.StrataError, 'Unsupported VIDEO'):
            nodes.StrataVideoSample().run(video=obj)
        obj.get_components.assert_not_called()
        obj.get_duration.assert_not_called()
        obj.get_stream_source.assert_not_called()

    def test_official_unknown_layout_and_crop_fail_before_getters(self):
        module_name = 'comfy_api.latest._input_impl.video_types'
        file_cls = type('VideoFromFile', (), {'__module__': module_name,
            'get_duration': mock.Mock(), 'get_stream_source': mock.Mock()})
        module = types.SimpleNamespace(VideoFromFile=file_cls)
        cases = ({'_VideoFromFile__file': 'file.mp4', 'future_transform': True},
                 {'_VideoFromFile__file': 'file.mp4', '_VideoFromFile__start_time': 0,
                  '_VideoFromFile__duration': 0, '_VideoFromFile__crop': (True, 0, 1, 1)})
        for state in cases:
            obj = file_cls()
            obj.__dict__.update(state)
            with self.subTest(state=state), mock.patch.dict(sys.modules, {module_name: module}):
                with self.assertRaisesRegex(core.StrataError, 'layout'):
                    nodes.StrataVideoSample().run(video=obj)
        file_cls.get_duration.assert_not_called()
        file_cls.get_stream_source.assert_not_called()

    def test_official_network_source_is_rejected_before_metadata_open(self):
        module_name = 'comfy_api.latest._input_impl.video_types'
        file_cls = type('VideoFromFile', (), {'__module__': module_name,
            'get_duration': mock.Mock(), 'get_stream_source': lambda self: 'https://example.invalid/video.mp4'})
        obj = file_cls()
        obj.__dict__['_VideoFromFile__file'] = 'https://example.invalid/video.mp4'
        with mock.patch.dict(sys.modules, {module_name: types.SimpleNamespace(VideoFromFile=file_cls)}):
            with self.assertRaisesRegex(core.StrataError, 'local regular file'):
                nodes.StrataVideoSample().run(video=obj)
        file_cls.get_duration.assert_not_called()

    def test_official_file_passes_display_crop_and_original_trim_to_decoder(self):
        from PIL import Image
        module_name = 'comfy_api.latest._input_impl.video_types'
        source = io.BytesIO(b'fixture')
        file_cls = type('VideoFromFile', (), {'__module__': module_name,
            'get_duration': lambda self: 2., 'get_active_trim_window': lambda self: (1., 2.),
            'get_stream_source': lambda self: source})
        obj = file_cls()
        obj.__dict__.update({'_VideoFromFile__file': source, '_VideoFromFile__start_time': 1.,
                            '_VideoFromFile__duration': 2., '_VideoFromFile__crop': (0, 4, 4, 4)})
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4))] * 2, timestamps=[1., 2.],
                                               source_duration_s=4., preselected=True, start_s=1., end_s=3.)
        with mock.patch.dict(sys.modules, {module_name: types.SimpleNamespace(VideoFromFile=file_cls)}), \
             mock.patch.object(video.shared(), 'sample_video', return_value=sampled) as decoder, \
             mock.patch.object(video, 'metadata_guard'):
            _, text = nodes.StrataVideoSample().run(video=obj)
        self.assertEqual(decoder.call_args.args, (source,))
        self.assertEqual(decoder.call_args.kwargs['crop'], (0, 4, 4, 4))
        self.assertEqual(decoder.call_args.kwargs['start_s'], 1.)
        self.assertEqual(decoder.call_args.kwargs['end_s'], 3.)
        manifest = video.shared().validate_manifest(json.loads(text), 2)
        self.assertTrue(manifest['source']['source_pts_preserved'])
        self.assertEqual([f['original_media_timestamp_s'] for f in manifest['frames']], [1., 2.])

    def test_unknown_duration_rejects_without_decoding(self):
        stream = types.SimpleNamespace(frames=0, average_rate=24)
        container = mock.MagicMock()
        container.duration = None
        container.streams.video = [stream]
        container.__enter__.return_value = container
        av = types.SimpleNamespace(open=mock.Mock(return_value=container), time_base=1_000_000, FFmpegError=RuntimeError)
        with mock.patch.dict(sys.modules, {'av': av}):
            with self.assertRaisesRegex(core.StrataError, 'metadata is missing'):
                video.metadata_guard(io.BytesIO(b'fixture'))
        container.decode.assert_not_called()
        container.demux.assert_not_called()

    def test_boolean_native_contract_version_is_rejected(self):
        with self.assertRaisesRegex(core.StrataError, 'native video'):
            video.native_capability({'capabilities': {'video': {'contract_version': True, 'modes': ['native']}}})

    def test_trimmed_native_part_uses_clip_origin_and_duration(self):
        from PIL import Image
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4))] * 2,
            timestamps=[3., 4.], time_origin_s=7., source_duration_s=10.,
            preselected=True, start_s=3., end_s=5.)
        part = video.native_part(['data:image/png;base64,a', 'data:image/png;base64,b'],
                                 sampled.manifest['frames'], sampled.manifest)
        self.assertEqual(part['metadata']['time_origin_s'], 10.)
        self.assertEqual(part['metadata']['duration_s'], 2.)
        for frame in part['frames']:
            self.assertEqual(frame['source_timestamp_s'], part['metadata']['time_origin_s'] + frame['timestamp_s'])

    def test_sampling_cancellation_never_reaches_inference(self):
        with mock.patch.object(core, 'interrupted', side_effect=InterruptedError('cancelled')):
            with self.assertRaises(InterruptedError):
                nodes.StrataVideoSample().run(images=self.images())

    def test_analysis_rejects_manifest_count_and_frame_times_before_encoding(self):
        frames, text = nodes.StrataVideoSample().run(images=self.images(4), fps=1)
        original = json.loads(text)
        for change in ('count', 'time', 'duplicate', 'index_budget', 'boolean_contract', 'bad_fps', 'long_window'):
            bad = json.loads(text)
            if change == 'count': bad['frames'].pop()
            elif change == 'time': bad['frames'][0]['timestamp_s'] = 12
            elif change == 'duplicate': bad['frames'][1]['index'] = bad['frames'][0]['index']
            elif change == 'index_budget': bad['frames'][0]['index'] = 64
            elif change == 'boolean_contract': bad['contract_version'] = True
            elif change == 'bad_fps': bad['sampling']['sampling_fps'] = 'invalid'
            else:
                bad['source']['duration_s'] = 3601
                bad['sampling']['end_s'] = 3601
            with self.subTest(change=change), mock.patch.object(core, 'encode_images') as encode:
                with self.assertRaises(core.StrataError):
                    nodes.StrataVideoAnalyze().run(core.Connection('test'), frames, json.dumps(bad))
                encode.assert_not_called()
        self.assertEqual(original['contract_version'], 1)


class VideoLease(unittest.TestCase):
    def setUp(self):
        torch_patch = mock.patch.dict(sys.modules, {'torch': cpu_torch()})
        torch_patch.start()
        self.addCleanup(torch_patch.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.directory.name))
        patch.start()
        self.addCleanup(patch.stop)
        core.save_profile('test', {'mode': 'external', 'url': 'http://127.0.0.1:18402',
                                  'allow_lifecycle': True, 'same_gpu': False})
        self.connection = core.Connection('test')
        self.status = round2.idle_status(vision={'enabled': True})

    def test_dependent_summaries_share_one_client_and_cleanup(self):
        client = mock.Mock()
        client.request.side_effect = [self.status, completion(result('one')), completion(result('two')), completion(result('summary'))]
        calls = []
        def next_stage(answers, status):
            calls.append(len(answers))
            return [nodes.request('merge')] if len(calls) == 1 else None
        with mock.patch.object(core, 'Client', return_value=client) as factory, mock.patch.object(core, 'cleanup') as cleanup:
            answers = core.generate_pipeline(self.connection, [nodes.request('a'), nodes.request('b')], next_stage)
        self.assertEqual(len(answers), 3)
        self.assertEqual(calls, [2, 1])
        factory.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual([c.args[0] for c in client.request.call_args_list],
                         ['/v1/status', '/v1/chat/completions', '/v1/chat/completions', '/v1/chat/completions'])

    def test_bad_static_later_request_fails_before_lease(self):
        with mock.patch.object(core, 'Client') as client:
            with self.assertRaises(core.StrataError):
                core.generate_pipeline(self.connection, [nodes.request('one'), {'messages': []}], lambda *_: None)
            client.assert_not_called()

    def test_summary_failure_and_cancellation_release_once(self):
        for error in (core.StrataError('bad summary'), InterruptedError('cancel summary')):
            client = mock.Mock()
            client.request.side_effect = [self.status, completion(result())]
            with self.subTest(error=type(error).__name__), mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
                def next_stage(*_): raise error
                with self.assertRaises(type(error)):
                    core.generate_pipeline(self.connection, [nodes.request('x')], next_stage)
                cleanup.assert_called_once()

    def test_pipeline_followup_bound_cannot_loop_forever(self):
        client = mock.Mock()
        client.request.side_effect = [self.status, completion(result()), completion(result())]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'follow-up'):
                core.generate_pipeline(self.connection, [nodes.request('a')], lambda *_: [nodes.request('again')], max_followups=1)
            cleanup.assert_called_once()

    def test_native_capability_failure_precedes_handoff_and_cleanup(self):
        client = mock.Mock()
        client.request.return_value = self.status
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'gpu_handoff') as handoff, mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'native video'):
                core.generate_pipeline(self.connection, [nodes.request('x')], lambda *_: None, capability_check=video.native_capability)
            handoff.assert_not_called()
            cleanup.assert_not_called()

    def test_real_node_chunk_requests_and_summary_use_original_evidence(self):
        frames, manifest = nodes.StrataVideoSample().run(images=images(4), fps=1)
        client = mock.Mock()
        seen = []
        def response(path, body=None, **_):
            if path == '/v1/status': return self.status
            seen.append(body)
            content = body['messages'][-1]['content']
            if isinstance(content, list):
                information = json.loads(content[0]['text'])
                span = information['frames']
                events = [{'start_s': span[0]['timestamp_s'], 'end_s': span[-1]['timestamp_s'],
                           'description': 'visible action', 'evidence_frames': [f['index'] for f in span]}]
            else:
                information = json.loads(content)
                events = [event for segment in information['segments'] for event in segment['events']]
            return completion(result('visual report', events))
        client.request.side_effect = response
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            output = nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, frames_per_segment=2, max_tokens=512)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(json.loads(output['result'][1])['segments']), 2)
        self.assertEqual(json.loads(output['result'][4])['requests'], 3)
        cleanup.assert_called_once()
        self.assertEqual([f['index'] for f in json.loads(output['result'][2])], [0, 1, 2, 3])

    def test_model_cannot_invent_evidence_indices(self):
        frames, manifest = nodes.StrataVideoSample().run(images=images(2), fps=1)
        client = mock.Mock()
        client.request.side_effect = [self.status, completion(result(events=[{'start_s': 0, 'end_s': 1,
            'description': 'fabricated', 'evidence_frames': [63]}]))]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(nodes.StructuredOutputError, 'evidence'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest)
            cleanup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
