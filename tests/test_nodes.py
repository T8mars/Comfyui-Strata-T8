"""HTTP batch resource handoff, schema extraction and local profile security."""
import importlib.util
import asyncio
import base64
import io
import os
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
NODE_ROOT = ROOT if (ROOT/'core.py').is_file() and (ROOT/'nodes.py').is_file() else ROOT/'comfyui-strata-t8'
if not (NODE_ROOT/'__init__.py').is_file():
    raise RuntimeError('Cannot locate the Strata node package next to this tests directory')
source_env = os.environ.get('STRATA_SOURCE_DIR')
SOURCE_ROOT = Path(source_env).expanduser().resolve() if source_env else ROOT if (ROOT/'serve/server.py').is_file() else None
if source_env and not (SOURCE_ROOT/'serve/server.py').is_file():
    raise RuntimeError('STRATA_SOURCE_DIR must point to a Strata source checkout containing serve/server.py')
if SOURCE_ROOT:
    sys.path.insert(0, str(SOURCE_ROOT))
    from serve.frontend import ChatTemplate
    from serve.server import ByteTokenizer, Service, serve
    from serve.test_lifecycle import ResidentEngine
SERVER_FILE = (SOURCE_ROOT or ROOT)/'serve/server.py'
spec = importlib.util.spec_from_file_location('strata_comfy_test', NODE_ROOT/'__init__.py', submodule_search_locations=[str(NODE_ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
core, nodes = package.nodes.core, package.nodes


@unittest.skipUnless(SOURCE_ROOT, 'Set STRATA_SOURCE_DIR to run real Strata HTTP lifecycle integration tests')
class BatchHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)
        tok = ByteTokenizer()
        self.engine = ResidentEngine(tok)
        self.svc = Service(self.engine, tok, ChatTemplate(SOURCE_ROOT/'serve/chat_template.jinja'))
        self.httpd = serve(self.svc, port=0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.profile = {'mode': 'external', 'url': f'http://127.0.0.1:{self.httpd.server_address[1]}', 'allow_lifecycle': True}
        core.save_profile('test', self.profile)

    def test_batch_loads_once_pairs_results_and_releases_at_end(self):
        results, paired, custom = nodes.StrataBatch().run(core.Connection('test'), ['first', 'second'], max_tokens=64, reasoning_effort='none')
        self.assertEqual(results, ['Hello.', 'Hello.'])
        self.assertEqual(custom, results)
        items = json.loads(paired)
        self.assertEqual([item['input'] for item in items], ['first', 'second'])
        self.assertEqual(self.engine.starts, 1)
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.engine.closes, 1)

    def test_external_inference_defaults_preserve_resident_service(self):
        core.save_profile('test', dict(self.profile, allow_lifecycle=False))
        result = nodes.StrataText().run(core.Connection('test'), 'hello', max_tokens=32, reasoning_effort='none')
        self.assertEqual(result[0], 'Hello.')
        self.assertTrue(self.engine.alive())
        with self.assertRaisesRegex(core.StrataError, 'explicitly'):
            core.control(core.Connection('test'), 'unload')
        with self.assertRaisesRegex(core.StrataError, 'managed'):
            core.control(core.Connection('test'), 'stop')

    def test_profile_and_connection_hide_credentials(self):
        profile = dict(self.profile, api_key='private-test-key')
        core.save_profile('test', profile)
        core.save_profile('test', dict(profile, api_key='__KEEP__'))
        self.assertEqual(core.read_profile('test')['api_key'], 'private-test-key')
        self.assertNotIn('private-test-key', repr(core.Connection('test')))
        for name in ('../bad', 'with/path', 'C:bad'):
            with self.assertRaises(core.StrataError):
                core.profile_path(name)

    def test_invalid_profile_does_not_replace_saved_profile(self):
        original = core.profile_path('test').read_bytes()
        for invalid in (dict(self.profile, url='file:///bad'), dict(self.profile, api_key='private\r\nkey')):
            with self.assertRaises(core.StrataError):
                core.save_profile('test', invalid)
            self.assertEqual(core.profile_path('test').read_bytes(), original)

    def test_preloaded_same_gpu_service_is_released_before_baseline(self):
        self.engine.restart()
        core.save_profile('test', dict(self.profile, same_gpu=True))
        def handoff(_):
            self.assertFalse(self.engine.alive())
            return None
        with mock.patch.object(core, 'gpu_handoff', side_effect=handoff):
            answer = nodes.StrataText().run(core.Connection('test'), 'hello', max_tokens=32, reasoning_effort='none')
        self.assertEqual(answer[0], 'Hello.')
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.engine.starts, 2)

    def test_transport_errors_cannot_leak_api_key(self):
        key = 'private-secret-value'
        client = core.Client(dict(self.profile, api_key=key))
        with mock.patch('http.client.HTTPConnection.connect', side_effect=ValueError(key)):
            with self.assertRaises(core.StrataError) as caught:
                client.request('/v1/status', check=None)
        self.assertNotIn(key, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_malformed_chat_completion_releases_resident_engine(self):
        original = core.Client.request
        for response in ({'choices': []}, {'choices': [{'message': {'content': 123}}]},
                         {'choices': [{'message': {'content': 'x', 'usage': {}}}], 'usage': False}):
            self.engine.restart()
            def request(client, path, *args, **kwargs):
                return response if path == '/v1/chat/completions' else original(client, path, *args, **kwargs)
            with mock.patch.object(core.Client, 'request', request), self.assertRaisesRegex(core.StrataError, 'Malformed'):
                nodes.StrataText().run(core.Connection('test'), 'hi')
            self.assertFalse(self.engine.alive())

    def test_incomplete_release_status_never_counts_as_idle(self):
        status = self.svc.v1_status()
        for changes in ({'processes': {}}, {'loaded': 'false'}, {'activity': {'in_flight': False}},
                        {'vision': {'enabled': 'no'}}, {'model': ''}):
            with self.subTest(changes=changes), self.assertRaises(core.StrataError):
                core.service_status(dict(status, **changes))
        client = mock.Mock()
        client.request.return_value = dict(status, processes={})
        with self.assertRaisesRegex(core.StrataError, 'External service release'):
            core.cleanup(client, {'cleanup_timeout_s': .01}, None, None)
        self.assertTrue(client.request.call_args_list)
        self.assertTrue(all(call.args[0] == '/v1/status' for call in client.request.call_args_list))  # Never POST unload after an untrusted idle response.

    def test_cleanup_cannot_claim_success_without_owned_stop(self):
        client, manager = mock.Mock(), mock.Mock()
        client.request.side_effect = core.StrataError('offline')
        manager.stop.return_value = False
        with self.assertRaisesRegex(core.StrataError, 'could not be confirmed'):
            core.cleanup(client, {'cleanup_timeout_s': .01}, manager, None)

    def test_second_batch_item_failure_releases_and_returns_no_partial_list(self):
        self.engine.restart()
        original = core.Client.request
        count = 0
        def request(client, path, *args, **kwargs):
            nonlocal count
            if path == '/v1/chat/completions':
                count += 1
                if count == 2: raise core.StrataError('second item failed')
            return original(client, path, *args, **kwargs)
        with mock.patch.object(core.Client, 'request', request), self.assertRaisesRegex(core.StrataError, 'second item failed'):
            nodes.StrataBatch().run(core.Connection('test'), ['a', 'b'], max_tokens=64, reasoning_effort='none')
        self.assertEqual(count, 2)
        self.assertFalse(self.engine.alive())

    def test_panel_control_callback_is_preserved_for_post_action_status(self):
        with mock.patch.object(core, 'interrupted', side_effect=InterruptedError('unrelated workflow cancelled')):
            status = core.control(core.Connection('test'), 'start', check=lambda: None)
        self.assertEqual(status['service'], 'strata')

    def test_startup_503_diagnostic_survives_cleanup_in_generation_and_control(self):
        core.save_profile('test', dict(self.profile, same_gpu=True))
        original = core.Client.request
        diagnostic = 'HTTP 503 engine_load_failed: GPU expert cache needs about 736 MiB more; lower context or close other GPU programs'
        for action in ('generate', 'load'):
            def request(client, path, *args, **kwargs):
                if path == ('/v1/chat/completions' if action == 'generate' else '/v1/load'):
                    self.engine.restart()  # Represent resources allocated before the startup error.
                    raise core.StrataError(diagnostic)
                return original(client, path, *args, **kwargs)
            with mock.patch.object(core.Client, 'request', request), mock.patch.object(core, 'gpu_handoff', return_value=None):
                with self.assertRaisesRegex(core.StrataError, 'HTTP 503 engine_load_failed.*736 MiB'):
                    if action == 'generate': nodes.StrataText().run(core.Connection('test'), 'hello')
                    else: core.control(core.Connection('test'), 'load', check=lambda: None)
            self.assertFalse(self.engine.alive())
            self.assertFalse(self.svc.v1_status()['loaded'])


class Structured(unittest.TestCase):
    def test_lists_and_json_pointer_are_typed(self):
        source = json.dumps({'shots': [{'prompt': 'a', 'duration': 1.25}, {'prompt': 'b', 'duration': 2}]})
        value, items, custom = nodes.StrataExtract().run(source, '/shots', 'prompt', 'array')
        self.assertEqual(items, ['a', 'b'])
        self.assertEqual(custom, items)
        self.assertEqual(nodes.StrataNumber().run(source, '/shots/0/duration'), (1.25,))
        self.assertEqual(nodes.pointer({'a/b': {'~k': 'v'}}, '/a~1b/~0k'), 'v')
        with self.assertRaises(core.StrataError):
            nodes.StrataExtract().run(source, '/shots', '', 'string')
        with self.assertRaises(core.StrataError):
            nodes.StrataNumber().run('{"number":true}', '/number')

    def test_schema_rejects_bad_types_and_remote_reference(self):
        with self.assertRaises(Exception):
            nodes.validated('{"shots":[]}', nodes.STORY_SCHEMA)
        with self.assertRaisesRegex(core.StrataError, 'local fragments'):
            nodes.validated('{}', {'$ref': 'https://example.invalid/schema'})

    def test_history_does_not_accept_workflow_images_or_unbounded_turns(self):
        for history in ('{}', '[{"role":"tool","content":"x"}]', '[{"role":"user","content":[]}]'):
            with self.assertRaises(core.StrataError):
                nodes.request('hi', history=history)
        self.assertEqual(nodes.request('hi', refresh=4, seed=8)['seed'], 8)
        self.assertNotIn('refresh', nodes.request('hi', refresh=4))

    def test_sampling_and_schema_reject_invalid_values_before_inference(self):
        for options in ({'top_k': 0}, {'top_k': 100}, {'top_p': 0}):
            with self.assertRaises(core.StrataError):
                nodes.request('hi', **options)
        with mock.patch.object(core, 'generate') as generate:
            with self.assertRaisesRegex(core.StrataError, 'local fragments'):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema='{"$ref":"https://example.invalid/"}')
            generate.assert_not_called()

    def test_sampling_rejects_nonfinite_boolean_and_fractional_integers(self):
        invalid = [{'top_k': True}, {'top_k': 2.5}, {'seed': -1}, {'seed': False},
                   {'max_tokens': 0}, {'max_tokens': 1.2}, {'temperature': float('nan')},
                   {'top_p': float('inf')}, {'temperature': 3}, {'reasoning_effort': 'unknown'}, {'max_tokens': 10**1000}]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(core.StrataError):
                nodes.request('hi', **options)
        self.assertEqual(nodes.request('hi', temperature=0, top_k=64, seed=0)['temperature'], 0)

    def test_structured_repairs_local_json_schema_failures_and_server_rejections(self):
        schema = json.dumps({'type': 'object', 'required': ['prompt'], 'properties': {'prompt': {'type': 'string'}}})
        good = [(' {"prompt":"ok"}', '', '{}')]
        for failure in ([('not json', '', '{}')], [('{"prompt":123}', '', '{}')],
                        core.StrataError('HTTP 422 structured_output_failed: invalid')):
            with mock.patch.object(core, 'generate', side_effect=[failure, good]) as generate:
                result = nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=schema, repair_attempts=1)
                self.assertEqual(json.loads(result[0]), {'prompt': 'ok'})
                self.assertEqual(generate.call_count, 2)
        with mock.patch.object(core, 'generate', side_effect=core.StrataError('offline')) as generate:
            with self.assertRaisesRegex(core.StrataError, 'offline'):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=schema, repair_attempts=2)
            self.assertEqual(generate.call_count, 1)

    def test_structured_repair_limits_and_nonstandard_numbers(self):
        for attempts in (-1, 3, True, 1.5):
            with mock.patch.object(core, 'generate') as generate, self.assertRaises(core.StrataError):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', repair_attempts=attempts)
            generate.assert_not_called()
        with mock.patch.object(core, 'generate', return_value=[('invalid', '', '{}')]) as generate:
            with self.assertRaises(nodes.StructuredOutputError):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema='{}', repair_attempts=2)
            self.assertEqual(generate.call_count, 3)
        for source in ('NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(nodes.StructuredOutputError): nodes.validated(source, {'type': 'number'})

    def test_pointer_does_not_silently_select_wrong_array_item(self):
        value = {'items': ['first', 'last'], '01': 'object key', '~1': 'literal'}
        self.assertEqual(nodes.pointer(value, '/01'), 'object key')
        self.assertEqual(nodes.pointer(value, '/~01'), 'literal')
        self.assertIs(nodes.pointer(value, ''), value)
        for path in ('/items/-1', '/items/01', '/items/+1', '/items/-', '/items/2', '/bad', '/~2', '/items/0/field', None):
            with self.subTest(path=path), self.assertRaises(core.StrataError): nodes.pointer(value, path)
        with self.assertRaisesRegex(core.StrataError, 'Every list item'):
            nodes.StrataExtract().run('{"shots":[{"prompt":"x"},{}]}', '/shots', 'prompt', 'array')
        with self.assertRaisesRegex(core.StrataError, 'Unknown expected'):
            nodes.StrataExtract().run('{}', '', '', 'unknown')

    def test_number_never_outputs_infinite_float(self):
        for source in ('{"n":NaN}', '{"n":Infinity}', '{"n":1e999}', '{"n":' + '9'*400 + '}'):
            with self.subTest(source=source[:40]), self.assertRaises((core.StrataError, ValueError)):
                nodes.StrataNumber().run(source, '/n')
        self.assertEqual(nodes.StrataNumber().run('{"n":-1.25}', '/n'), (-1.25,))


class Registration(unittest.TestCase):
    def test_legacy_ids_and_list_contracts_match_outputs(self):
        self.assertEqual(len(nodes.NODE_CLASS_MAPPINGS), 10)
        for node_id, cls in nodes.NODE_CLASS_MAPPINGS.items():
            self.assertIn(node_id, nodes.NODE_DISPLAY_NAME_MAPPINGS)
            self.assertEqual(len(cls.RETURN_TYPES), len(cls.RETURN_NAMES))
            self.assertEqual(len(getattr(cls, 'OUTPUT_IS_LIST', cls.RETURN_TYPES)), len(cls.RETURN_TYPES))
        self.assertTrue(nodes.StrataControl.OUTPUT_NODE)
        self.assertNotEqual(nodes.StrataControl.IS_CHANGED(), nodes.StrataControl.IS_CHANGED())

    def test_v3_adapter_preserves_lists_inputs_and_execution(self):
        class Type:
            @staticmethod
            def Input(name, **options): return {'name': name, **options}
            @staticmethod
            def Output(name, **options): return {'name': name, **options}
        class Schema:
            def __init__(self, **kwargs): self.__dict__.update(kwargs)
        class Output:
            def __init__(self, *values, **options): self.values, self.options = values, options
        io = types.SimpleNamespace(ComfyNode=object, Schema=Schema, NodeOutput=Output,
                                   String=Type, Int=Type, Float=Type, Boolean=Type, Image=Type,
                                   Combo=Type, Custom=lambda _: Type)
        latest = types.ModuleType('comfy_api.latest')
        latest.io, latest.ComfyExtension = io, object
        parent = types.ModuleType('comfy_api')
        spec_v3 = importlib.util.spec_from_file_location('strata_comfy_v3_test', NODE_ROOT/'__init__.py',
                                                        submodule_search_locations=[str(NODE_ROOT)])
        v3 = importlib.util.module_from_spec(spec_v3)
        with mock.patch.dict(sys.modules, {'comfy_api': parent, 'comfy_api.latest': latest, spec_v3.name: v3}):
            spec_v3.loader.exec_module(v3)
        mapping = {cls.__name__: cls for cls in v3.V3_NODES}
        self.assertEqual(set(mapping), set(nodes.NODE_CLASS_MAPPINGS))
        structured = mapping['StrataT8Structured'].define_schema()
        self.assertEqual([o['is_output_list'] for o in structured.outputs], [False, True, False, False])
        extract = mapping['StrataT8Extract']
        result = extract.execute(json_text='{"shots":["a","b"]}', pointer='/shots', item_field='', expected_type='array')
        self.assertEqual(result.values[1:], (['a', 'b'], ['a', 'b']))
        self.assertTrue(extract.define_schema().inputs[0]['force_input'])


class Transport(unittest.TestCase):
    def connection(self, raw=b'{"ok":true}', status=200, stalled=False):
        released = threading.Event()
        sock = mock.Mock()
        sock.shutdown.side_effect = lambda _: released.set()
        response = mock.Mock(status=status)
        response.read.side_effect = lambda _: (released.wait(2) if stalled else None) or raw
        if stalled:
            def read(_):
                released.wait(2)
                return raw
            response.read.side_effect = read
        conn = mock.Mock(sock=sock)
        def getresponse():
            conn.sock = None  # HTTP/1.0 detaches the socket, but response.fp still owns it.
            return response
        conn.getresponse.side_effect = getresponse
        return conn, sock, released

    def test_total_timeout_closes_detached_response_socket(self):
        conn, sock, released = self.connection(stalled=True)
        start = time.monotonic()
        with mock.patch('http.client.HTTPConnection', return_value=conn):
            with self.assertRaisesRegex(core.StrataError, 'total timeout'):
                core.Client({'url': 'http://localhost:8080'}).request('/v1/status', timeout=.2, check=None)
        self.assertLess(time.monotonic()-start, 1)
        self.assertTrue(released.is_set())
        sock.shutdown.assert_called_once()
        self.assertFalse(conn.auto_open)

    def test_cancel_closes_detached_socket_and_preserves_interrupt(self):
        conn, _, released = self.connection(stalled=True)
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls > 1: raise InterruptedError('user cancelled')
        with mock.patch('http.client.HTTPConnection', return_value=conn):
            with self.assertRaises(InterruptedError): core.Client({'url': 'http://localhost:8080'}).request('/v1/status', check=cancel)
        self.assertTrue(released.is_set())

    def test_bad_response_shape_and_error_text_hide_credentials(self):
        for raw, status in ((b'[]', 200), (b'{"error":"private-key"}', 503)):
            conn, _, _ = self.connection(raw, status)
            with mock.patch('http.client.HTTPConnection', return_value=conn), self.assertRaises(core.StrataError) as caught:
                core.Client({'url': 'http://localhost:8080', 'api_key': 'private-key'}).request('/v1/status', check=None)
            self.assertNotIn('private-key', str(caught.exception))

    def test_startup_503_code_and_memory_diagnostic_are_returned_verbatim(self):
        raw = json.dumps({'error': {'code': 'engine_load_failed', 'message': 'GPU expert cache needs 736 MiB more'}}).encode()
        conn, _, _ = self.connection(raw, 503)
        with mock.patch('http.client.HTTPConnection', return_value=conn), self.assertRaisesRegex(core.StrataError, '^HTTP 503 engine_load_failed: GPU expert cache needs 736 MiB more$'):
            core.Client({'url': 'http://localhost:8080'}).request('/v1/chat/completions', {}, check=None)


class Images(unittest.TestCase):
    class Tensor:
        def __init__(self, array, transfers=None):
            self.array, self.shape = array, array.shape
            self.transfers = transfers if transfers is not None else []
        def numel(self): return self.array.size
        def __iter__(self): return (Images.Tensor(array, self.transfers) for array in self.array)
        def detach(self): return self
        def to(self, device):
            self.transfers.append(device)
            return self
        def float(self):
            import numpy as np
            return Images.Tensor(self.array.astype(np.float32), self.transfers)
        def numpy(self): return self.array

    def test_images_convert_to_cpu_resize_and_composite_alpha(self):
        import numpy as np
        from PIL import Image
        tensor = self.Tensor(np.zeros((1, 512, 512, 4), dtype=np.float16))
        encoded = core.encode_images(tensor, max_pixels=65536)
        image = Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',', 1)[1])))
        self.assertEqual(image.size, (256, 256))
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(tensor.transfers, ['cpu'])

    def test_invalid_dimensions_limits_and_nonfinite_pixels_fail_before_inference(self):
        import numpy as np
        for shape in ((0, 2, 2, 3), (9, 2, 2, 3), (1, 0, 2, 3), (1, 2, 2, 1), (2, 2, 3)):
            with self.subTest(shape=shape), self.assertRaises(core.StrataError):
                core.encode_images(self.Tensor(np.zeros(shape)))
        for limit in (0, -1, True, 1048576.5, 4194305):
            with self.subTest(limit=limit), self.assertRaises(core.StrataError):
                core.encode_images(self.Tensor(np.zeros((1, 1, 1, 3))), max_pixels=limit)
        for number in (float('nan'), float('inf'), -float('inf')):
            with mock.patch.object(core, 'generate') as generate, self.assertRaises(core.StrataError):
                nodes.StrataImage().run(core.Connection('test'), self.Tensor(np.full((1, 1, 1, 3), number)))
            generate.assert_not_called()

    def test_cancel_between_images_never_submits_partial_batch(self):
        import numpy as np
        checks = 0
        def cancel():
            nonlocal checks
            checks += 1
            if checks == 2: raise InterruptedError('cancelled')
        with mock.patch.object(core, 'interrupted', side_effect=cancel), mock.patch.object(core, 'generate') as generate:
            with self.assertRaises(InterruptedError):
                nodes.StrataImageBatch().run(core.Connection('test'), self.Tensor(np.zeros((2, 2, 2, 3))))
            generate.assert_not_called()

    def test_image_batch_preserves_order_reasoning_and_one_generate_transaction(self):
        import numpy as np
        answers = [('first', 'reason 0', '{"total_tokens":1}'), ('second', 'reason 1', '{"total_tokens":2}')]
        with mock.patch.object(core, 'generate', return_value=answers) as generate:
            result = nodes.StrataImageBatch().run(core.Connection('test'), self.Tensor(np.zeros((2, 2, 2, 3))), question='question')
        self.assertEqual(result[0], ['first', 'second'])
        self.assertEqual(result[2], result[0])
        pairs = json.loads(result[1])
        self.assertEqual([p['index'] for p in pairs], [0, 1])
        self.assertEqual([p['reasoning'] for p in pairs], ['reason 0', 'reason 1'])
        generate.assert_called_once()
        self.assertEqual(len(generate.call_args.args[1]), 2)

    def test_bad_image_task_sampling_schema_and_batch_inputs_do_not_copy_images(self):
        for options in ({'task': 'unknown'}, {'question': None}, {'top_k': True},
                        {'schema': '{"$ref":"https://example.invalid/"}'}):
            with mock.patch.object(core, 'encode_images') as encode, mock.patch.object(core, 'generate') as generate:
                with self.assertRaises(core.StrataError): nodes.StrataImage().run(core.Connection('test'), object(), **options)
                encode.assert_not_called()
                generate.assert_not_called()
        for texts in ([], ['a']*65, ['a', 1], 'single'):
            with mock.patch.object(core, 'generate') as generate, self.assertRaises(core.StrataError):
                nodes.StrataBatch().run(core.Connection('test'), texts)
            generate.assert_not_called()


