"""Eighth audit: install artifacts, cancellation races and protocol 1 boundaries.

The numbered tests are twenty distinct audit scenarios. GPU/native processes are
substitutes; real HTTP integration uses random loopback ports and byte tokenizers.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
import uuid
import zipfile

import test_nodes as fixture
import test_audit_round2 as round2
import test_audit_round5 as round5
import test_audit_round6 as round6
from test_release import builder

core, nodes = fixture.core, fixture.nodes


class InstallArtifacts(unittest.TestCase):
    def stage(self, root):
        tracked = subprocess.check_output(['git', '-c', 'index.threads=1', 'ls-files', '-z'], cwd=fixture.NODE_ROOT)
        names = [name for name in tracked.decode().split('\0') if name and (
            name in builder.SHIPPING or name.startswith(('web/', 'examples/')))]
        for name in [*names, 'meta.json']:
            destination = root/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((fixture.NODE_ROOT/name).read_bytes())
        return tracked

    def packaged_core(self, installed):
        spec = importlib.util.spec_from_file_location('audit8_core_'+uuid.uuid4().hex, installed/'core.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name)
        spec.loader.exec_module(module)
        return module

    def test_r01_real_release_zip_authorizes_managed_preparation_without_meta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracked = self.stage(root)
            with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder.subprocess, 'check_output', return_value=tracked), mock.patch('builtins.print'):
                builder.main()
            with zipfile.ZipFile(next((root/'dist').glob('*.zip'))) as archive:
                self.assertIsNone(archive.testzip())
                archive.extractall(root/'unpacked')
            installed = root/'unpacked/Comfyui-Strata-T8'
            self.assertFalse((installed/'meta.json').exists())
            packaged = self.packaged_core(installed)
            packaged.HOME = root/'private-home'
            runtime = root/'runtime'
            (runtime/'runtime/python').mkdir(parents=True)
            (runtime/'runtime/python/python.exe').touch()
            (runtime/'tools').mkdir()
            (runtime/'tools/managed_config.py').touch()
            version = json.loads((installed/'version.json').read_text())['runtime_min_version']
            (runtime/'meta.json').write_text(json.dumps({'version': version, 'protocol_version': 1}))
            manager = packaged.Managed('test', {'runtime': str(runtime), 'data_dir': str(root/'data'), 'api_key': 'test-key'})
            with mock.patch.object(packaged.socket, 'socket'), mock.patch.object(packaged.subprocess, 'Popen', side_effect=OSError('native boundary reached')) as spawn:
                with self.assertRaisesRegex(OSError, 'native boundary reached'):
                    manager.ensure(mock.Mock(), check=lambda: None)
            spawn.assert_called_once()

    def test_r02_registry_ignore_keeps_all_managed_runtime_contract_files(self):
        # Registry follows .comfyignore rather than the GitHub builder's whitelist.
        import fnmatch
        patterns = (fixture.NODE_ROOT/'.comfyignore').read_text().splitlines()
        for name in ('core.py', 'version.json', 'nodes.py', '__init__.py'):
            with self.subTest(name=name):
                self.assertFalse(any(fnmatch.fnmatch(name, pattern) for pattern in patterns if pattern))
        self.assertIn('meta.json', patterns)
        version = json.loads((fixture.NODE_ROOT/'version.json').read_text())
        self.assertEqual(version['protocol_version'], 1)
        self.assertRegex(version['runtime_min_version'], r'^\d+\.\d+\.\d+-t8\.\d+$')
        self.assertNotIn('runtime_min_version', json.loads((fixture.NODE_ROOT/'meta.json').read_text()))

    def test_r03_build_rejects_a_stale_shipping_version_before_writing_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracked = self.stage(root)
            metadata = json.loads((root/'version.json').read_text())
            metadata['version'] = '0.0.0'
            (root/'version.json').write_text(json.dumps(metadata))
            with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder.subprocess, 'check_output', return_value=tracked):
                with self.assertRaisesRegex(ValueError, 'Node version differs'):
                    builder.main()
            self.assertFalse((root/'dist').exists())

    def test_r04_shipping_minimum_and_protocol_are_validated_before_a_zip_is_created(self):
        for field, value in (('protocol_version', True), ('protocol_version', 2), ('runtime_min_version', None),
                             ('runtime_min_version', '0.1.40.2-t8.2'), ('runtime_min_version', '0.1.40-t8.2\n')):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                tracked = self.stage(root)
                metadata = json.loads((root/'version.json').read_text())
                metadata[field] = value
                (root/'version.json').write_text(json.dumps(metadata))
                with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder.subprocess, 'check_output', return_value=tracked):
                    with self.assertRaisesRegex(ValueError, 'compatibility metadata'):
                        builder.main()
                self.assertFalse((root/'dist').exists())


class OwnershipAndGPU(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_r05_unchanged_hotfix_instance_is_reused_without_native_preparation(self):
        manager = fixture.OwnedProcesses.prepared_manager(self)
        version = '0.1.40-t8.2'
        (manager.root/'meta.json').write_text(json.dumps({'version': version, 'protocol_version': 1, 'engine_version': '0.1.40.2'}))
        startup = {key: manager.profile.get(key) for key in ('runtime', 'data_dir', 'port', 'context', 'vision', 'api_key')}
        startup['runtime_version'] = version
        manager.state_path.write_text(json.dumps({'fingerprint': hashlib.sha256(json.dumps(startup, sort_keys=True).encode()).hexdigest(), 'instance': 'fixed-instance'}))
        client, proc = mock.Mock(), mock.Mock()
        client.request.return_value = round2.idle_status(instance_id='fixed-instance')
        with mock.patch.object(manager, 'owned', return_value=proc), mock.patch.object(manager, 'stop') as stop, mock.patch.object(core.subprocess, 'Popen') as spawn:
            self.assertEqual(manager.ensure(client, check=lambda: None)['instance_id'], 'fixed-instance')
        spawn.assert_not_called()
        stop.assert_not_called()

    def test_r06_recycled_pid_cannot_stop_a_foreign_server(self):
        manager = fixture.OwnedProcesses.prepared_manager(self)
        manager.state_path.write_text(json.dumps({'pid': 123, 'created': 7, 'python': sys.executable, 'server': str(fixture.SERVER_FILE)}))
        proc = mock.Mock()
        proc.create_time.return_value = 8
        proc.exe.return_value = sys.executable
        proc.cmdline.return_value = [sys.executable, str(fixture.SERVER_FILE), '--config', str(manager.dir/'service.json')]
        with mock.patch('psutil.Process', return_value=proc):
            self.assertFalse(manager.stop())
        proc.children.assert_not_called()
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        self.assertTrue(manager.state_path.exists())

    def test_r07_same_gpu_parallel_service_is_rejected_before_offload_and_cleanup(self):
        profile = {'mode': 'external', 'url': 'http://127.0.0.1:1', 'same_gpu': True, 'allow_lifecycle': True}
        client = mock.Mock()
        client.request.return_value = round2.idle_status(concurrency={'serving': 2})
        with mock.patch.object(core, 'read_profile', return_value=profile), mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'gpu_handoff') as handoff, mock.patch.object(core, 'cleanup') as release:
            with self.assertRaisesRegex(core.StrataError, 'parallel=1'):
                core.generate(core.Connection('test'), [nodes.request('hi')])
        handoff.assert_not_called()
        release.assert_not_called()
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['/v1/status'])

    def test_r08_cleanup_waits_for_an_active_request_before_unloading(self):
        profile = {'cleanup_timeout_s': 1}
        client = mock.Mock()
        client.request.side_effect = [round2.idle_status(activity={'in_flight': 1}), round2.idle_status(), {}, round2.idle_status()]
        with mock.patch.object(core.time, 'sleep'):
            self.assertFalse(core.cleanup(client, profile, None, None))
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['/v1/status', '/v1/status', '/v1/unload', '/v1/status'])
        self.assertTrue(all(call.kwargs['check'] is None for call in client.request.call_args_list))

    def test_r09_background_gpu_allocation_blocks_chat_and_still_releases(self):
        profile = {'mode': 'external', 'url': 'http://localhost:1', 'same_gpu': True, 'allow_lifecycle': True}
        client = mock.Mock()
        client.request.return_value = round2.idle_status()
        cuda = mock.Mock()
        cuda.memory_allocated.return_value = 128*1024**2+1
        with mock.patch.object(core, 'read_profile', return_value=profile), mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'gpu_handoff', return_value=('device', 12*1024**3, 0)), mock.patch.object(core, 'cleanup') as cleanup, mock.patch.dict(sys.modules, {'torch': types.SimpleNamespace(cuda=cuda)}):
            with self.assertRaisesRegex(core.StrataError, 'background GPU work'):
                core.generate(core.Connection('test'), [nodes.request('hi')])
        cleanup.assert_called_once()
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['/v1/status'])


class CancellationAndWire(unittest.TestCase):
    def test_r10_cancel_during_payload_encoding_cannot_connect_later(self):
        encoding, released, closed = threading.Event(), threading.Event(), threading.Event()
        conn = mock.Mock()
        conn.sock = None
        conn.close.side_effect = closed.set
        def payload(body):
            encoding.set()
            released.wait(3)
            return b'{}'
        def check():
            if encoding.is_set():
                raise InterruptedError('cancelled while encoding')
        try:
            with mock.patch.object(core, 'request_payload', side_effect=payload), mock.patch('http.client.HTTPConnection', return_value=conn):
                with self.assertRaisesRegex(InterruptedError, 'while encoding'):
                    core.Client({'url': 'http://localhost:1'}).request('/test', {}, check=check)
        finally:
            released.set()
        self.assertTrue(closed.wait(1))
        conn.connect.assert_not_called()
        conn.request.assert_not_called()

    def test_r11_cancel_during_connect_cannot_send_a_late_post(self):
        connecting, released, closed = threading.Event(), threading.Event(), threading.Event()
        conn = mock.Mock()
        conn.sock = None
        conn.close.side_effect = closed.set
        def connect():
            connecting.set()
            released.wait(3)
            conn.sock = mock.Mock()
        conn.connect.side_effect = connect
        def check():
            if connecting.is_set():
                raise InterruptedError('cancelled during connect')
        try:
            with mock.patch('http.client.HTTPConnection', return_value=conn):
                with self.assertRaisesRegex(InterruptedError, 'during connect'):
                    core.Client({'url': 'http://localhost:1'}).request('/test', {}, check=check)
        finally:
            released.set()
        self.assertTrue(closed.wait(1))
        conn.request.assert_not_called()
        self.assertEqual(conn.auto_open, 0)

    def test_r12_unexpected_sse_cannot_be_accepted_as_a_nonstream_completion(self):
        # Nodes deliberately request stream:false, so event chunks must never masquerade as final JSON.
        data = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: [DONE]\n\n'
        client, _, _ = round5.WireFraming.response_server(self, [('Content-Type', 'text/event-stream'), ('Content-Length', str(len(data)))], data)
        client.profile['api_key'] = 'temporary-key'
        with self.assertRaises(core.StrataError) as caught:
            client.request('/v1/chat/completions', check=None)
        self.assertNotIsInstance(caught.exception, core.StructuredServiceError)
        self.assertNotIn('temporary-key', str(caught.exception))


@unittest.skipUnless(fixture.SOURCE_ROOT, 'Set STRATA_SOURCE_DIR for actual Strata HTTP integration')
class ActualProtocol(unittest.TestCase):
    setUp = fixture.BatchHTTP.setUp

    def replies(self, scripts):
        self.engine.reply = self.engine.reply.__class__(self.svc.tok, list(scripts), max_context=4096)

    def test_r13_reasoning_only_wire_output_never_reaches_downstream_and_releases(self):
        self.replies(['only analysis</think>'])
        with self.assertRaisesRegex(core.StrataError, 'no final answer'):
            nodes.StrataText().run(core.Connection('test'), 'question', max_tokens=128, reasoning_effort='high')
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.svc.v1_status()['activity']['in_flight'], 0)
        self.assertEqual(self.engine.closes, 1)

    def test_r14_upstream_multibyte_stop_and_overlapping_markers_keep_only_final_prefix(self):
        self.replies(['保留 🚀ENDTAIL', 'second 🚀ETAIL'])
        result, paired, _ = nodes.StrataBatch().run(core.Connection('test'), ['first', 'second'], stop=['🚀END', '🚀E'], max_tokens=128, reasoning_effort='none')
        self.assertEqual(result, ['保留 ', 'second '])
        self.assertEqual([item['index'] for item in json.loads(paired)], [0, 1])
        self.assertEqual((self.engine.starts, self.engine.closes), (1, 1))
        self.assertEqual(self.svc.v1_status()['activity']['in_flight'], 0)

    def test_r15_sixty_four_requests_keep_order_and_use_one_resident_transaction(self):
        answers = [f'answer-{index}' for index in range(64)]
        self.replies(answers)
        prompts = [f'question-{index}' for index in range(64)]
        outputs, paired, custom = nodes.StrataBatch().run(core.Connection('test'), prompts, max_tokens=64, reasoning_effort='none')
        self.assertEqual(outputs, answers)
        self.assertEqual(custom, answers)
        self.assertEqual([item['input'] for item in json.loads(paired)], prompts)
        self.assertEqual((self.engine.starts, self.engine.closes), (1, 1))
        self.assertEqual(self.svc.totals['requests'], 64)
        self.assertEqual(self.svc.v1_status()['activity']['in_flight'], 0)


class ImageAndJSON(unittest.TestCase):
    def test_r16_ninth_image_is_rejected_before_any_cpu_copy(self):
        images = mock.Mock()
        images.shape = (9, 2, 2, 3)
        with mock.patch.object(core, 'generate') as generate:
            with self.assertRaisesRegex(core.StrataError, 'at most 8'):
                nodes.StrataImageBatch().run(core.Connection('test'), images)
        images.numel.assert_not_called()
        images.detach.assert_not_called()
        generate.assert_not_called()

    def test_r17_recursive_local_schema_validates_all_descendants_without_retrieval(self):
        schema = {'$schema': 'https://json-schema.org/draft/2019-09/schema', '$recursiveAnchor': True, 'type': 'object',
                  'required': ['name'], 'properties': {'name': {'type': 'string'}, 'next': {'$recursiveRef': '#'}}}
        value = {'name': 'first', 'next': {'name': 'second', 'next': {'name': 'third'}}}
        self.assertEqual(nodes.validated(json.dumps(value), schema), value)
        value['next']['next']['name'] = 3
        with self.assertRaises(nodes.StructuredOutputError):
            nodes.validated(json.dumps(value), schema)

    def test_r18_extract_preserves_large_integers_and_boolean_types_without_model_work(self):
        value = {'shots': [{'duration': 9007199254740993, 'enabled': True}, {'duration': -9007199254740993, 'enabled': False}]}
        source = json.dumps(value)
        with mock.patch.object(core, 'generate') as generate:
            extracted, items, custom = nodes.StrataExtract().run(source, '/shots', 'duration', 'array')
            self.assertEqual(items, ['9007199254740993', '-9007199254740993'])
            self.assertEqual(custom, items)
            self.assertEqual(json.loads(extracted), value['shots'])
            self.assertEqual(nodes.StrataExtract().run(source, '/shots/0/enabled', '', 'boolean')[0], 'true')
            with self.assertRaisesRegex(core.StrataError, 'number'):
                nodes.StrataNumber().run(source, '/shots/0/enabled')
        generate.assert_not_called()


class PanelAndWorkflow(unittest.TestCase):
    run_browser = round6.BrowserRawProfile.run_browser

    def test_r19_late_status_does_not_replace_newer_queue_receipt_or_send_key_draft(self):
        self.run_browser(r'''
 key.value='unsaved-private-key';const statusRequest=action('状态').onclick();await flush();
 const queued=action('卸载').onclick();await flush();
 pending.find(p=>p.path==='/prompt').resolve(response({prompt_id:'newest-queued'}));await queued;
 pending.find(p=>p.path.endsWith('/control')).resolve(response({service:'strata',old:'old-status'}));await statusRequest;
 if(!status.textContent.includes('newest-queued') || status.textContent.includes('old-status'))throw Error('late status replaced a newer action receipt');
 if(key.value!=='unsaved-private-key' || JSON.stringify(calls).includes('unsaved-private-key'))throw Error('unsaved key leaked or was erased');
''')

    def test_r20_examples_have_valid_links_types_required_inputs_and_no_private_configuration(self):
        for path in sorted((fixture.NODE_ROOT/'examples').glob('*.api.json')):
            graph = core.json_loads(path.read_text(encoding='utf-8'))
            with self.subTest(workflow=path.name):
                self.assertGreater(len(graph), 0)
                for item in graph.values():
                    inputs = item['inputs']
                    self.assertFalse({'api_key', 'runtime', 'data_dir', 'url'}.intersection(inputs))
                    legacy = nodes.NODE_CLASS_MAPPINGS.get(item['class_type'])
                    if legacy:
                        spec = legacy.INPUT_TYPES()
                        self.assertTrue(set(spec['required']).issubset(inputs))
                    for name, value in inputs.items():
                        if not isinstance(value, list) or len(value) != 2:
                            continue
                        source, output = value
                        self.assertIn(source, graph)
                        self.assertIs(type(output), int)
                        upstream = nodes.NODE_CLASS_MAPPINGS.get(graph[source]['class_type'])
                        if upstream:
                            self.assertLess(output, len(upstream.RETURN_TYPES))
                            if legacy:
                                kind = (spec.get('required', {}) | spec.get('optional', {}))[name][0]
                                self.assertEqual(kind, upstream.RETURN_TYPES[output])


if __name__ == '__main__':
    unittest.main()
