"""Fifth audit: dialect boundaries, wire ambiguity and pending UI operations."""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2

core, nodes = fixture.core, fixture.nodes
DRAFT7 = 'http://json-schema.org/draft-07/schema#'
DRAFT2020 = 'https://json-schema.org/draft/2020-12/schema'


class ScopedSchemas(unittest.TestCase):
    def test_wrong_dialect_types_are_controlled_before_model_work(self):
        for declaration in ([], {}, 7, False, None, 'https://schema.invalid/unknown'):
            with self.subTest(declaration=declaration), mock.patch.object(core, 'generate') as generate:
                with self.assertRaises(core.StrataError):
                    nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=json.dumps({'type':'object', '$schema':declaration}))
                generate.assert_not_called()

    def test_cross_draft_child_tuple_uses_its_own_meta_schema(self):
        schema = {'$schema':DRAFT2020, '$id':'https://fixture.invalid/root', 'type':'object', 'properties':{
            'pair':{'$schema':DRAFT7, '$id':'https://fixture.invalid/legacy', 'type':'array', 'items':[{'type':'string'},{'type':'integer'}], 'additionalItems':False}}}
        self.assertEqual(nodes.validated('{"pair":["ok",2]}', schema), {'pair':['ok',2]})
        for value in ('{"pair":[2,"bad"]}', '{"pair":["ok",2,3]}'):
            with self.subTest(value=value), self.assertRaises(nodes.StructuredOutputError): nodes.validated(value, schema)

    def test_grandchild_reference_keyword_inherits_the_child_draft(self):
        schema = {'$schema':DRAFT2020, '$id':'https://fixture.invalid/root', 'type':'object', 'properties':{
            'old':{'$schema':DRAFT7, '$id':'https://fixture.invalid/legacy', 'type':'object', 'properties':{
                'inner':{'$dynamicRef':'https://annotation.invalid/data', 'type':'string'}}}}}
        self.assertEqual(nodes.validated('{"old":{"inner":"yes"}}', schema), {'old':{'inner':'yes'}})
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"old":{"inner":2}}', schema)

    def test_draft7_ref_siblings_do_not_activate_unused_remote_assertions(self):
        schema = {'$schema':DRAFT7, 'type':'object', '$ref':'#/definitions/target',
                  'definitions':{'target':{'type':'object','properties':{'n':{'type':'integer'}}}},
                  'properties':{'unused':{'$ref':'https://unused.invalid/never'}}}
        self.assertEqual(nodes.validated('{"n":1,"unused":"data"}', schema), {'n':1,'unused':'data'})
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"n":"bad"}', schema)

    def test_uri_fragment_pointer_percent_and_tilde_decoding_keep_the_selected_schema(self):
        schema = {'type':'object', '$defs':{'a b/~1':{'type':'integer'}},
                  'properties':{'n':{'$ref':'#/$defs/a%20b~1~01'}}}
        self.assertEqual(nodes.validated('{"n":2}', schema), {'n':2})
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"n":"wrong"}', schema)

    def test_cross_draft_literal_schema_and_reference_keys_remain_data(self):
        literal = {'$schema':'https://literal.invalid/draft', '$ref':'https://literal.invalid/ref', 'items':[1,2]}
        schema = {'$schema':DRAFT2020, '$id':'https://fixture.invalid/root', 'type':'object', 'properties':{
            'old':{'$schema':DRAFT7, '$id':'https://fixture.invalid/legacy', 'const':literal}}}
        self.assertEqual(nodes.validated(json.dumps({'old':literal}), schema), {'old':literal})

    def test_mixed_property_and_schema_dependencies_keep_both_keyword_meanings(self):
        for draft in ('http://json-schema.org/draft-04/schema#', DRAFT7):
            for reverse in (False,True):
                dependency={'properties':{'n':{'type':'integer'}}}
                entries=[('a',['b']),('c',dependency)]
                schema={'$schema':draft,'type':'object','dependencies':dict(reversed(entries) if reverse else entries)}
                with self.subTest(draft=draft,reverse=reverse):
                    value={'a':True,'b':False,'c':True,'n':2}
                    self.assertEqual(nodes.validated(json.dumps(value),schema),value)
                    for invalid in ('{"a":true}', '{"c":true,"n":"bad"}'):
                        with self.assertRaises(nodes.StructuredOutputError):nodes.validated(invalid,schema)

    def test_schema_dependency_after_property_name_list_still_preflights_active_references(self):
        for reverse in (False,True):
            entries=[('a',['b']),('c',{'$ref':'https://active.invalid/schema'})]
            schema={'$schema':DRAFT7,'type':'object','dependencies':dict(reversed(entries) if reverse else entries)}
            with self.subTest(reverse=reverse),mock.patch.object(core,'generate') as generate:
                with self.assertRaisesRegex(core.StrataError,'local fragments'):
                    nodes.StrataStructured().run(core.Connection('test'),'hi',schema=json.dumps(schema))
                generate.assert_not_called()

    def test_legacy_mixed_dependencies_anchor_fails_before_inference_with_a_pointer_alternative(self):
        for draft,identifier in (('http://json-schema.org/draft-04/schema#','id'),(DRAFT7,'$id')):
            schema={'$schema':draft,'type':'object','dependencies':{'c':{'$ref':'#t'},'a':['b']},
                    'definitions':{'t':{identifier:'#t','required':['d']}}}
            with self.subTest(draft=draft),mock.patch.object(core,'generate') as generate:
                with self.assertRaisesRegex(core.StrataError,'mixed legacy dependencies.*JSON Pointer'):
                    nodes.StrataStructured().run(core.Connection('test'),'hi',schema=json.dumps(schema))
                generate.assert_not_called()
            schema['dependencies']['c']['$ref']='#/definitions/t'
            self.assertEqual(nodes.validated('{"c":1,"d":true}',schema),{'c':1,'d':True})
            with self.assertRaises(nodes.StructuredOutputError):nodes.validated('{"c":1}',schema)


