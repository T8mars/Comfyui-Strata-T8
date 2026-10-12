"""Fourth local audit: transfer tags, structural answers and dependent-stage errors."""
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import numpy as np
import test_nodes as fixture
import test_video_nodes as vf
import test_video_audit20_second as second

core, nodes = fixture.core, fixture.nodes
video = nodes.video_tools


def tagged_video(path, transfer):
    import av
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=2)
        stream.width = stream.height = 32
        stream.pix_fmt = 'yuv420p'
        stream.codec_context.color_trc = transfer
        stream.codec_context.color_primaries = 9 if transfer in (16, 18) else 1
        stream.codec_context.colorspace = 9 if transfer in (16, 18) else 1
        for index in range(4):
            array = np.full((32, 32, 3), 40 + index * 40, np.uint8)
            frame = av.VideoFrame.from_ndarray(array, format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class VideoAudit20Fourth(unittest.TestCase):
    setUp = vf.VideoLease.setUp

    def sampled(self, count=4):
        return nodes.StrataVideoSample().run(images=vf.images(count), fps=1)

    def responder(self, path, body=None, **kwargs):
        if path == '/v1/status':
            return self.status
        if path == '/v1/unload':
            return {'ok': True}
        content = body['messages'][-1]['content']
        information = json.loads(content[0]['text'] if isinstance(content, list) else content)
        evidence = information['frames'] if isinstance(content, list) else information['evidence_frames']
        value = vf.result('Visible sampled evidence.', [{'start_s': evidence[0]['timestamp_s'],
            'end_s': evidence[-1]['timestamp_s'], 'description': 'Sampled progression',
            'evidence_frames': [frame['index'] for frame in evidence]}])
        value['prompt'] = 'A subject crosses the visible sequence.'
        return vf.completion(value)

    def analyze(self, *, count=4, response=None, frames=None, manifest=None, **kwargs):
        if frames is None:
            frames, manifest = self.sampled(count)
        client = mock.Mock()
        client.request.side_effect = response or self.responder
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as release:
            output = nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, **kwargs)
        release.assert_called_once()
        return output, client

    def rejected(self, response, pattern=None, *, count=4, **kwargs):
        frames, manifest = self.sampled(count)
        client = mock.Mock()
        client.request.side_effect = response
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as release:
            if pattern:
                with self.assertRaisesRegex(core.StrataError, pattern):
                    nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, **kwargs)
            else:
                with self.assertRaises(nodes.StructuredOutputError):
                    nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, **kwargs)
        release.assert_called_once()
        return client

    def file(self, path, **kwargs):
        value, _ = second.official_file(path)
        return video.file_adapter(value, start_s=0., end_s=None, sampling_fps=1.,
            max_frames=4, max_pixels=65536, cancel=lambda: None, **kwargs)

    def test_01_actual_pq_file_is_rejected_before_pixel_conversion(self):
        path = Path(core.HOME) / 'pq.mp4'
        tagged_video(path, 16)
        with mock.patch.object(video.shared(), 'sample_video') as decode:
            with self.assertRaisesRegex(core.StrataError, 'color|HDR|transfer'):
                self.file(path)
        decode.assert_not_called()

    def test_02_actual_hlg_bytesio_is_rejected_without_materialization(self):
        path = Path(core.HOME) / 'hlg.mp4'
        tagged_video(path, 18)
        source = io.BytesIO(path.read_bytes())
        with mock.patch.object(video.shared(), 'sample_video') as decode:
            with self.assertRaisesRegex(core.StrataError, 'color|HDR|transfer'):
                self.file(source)
        decode.assert_not_called()
        self.assertFalse(source.closed)

    def test_03_accumulated_hdr_member_cannot_be_silently_treated_as_sdr(self):
        path = Path(core.HOME) / 'pq-list.mp4'
        tagged_video(path, 16)
        accumulated, module = second.official_list()
        for member in accumulated.videos:
            member.__dict__['_VideoFromFile__file'] = path
        module.VideoFromFile.get_stream_source = lambda self: self.__dict__['_VideoFromFile__file']
        with mock.patch.dict(sys.modules, {second.MODULE: module}), \
                mock.patch.object(video.shared(), 'sample_video', wraps=video.shared().sample_video) as decode:
            with self.assertRaisesRegex(core.StrataError, 'color|HDR|transfer'):
                video.sample_input(video=accumulated)
        decode.assert_not_called()

    def test_04_actual_bt709_unspecified_and_srgb_files_keep_exact_pixels(self):
        for transfer in (1, 2, 13):
            with self.subTest(transfer=transfer):
                path = Path(core.HOME) / f'sdr-{transfer}.mp4'
                tagged_video(path, transfer)
                expected = video.shared().sample_video(path, sampling_fps=1., max_frames=4, max_pixels=65536)
                actual = self.file(path)
                self.assertEqual(video.metadata_guard(path), 2.)
                self.assertEqual([frame.image.tobytes() for frame in actual.frames],
                                 [frame.image.tobytes() for frame in expected.frames])
                self.assertEqual([f['source_timestamp_s'] for f in actual.manifest['frames']], [0., 1.])

    def test_05_fractional_component_alpha_composes_once_on_white(self):
        images = vf.Tensor(np.zeros((2, 2, 2, 3), np.float32))
        images.array[..., 0] = 1.
        alpha = vf.Tensor(np.full((2, 2, 2, 1), .5, np.float32))
        value, module = second.official_components(images, alpha=alpha)
        with mock.patch.dict(sys.modules, {second.MODULE: module}):
            result = video.sample_input(video=value, max_frames=1)
        self.assertEqual(result.frames[0].image.getpixel((0, 0)), (255, 128, 128))
        self.assertEqual(images.transfers, ['cpu'])

    def test_06_storyboard_objects_are_not_accepted_as_string_shots(self):
        def response(path, body=None, **kwargs):
            answer = self.responder(path, body, **kwargs)
            if path == '/v1/chat/completions':
                value = vf.result()
                value['shots'] = [{'prompt': 'wrong schema'}]
                return vf.completion(value)
            return answer
        client = self.rejected(response, count=2, task='storyboard')
        self.assertEqual(len(client.request.call_args_list), 2)

    def test_07_reversed_event_interval_is_rejected_inside_a_valid_scope(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return vf.completion(vf.result(events=[{'start_s': 1., 'end_s': .5,
                    'description': 'reversed', 'evidence_frames': [0, 1]}]))
            return self.responder(path, body, **kwargs)
        self.rejected(response, 'evidence', count=2)

    def test_08_json_integer_evidence_representation_and_time_tolerance(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return vf.completion(vf.result(events=[{'start_s': 1. - 5e-7, 'end_s': 1. + 5e-7,
                    'description': 'precision boundary', 'evidence_frames': [1.0]}]))
            return self.responder(path, body, **kwargs)
        output, _ = self.analyze(response=response, count=2)
        self.assertEqual(json.loads(output['result'][1])['analysis']['events'][0]['evidence_frames'], [1.0])

    def test_09_noncontiguous_evidence_ids_survive_native_and_summary(self):
        frames, text = self.sampled(5)
        manifest = json.loads(text)
        ids = [0, 7, 18, 31, 63]
        for frame, index in zip(manifest['frames'], ids):
            frame['index'] = index
        self.status['capabilities'] = {'video': {'contract_version': 1, 'modes': ['native']}}
        output, client = self.analyze(frames=frames, manifest=json.dumps(manifest), mode='native', frames_per_segment=2)
        sent = [frame['index'] for call in client.request.call_args_list if call.args[0] == '/v1/chat/completions'
                and isinstance(call.args[1]['messages'][-1]['content'], list)
                for frame in call.args[1]['messages'][-1]['content'][1]['frames']]
        self.assertEqual(sent, ids)
        self.assertEqual(json.loads(output['result'][1])['analysis']['events'][0]['evidence_frames'], ids)

    def test_10_fractional_pts_are_not_rounded_in_segment_evidence_json(self):
        frames, text = nodes.StrataVideoSample().run(images=vf.images(4), fps=30,
            timestamps_json='[0.1,0.13333333333333333,0.2,0.26666666666666666]',
            start_s=.1, end_s=.3, sampling_fps=30)
        manifest = json.loads(text)
        _, client = self.analyze(frames=frames, manifest=text, frames_per_segment=2)
        sent = [f for call in client.request.call_args_list if call.args[0] == '/v1/chat/completions'
                and isinstance(call.args[1]['messages'][-1]['content'], list)
                for f in json.loads(call.args[1]['messages'][-1]['content'][0]['text'])['frames']]
        self.assertEqual([f['source_timestamp_s'] for f in sent], [f['source_timestamp_s'] for f in manifest['frames']])
        self.assertEqual([f['timestamp_s'] for f in sent], [f['timestamp_s'] for f in manifest['frames']])

    def test_11_exact_4k_question_retained_at_every_hierarchical_level(self):
        question = 'q' * 4096
        output, client = self.analyze(count=20, frames_per_segment=2, question=question)
        levels = []
        for call in client.request.call_args_list:
            if call.args[0] != '/v1/chat/completions':
                continue
            content = call.args[1]['messages'][-1]['content']
            info = json.loads(content[0]['text'] if isinstance(content, list) else content)
            self.assertEqual(info['question'], question)
            if isinstance(content, str):
                self.assertLessEqual(len(content.encode()), 12000)
                levels.append(len(info['evidence_frames']))
        self.assertEqual(levels, [8, 8, 4, 20])
        self.assertEqual(json.loads(output['result'][4])['requests'], 14)

    def test_12_final_summary_can_cite_visible_frames_not_named_in_segment_events(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions' and isinstance(body['messages'][-1]['content'], list):
                return vf.completion(vf.result('No confirmed movement event.'))
            return self.responder(path, body, **kwargs)
        output, _ = self.analyze(response=response, count=6, frames_per_segment=2)
        result = json.loads(output['result'][1])
        self.assertTrue(all(not segment['events'] for segment in result['segments']))
        self.assertEqual(result['analysis']['events'][0]['evidence_frames'], list(range(6)))

    def test_13_unicode_3000_character_report_preserved_without_a_merge(self):
        report = '界' * 2997 + '\n\x00"'
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return vf.completion(vf.result(report))
            return self.responder(path, body, **kwargs)
        output, client = self.analyze(response=response, count=2)
        self.assertEqual(output['result'][0], report)
        self.assertEqual(output['ui']['text'], [report])
        self.assertEqual(json.loads(output['result'][1])['analysis']['report'], report)
        self.assertEqual(len(client.request.call_args_list), 2)

    def test_14_duplicate_nested_event_json_member_is_rejected(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                text = ('{"report":"visible","events":[{"start_s":0,"end_s":0,"description":"x",'
                        '"evidence_frames":[0],"evidence_frames":[1]}],"shots":[],"prompt":"","limitations":[]}')
                return {'choices': [{'message': {'content': text}}]}
            return self.responder(path, body, **kwargs)
        self.rejected(response, count=2)

    def http(self, respond):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def answer(self, body=None):
                calls.append((self.path, body))
                status, payload = respond(self.path, body)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_GET(self):
                self.answer()
            def do_POST(self):
                self.answer(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        core.save_profile('test', {'mode': 'external', 'url': f'http://127.0.0.1:{server.server_port}',
            'api_key': 'private-video-key', 'allow_lifecycle': True, 'same_gpu': False})
        return calls

    def test_15_real_http_structured_rejection_at_merge_releases_once(self):
        def response(path, body):
            if path == '/v1/chat/completions' and isinstance(body['messages'][-1]['content'], str):
                return 422, json.dumps({'error': {'code': 'structured_output_failed',
                    'message': 'Cannot complete private-video-key'}}).encode()
            return 200, json.dumps(self.responder(path, body)).encode()
        calls = self.http(response)
        frames, manifest = self.sampled(4)
        with mock.patch.object(core, 'cleanup') as release:
            with self.assertRaises(core.StructuredServiceError) as error:
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, frames_per_segment=2)
        self.assertNotIn('private-video-key', str(error.exception))
        self.assertIn('[redacted]', str(error.exception))
        release.assert_called_once()
        self.assertEqual([path for path, body in calls], ['/v1/status'] + ['/v1/chat/completions'] * 3)

    def test_16_huge_but_finite_json_integer_event_is_a_controlled_scope_error(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return vf.completion(vf.result(events=[{'start_s': 0, 'end_s': 10**300,
                    'description': 'out of scope', 'evidence_frames': [0, 1]}]))
            return self.responder(path, body, **kwargs)
        self.rejected(response, 'evidence', count=2)

    def test_17_real_http_html_completion_fails_without_partial_summary(self):
        def response(path, body):
            return (200, b'<html>service proxy error</html>') if path == '/v1/chat/completions' else (
                200, json.dumps(self.status).encode())
        calls = self.http(response)
        frames, manifest = self.sampled(2)
        with mock.patch.object(core, 'cleanup') as release:
            with self.assertRaisesRegex(core.StrataError, 'Invalid JSON'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest)
        release.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_18_nested_unicode_usage_and_reasoning_remain_distinct_from_analysis(self):
        usage = {'prompt_tokens': 30, 'completion_tokens': 20, 'details': {'cached': 4, 'label': '视觉'}}
        def response(path, body=None, **kwargs):
            value = self.responder(path, body, **kwargs)
            if path == '/v1/chat/completions':
                value['usage'] = usage
                value['choices'][0]['message']['reasoning_content'] = 'private reasoning, not observed evidence'
            return value
        output, _ = self.analyze(response=response, count=4, frames_per_segment=2)
        self.assertEqual(json.loads(output['result'][4]), {'requests': 3, 'stages': [usage] * 3})
        self.assertNotIn('private reasoning', output['result'][1])

    def test_19_cancel_after_successful_answer_validation_cannot_return_prompt(self):
        cancelled = False
        original = nodes.validated
        error = InterruptedError('cancel after schema validation')
        def validate(*args):
            nonlocal cancelled
            value = original(*args)
            cancelled = True
            return value
        def check():
            if cancelled:
                raise error
        frames, manifest = self.sampled(2)
        client = mock.Mock()
        client.request.side_effect = self.responder
        with mock.patch.object(nodes, 'validated', side_effect=validate), mock.patch.object(core, 'interrupted', side_effect=check), \
                mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as release:
            with self.assertRaises(InterruptedError) as caught:
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt')
        self.assertIs(caught.exception, error)
        release.assert_called_once()

    def test_20_cleanup_failure_keeps_primary_structured_error_as_context(self):
        frames, manifest = self.sampled(2)
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion({'report': 'missing schema fields'})]
        error = core.StrataError('release unconfirmed')
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup', side_effect=error) as release:
            with self.assertRaises(core.StrataError) as caught:
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest)
        self.assertIs(caught.exception, error)
        self.assertIsInstance(caught.exception.__context__, nodes.StructuredOutputError)
        release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
