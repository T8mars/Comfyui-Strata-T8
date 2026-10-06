"""Second audit: new boundary/combination regressions, independent of the first 20 rounds."""
import asyncio
import base64
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from unittest import mock

import test_nodes as fixture

core, nodes = fixture.core, fixture.nodes


class JsonBoundaries(unittest.TestCase):
    def test_exponent_overflow_cannot_escape_structured_validation_or_extract(self):
        for text in ('1e999', '{"nested":[1e999]}'):
            with self.subTest(text=text):
                with self.assertRaises(nodes.StructuredOutputError):
                    nodes.validated(text, {})
                with self.assertRaises(core.StrataError):
                    nodes.StrataExtract().run(text)

    def test_schema_exponent_overflow_fails_before_inference(self):
        with mock.patch.object(core, 'generate') as generate:
            with self.assertRaises(core.StrataError):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema='{"maximum":1e999}')
            generate.assert_not_called()

    def test_boolean_schemas_remain_supported(self):
        self.assertEqual(nodes.validated('false', True), False)
        with self.assertRaises(nodes.StructuredOutputError):
            nodes.validated('{}', False)

    def test_schema_literal_ref_fields_are_data_not_reference_keywords(self):
        value = {'$ref':'https://example.invalid/data', '$dynamicRef':'file:///literal'}
        self.assertEqual(nodes.validated(json.dumps(value), {'const':value}), value)

    def test_missing_local_schema_reference_is_rejected_before_model_work(self):
        for reference in ('#/$defs/missing', '#missing_anchor'):
            with mock.patch.object(core, 'generate') as generate:
                with self.assertRaisesRegex(core.StrataError, 'local reference'):
                    nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=json.dumps({'$ref':reference}))
                generate.assert_not_called()

    def test_local_anchors_and_recursive_schema_continue_to_validate(self):
        schema = {'$defs':{'word':{'$anchor':'word','type':'string'}}, '$ref':'#word'}
        self.assertEqual(nodes.validated('"word"', schema), 'word')
        schema = {'type':'object','properties':{'next':{'$ref':'#'}}}
        self.assertEqual(nodes.validated('{"next":{}}', schema), {'next':{}})

    def test_reference_targets_are_checked_even_when_reached_through_literal_keywords(self):
        for schema in ({'description':'text', '$ref':'#/description'},
                       {'const':{'$ref':'https://example.invalid/schema'}, '$ref':'#/const'}):
            with self.subTest(schema=schema):
                with mock.patch.object(core, 'generate') as generate, self.assertRaisesRegex(core.StrataError, 'reference|local fragments'):
                    nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=json.dumps(schema))
                generate.assert_not_called()