class UnicodeContracts(unittest.TestCase):
    def test_decoded_surrogates_never_escape_structured_or_extract_outputs(self):
        for source in ('{"s":"\\ud800"}', '{"\\udfff":1}', '["\\ud800"]'):
            with self.subTest(source=source), self.assertRaises(core.StrataError): core.json_loads(source)
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"s":"\\ud800"}', {'type':'object'})
        with self.assertRaises(core.StrataError): nodes.StrataExtract().run('["\\ud800"]', expected_type='array')
        self.assertEqual(core.json_loads('"\\ud83d\\ude80"'), '🚀')

    def test_unpaired_request_text_fails_before_generate_or_image_copy(self):
        cases = [(nodes.StrataText, {'prompt':'\ud800'}), (nodes.StrataText, {'prompt':'hi','system':'\udfff'}),
                 (nodes.StrataText, {'prompt':'hi','history':'[{"role":"user","content":"\\ud800"}]'}),
                 (nodes.StrataPrompt, {'text':'hi','template':'\ud800'}),
                 (nodes.StrataImage, {'images':object(),'question':'\ud800'}),
                 (nodes.StrataBatch, {'texts':['valid','\ud800']})]
        for cls, kwargs in cases:
            with self.subTest(node=cls.__name__, keys=list(kwargs)), mock.patch.object(core,'generate') as generate, mock.patch.object(core,'encode_images') as encode:
                with self.assertRaises(core.StrataError): cls().run(core.Connection('test'), **kwargs)
                generate.assert_not_called(); encode.assert_not_called()

    def test_json_pointer_unicode_empty_and_percent_literal_keys_are_not_uri_fragments(self):
        data={'':{'a%20b':{'🚀/键~':7}, 'a b':9}}
        self.assertEqual(nodes.pointer(data, '//a%20b/🚀~1键~0'), 7)
        self.assertEqual(nodes.StrataNumber().run(json.dumps(data,ensure_ascii=False), '//a b'), (9.0,))
        with self.assertRaises(core.StrataError): nodes.pointer(data, '#//a%20b')


