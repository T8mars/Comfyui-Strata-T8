"""Seventh audit: request authorization, typed repair and precise handoff boundaries."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import sys
import threading
import types
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2
import test_audit_round6 as round6

core, nodes = fixture.core, fixture.nodes


def deep(value, layers=130):
    for _ in range(layers):
        value = {'next': value}
    return value


class HandoffPrecision(unittest.TestCase):
    def handoff(self, free, vram=.75, ram=.5, available=2**30):
        device = types.SimpleNamespace(type='cuda')
        mm = types.SimpleNamespace(get_torch_device=lambda: device,
                                   unload_all_models=mock.Mock(), soft_empty_cache=mock.Mock())
        torch = types.SimpleNamespace(version=types.SimpleNamespace(hip=None),
            cuda=types.SimpleNamespace(mem_get_info=lambda _: (free, 2**30), memory_allocated=lambda _: 0))
        comfy = types.ModuleType('comfy'); comfy.model_management = mm
        with mock.patch.dict(sys.modules, {'comfy':comfy, 'comfy.model_management':mm, 'torch':torch}), \
             mock.patch('psutil.virtual_memory', return_value=types.SimpleNamespace(available=available)):
            result = core.gpu_handoff({'min_free_vram_mib':vram, 'min_free_ram_gib':ram})
        return result, mm

    def test_fractional_vram_threshold_cannot_be_rounded_down(self):
        with self.assertRaisesRegex(core.StrataError, 'GPU conflict'):
            self.handoff(2**19)
        result, mm = self.handoff(3*2**18)
        self.assertEqual(result[1], 3*2**18)
        mm.unload_all_models.assert_called_once()

    def test_fractional_ram_threshold_is_inclusive_and_measured_in_gib(self):
        with self.assertRaisesRegex(core.StrataError, 'available RAM'):
            self.handoff(2**20, available=2**29-1)
        result, _ = self.handoff(2**20, available=2**29)
        self.assertEqual(result[2], 0)


class RepairAuthorization(unittest.TestCase):
    schema = '{"type":"object","required":["answer"],"properties":{"answer":{"type":"string"}}}'

    def test_an_error_message_cannot_authorize_structured_repair(self):
        for message in ('HTTP 500 internal_error: structured_output_failed appeared in a log',
                        'HTTP 422 bad_request: text says structured_output_failed',
                        'Strata connection failed: structured_output_failed'):
            with self.subTest(message=message), mock.patch.object(core, 'generate', side_effect=core.StrataError(message)) as generate:
                with self.assertRaises(core.StrataError):
                    nodes.StrataStructured().run(core.Connection('test'), 'question', schema=self.schema, repair_attempts=2)
                self.assertEqual(generate.call_count, 1)

    def wire_error(self, status, error):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                payload = json.dumps({'error':error}).encode()
                self.send_response(status); self.send_header('Content-Length', str(len(payload))); self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = core.Client({'url':f'http://127.0.0.1:{server.server_port}', 'timeout_s':3, 'api_key':'temporary-key'})
        try:
            client.request('/v1/chat/completions', check=None)
        except core.StrataError as error:
            return error
        self.fail('HTTP error returned as success')

    def test_real_wire_502_and_legacy_422_code_authorize_one_bounded_repair(self):
        for status in (502,422):
            error = self.wire_error(status, {'code':'structured_output_failed','message':'temporary-key output invalid'})
            with self.subTest(status=status), mock.patch.object(core, 'generate', side_effect=[error, [('{"answer":"fixed"}', '', '{"total_tokens":7}')]]) as generate:
                result = nodes.StrataStructured().run(core.Connection('test'), 'question', schema=self.schema, repair_attempts=1)
                self.assertEqual(generate.call_count, 2)
                self.assertEqual(json.loads(result[0]), {'answer':'fixed'})
                self.assertNotIn('temporary-key', str(error))

    def test_other_http_statuses_do_not_acquire_repair_authority_from_the_code(self):
        for status in (400,429,503):
            error = self.wire_error(status, {'code':'structured_output_failed','message':'invalid'})
            with self.subTest(status=status), mock.patch.object(core, 'generate', side_effect=error) as generate:
                with self.assertRaises(core.StrataError):
                    nodes.StrataStructured().run(core.Connection('test'), 'question', schema=self.schema, repair_attempts=2)
            self.assertEqual(generate.call_count, 1)

    def test_real_wire_message_marker_with_another_code_does_not_retry(self):
        error = self.wire_error(502, {'code':'engine_failed','message':'diagnostic: structured_output_failed'})
        with mock.patch.object(core, 'generate', side_effect=error) as generate:
            with self.assertRaises(core.StrataError):
                nodes.StrataStructured().run(core.Connection('test'), 'question', schema=self.schema, repair_attempts=2)
        self.assertEqual(generate.call_count, 1)

    def test_repaired_result_uses_successful_attempt_usage_and_empty_prompt_lists(self):
        with mock.patch.object(core, 'generate', side_effect=[[('{"answer":7}', '', '{"total_tokens":3}')],
                                                              [('{"answer":"中文 🚀"}', '', '{"total_tokens":9}')]]):
            result = nodes.StrataStructured().run(core.Connection('test'), 'question', schema=self.schema, repair_attempts=1)
        self.assertEqual(result[1:3], ([], []))
        self.assertEqual(json.loads(result[3]), {'total_tokens':9})


class RequestPreflight(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def assert_preflight(self, requests):
        with mock.patch.object(core,'read_profile',side_effect=AssertionError('request reached local profile')) as read, \
             mock.patch.object(core,'Client') as client, mock.patch.object(core,'gpu_handoff') as handoff:
            with self.assertRaises(core.StrataError): core.generate(core.Connection('test'), requests)
        read.assert_not_called(); client.assert_not_called(); handoff.assert_not_called()
        self.assertFalse((core.HOME/'locks').exists())

    def test_reserved_options_cannot_replace_validated_messages_or_service_identity(self):
        for options in ({'messages':[{'role':'user','content':'replaced'}]}, {'model':'different'}, {'stream':True}):
            with self.subTest(options=options), mock.patch.object(core,'generate') as generate:
                with self.assertRaises(core.StrataError): nodes.StrataText().run(core.Connection('test'), 'original', **options)
            generate.assert_not_called()

    def test_additional_supported_api_options_are_preserved(self):
        value = nodes.request('original', max_completion_tokens=17, presence_penalty=.5,
                              chat_template_kwargs={'enable_thinking':False}, refresh=3)
        self.assertEqual(value['max_completion_tokens'],17)
        self.assertEqual(value['presence_penalty'],.5)
        self.assertFalse(value['chat_template_kwargs']['enable_thinking'])
        self.assertNotIn('refresh',value)
        self.assertEqual(value['messages'][-1]['content'],'original')

    def test_deep_history_extensions_fail_before_any_profile_or_gpu_transaction(self):
        req = nodes.request('question', history=json.dumps([{'role':'assistant','content':'old','extension':deep(1)}]))
        self.assert_preflight([req])

    def test_deep_schema_literal_data_fails_before_any_profile_or_gpu_transaction(self):
        req = nodes.request('question')
        req['response_format']={'type':'json_schema','json_schema':{'name':'test','schema':{'type':'object','const':deep({})}}}
        self.assert_preflight([req])

    def test_non_string_request_member_names_cannot_serialize_as_duplicate_json_keys(self):
        req = nodes.request('question', extension={1:'first','1':'second'})
        self.assert_preflight([req])

    def test_nonfinite_and_non_json_api_extensions_fail_before_resource_mutation(self):
        for value in (float('inf'), float('nan'), object(), '\ud800', 10**5000):
            with self.subTest(value=type(value).__name__):
                self.assert_preflight([nodes.request('question', extension=value)])

    def test_empty_and_invalid_request_batches_cannot_open_a_resource_transaction(self):
        for requests in ([], None, {}, [None], ['not a request'], [{'messages':[]}],
                         [{'messages':[None]}], [nodes.request('q')]*65):
            with self.subTest(type=type(requests).__name__): self.assert_preflight(requests)

    def test_invalid_later_batch_item_is_rejected_before_the_first_request(self):
        first = nodes.request('first')
        second = nodes.request('second', extension=deep('late'))
        self.assert_preflight([first, second])

    def test_shared_json_subtrees_are_not_mistaken_for_a_cycle(self):
        value = {'text':'中文 🚀','items':[1,2]}
        profile={'mode':'external','url':'http://localhost:8080'}
        client=mock.Mock();client.request.side_effect=[round2.idle_status(),{'choices':[{'message':{'content':'answer'}}]}]
        with mock.patch.object(core,'read_profile',return_value=profile), mock.patch.object(core,'Client',return_value=client):
            self.assertEqual(core.generate(core.Connection('test'),[nodes.request('q', extension={'a':value,'b':value})]),[('answer','','{}')])

    def test_exact_128_container_levels_remain_accepted(self):
        profile={'mode':'external','url':'http://localhost:8080'}
        client=mock.Mock();client.request.side_effect=[round2.idle_status(),{'choices':[{'message':{'content':'answer'}}]}]
        req=nodes.request('q',extension=deep('end',127))  # Request root adds one container.
        with mock.patch.object(core,'read_profile',return_value=profile), mock.patch.object(core,'Client',return_value=client):
            self.assertEqual(core.generate(core.Connection('test'),[req])[0][0],'answer')

    def test_cyclic_extension_is_a_controlled_preflight_error(self):
        cycle={};cycle['next']=cycle
        self.assert_preflight([nodes.request('question', extension=cycle)])


class WireAndPixels(unittest.TestCase):
    def test_chunked_extensions_and_trailers_do_not_become_json_body_bytes(self):
        import socketserver
        raw=b'{"answer":"ok"}'
        response=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'+format(len(raw),'x').encode()+b';kind=json\r\n'+raw+b'\r\n0\r\nX-Audit: complete\r\n\r\n'
        class Handler(socketserver.BaseRequestHandler):
            def handle(self): self.request.recv(4096);self.request.sendall(response)
        server=socketserver.TCPServer(('127.0.0.1',0), Handler)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        client=core.Client({'url':f'http://127.0.0.1:{server.server_address[1]}','timeout_s':3})
        self.assertEqual(client.request('/v1/status',check=None),{'answer':'ok'})

    def test_direct_client_deep_json_is_rejected_before_connect(self):
        with mock.patch('http.client.HTTPConnection') as connection:
            with self.assertRaises(core.StrataError):
                core.Client({'url':'http://localhost:8080','timeout_s':3}).request('/v1/chat/completions',deep('end'),check=None)
        connection.return_value.connect.assert_not_called()

    def test_partial_alpha_preserves_pixel_order_and_blends_against_white(self):
        import numpy as np
        from PIL import Image
        tensor=fixture.Images.Tensor(np.array([[[[1,0,0,.5],[0,0,1,1]]]],dtype=np.float32))
        encoded=core.encode_images(tensor)
        image=Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',',1)[1])))
        self.assertEqual(image.size,(2,1))
        self.assertEqual(image.getpixel((0,0)),(255,127,127))
        self.assertEqual(image.getpixel((1,0)),(0,0,255))


class BrowserDiagnostics(unittest.TestCase):
    run_browser = round6.BrowserRawProfile.run_browser

    def test_queued_profile_is_fixed_while_a_later_local_draft_changes(self):
        self.run_browser(r'''
 const loading=action('加载').onclick();await flush();name.value='later-draft';
 const queued=calls.find(c=>c[0]==='/prompt')[1];
 if(queued.prompt['1'].inputs.profile!=='test')throw Error('queued profile mutated with the editor');
 pending.find(p=>p.path==='/prompt').resolve(response({prompt_id:'queued'}));await loading;
 if(name.value!=='later-draft'||!status.textContent.includes('queued'))throw Error('queue completion erased draft');
''')

    def test_nested_queue_diagnostics_are_visible_without_erasing_the_key_draft(self):
        self.run_browser(r'''
 key.value='temporary-key-draft';const loading=action('启动').onclick();await flush();
 pending.find(p=>p.path==='/prompt').resolve(response({error:{message:'Invalid prompt'},node_errors:{'1':{errors:[{message:'Connection invalid',details:'profile not found'}]}}},false));await loading;
 if(!status.textContent.includes('Connection invalid')||!status.textContent.includes('profile not found')||key.value!=='temporary-key-draft')throw Error('diagnostics or private draft lost');
 if(JSON.stringify(calls).includes('temporary-key-draft'))throw Error('queue sent unsaved key');
''')


if __name__ == '__main__': unittest.main()