class Panel(unittest.TestCase):
    def setUp(self):
        self.handlers = {}
        handlers = self.handlers
        class Routes:
            def get(self, path): return self.register(path)
            def post(self, path): return self.register(path)
            def register(self, path):
                def add(handler):
                    handlers[path] = handler
                    return handler
                return add
        self.http_error = type('HTTPError', (Exception,), {})
        web = types.SimpleNamespace(json_response=lambda body, status=200: types.SimpleNamespace(body=body, status=status),
                                    HTTPForbidden=lambda **kwargs: self.http_error(kwargs.get('text')),
                                    HTTPUnsupportedMediaType=lambda **kwargs: self.http_error(kwargs.get('text')))
        server = types.ModuleType('server')
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes()))
        aiohttp = types.ModuleType('aiohttp')
        aiohttp.web = web
        panel = importlib.import_module(spec.name+'.panel')
        with mock.patch.dict(sys.modules, {'server': server, 'aiohttp': aiohttp}): panel.register()

    def request(self, body=None, **changes):
        request = types.SimpleNamespace(host='localhost:8188', scheme='http', remote='127.0.0.1',
                                        headers={}, content_type='application/json', json=mock.AsyncMock(return_value=body))
        request.__dict__.update(changes)
        return request

    def test_all_lifecycle_buttons_require_queue_and_status_does_not(self):
        for action in ('start', 'load', 'unload', 'stop'):
            with mock.patch.object(core, 'control') as control:
                response = asyncio.run(self.handlers['/strata_t8/control'](self.request({'name':'test', 'action':action})))
            self.assertEqual(response.status, 409)
            control.assert_not_called()
        with mock.patch.object(core, 'control', return_value={'service':'strata'}) as control:
            response = asyncio.run(self.handlers['/strata_t8/control'](self.request({'name':'test', 'action':'status'})))
        self.assertEqual(response.status, 200)
        self.assertEqual(control.call_args.args[1], 'status')

    def test_loopback_origin_json_body_and_error_privacy(self):
        for changes in ({'remote':'192.0.2.1'}, {'host':'evil.invalid'}, {'headers':{'Origin':'https://localhost:8188'}},
                        {'headers':{'Origin':'http://evil.invalid'}}, {'content_type':'text/plain'}):
            with self.subTest(changes=changes), self.assertRaises(self.http_error):
                asyncio.run(self.handlers['/strata_t8/profile'](self.request({}, **changes)))
        for body in ([], None, {}, {'name':'x','profile':[]}):
            response = asyncio.run(self.handlers['/strata_t8/profile'](self.request(body)))
            self.assertEqual(response.status, 400)
        with mock.patch.object(core, 'save_profile', side_effect=OSError('private-key')):
            response = asyncio.run(self.handlers['/strata_t8/profile'](self.request({'name':'test','profile':{}})))
        self.assertEqual(response.status, 400)
        self.assertNotIn('private-key', json.dumps(response.body))

    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the browser panel handler test')
    def test_browser_buttons_enqueue_lifecycle_graphs(self):
        script = r'''
const fs = require('fs'), vm = require('vm');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.value=''; this.style={cssText:''}; }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children=values; }
  setAttribute() {}
}
let extension, panel; const calls=[];
const app={extensionManager:{registerSidebarTab:value=>panel=value},registerExtension:value=>extension=value};
const api={clientId:'test',fetchApi:async(path,options)=>{
  calls.push({path,body:options?.body ? JSON.parse(options.body) : null});
  return {ok:true,json:async()=>path.endsWith('/profiles') ? {profiles:{default:{mode:'external',url:'http://localhost:8080'}},home:'private'} : {prompt_id:'queued',service:'strata'}};
}};
const source=fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,'');
vm.runInNewContext(source,{app,api,document:{createElement:tag=>new Element(tag)},console});
(async()=>{
  await extension.setup(); const root=new Element('div'); panel.render(root);
  await new Promise(resolve=>setImmediate(resolve));
  const actions=root.children.find(e=>e.tag==='div').children;
  for (const button of actions) await button.onclick();
  const prompts=calls.filter(c=>c.path==='/prompt');
  const expected=['start','load','unload','stop'];
  if(JSON.stringify(prompts.map(c=>c.body.prompt['2'].inputs.action))!==JSON.stringify(expected)) throw Error('lifecycle action did not enter queue');
  for(const call of prompts) {
    if(call.body.prompt['1'].class_type!=='StrataT8Connection' || call.body.prompt['2'].class_type!=='StrataT8Control') throw Error('unstable node id');
    if(JSON.stringify(call.body).includes('api_key')) throw Error('credential serialized into workflow');
  }
  if(calls.filter(c=>c.path==='/strata_t8/control').length!==1) throw Error('only status uses direct control');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(NODE_ROOT/'web/strata.js')], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


class OwnedProcesses(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_owned_uses_recorded_python_after_runtime_change(self):
        manager = core.Managed('test', {'runtime': str(Path(self.temp.name)/'new-runtime')})
        manager.state_path.write_text(json.dumps({'pid':123,'created':7,'python':sys.executable}),encoding='utf-8')
        proc = mock.Mock()
        proc.create_time.return_value=7
        proc.exe.return_value=sys.executable
        proc.cmdline.return_value=[sys.executable, str(Path(sys.executable).parents[2]/'serve/server.py'), '--config', str(manager.dir/'service.json')]
        with mock.patch('psutil.Process', return_value=proc):
            self.assertIs(manager.owned(),proc)
            proc.create_time.return_value=8
            self.assertIsNone(manager.owned())

    def test_owner_requires_real_server_config_pair_and_finite_creation_time(self):
        manager = core.Managed('test', {'runtime': self.temp.name})
        state = {'pid': 123, 'created': 7, 'python': sys.executable, 'server': str(SERVER_FILE)}
        manager.state_path.write_text(json.dumps(state), encoding='utf-8')
        proc = mock.Mock()
        proc.exe.return_value, proc.create_time.return_value = sys.executable, 7
        with mock.patch('psutil.Process', return_value=proc):
            for command in ([sys.executable, 'foreign.py', '--config', str(manager.dir/'service.json')],
                            [sys.executable, str(SERVER_FILE), '--note', str(manager.dir/'service.json')],
                            [sys.executable, 'foreign.py', str(SERVER_FILE), '--config', str(manager.dir/'service.json')],
                            [sys.executable, '-c', 'pass', str(SERVER_FILE), '--config', str(manager.dir/'service.json')]):
                proc.cmdline.return_value = command
                self.assertIsNone(manager.owned())
            manager.state_path.write_text(json.dumps(dict(state, created=float('nan'))), encoding='utf-8')
            with self.assertRaisesRegex(core.StrataError, 'ownership'): manager.owned()

    def test_owner_access_denied_blocks_start_and_stop_attempts_every_target(self):
        import psutil
        manager = core.Managed('test', {'runtime': self.temp.name})
        manager.state_path.write_text(json.dumps({'pid':123, 'created':7, 'python':sys.executable}), encoding='utf-8')
        with mock.patch('psutil.Process', side_effect=psutil.AccessDenied(123)), self.assertRaisesRegex(core.StrataError, 'ownership'):
            manager.owned()
        parent, first, second = mock.Mock(), mock.Mock(), mock.Mock()
        parent.children.return_value = [first, second]
        first.terminate.side_effect = psutil.AccessDenied(123)
        first.kill.side_effect = psutil.AccessDenied(123)
        with mock.patch.object(manager, 'owned', return_value=parent), mock.patch('psutil.wait_procs', side_effect=[([],[first,second]),([],[first])]):
            with self.assertRaisesRegex(core.StrataError, 'still exiting'): manager.stop()
        second.terminate.assert_called_once()
        second.kill.assert_called_once()
        self.assertTrue(manager.state_path.exists())

    def test_profile_cache_fingerprint_changes_even_if_timestamp_is_preserved(self):
        profile = {'mode':'external', 'url':'http://localhost:8080', 'api_key':'private'}
        core.save_profile('test', profile)
        path = core.profile_path('test')
        stamp = path.stat().st_mtime_ns
        previous = nodes.StrataConnection.IS_CHANGED('test')
        core.save_profile('test', dict(profile, url='http://localhost:8081'))
        os.utime(path, ns=(stamp, stamp))
        self.assertNotEqual(previous, nodes.StrataConnection.IS_CHANGED('test'))
        self.assertNotIn('private', nodes.StrataConnection.IS_CHANGED('test'))

    @unittest.skipUnless(os.name == 'nt', 'Windows filesystem profile aliases')
    def test_profile_case_aliases_share_the_same_transaction_lock(self):
        entered = threading.Event()
        def enter():
            with core.profile_lock('TEST', check=None): entered.set()
        with core.profile_lock('test', check=None):
            worker = threading.Thread(target=enter)
            worker.start()
            self.assertFalse(entered.wait(.15))
        self.assertTrue(entered.wait(3))
        worker.join()

    def prepared_manager(self):
        profile = {'runtime': str(Path(self.temp.name)/'runtime-root'), 'data_dir': str(Path(self.temp.name)/'data'),
                   'api_key': 'local-key', 'port': 8082}
        manager = core.Managed('test', profile)
        manager.python.parent.mkdir(parents=True)
        manager.python.touch()
        (manager.root/'tools').mkdir()
        (manager.root/'tools/managed_config.py').touch()
        return manager

    def test_configuration_spawn_failure_closes_log(self):
        manager = self.prepared_manager()
        logs = []
        def fail(*args, **kwargs):
            logs.append(kwargs['stdout'])
            raise OSError('spawn failed')
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', side_effect=fail):
            with self.assertRaises(OSError): manager.ensure(mock.Mock(), check=lambda: None)
        self.assertTrue(logs[0].closed)

    def test_first_configuration_handoff_precedes_native_subprocess(self):
        manager = self.prepared_manager()
        sequence = []
        def prepare(): sequence.append('handoff')
        def spawn(*args, **kwargs):
            sequence.append('spawn')
            raise OSError('test stops before starting any process')
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaises(OSError): manager.ensure(mock.Mock(), check=lambda: None, prepare_check=prepare)
        self.assertEqual(sequence, ['handoff', 'spawn'])
        def cancel(): raise InterruptedError('cancelled before preparation')
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen') as popen:
            with self.assertRaises(InterruptedError): manager.ensure(mock.Mock(), check=lambda: None, prepare_check=cancel)
            popen.assert_not_called()

    def test_same_gpu_gate_rejects_unverified_rocm_before_comfy_offload(self):
        import psutil
        comfy = types.ModuleType('comfy')
        mm = types.ModuleType('comfy.model_management')
        comfy.model_management = mm
        device = types.SimpleNamespace(type='cuda')  # ROCm also reports the CUDA torch device type.
        mm.get_torch_device = mock.Mock(return_value=device)
        mm.unload_all_models, mm.soft_empty_cache = mock.Mock(), mock.Mock()
        cuda = mock.Mock()
        cuda.mem_get_info.return_value = (16*1024**3, 24*1024**3)
        cuda.memory_allocated.return_value = 0
        torch = types.SimpleNamespace(version=types.SimpleNamespace(hip='6.0'), cuda=cuda)
        with mock.patch.dict(sys.modules, {'comfy':comfy, 'comfy.model_management':mm, 'torch':torch}), \
                mock.patch.object(psutil, 'virtual_memory', return_value=types.SimpleNamespace(available=120*1024**3)):
            with self.assertRaisesRegex(core.StrataError, 'NVIDIA'): core.gpu_handoff({})
            mm.unload_all_models.assert_not_called()
            cuda.mem_get_info.assert_not_called()
            torch.version.hip = None
            self.assertEqual(core.gpu_handoff({}), (device, 16*1024**3, 0))
        mm.unload_all_models.assert_called_once()

    def test_owner_registration_failure_kills_new_child(self):
        import psutil
        manager = self.prepared_manager()
        config, child = mock.Mock(), mock.Mock()
        config.poll.return_value, config.returncode = 0, 0
        child.pid = 123
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', side_effect=[config, child]), \
                mock.patch('psutil.Process', side_effect=psutil.AccessDenied(123)):
            with self.assertRaises(psutil.AccessDenied): manager.ensure(mock.Mock(), check=lambda: None)
        child.kill.assert_called_once()
        child.wait.assert_called_once_with(timeout=20)
        self.assertFalse(manager.state_path.exists())
        self.assertEqual(list(manager.dir.glob('*.tmp')), [])

    def test_preparation_cancel_still_kills_parent_when_child_access_is_denied(self):
        import psutil
        manager = self.prepared_manager()
        config, parent = mock.Mock(), mock.Mock()
        config.pid, config.poll.return_value = 123, None
        parent.children.side_effect = psutil.AccessDenied(123)
        checks = 0
        def cancel():
            nonlocal checks
            checks += 1
            if checks > 1: raise InterruptedError('cancelled')
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', return_value=config), \
                mock.patch('psutil.Process', return_value=parent), mock.patch('psutil.wait_procs', return_value=([], [])):
            with self.assertRaisesRegex(core.StrataError, 'preparation children'): manager.ensure(mock.Mock(), check=cancel)
        config.kill.assert_called_once()
        config.wait.assert_called_once_with(timeout=20)

    def test_disappearing_child_does_not_skip_remaining_kills(self):
        import psutil
        manager = core.Managed('test', {'runtime': self.temp.name})
        parent, first, second = mock.Mock(), mock.Mock(), mock.Mock()
        parent.children.return_value=[first,second]
        first.kill.side_effect=psutil.NoSuchProcess(123)
        with mock.patch.object(manager,'owned',return_value=parent), mock.patch('psutil.wait_procs',side_effect=[([],[first,second]),([] ,[])]):
            manager.stop()
        first.kill.assert_called_once()
        second.kill.assert_called_once()
        parent.terminate.assert_called_once()

    def test_profile_save_waits_for_active_profile_transaction(self):
        original={'mode':'external','url':'http://127.0.0.1:8080'}
        core.save_profile('test',original)
        saved=threading.Event()
        def save():
            core.save_profile('test',dict(original,url='http://127.0.0.1:8081'))
            saved.set()
        with core.profile_lock('test'):
            worker=threading.Thread(target=save)
            worker.start()
            self.assertFalse(saved.wait(.15))
            self.assertEqual(core.read_profile('test')['url'],original['url'])
        self.assertTrue(saved.wait(3))
        worker.join()

    def test_profile_rejects_truthy_false_and_invalid_numeric_values(self):
        base = {'mode': 'external', 'url': 'http://127.0.0.1:8080'}
        for value in ({'allow_lifecycle': 'false'}, {'same_gpu': 1}, {'timeout_s': 0},
                      {'timeout_s': float('nan')}, {'cleanup_timeout_s': -1}, {'context': True},
                      {'vision': 'unknown'}, {'min_free_vram_mib': -2}, {'min_free_vram_mib': -.5}, {'min_free_ram_gib': -.5}, {'timeout_s':10**1000}):
            with self.subTest(value=value), self.assertRaises(core.StrataError):
                core.normalize_profile(dict(base, **value))
        self.assertFalse(core.normalize_profile(dict(base, allow_lifecycle=False))['allow_lifecycle'])
        for value in ([], None, 'text'):
            with self.assertRaises(core.StrataError): core.normalize_profile(value)

    def test_profile_validates_ports_and_canonicalizes_endpoint(self):
        for address in ('http://localhost:notaport', 'http://localhost:0', 'http://localhost:65536',
                        'http://localhost:8080\n', 'http://@localhost', 'http://[broken'):
            with self.subTest(address=address), self.assertRaises(core.StrataError):
                core.normalize_profile({'mode': 'external', 'url': address})
        profile = core.normalize_profile({'mode': 'external', 'url': 'HTTP://LOCALHOST/v1/'})
        self.assertEqual(profile['url'], 'http://localhost:80/v1')
        self.assertEqual(core.normalize_profile({'mode': 'external', 'url': 'https://[::1]/'})['url'], 'https://[::1]:443')

    def test_failed_replace_removes_private_temporary_file(self):
        base = {'mode': 'external', 'url': 'http://127.0.0.1:8080', 'api_key': 'private-test'}
        core.save_profile('test', base)
        original = core.profile_path('test').read_bytes()
        with mock.patch.object(core.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError): core.save_profile('test', dict(base, api_key='new-private'))
        self.assertEqual(core.profile_path('test').read_bytes(), original)
        self.assertEqual(list(core.profile_path('test').parent.glob('*.tmp')), [])

    def test_lock_cancel_before_entry_and_file_does_not_grow(self):
        for _ in range(3):
            with core.file_lock('test', check=None): pass
        self.assertEqual(next((core.HOME/'locks').glob('*.lock')).stat().st_size, 1)
        def cancelled(): raise InterruptedError('cancelled before acquisition')
        with self.assertRaises(InterruptedError):
            with core.file_lock('test', check=cancelled): self.fail('entered cancelled transaction')

    def test_endpoint_lock_covers_aliases_and_different_gpu_flags(self):
        first = {'mode': 'external', 'url': 'http://127.0.0.1:8080', 'same_gpu': True}
        second = {'mode': 'external', 'url': 'http://localhost:8080/v1', 'same_gpu': False}
        entered = threading.Event()
        def enter():
            with core.service_lock(second, check=None): entered.set()
        with core.service_lock(first, check=None):
            worker = threading.Thread(target=enter)
            worker.start()
            self.assertFalse(entered.wait(.15))
        self.assertTrue(entered.wait(3))
        worker.join()


if __name__ == '__main__':
    unittest.main()