class WireFraming(unittest.TestCase):
    def response_server(self, headers, raw=b'{"ok":true}', *, wait=False):
        seen, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200)
                for key,value in headers: self.send_header(key,value)
                self.end_headers(); self.wfile.flush(); seen.set()
                if wait: release.wait(3)
                try: self.wfile.write(raw)
                except OSError: pass
        server=ThreadingHTTPServer(('127.0.0.1',0), Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown); self.addCleanup(release.set)
        return core.Client({'url':f'http://127.0.0.1:{server.server_port}'}), seen, release

    def test_ambiguous_response_framing_is_not_accepted_as_success(self):
        raw=b'{"ok":true}'
        for headers, body in [([('Content-Length','-1')],raw),
                              ([('Content-Length',str(len(raw))),('Content-Length','999')],raw),
                              ([('Content-Length',str(len(raw))),('Content-Length',str(len(raw)))],raw),
                              ([('Transfer-Encoding','chunked'),('Content-Length',str(len(raw)))],format(len(raw),'x').encode()+b'\r\n'+raw+b'\r\n0\r\n\r\n'),
                              ([('Transfer-Encoding','identity')],raw)]:
            with self.subTest(headers=headers):
                client, _, _=self.response_server(headers,body)
                with self.assertRaisesRegex(core.StrataError,'framing|Content-Length|Transfer-Encoding'): client.request('/test',check=None)

    def test_oversized_declared_response_is_rejected_without_waiting_for_a_body(self):
        client, seen, release=self.response_server([('Content-Length',str(16*1024**2+1))],wait=True)
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(core.StrataError,'16 MiB'): client.request('/test',timeout=1,check=None)
            self.assertLess(time.monotonic()-started,.8)
            self.assertTrue(seen.wait(.2))
        finally: release.set()

    def test_cancel_while_real_response_headers_are_pending_closes_the_owned_connection(self):
        accepted, release=threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                accepted.set(); release.wait(3)
                try: self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
                except OSError: pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.addCleanup(release.set)
        def check():
            if accepted.is_set(): raise InterruptedError('cancel during headers')
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(InterruptedError,'during headers'):
                core.Client({'url':f'http://127.0.0.1:{server.server_port}'}).request('/test',check=check)
            self.assertLess(time.monotonic()-started,1)
        finally: release.set()

    def test_outbound_json_limit_is_checked_before_connect(self):
        conn, _, _=fixture.Transport.connection(self)
        with mock.patch('http.client.HTTPConnection',return_value=conn), self.assertRaisesRegex(core.StrataError,'64 MiB'):
            core.Client({'url':'http://localhost:8080'}).request('/test',{'text':'x'*(64*1024**2)},check=None)
        conn.connect.assert_not_called();conn.request.assert_not_called();conn.close.assert_called()

    def test_cancel_while_real_body_read_is_pending_does_not_wait_for_a_buffer_lock(self):
        client, seen, release=self.response_server([('Content-Length','11')],wait=True)
        def check():
            if seen.is_set(): raise InterruptedError('cancel during body')
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(InterruptedError,'during body'):client.request('/test',check=check)
            self.assertLess(time.monotonic()-started,1)
        finally:release.set()

    def test_http_surrogate_output_is_rejected_without_echoing_a_private_key(self):
        client, _, _=self.response_server([('Content-Length','14')],b'{"s":"\\ud800"}')
        client.profile['api_key']='temporary-private'
        with self.assertRaisesRegex(core.StrataError,'JSON') as caught: client.request('/test',check=None)
        self.assertNotIn('temporary-private',str(caught.exception))

    def test_failed_shutdown_preserves_cancellation_without_claiming_engine_release(self):
        conn, sock, release=fixture.Transport.connection(self,stalled=True)
        sock.shutdown.side_effect=OSError('shutdown unavailable')
        checks=0
        def check():
            nonlocal checks
            checks+=1
            if checks>1:raise InterruptedError('cancel despite transport error')
        started=time.monotonic()
        try:
            with mock.patch('http.client.HTTPConnection',return_value=conn),self.assertRaisesRegex(InterruptedError,'transport error'):
                core.Client({'url':'http://localhost:8080'}).request('/test',check=check)
            self.assertLess(time.monotonic()-started,1)
            sock.close.assert_called_once();self.assertFalse(conn.auto_open)
        finally:release.set()
        # Engine release is a separate cleanup/status operation, never inferred from this exception.


