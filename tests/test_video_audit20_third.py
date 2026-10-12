"""Third local video audit: renewed leases, transport and adapter failure boundaries."""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import socketserver
import sys
import threading
import time
import types
import unittest
from unittest import mock

import test_nodes as fixture
import test_video_nodes as vf
import test_audit_round2 as round2

core, nodes = fixture.core, fixture.nodes
video = nodes.video_tools


class VideoAudit20Third(unittest.TestCase):
    setUp = vf.VideoLease.setUp

    def restarted(self, fresh, *, media=True, capability=True):
        old = round2.idle_status(model='old-model', vision={'enabled': True})
        old['loaded'] = True
        old['capabilities'] = {'video': {'contract_version': 1, 'modes': ['native']}}
        manager = mock.Mock()
        manager.ensure.side_effect = [old, fresh]
        client = mock.Mock()
        client.request.return_value = vf.completion(vf.result('complete'))
        request = nodes.request('inspect')
        if media:
            request['messages'][-1]['content'] = [{'type': 'text', 'text': 'inspect'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,a'}}]
        profile = {'mode': 'managed', 'runtime': str(core.HOME), 'url': 'http://127.0.0.1:18092',
                   'allow_lifecycle': True, 'same_gpu': True}
        stack = ExitStack()
        stack.enter_context(mock.patch.object(core, 'read_profile', return_value=profile))
        stack.enter_context(mock.patch.object(core, 'Managed', return_value=manager))
        stack.enter_context(mock.patch.object(core, 'Client', return_value=client))
        cleanup = stack.enter_context(mock.patch.object(core, 'cleanup', side_effect=[True, False]))
        handoff = stack.enter_context(mock.patch.object(core, 'gpu_handoff', return_value=None))
        self.addCleanup(stack.close)
        def run():
            return core.generate_pipeline(self.connection, [request], lambda *_: None,
                capability_check=video.native_capability if capability else None)
        return run, client, cleanup, handoff, manager

    def test_01_restart_busy_and_missing_native_cannot_acquire_a_new_cleanup_lease(self):
        fresh = round2.idle_status(activity={'in_flight': 1}, vision={'enabled': True})
        run, client, cleanup, handoff, _ = self.restarted(fresh)
        with self.assertRaisesRegex(core.StrataError, 'busy'):
            run()
        self.assertEqual(cleanup.call_count, 1)
        client.request.assert_not_called()
        handoff.assert_not_called()

    def test_02_restart_without_vision_fails_before_comfy_offload(self):
        fresh = round2.idle_status(vision={'enabled': False})
        run, client, cleanup, handoff, _ = self.restarted(fresh, capability=False)
        with self.assertRaisesRegex(core.StrataError, 'vision'):
            run()
        handoff.assert_not_called()
        self.assertEqual(cleanup.call_count, 1)
        client.request.assert_not_called()

    def test_03_restart_invalid_protocol_cannot_reuse_the_old_cleanup_lease(self):
        fresh = round2.idle_status(protocol_version=2, vision={'enabled': True})
        run, client, cleanup, handoff, _ = self.restarted(fresh)
        with self.assertRaisesRegex(core.StrataError, 'protocol'):
            run()
        self.assertEqual(cleanup.call_count, 1)
        handoff.assert_not_called()
        client.request.assert_not_called()

    def test_04_restart_missing_native_idle_matches_initial_capability_rejection(self):
        fresh = round2.idle_status(vision={'enabled': True})
        run, client, cleanup, handoff, _ = self.restarted(fresh)
        with self.assertRaisesRegex(core.StrataError, 'native video'):
            run()
        self.assertEqual(cleanup.call_count, 1)
        handoff.assert_not_called()
        client.request.assert_not_called()

    def test_05_restart_valid_native_uses_fresh_model_and_exactly_two_release_phases(self):
        fresh = round2.idle_status(model='new-native-model', vision={'enabled': True})
        fresh['capabilities'] = {'video': {'contract_version': 1, 'modes': ['native']}}
        run, client, cleanup, handoff, manager = self.restarted(fresh)
        self.assertEqual(len(run()), 1)
        self.assertEqual(client.request.call_args.args[1]['model'], 'new-native-model')
        self.assertEqual(manager.ensure.call_count, 2)
        self.assertEqual(cleanup.call_count, 2)
        handoff.assert_called_once()

    def test_06_exactly_32_followups_stay_in_one_lease(self):
        client = mock.Mock()
        client.request.side_effect = [self.status] + [vf.completion(vf.result())] * 33
        stages = []
        def followup(answers, status):
            stages.append(len(answers))
            return [nodes.request(str(i)) for i in range(32)] if len(stages) == 1 else None
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            result = core.generate_pipeline(self.connection, [nodes.request('first')], followup, max_followups=32)
        self.assertEqual(len(result), 33)
        self.assertEqual(stages, [1, 32])
        cleanup.assert_called_once()

    def test_07_bad_final_followup_is_rejected_before_any_followup_is_sent(self):
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(vf.result())]
        bad = [nodes.request(str(i)) for i in range(31)] + [{'messages': []}]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaises(core.StrataError):
                core.generate_pipeline(self.connection, [nodes.request('first')], lambda *_: bad)
        self.assertEqual(client.request.call_count, 2)
        cleanup.assert_called_once()

    def test_08_empty_followup_batch_is_an_error_with_one_cleanup(self):
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(vf.result())]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, '1–64'):
                core.generate_pipeline(self.connection, [nodes.request('first')], lambda *_: [])
        self.assertEqual(client.request.call_count, 2)
        cleanup.assert_called_once()

    def test_09_background_gpu_work_after_summary_callback_blocks_next_stage(self):
        core.save_profile('test', {'mode': 'external', 'url': 'http://127.0.0.1:18402',
                                  'allow_lifecycle': True, 'same_gpu': True})
        allocated = [0]
        torch = types.SimpleNamespace(cuda=types.SimpleNamespace(memory_allocated=lambda _: allocated[0]))
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(vf.result())]
        def followup(*_):
            allocated[0] = 129 * 1024**2
            return [nodes.request('merge')]
        with mock.patch.dict(sys.modules, {'torch': torch}), mock.patch.object(core, 'Client', return_value=client), \
             mock.patch.object(core, 'gpu_handoff', return_value=('cuda', 2**30, 0)), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'background GPU work'):
                core.generate_pipeline(self.connection, [nodes.request('first')], followup)
        self.assertEqual(client.request.call_count, 2)
        cleanup.assert_called_once()

    def test_10_keyboard_interrupt_during_merge_preserves_exception_and_cleans_once(self):
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(vf.result())]
        error = KeyboardInterrupt('merge interrupted')
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaises(KeyboardInterrupt) as caught:
                core.generate_pipeline(self.connection, [nodes.request('first')], mock.Mock(side_effect=error))
        self.assertIs(caught.exception, error)
        cleanup.assert_called_once()

    def wire(self, send):
        connections = []
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    connections.append(self.client_address)
                    self.request.settimeout(5)
                    request = b''
                    while b'\r\n\r\n' not in request:
                        packet = self.request.recv(65536)
                        if not packet:
                            return
                        request += packet
                    headers, body = request.split(b'\r\n\r\n', 1)
                    length = next((int(line.split(b':', 1)[1]) for line in headers.split(b'\r\n')[1:]
                                   if line.lower().startswith(b'content-length:')), 0)
                    while len(body) < length:
                        packet = self.request.recv(65536)
                        if not packet:
                            return
                        body += packet
                    send(self.request)
                except (OSError, BrokenPipeError):
                    pass
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = core.Client({'url': f'http://127.0.0.1:{server.server_address[1]}', 'timeout_s': 2})
        client.audit_connections = connections
        return client

    def test_11_real_chunked_trickle_cannot_extend_the_total_deadline(self):
        finished = threading.Event()
        def send(sock):
            try:
                sock.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n')
                for byte in b'{"ok":true}':
                    sock.sendall(b'1\r\n' + bytes([byte]) + b'\r\n')
                    time.sleep(.08)
                sock.sendall(b'0\r\n\r\n')
            finally:
                finished.set()
        client = self.wire(send)
        started = time.monotonic()
        with self.assertRaisesRegex(core.StrataError, 'total timeout'):
            client.request('/v1/status', timeout=.3, check=None)
        self.assertLess(time.monotonic() - started, .9)
        self.assertTrue(finished.wait(2))

    def test_12_real_native_post_cancellation_closes_one_connection_without_retry(self):
        received, closed = threading.Event(), threading.Event()
        def send(sock):
            received.set()
            sock.settimeout(2)
            if sock.recv(1) == b'':
                closed.set()
        client = self.wire(send)
        error = InterruptedError('native post cancelled')
        def check():
            if received.is_set():
                raise error
        with self.assertRaises(InterruptedError) as caught:
            client.request('/v1/chat/completions', {'messages': [{'role': 'user', 'content': [
                {'type': 'strata_video', 'mode': 'native', 'frames': []}]}]}, check=check)
        self.assertIs(caught.exception, error)
        self.assertTrue(closed.wait(2))
        self.assertEqual(len(client.audit_connections), 1)

    def test_13_duplicate_error_json_has_http_context_without_private_message_echo(self):
        payload = b'{"error":{"message":"private-key","message":"again"}}'
        client = self.wire(lambda sock: sock.sendall(b'HTTP/1.1 500 Error\r\nContent-Length: ' +
                            str(len(payload)).encode() + b'\r\n\r\n' + payload))
        client.profile['api_key'] = 'private-key'
        with self.assertRaisesRegex(core.StrataError, 'HTTP 500') as caught:
            client.request('/v1/status', check=None)
        self.assertNotIn('private-key', str(caught.exception))

    def test_14_leading_zero_length_is_parsed_without_changing_json(self):
        payload = b'{"ok":true}'
        client = self.wire(lambda sock: sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: ' +
                            b'0' * 40 + str(len(payload)).encode() + b'\r\n\r\n' + payload))
        self.assertEqual(client.request('/v1/status', check=None), {'ok': True})

    def test_15_chunked_response_exact_cap_and_cap_plus_one(self):
        cap = 16 * 1024**2
        for extra in (0, 1):
            payload = b'{"text":"' + b'x' * (cap - 11 + extra) + b'"}'
            self.assertEqual(len(payload), cap + extra)
            response = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' + \
                       format(len(payload), 'x').encode() + b'\r\n' + payload + b'\r\n0\r\n\r\n'
            client = self.wire(lambda sock, value=response: sock.sendall(value))
            with self.subTest(extra=extra):
                if extra:
                    with self.assertRaisesRegex(core.StrataError, '16 MiB'):
                        client.request('/v1/status', timeout=5, check=None)
                else:
                    self.assertEqual(len(client.request('/v1/status', timeout=5, check=None)['text']), cap - 11)

    def file(self, source):
        cls = type('VideoFromFile', (), {'__module__': 'comfy_api.latest._input_impl.video_types',
            'get_stream_source': lambda _: source, 'get_duration': mock.Mock(return_value=2.)})
        value = cls()
        value.__dict__['_VideoFromFile__file'] = source
        return value

    def test_16_disappearing_file_is_a_controlled_adapter_error(self):
        obj = self.file(Path(core.HOME) / 'vanished.mp4')
        with mock.patch.object(Path, 'is_file', return_value=True), \
             mock.patch.object(Path, 'stat', side_effect=FileNotFoundError('vanished')):
            with self.assertRaisesRegex(core.StrataError, 'source'):
                video.file_source(obj)
        obj.get_duration.assert_not_called()

    def test_17_closed_bytesio_is_a_controlled_adapter_error(self):
        source = io.BytesIO(b'closed')
        source.close()
        obj = self.file(source)
        with self.assertRaisesRegex(core.StrataError, 'source'):
            video.file_source(obj)
        def official_getter(self):
            source.seek(0)
            return source
        type(obj).get_stream_source = official_getter
        with self.assertRaisesRegex(core.StrataError, 'source'):
            video.file_source(obj)
        obj.get_duration.assert_not_called()

    def test_18_exact_file_size_boundary_is_checked_before_metadata(self):
        source = Path(core.HOME) / 'bounded.mp4'
        source.write_bytes(b'fixture')
        obj = self.file(source)
        actual = Path.stat
        for size in (video.MAX_INPUT_BYTES, video.MAX_INPUT_BYTES + 1):
            def stat(path, *args, **kwargs):
                item = actual(path, *args, **kwargs)
                return types.SimpleNamespace(st_size=size, st_mode=item.st_mode)
            with self.subTest(size=size), mock.patch.object(Path, 'stat', stat):
                if size > video.MAX_INPUT_BYTES:
                    with self.assertRaisesRegex(core.StrataError, '256 MiB'):
                        video.file_source(obj)
                else:
                    self.assertEqual(video.file_source(obj)[2], source)
        obj.get_duration.assert_not_called()

    def test_19_accumulated_partial_window_preserves_component_and_original_pts(self):
        from PIL import Image
        components = [self.file(io.BytesIO(b'x')) for _ in range(3)]
        value = types.SimpleNamespace(videos=components, complete_audio=None)
        value.__dict__['_VideoFromList__buffer'] = None
        def decode(item, **kwargs):
            index = components.index(item)
            count = kwargs['max_frames']
            targets = video.shared().sampling_schedule(kwargs['start_s'], kwargs['end_s'], kwargs['sampling_fps'], count)
            return video.shared().sample_images([Image.new('RGB', (32, 24), (index * 60, 0, 0))] * count,
                timestamps=targets, time_origin_s=10 + index * 10, source_duration_s=2., preselected=True,
                start_s=kwargs['start_s'], end_s=kwargs['end_s'], sampling_fps=kwargs['sampling_fps'], max_frames=count)
        with mock.patch.object(video, 'official_type', return_value='VideoFromFile'), \
             mock.patch.object(video, 'metadata_guard', return_value=2.), mock.patch.object(video, 'file_adapter', side_effect=decode):
            sampled = video.list_adapter(value, start_s=1., end_s=5., sampling_fps=1., max_frames=4, max_pixels=65536)
        self.assertEqual([f['component_index'] for f in sampled.manifest['frames']], [0, 1, 1, 2])
        self.assertEqual([f['timestamp_s'] for f in sampled.manifest['frames']], [0., 1., 2., 3.])
        self.assertEqual([f['component_source_timestamp_s'] for f in sampled.manifest['frames']], [11., 20., 21., 30.])
        video.shared().validate_manifest(sampled.manifest, 4)

    def test_20_native_negative_origin_keeps_trimmed_pts_and_clip_relative_evidence(self):
        from PIL import Image
        from serve.video import video_part
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4))] * 2,
            timestamps=[1., 1.5], time_origin_s=-3., source_duration_s=2., preselected=True,
            start_s=1., end_s=2., sampling_fps=2.)
        stream = io.BytesIO()
        Image.new('RGB', (4, 4)).save(stream, format='PNG')
        source = 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()
        part = video.native_part([source, source], sampled.manifest['frames'], sampled.manifest)
        self.assertEqual(part['metadata']['time_origin_s'], -2.)
        self.assertEqual([f['source_timestamp_s'] for f in part['frames']], [-2., -1.5])
        self.assertEqual([f['timestamp_s'] for f in part['frames']], [0., .5])
        self.assertEqual(video_part(part)['metadata']['duration_s'], 1.)