class ProfileBoundaries(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_windows_device_profile_names_fail_as_node_errors(self):
        for name in ('CON','nul','AUX','PRN','COM1','lpt9','COM¹','LPT²'):
            with self.subTest(name=name), self.assertRaises(core.StrataError):
                core.profile_path(name)
        self.assertEqual(core.profile_path('CON_work').name, 'CON_work.json')

    def test_explicit_replacement_can_repair_a_corrupt_profile(self):
        path = core.profile_path('broken')
        path.parent.mkdir(parents=True)
        path.write_text('{broken private-key', encoding='utf-8')
        core.save_profile('broken', {'mode':'external','url':'http://localhost:8080','api_key':'replacement'})
        self.assertEqual(core.read_profile('broken')['api_key'], 'replacement')

    def test_corrupt_profile_cannot_silently_keep_an_unknown_key(self):
        path = core.profile_path('broken')
        path.parent.mkdir(parents=True)
        path.write_text('{broken private-key', encoding='utf-8')
        with self.assertRaisesRegex(core.StrataError, 'replacement API key') as caught:
            core.save_profile('broken', {'mode':'external','url':'http://localhost:8080','api_key':'__KEEP__'})
        self.assertNotIn('private-key', str(caught.exception))
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken private-key')

    def test_managed_paths_are_fixed_before_subprocess_cwd_changes(self):
        profile = core.normalize_profile({'mode':'managed','runtime':'runtime-relative','data_dir':'data-relative'})
        self.assertEqual(profile['runtime'], str(Path('runtime-relative').resolve()))
        self.assertEqual(profile['data_dir'], str(Path('data-relative').resolve()))
        for field in ('runtime','data_dir'):
            with self.subTest(field=field), self.assertRaises(core.StrataError):
                core.normalize_profile({'mode':'managed','runtime':self.temp.name,'data_dir':self.temp.name,field:'bad\0path'})


class PanelProfileBoundaries(unittest.TestCase):
    request = fixture.Panel.request

    def setUp(self):
        fixture.Panel.setUp(self)
        fixture.OwnedProcesses.setUp(self)

    def test_one_corrupt_profile_does_not_hide_all_valid_profiles(self):
        core.save_profile('good', {'mode':'external','url':'http://localhost:8080','api_key':'private-test'})
        core.profile_path('broken').write_text('{bad private-key', encoding='utf-8')
        response = asyncio.run(self.handlers['/strata_t8/profiles'](self.request()))
        self.assertEqual(response.status, 200)
        self.assertEqual(set(response.body['profiles']), {'good'})
        self.assertIn('broken', response.body['profile_errors'])
        self.assertNotIn('private', json.dumps(response.body))


class HttpBoundaries(unittest.TestCase):
    connection = fixture.Transport.connection

    def test_completed_response_after_deadline_is_not_success(self):
        conn, sock, _ = self.connection()
        response = conn.getresponse()
        conn.sock = sock
        def delayed_read(_):
            time.sleep(.04)
            return b'{"ok":true}'
        response.read.side_effect = delayed_read
        with mock.patch('http.client.HTTPConnection', return_value=conn):
            with self.assertRaisesRegex(core.StrataError, 'total timeout'):
                core.Client({'url':'http://localhost:8080'}).request('/v1/status', timeout=.02, check=None)

    def test_response_is_explicitly_closed_on_size_and_decode_failures(self):
        for raw in (b'x'*(16*1024**2+1), b'{broken'):
            conn, sock, _ = self.connection(raw)
            response = conn.getresponse()
            conn.sock = sock
            with mock.patch('http.client.HTTPConnection', return_value=conn), self.assertRaises(core.StrataError):
                core.Client({'url':'http://localhost:8080'}).request('/v1/status', check=None)
            response.close.assert_called_once()

    def test_non_json_rate_limit_preserves_http_status_without_echoing_body(self):
        conn, _, _ = self.connection(b'private-key: too many requests', 429)
        with mock.patch('http.client.HTTPConnection', return_value=conn), self.assertRaisesRegex(core.StrataError, 'HTTP 429') as caught:
            core.Client({'url':'http://localhost:8080','api_key':'private-key'}).request('/v1/status', check=None)
        self.assertNotIn('private-key', str(caught.exception))

    def test_exact_response_limit_is_valid_and_one_extra_byte_is_rejected(self):
        prefix = b'{"padding":"'
        suffix = b'"}'
        raw = prefix+b' '*(16*1024**2-len(prefix)-len(suffix))+suffix
        conn, _, _ = self.connection(raw)
        with mock.patch('http.client.HTTPConnection', return_value=conn):
            self.assertEqual(len(core.Client({'url':'http://localhost:8080'}).request('/v1/status', check=None)['padding']), len(raw)-len(prefix)-len(suffix))
        conn, _, _ = self.connection(raw+b' ')
        with mock.patch('http.client.HTTPConnection', return_value=conn), self.assertRaisesRegex(core.StrataError, '16 MiB'):
            core.Client({'url':'http://localhost:8080'}).request('/v1/status', check=None)


def idle_status(**changes):
    status = {'service':'strata', 'protocol_version':1, 'model':'fixture', 'loaded':False,
              'vision':{'enabled':False}, 'activity':{'in_flight':0}, 'concurrency':{'serving':1},
              'processes':{name:{'running':False,'loaded':False,'starting':False} for name in ('engine','vision')}}
    return dict(status, **changes)


class ServiceBoundaries(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_protocol_boolean_and_malformed_additional_processes_are_rejected(self):
        for status in (idle_status(protocol_version=True), idle_status(processes={**idle_status()['processes'], 'future':False})):
            with self.subTest(status=status), self.assertRaises(core.StrataError):
                core.service_status(status)

    def test_same_gpu_control_load_rejects_parallel_service_before_mutation(self):
        profile = {'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':True}
        client = mock.Mock()
        client.request.return_value = idle_status(concurrency={'serving':2})
        with mock.patch.object(core, 'read_profile', return_value=profile), mock.patch.object(core, 'Client', return_value=client), \
                mock.patch.object(core, 'cleanup') as cleanup, mock.patch.object(core, 'gpu_handoff') as handoff:
            with self.assertRaisesRegex(core.StrataError, 'parallel=1'):
                core.control(core.Connection('test'), 'load', check=lambda: None)
        cleanup.assert_not_called()
        handoff.assert_not_called()
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['/v1/status'])

    def test_release_requires_final_activity_idle(self):
        client = mock.Mock()
        def request(*args, **kwargs):
            return idle_status() if client.request.call_count == 1 else idle_status(activity={'in_flight':1})
        client.request.side_effect = request
        with self.assertRaisesRegex(core.StrataError, 'release could not be confirmed'):
            core.cleanup(client, {'cleanup_timeout_s':.01}, None, None)

    def test_cleanup_http_timeouts_use_remaining_budget(self):
        client = mock.Mock()
        waits = []
        def request(path, *args, **kwargs):
            waits.append(kwargs['timeout'])
            return idle_status()
        client.request.side_effect = request
        with mock.patch.object(core.time, 'monotonic', side_effect=[1,1,1,1.01,1.02]):
            core.cleanup(client, {'cleanup_timeout_s':.05}, None, None)
        self.assertEqual(len(waits), 3)
        self.assertTrue(all(0 < timeout <= .050001 for timeout in waits), waits)
        self.assertLess(waits[-1], waits[0])

    def test_cleanup_poll_sleep_does_not_add_fixed_two_hundred_milliseconds(self):
        client = mock.Mock()
        client.request.side_effect = core.StrataError('unreachable')
        start = time.monotonic()
        with self.assertRaises(core.StrataError):
            core.cleanup(client, {'cleanup_timeout_s':.02}, None, None)
        self.assertLess(time.monotonic()-start, .15)


class ManagedBoundaries(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp
    prepared_manager = fixture.OwnedProcesses.prepared_manager

    def test_cancel_after_preparation_exit_never_spawns_server(self):
        manager = self.prepared_manager()
        config = mock.Mock()
        config.poll.return_value, config.returncode = 0, 0
        checks = 0
        def check():
            nonlocal checks
            checks += 1
            if checks > 1:
                raise InterruptedError('cancelled after preparation')
        with mock.patch.object(core.socket, 'socket'), mock.patch.object(core.subprocess, 'Popen', side_effect=[config, OSError('unexpected server spawn')]) as popen:
            with self.assertRaisesRegex(InterruptedError, 'after preparation'):
                manager.ensure(mock.Mock(), check=check)
        self.assertEqual(popen.call_count, 1)
        self.assertFalse(manager.state_path.exists())


class BrowserBoundaries(unittest.TestCase):
    """The subprocess budget includes hosted runner startup; UI behavior remains asserted."""
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the browser error contract test')
    def test_comfy_queue_validation_error_and_non_json_response_are_readable(self):
        script = r'''
const fs=require('fs'),vm=require('vm');
let response;
const context=vm.createContext({app:{registerExtension(){}},api:{fetchApi:async()=>response},console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,''),context);
(async()=>{
  response={ok:false,status:400,json:async()=>({error:{type:'prompt_outputs_failed_validation',message:'Prompt outputs failed validation',details:'Profile is missing'},node_errors:{}})};
  try { await vm.runInContext('request("/prompt",{})',context);throw Error('expected rejection'); }
  catch(error) { if(!error.message.includes('Profile is missing')||error.message.includes('[object Object]')) throw error; }
  response={ok:false,status:503,json:async()=>{throw new SyntaxError('unexpected HTML')}};
  try { await vm.runInContext('request("/prompt",{})',context);throw Error('expected rejection'); }
  catch(error) { if(!error.message.includes('HTTP 503')) throw error; }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(fixture.NODE_ROOT/'web/strata.js')], capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the browser queue receipt test')
    def test_missing_prompt_receipt_is_not_reported_as_queued_and_busy_is_reset(self):
        script = r'''
const fs=require('fs'),vm=require('vm');
class Element {
  constructor(tag) { this.tag=tag;this.children=[];this.value='';this.style={cssText:''}; }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children=values; }
  setAttribute() {}
}
let extension,panel;
const app={extensionManager:{registerSidebarTab:value=>panel=value},registerExtension:value=>extension=value};
const api={fetchApi:async path=>({ok:true,json:async()=>path.endsWith('/profiles') ? {profiles:{},home:'test'} : {node_errors:{}}})};
const context=vm.createContext({app,api,document:{createElement:tag=>new Element(tag)},console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,''),context);
(async()=>{
  await extension.setup(); const root=new Element('root');panel.render(root);await new Promise(resolve=>setImmediate(resolve));
  const load=root.children.find(e=>e.tag==='div').children.find(e=>e.textContent==='加载');
  await load.onclick();
  const status=root.children.find(e=>e.tag==='pre').textContent;
  if(!status.includes('任务 ID')||status.includes('服务操作已进入')) throw Error(status);
  if(load.disabled) throw Error('busy state remains after queue error');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(fixture.NODE_ROOT/'web/strata.js')], capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


class HttpWireBoundaries(unittest.TestCase):
    def setUp(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_GET(self):
                status, body, chunked = outer.reply
                self.send_response(status)
                self.send_header('Connection', 'close')
                self.send_header('Transfer-Encoding' if chunked else 'Content-Length', 'chunked' if chunked else str(len(body)))
                self.end_headers()
                try:
                    if chunked:
                        for chunk in (body[:3], body[3:]):
                            self.wfile.write(f'{len(chunk):x}\r\n'.encode()+chunk+b'\r\n')
                        self.wfile.write(b'0\r\n\r\n')
                    else:
                        self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The response-cap test deliberately closes before the body is drained.
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = core.Client({'url':f'http://127.0.0.1:{self.server.server_port}'})

    def test_real_chunked_response_and_non_json_429(self):
        self.reply = (200, b'{"ok":true}', True)
        self.assertEqual(self.client.request('/test', check=None), {'ok':True})
        self.reply = (429, b'<html>rate limited</html>', True)
        with self.assertRaisesRegex(core.StrataError, 'HTTP 429'):
            self.client.request('/test', check=None)

    def test_real_connection_close_response_obeys_byte_limit(self):
        self.reply = (200, b'x'*(16*1024**2+1), False)
        with self.assertRaisesRegex(core.StrataError, '16 MiB'):
            self.client.request('/test', timeout=3, check=None)

    def test_real_response_cannot_supply_nonfinite_usage_json(self):
        self.reply = (200, b'{"usage":{"total_tokens":1e999}}', False)
        with self.assertRaisesRegex(core.StrataError, 'finite numbers'):
            self.client.request('/test', check=None)


@unittest.skipUnless(fixture.SOURCE_ROOT, 'Set STRATA_SOURCE_DIR for real HTTP structured-repair integration')
class StructuredWireBoundaries(unittest.TestCase):
    setUp = fixture.BatchHTTP.setUp

    def test_server_schema_repair_then_extract_preserves_order_and_releases_each_attempt(self):
        from serve.server import MockEngine
        value = {'shots':[{'prompt':'first','duration':1.25},{'prompt':'second','duration':2}]}
        self.engine.reply = MockEngine(fixture.ByteTokenizer(), ['not JSON', json.dumps(value)], max_context=4096)
        schema = {'type':'object','required':['shots'],'properties':{'shots':{'type':'array','minItems':2,
                  'items':{'type':'object','required':['prompt','duration'],'properties':{'prompt':{'type':'string'},'duration':{'type':'number'}}}}}}
        result = nodes.StrataStructured().run(core.Connection('test'), 'two shots', schema=json.dumps(schema),
                                              repair_attempts=1, max_tokens=256, reasoning_effort='none')
        self.assertEqual(result[1:3], (['first','second'], ['first','second']))
        self.assertEqual(nodes.StrataExtract().run(result[0], '/shots', 'prompt', 'array')[1], ['first','second'])
        self.assertEqual(nodes.StrataNumber().run(result[0], '/shots/0/duration'), (1.25,))
        self.assertEqual((self.engine.starts,self.engine.closes), (2,2))
        self.assertFalse(self.engine.alive())
        self.assertEqual(self.svc.v1_status()['activity']['in_flight'], 0)


class StandalonePackageBoundaries(unittest.TestCase):
    def test_current_public_zip_loads_after_rename_without_runtime_source(self):
        from test_release import builder
        tracked = subprocess.check_output(['git','ls-files','-z'], cwd=fixture.NODE_ROOT)
        names = [name for name in tracked.decode('utf-8').split('\0') if name and (name in builder.SHIPPING or name.startswith(('web/','examples/')))]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [*names, 'meta.json']:
                destination = root/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((fixture.NODE_ROOT/name).read_bytes())
            with mock.patch.object(builder,'ROOT',root), mock.patch.object(builder.subprocess,'check_output',return_value=tracked), mock.patch('builtins.print'):
                builder.main()
            archive = next((root/'dist').glob('*.zip'))
            with zipfile.ZipFile(archive) as package:
                package.extractall(root/'unpacked')
            extracted = root/'unpacked/Comfyui-Strata-T8'
            renamed = root/'unpacked/renamed-node-folder'
            extracted.rename(renamed)
            env = dict(os.environ, STRATA_COMFY_HOME=str(root/'fresh-home'))
            env.pop('STRATA_SOURCE_DIR',None)
            env.pop('PYTHONPATH',None)
            script = r'''
import importlib.util,json,sys
from pathlib import Path
root=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('standalone_node',root/'__init__.py',submodule_search_locations=[str(root)])
node=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=node
spec.loader.exec_module(node)
assert len(node.NODE_CLASS_MAPPINGS)==10
assert node.nodes.StrataExtract().run('["a","b"]')[1:]==(['a','b'],['a','b'])
assert not node.nodes.core.HOME.exists()
assert 'serve' not in sys.modules
print(json.dumps({'nodes':len(node.NODE_CLASS_MAPPINGS),'runtime_imported':False}))
'''
            result = subprocess.run([sys.executable,'-B','-c',script,str(renamed)], cwd=root, env=env,
                                    capture_output=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout), {'nodes':10,'runtime_imported':False})


if __name__ == '__main__':
    unittest.main()