class ManagedAndLifecycle(unittest.TestCase):
    setUp=fixture.OwnedProcesses.setUp
    prepared_manager=fixture.OwnedProcesses.prepared_manager

    def test_parent_disappearing_before_children_enumeration_cannot_claim_release(self):
        import psutil
        manager=core.Managed('test',{'runtime':self.temp.name});manager.state_path.write_text('{"evidence":true}')
        proc=mock.Mock();proc.children.side_effect=psutil.NoSuchProcess(123)
        with mock.patch.object(manager,'owned',return_value=proc),mock.patch('psutil.wait_procs',return_value=([],[])):
            with self.assertRaisesRegex(core.StrataError,'children|resource|ownership'): manager.stop()
        self.assertTrue(manager.state_path.exists());proc.terminate.assert_not_called()

    def test_control_status_and_non_same_gpu_load_reject_wrong_protocol_before_mutation(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':False}
        for action in ('status','start','load'):
            client=mock.Mock();client.request.return_value=round2.idle_status(protocol_version=2)
            with self.subTest(action=action),mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client):
                with self.assertRaisesRegex(core.StrataError,'protocol'): core.control(core.Connection('test'),action,check=lambda:None)
            self.assertTrue(all(call.args[0]=='/v1/status' for call in client.request.call_args_list))

    def test_non_same_gpu_load_does_not_mutate_an_existing_active_request(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':False}
        client=mock.Mock();client.request.return_value=round2.idle_status(activity={'in_flight':1})
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError,'idle|busy'): core.control(core.Connection('test'),'load',check=lambda:None)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/status']);cleanup.assert_not_called()

    def test_changed_managed_key_invalidates_an_old_fingerprint_before_preparation(self):
        manager=self.prepared_manager();manager.state_path.write_text('{"fingerprint":"old"}')
        order=[]
        def spawn(*args,**kwargs): order.append('prepare');raise OSError('stop before native process')
        with mock.patch.object(manager,'owned',return_value=mock.Mock()),mock.patch.object(manager,'stop',side_effect=lambda:order.append('stop') or True), \
             mock.patch.object(core.socket,'socket'),mock.patch.object(core.subprocess,'Popen',side_effect=spawn):
            with self.assertRaises(OSError):manager.ensure(mock.Mock(),check=lambda:None,prepare_check=lambda:order.append('handoff'))
        self.assertEqual(order,['stop','handoff','prepare'])


class FinalImageBoundary(unittest.TestCase):
    setUp=fixture.OwnedProcesses.setUp

    def test_cancel_after_last_image_conversion_never_enters_an_inference_transaction(self):
        import numpy as np
        cancelled=False
        class Tensor(fixture.Images.Tensor):
            def __iter__(self): return (Tensor(a) for a in self.array)
            def float(self): return self
            def numpy(self):
                nonlocal cancelled
                cancelled=True
                return self.array
        mm=types.ModuleType('comfy.model_management')
        def check():
            if cancelled: raise InterruptedError('after final image')
        mm.throw_exception_if_processing_interrupted=check
        comfy=types.ModuleType('comfy');comfy.model_management=mm
        with mock.patch.dict(sys.modules,{'comfy':comfy,'comfy.model_management':mm}),mock.patch.object(core,'read_profile') as read, mock.patch.object(core,'Client') as client:
            with self.assertRaisesRegex(InterruptedError,'final image'):nodes.StrataImageBatch().run(core.Connection('test'),Tensor(np.zeros((1,2,2,3))))
        read.assert_not_called();client.assert_not_called();self.assertFalse((core.HOME/'locks').exists())


@unittest.skipUnless(fixture.SOURCE_ROOT,'Set STRATA_SOURCE_DIR for actual compound-schema HTTP integration')
class CompoundSchemaWire(unittest.TestCase):
    setUp=fixture.BatchHTTP.setUp

    def test_compound_tuple_survives_real_http_and_local_validation_then_releases(self):
        from serve.server import MockEngine
        answer={'pair':['yes',2]}
        self.engine.reply=MockEngine(fixture.ByteTokenizer(),json.dumps(answer),max_context=4096)
        schema={'$schema':DRAFT2020,'$id':'https://fixture.invalid/root','type':'object','properties':{
            'pair':{'$schema':DRAFT7,'$id':'https://fixture.invalid/old','type':'array',
                    'items':[{'type':'string'},{'type':'integer'}],'additionalItems':False}}}
        result=nodes.StrataStructured().run(core.Connection('test'),'pair',schema=json.dumps(schema),max_tokens=128,reasoning_effort='none')
        self.assertEqual(json.loads(result[0]),answer);self.assertEqual(nodes.StrataNumber().run(result[0],'/pair/1'),(2.0,))
        self.assertEqual((self.engine.starts,self.engine.closes),(1,1));self.assertFalse(self.engine.alive())

    def test_inherited_annotation_survives_real_http_and_local_validation_then_releases(self):
        from serve.server import MockEngine
        answer={'old':{'inner':'yes'}}
        self.engine.reply=MockEngine(fixture.ByteTokenizer(),json.dumps(answer),max_context=4096)
        schema={'$schema':DRAFT2020,'$id':'https://fixture.invalid/root','type':'object','properties':{
            'old':{'$schema':DRAFT7,'$id':'https://fixture.invalid/old','type':'object','properties':{
                'inner':{'type':'string','$dynamicRef':'https://annotation.invalid/data'}}}}}
        result=nodes.StrataStructured().run(core.Connection('test'),'value',schema=json.dumps(schema),max_tokens=128,reasoning_effort='none')
        self.assertEqual(json.loads(result[0]),answer)
        self.assertEqual(nodes.StrataExtract().run(result[0],'/old/inner',expected_type='string')[0],'yes')
        self.assertEqual((self.engine.starts,self.engine.closes),(1,1));self.assertFalse(self.engine.alive())