class VideoDurationEntryPoints(unittest.TestCase):
    """Same offset-container root cause, checked at the extra node entry points."""
    setUp = vf.VideoLease.setUp

    def fixture_file(self, suffix='.mkv', *, trim=0., duration=0.):
        from tools.test_strata_video import write_video
        path = Path(core.HOME) / ('offset' + suffix)
        write_video(path, [i / 10 for i in range(20)], origin=10., rate=10)
        cls = type('VideoFromFile', (), {'__module__': 'comfy_api.latest._input_impl.video_types',
            'get_stream_source': lambda self: str(path),
            'get_duration': mock.Mock(side_effect=AssertionError('incorrect official raw-duration getter')),
            'get_active_trim_window': mock.Mock(side_effect=AssertionError('incorrect official trim getter'))})
        value = cls()
        value.__dict__.update({'_VideoFromFile__file': str(path), '_VideoFromFile__start_time': trim,
            '_VideoFromFile__duration': duration, '_VideoFromFile__crop': None})
        return value, path

    def sample(self, value, **kwargs):
        return video.file_adapter(value, start_s=kwargs.get('start_s', 0.), end_s=kwargs.get('end_s'),
            sampling_fps=kwargs.get('sampling_fps', 1.), max_frames=kwargs.get('max_frames', 4),
            max_pixels=65536, cancel=lambda: None)

    def test_offset_mkv_raw_duration_is_two_seconds(self):
        value, path = self.fixture_file()
        self.assertEqual(video.metadata_guard(str(path)), 2.)
        sampled = self.sample(value)
        self.assertEqual(sampled.manifest['source']['input_duration_s'], 2.)
        self.assertEqual([f['timestamp_s'] for f in sampled.manifest['frames']], [0., 1.])
        value.get_duration.assert_not_called()

    def test_offset_mp4_keeps_explicit_track_duration(self):
        value, path = self.fixture_file('.mp4')
        self.assertEqual(video.metadata_guard(str(path)), 2.)
        self.assertEqual(self.sample(value).manifest['source']['input_duration_s'], 2.)

    def test_offset_negative_start_uses_correct_tail_origin(self):
        value, _ = self.fixture_file(trim=-.5)
        sampled = self.sample(value, sampling_fps=4., max_frames=2)
        self.assertEqual(sampled.manifest['source']['input_duration_s'], .5)
        self.assertEqual(sampled.manifest['source']['original_media_time_origin_s'], 1.5)
        self.assertEqual(sampled.manifest['frames'][0]['source_timestamp_s'], 11.5)
        self.assertEqual(sampled.manifest['frames'][0]['timestamp_s'], 0.)
        video.shared().validate_manifest(sampled.manifest, 2)
        value.get_active_trim_window.assert_not_called()

    def test_offset_list_uses_real_component_lengths_at_the_join(self):
        first, _ = self.fixture_file()
        second = type(first)()
        second.__dict__.update(first.__dict__)
        value = types.SimpleNamespace(videos=[first, second], complete_audio=None)
        value.__dict__['_VideoFromList__buffer'] = None
        with mock.patch.object(video, 'official_type', return_value='VideoFromFile'):
            sampled = video.list_adapter(value, start_s=0., end_s=4., sampling_fps=1., max_frames=4, max_pixels=65536)
        self.assertEqual(sampled.manifest['source']['duration_s'], 4.)
        self.assertEqual([f['component_index'] for f in sampled.manifest['frames']], [0, 0, 1, 1])
        self.assertEqual([f['timestamp_s'] for f in sampled.manifest['frames']], [0., 1., 2., 3.])
        self.assertEqual([f['component_source_timestamp_s'] for f in sampled.manifest['frames']], [10., 11., 10., 11.])
        video.shared().validate_manifest(sampled.manifest, 4)

    def test_missing_guard_return_fails_before_decode(self):
        value, _ = self.fixture_file()
        with mock.patch.object(video, 'metadata_guard', return_value=None), mock.patch.object(video.shared(), 'sample_video') as decode:
            with self.assertRaisesRegex(core.StrataError, 'source duration'):
                self.sample(value)
        decode.assert_not_called()
        value.get_duration.assert_not_called()

    def test_invalid_trim_values_are_domain_errors_before_decode(self):
        for state in ({'_VideoFromFile__start_time': True}, {'_VideoFromFile__start_time': float('inf')},
                      {'_VideoFromFile__start_time': 10**1000}, {'_VideoFromFile__duration': -1.},
                      {'_VideoFromFile__duration': True}, {'_VideoFromFile__start_time': 2.}):
            with self.subTest(state=state), self.assertRaises(core.StrataError):
                video.file_timing(state, 2.)

    def test_frame_count_metadata_stays_a_bounded_duration_fallback(self):
        stream = types.SimpleNamespace(duration=None, frames=24, average_rate=12)
        container = mock.MagicMock()
        container.duration = None
        container.streams.video = [stream]
        container.__enter__.return_value = container
        av = types.SimpleNamespace(open=mock.Mock(return_value=container), FFmpegError=RuntimeError)
        with mock.patch.dict(sys.modules, {'av': av}):
            self.assertEqual(video.metadata_guard(io.BytesIO(b'fixture')), 2.)
        container.decode.assert_not_called()
        container.demux.assert_not_called()


if __name__ == '__main__':
    unittest.main()
