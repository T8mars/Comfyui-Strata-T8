"""Local video follow-up audit: protocol, evidence and single-lease regressions."""
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock

import test_nodes as fixture
import test_video_nodes as video_fixture
from test_video_nodes import images, result, completion

core, nodes = fixture.core, fixture.nodes
video = nodes.video_tools


class VideoAudit20(unittest.TestCase):
    setUp = video_fixture.VideoLease.setUp
    def sampled(self, count=4, **kwargs):
        return nodes.StrataVideoSample().run(images=images(count), fps=1, **kwargs)

    def responder(self, path, body=None, **kwargs):
        if path == '/v1/status':
            return self.status
        if path == '/v1/unload':
            return {'ok': True}
        content = body['messages'][-1]['content']
        if isinstance(content, list):
            information = json.loads(content[0]['text'])
            evidence = information['frames']
            events = [{'start_s': evidence[0]['timestamp_s'], 'end_s': evidence[-1]['timestamp_s'],
                       'description': 'visible progression', 'evidence_frames': [f['index'] for f in evidence]}]
        else:
            information = json.loads(content)
            events = [event for segment in information['segments'] for event in segment['events']]
        value = result('observed', events)
        value['prompt'] = 'A visible subject moves across the sampled sequence.'
        return completion(value)

    def generate(self, count=4, responder=None, **kwargs):
        frames, manifest = self.sampled(count)
        client = mock.Mock()
        client.request.side_effect = responder or self.responder
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            output = nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', **kwargs)
        cleanup.assert_called_once()
        return output, client

    def test_native_prompt_uses_native_wire_and_prompt_output(self):
        self.status['capabilities'] = {'video': {'contract_version': 1, 'modes': ['frames', 'native']}}
        output, client = self.generate(4, mode='native', frames_per_segment=2)
        requests = [c.args[1] for c in client.request.call_args_list if c.args[0] == '/v1/chat/completions']
        for body in requests[:2]:
            parts = body['messages'][-1]['content']
            self.assertEqual(parts[1]['type'], 'strata_video')
            self.assertEqual(parts[1]['mode'], 'native')
            self.assertFalse(any(p['type'] == 'image_url' for p in parts))
        self.assertEqual(output['result'][0], json.loads(output['result'][1])['analysis']['prompt'])
        self.assertEqual(len(requests), 3)

    def test_native_short_odd_total_and_segment_counts_preserve_evidence(self):
        self.status['capabilities'] = {'video': {'contract_version': 1, 'modes': ['native']}}
        for count, segment in ((1, 2), (3, 2), (5, 3), (7, 3)):
            with self.subTest(count=count, segment=segment):
                output, client = self.generate(count, mode='native', frames_per_segment=segment)
                media = [c.args[1]['messages'][-1]['content'][1] for c in client.request.call_args_list
                         if c.args[0] == '/v1/chat/completions' and isinstance(c.args[1]['messages'][-1]['content'], list)]
                sent = [f for part in media for f in part['frames']]
                self.assertEqual([f['index'] for f in sent], list(range(count)))
                self.assertEqual([f['timestamp_s'] for f in sent], list(range(count)))
                self.assertEqual(len(json.loads(output['result'][2])), count)

    def test_segment_event_cannot_exceed_its_cited_evidence_times(self):
        def response(path, body=None, **kwargs):
            answer = self.responder(path, body, **kwargs)
            if path == '/v1/chat/completions':
                value = result(events=[{'start_s': 0, 'end_s': 1, 'description': 'unsupported second', 'evidence_frames': [0]}])
                answer = completion(value)
            return answer
        with self.assertRaisesRegex(nodes.StructuredOutputError, 'evidence'):
            self.generate(2, responder=response)

    def test_hierarchical_summary_cannot_cite_frames_outside_its_input_group(self):
        def response(path, body=None, **kwargs):
            answer = self.responder(path, body, **kwargs)
            if path == '/v1/chat/completions' and isinstance(body['messages'][-1]['content'], str):
                info = json.loads(body['messages'][-1]['content'])
                ids = {i for s in info['segments'] for event in s['events'] for i in event['evidence_frames']}
                if 0 in ids and 8 not in ids:
                    answer = completion(result(events=[{'start_s': 8, 'end_s': 8, 'description': 'foreign group', 'evidence_frames': [8]}]))
            return answer
        with self.assertRaisesRegex(nodes.StructuredOutputError, 'evidence'):
            self.generate(20, responder=response, frames_per_segment=2)

    def test_duplicate_evidence_ids_are_rejected(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return completion(result(events=[{'start_s': 0, 'end_s': 0, 'description': 'repeated', 'evidence_frames': [0, 0]}]))
            return self.responder(path, body, **kwargs)
        with self.assertRaises(nodes.StructuredOutputError):
            self.generate(2, responder=response)

    def test_frames_wire_has_timestamps_and_individual_images(self):
        output, client = self.generate(4, mode='frames', frames_per_segment=2)
        requests = [c.args[1] for c in client.request.call_args_list if c.args[0] == '/v1/chat/completions']
        for body in requests[:2]:
            parts = body['messages'][-1]['content']
            self.assertEqual([p['type'] for p in parts], ['text', 'text', 'image_url', 'text', 'image_url'])
            self.assertIn('seconds', parts[1]['text'])
        self.assertEqual(json.loads(output['result'][1])['mode'], 'frames')

    def test_fabricated_json_boolean_evidence_is_rejected(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return completion(result(events=[{'start_s': 0, 'end_s': 0, 'description': 'boolean', 'evidence_frames': [False]}]))
            return self.responder(path, body, **kwargs)
        with self.assertRaises(nodes.StructuredOutputError):
            self.generate(2, responder=response)

    def test_empty_events_hierarchical_summary_has_valid_scope(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return completion(dict(result('no confirmed events'), prompt='Visible sampled composition.'))
            return self.responder(path, body, **kwargs)
        output, client = self.generate(20, responder=response, frames_per_segment=2)
        self.assertEqual(len(json.loads(output['result'][1])['segments']), 10)
        self.assertEqual(json.loads(output['result'][1])['analysis']['events'], [])
        self.assertEqual(json.loads(output['result'][4])['requests'], 14)

    def test_cancellation_during_cpu_conversion_never_opens_client(self):
        frames, manifest = self.sampled(2)
        with mock.patch.object(core, 'interrupted', side_effect=InterruptedError('CPU cancel')), mock.patch.object(core, 'Client') as client:
            with self.assertRaises(InterruptedError):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt')
            client.assert_not_called()

    def test_failure_of_summary_cleanup_never_returns_downstream_prompt(self):
        frames, manifest = self.sampled(4)
        client = mock.Mock()
        client.request.side_effect = self.responder
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup', side_effect=core.StrataError('release unconfirmed')) as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'release unconfirmed'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', frames_per_segment=2)
            cleanup.assert_called_once()

    def test_oversize_unicode_reports_fail_merge_bound_and_cleanup(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions':
                return completion(dict(result('界' * 3000), prompt='界' * 3000))
            return self.responder(path, body, **kwargs)
        frames, manifest = self.sampled(4)
        client = mock.Mock()
        client.request.side_effect = response
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'merge budget'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, frames_per_segment=2)
            cleanup.assert_called_once()

    def test_real_local_http_native_prompt_and_single_cleanup(self):
        owner = self
        calls = []
        self.status['capabilities'] = {'video': {'contract_version': 1, 'modes': ['native']}}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def answer(self, body=None):
                calls.append((self.path, body))
                value = owner.responder(self.path, body)
                payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_GET(self): self.answer()
            def do_POST(self): self.answer(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        core.save_profile('test', {'mode': 'external', 'url': f'http://127.0.0.1:{server.server_address[1]}',
                                  'allow_lifecycle': True, 'same_gpu': False})
        frames, manifest = self.sampled(3)
        output = nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', mode='native', frames_per_segment=2)
        self.assertEqual(json.loads(output['result'][4])['requests'], 3)
        self.assertEqual(sum(path == '/v1/unload' for path, body in calls), 1)
        self.assertEqual([len(body['messages'][-1]['content'][1]['frames']) for path, body in calls
                          if path == '/v1/chat/completions' and isinstance(body['messages'][-1]['content'], list)], [2, 1])

    def test_native_tail_pair_uses_real_last_pixels_and_time_without_new_evidence(self):
        from PIL import Image
        from serve.video import expand_videos
        for count in (1, 3, 5):
            frames, manifest_text = self.sampled(count)
            manifest = json.loads(manifest_text)
            encoded = []
            for index in range(count):
                stream = io.BytesIO()
                Image.new('RGB', (32, 24), (index * 40, 0, 0)).save(stream, format='PNG')
                encoded.append('data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
            part = video.native_part(encoded, manifest['frames'], manifest)
            expanded = expand_videos([{'role': 'user', 'content': [part]}],
                lambda source: base64.b64decode(source.split(',', 1)[1]), lambda: None)
            pairs = expanded[0]['content'][0]['pairs']
            self.assertEqual(len(pairs), (count + 1) // 2)
            self.assertEqual(pairs[-1]['timestamp_s'], count - 1)
            self.assertEqual(pairs[-1]['pair'][0], pairs[-1]['pair'][1])
            self.assertEqual(len(part['frames']), count)

    def test_sixty_four_frame_hierarchy_retains_exact_evidence_and_bounded_requests(self):
        output, client = self.generate(64, frames_per_segment=2)
        analysis = json.loads(output['result'][1])
        self.assertEqual(len(analysis['segments']), 32)
        self.assertEqual(json.loads(output['result'][4])['requests'], 43)
        self.assertEqual({i for event in analysis['analysis']['events'] for i in event['evidence_frames']}, set(range(64)))
        requests = [c.args[1] for c in client.request.call_args_list if c.args[0] == '/v1/chat/completions']
        for body in requests:
            content = body['messages'][-1]['content']
            if isinstance(content, str):
                self.assertLessEqual(len(content.encode('utf-8')), 12000)

    def test_invalid_manifest_times_and_ids_fail_before_image_copy(self):
        frames, manifest_text = self.sampled(2)
        for key, value in (('index', 64), ('index', True), ('timestamp_s', 3), ('source_timestamp_s', 10), ('width', 1048577)):
            manifest = json.loads(manifest_text)
            manifest['frames'][0][key] = value
            with self.subTest(key=key, value=value), mock.patch.object(core, 'encode_images') as copied, mock.patch.object(core, 'Client') as client:
                with self.assertRaises(core.StrataError):
                    nodes.StrataVideoAnalyze().run(self.connection, frames, json.dumps(manifest), task='prompt')
                copied.assert_not_called()
                client.assert_not_called()

    def test_user_text_byte_limits_and_unicode_fail_before_image_copy(self):
        frames, manifest = self.sampled(2)
        for kwargs in ({'question': '界' * 1366}, {'transcript': '界' * 2731}, {'question': '\ud800'},
                       {'transcript': '\ud800'}, {'max_tokens': 4097}, {'temperature': True}, {'max_pixels': False}):
            with self.subTest(kwargs=kwargs), mock.patch.object(core, 'encode_images') as copied, mock.patch.object(core, 'Client') as client:
                with self.assertRaises(core.StrataError):
                    nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', **kwargs)
                copied.assert_not_called()
                client.assert_not_called()

    def test_native_capability_failure_does_not_offload_comfy_or_unload_service(self):
        frames, manifest = self.sampled(1)
        core.save_profile('test', {'mode': 'external', 'url': 'http://127.0.0.1:18402',
                                  'allow_lifecycle': True, 'same_gpu': True})
        client = mock.Mock()
        client.request.return_value = self.status
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'gpu_handoff') as handoff, mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'native video'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, mode='native', task='prompt')
            handoff.assert_not_called()
            cleanup.assert_not_called()
            self.assertEqual([c.args[0] for c in client.request.call_args_list], ['/v1/status'])

    def test_merge_budget_covers_metadata_and_question_not_only_segment_reports(self):
        frames, manifest_text = self.sampled(4)
        manifest = json.loads(manifest_text)
        manifest['coverage']['additional_metadata'] = '界' * 5000
        client = mock.Mock()
        client.request.side_effect = self.responder
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError, 'merge budget'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, json.dumps(manifest), frames_per_segment=2)
            cleanup.assert_called_once()
        self.assertEqual(sum(c.args[0] == '/v1/chat/completions' for c in client.request.call_args_list), 2)

    def test_user_transcript_is_explicitly_unverified_and_no_audio_media_sent(self):
        output, client = self.generate(2, transcript='[00:00] User supplied words.')
        body = next(c.args[1] for c in client.request.call_args_list if c.args[0] == '/v1/chat/completions')
        self.assertIn('user-supplied text, not verified audio', body['messages'][0]['content'])
        self.assertIn('Never invent unseen motion or audio', body['messages'][0]['content'])
        self.assertEqual(json.loads(body['messages'][-1]['content'][0]['text'])['transcript'], '[00:00] User supplied words.')
        self.assertTrue(all(part['type'] in ('text', 'image_url') for part in body['messages'][-1]['content']))

    def test_summary_transport_cancellation_cleans_up_once(self):
        def response(path, body=None, **kwargs):
            if path == '/v1/chat/completions' and isinstance(body['messages'][-1]['content'], str):
                raise InterruptedError('cancel summary transport')
            return self.responder(path, body, **kwargs)
        frames, manifest = self.sampled(4)
        client = mock.Mock()
        client.request.side_effect = response
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(InterruptedError, 'cancel summary'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, frames_per_segment=2)
            cleanup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