class BrowserPendingOperations(unittest.TestCase):
    def run_browser(self, body):
        if not shutil.which('node'): self.skipTest('Node.js is needed for actual browser handlers')
        harness=r'''
const fs=require('fs'),vm=require('vm');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.checked=false;this.style={cssText:''};}
 append(...v){this.children.push(...v);if(this.tag==='select'&&!this.value&&v[0]?.value)this.value=v[0].value;}
 replaceChildren(...v){this.children=v;if(this.tag==='select')this.value='';}
 setAttribute(){}
}
let ext,panel;const calls=[],pending=[];const saved={test:{mode:'external',url:'http://localhost:8080',context:32768}};
const response=(data,ok=true,status=ok?200:400)=>({ok,status,json:async()=>data});
const api={clientId:'test-client',fetchApi:async(path,options)=>{
 calls.push([path,options?.body?JSON.parse(options.body):null]);
 if(path.endsWith('/profiles'))return response({profiles:saved,home:'temporary-home'});
 return new Promise(resolve=>pending.push({path,resolve}));
}};
const app={registerExtension:v=>ext=v,extensionManager:{registerSidebarTab:v=>panel=v}};
const context=vm.createContext({app,api,document:{createElement:t=>new Element(t)},console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,''),context);
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await ext.setup();const root=new Element('root');panel.render(root);await flush();
 const select=root.children.find(e=>e.tag==='select');
 const name=root.children.find(e=>e.tag==='input'&&e.placeholder==='配置名称');
 const fields=root.children.find(e=>e.tag==='textarea');
 const key=root.children.find(e=>e.tag==='input'&&e.type==='password');
 const refresh=root.children.find(e=>e.textContent==='刷新配置');
 const actions=root.children.find(e=>e.tag==='div').children;
 const status=root.children.find(e=>e.tag==='pre');
 const action=label=>actions.find(e=>e.textContent===label);
''' + body + r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result=subprocess.run([shutil.which('node'),'-e',harness,str(fixture.NODE_ROOT/'web/strata.js')],capture_output=True,encoding='utf8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_refresh_preserves_edits_made_before_the_refresh_started(self):
        self.run_browser(r'''
 fields.value=JSON.stringify({mode:'external',url:'http://localhost:9000',context:8192});key.value='replacement-draft';
 const before=[name.value,fields.value,key.value];await refresh.onclick();
 if(JSON.stringify(before)!==JSON.stringify([name.value,fields.value,key.value]))throw Error('refresh discarded an existing dirty draft');
 select.value='test';select.onchange();
 if(JSON.parse(fields.value).context!==32768 || key.value!=='')throw Error('explicit selection failed to load the saved profile');
''')

    def test_late_action_completion_cannot_replace_a_newer_status_result(self):
        self.run_browser(r'''
 const loading=action('加载').onclick();await flush();
 const showing=action('状态').onclick();await flush();
 pending.find(p=>p.path.endsWith('/control')).resolve(response({service:'strata',model:'new-status'}));await showing;
 const newest=status.textContent;
 pending.find(p=>p.path==='/prompt').resolve(response({prompt_id:'old-queue'}));await loading;
 if(status.textContent!==newest)throw Error('late queue response replaced the newer status');
 if(actions.some(b=>b.disabled))throw Error('completed operations did not restore buttons');
''')

    def test_late_action_errors_cannot_replace_a_profile_refresh_and_queue_identity_is_fixed(self):
        self.run_browser(r'''
 const loading=action('加载').onclick();await flush();
 name.value='different-draft';await refresh.onclick();const newest=status.textContent;
 pending.find(p=>p.path==='/prompt').resolve(response({error:{message:'old action failed'}},false));await loading;
 if(status.textContent!==newest)throw Error('late action error replaced current profile status');
 const submitted=calls.find(c=>c[0]==='/prompt')[1];
 if(submitted.prompt['1'].inputs.profile!=='test' || JSON.stringify(submitted).includes('api_key'))throw Error('queue identity or credential contract changed');
''')


if __name__=='__main__':unittest.main()
