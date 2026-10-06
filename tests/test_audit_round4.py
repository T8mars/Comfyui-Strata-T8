"""Fourth audit: fresh dialect, restart and UI transaction combinations."""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2

core, nodes = fixture.core, fixture.nodes


class DialectContracts(unittest.TestCase):
    def test_draft4_exclusive_minimum_and_tuple_items_use_the_declared_draft(self):
        schema = {'$schema':'http://json-schema.org/draft-04/schema#', 'type':'object',
                  'properties':{'value':{'type':'number','minimum':0,'exclusiveMinimum':True},
                                'pair':{'type':'array','items':[{'type':'string'},{'type':'integer'}], 'additionalItems':False}}}
        self.assertEqual(nodes.validated('{"value":1,"pair":["a",2]}', schema), {'value':1,'pair':['a',2]})
        for text in ('{"value":0}', '{"pair":["a",2,3]}'):
            with self.assertRaises(nodes.StructuredOutputError): nodes.validated(text, schema)

    def test_draft7_unknown_dynamicref_is_an_annotation_not_an_http_reference(self):
        schema = {'$schema':'http://json-schema.org/draft-07/schema#', 'type':'object',
                  '$dynamicRef':'https://annotation.invalid/data', 'properties':{'n':{'type':'integer'}}}
        self.assertEqual(nodes.validated('{"n":2}', schema), {'n':2})
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"n":"bad"}', schema)

    def test_draft2019_recursive_reference_keeps_local_recursive_validation(self):
        schema = {'$schema':'https://json-schema.org/draft/2019-09/schema', '$recursiveAnchor':True,
                  'type':'object', 'properties':{'value':{'type':'integer'}, 'next':{'$recursiveRef':'#'}}}
        self.assertEqual(nodes.validated('{"value":1,"next":{"value":2}}', schema), {'value':1,'next':{'value':2}})
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"next":{"value":"bad"}}', schema)
        remote = dict(schema, properties={'next':{'$recursiveRef':'https://schema.invalid/remote'}})
        with self.assertRaisesRegex(core.StrataError, 'local fragments'): nodes.check_schema(remote)

    def test_non_object_service_schemas_fail_before_model_work(self):
        for schema in (True, False, {'type':'array'}, {'properties':{'value':{'type':'string'}}}):
            with self.subTest(schema=schema), mock.patch.object(core,'generate',return_value=[('{}','','{}')]) as generate:
                with self.assertRaisesRegex(core.StrataError, 'root.*object'):
                    nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=json.dumps(schema))
                generate.assert_not_called()

    def test_explicit_null_image_schema_is_not_silently_ignored(self):
        for schema in ('null','true','false','{"type":"array"}'):
            with self.subTest(schema=schema), mock.patch.object(core,'encode_images',return_value=['stub']) as encode, mock.patch.object(core,'generate',return_value=[('{}','','{}')]) as generate:
                with self.assertRaises(core.StrataError): nodes.StrataImage().run(core.Connection('test'), object(), schema=schema)
                encode.assert_not_called(); generate.assert_not_called()

    def test_duplicate_json_members_cannot_change_structured_output_or_history(self):
        with self.assertRaises(nodes.StructuredOutputError): nodes.validated('{"value":1,"value":2}', {'type':'object'})
        for text in ('{"a":{"n":1,"n":2}}', '[{"role":"system","role":"user","content":"hi"}]'):
            with self.subTest(text=text), self.assertRaises(core.StrataError): core.json_loads(text)
        self.assertEqual(core.json_loads('{"A":1,"a":2}'), {'A':1,'a':2})


class ManagedRestartContracts(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def profile(self):
        return {'mode':'managed','runtime':self.temp.name,'url':'http://localhost:8082',
                'allow_lifecycle':True,'same_gpu':True}

    def test_preclear_fallback_stop_restarts_and_uses_the_new_model_identity(self):
        old = round2.idle_status(model='old'); old['loaded']=True
        fresh = round2.idle_status(model='fresh')
        manager=mock.Mock();manager.ensure.side_effect=[old,fresh]
        client=mock.Mock();client.request.return_value={'choices':[{'message':{'content':'ok'}}]}
        order=[]
        with mock.patch.object(core,'read_profile',return_value=self.profile()), mock.patch.object(core,'Managed',return_value=manager), \
             mock.patch.object(core,'Client',return_value=client), mock.patch.object(core,'cleanup',side_effect=[True,False]), \
             mock.patch.object(core,'gpu_handoff',side_effect=lambda *a:order.append('handoff')):
            self.assertEqual(core.generate(core.Connection('test'),[nodes.request('hi')])[0][0], 'ok')
        self.assertEqual(manager.ensure.call_count,2)
        self.assertEqual(client.request.call_args.args[1]['model'],'fresh')
        self.assertEqual(order,['handoff'])

    def test_control_load_restarts_after_preclear_stopped_its_http_server(self):
        manager=mock.Mock();manager.ensure.return_value=round2.idle_status()
        client=mock.Mock();client.request.side_effect=[round2.idle_status(), {}, round2.idle_status(), round2.idle_status()]
        with mock.patch.object(core,'read_profile',return_value=self.profile()),mock.patch.object(core,'Managed',return_value=manager), \
             mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'gpu_handoff'), \
             mock.patch.object(core,'cleanup',side_effect=[True,False]):
            core.control(core.Connection('test'),'load',check=lambda:None)
        self.assertEqual(manager.ensure.call_count,2)
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['/v1/status','/v1/load','/v1/status','/v1/status'])

    def test_control_load_final_fallback_reports_release_without_querying_dead_server(self):
        manager=mock.Mock();manager.ensure.return_value=round2.idle_status()
        client=mock.Mock();client.request.side_effect=[round2.idle_status(), {}, round2.idle_status(), core.StrataError('server stopped')]
        with mock.patch.object(core,'read_profile',return_value=self.profile()),mock.patch.object(core,'Managed',return_value=manager), \
             mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'gpu_handoff'), \
             mock.patch.object(core,'cleanup',side_effect=[False,True]):
            result=core.control(core.Connection('test'),'load',check=lambda:None)
        self.assertEqual(result['after_release'],{'service':'strata','stopped':True,'released':True})
        self.assertEqual(client.request.call_count,3)

    def test_restarted_preclear_still_rejects_foreign_activity_before_inference(self):
        old=round2.idle_status();old['loaded']=True
        fresh=round2.idle_status(activity={'in_flight':1})
        manager=mock.Mock();manager.ensure.side_effect=[old,fresh]
        client=mock.Mock()
        with mock.patch.object(core,'read_profile',return_value=self.profile()),mock.patch.object(core,'Managed',return_value=manager), \
             mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup',return_value=True),mock.patch.object(core,'gpu_handoff',return_value=None) as handoff:
            with self.assertRaisesRegex(core.StrataError,'busy'):core.generate(core.Connection('test'),[nodes.request('hi')])
        client.request.assert_not_called();handoff.assert_not_called()

    def test_damaged_owner_creation_numbers_and_encoding_are_controlled_ownership_errors(self):
        manager=core.Managed('test', {'runtime':self.temp.name})
        for content in (json.dumps({'pid':123,'created':10**1000}).encode(), b'{"pid":123,"created":"\xff"}'):
            manager.state_path.write_bytes(content)
            with self.subTest(content=len(content)),mock.patch('psutil.Process') as process:
                with self.assertRaisesRegex(core.StrataError,'ownership'):manager.owned()
                process.assert_not_called()

    def test_false_positive_owner_pid_overflow_does_not_reach_process_termination(self):
        manager=core.Managed('test', {'runtime':self.temp.name})
        manager.state_path.write_text(json.dumps({'pid':10**1000,'created':7}),encoding='utf8')
        with mock.patch('psutil.Process',side_effect=OverflowError),self.assertRaisesRegex(core.StrataError,'ownership'):
            manager.stop()


class ProfileAndPanelContracts(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_profile_dropdown_ignores_directories_named_like_profiles(self):
        (core.HOME/'profiles/directory.json').mkdir(parents=True)
        self.assertEqual(core.profiles(),['default'])
        core.save_profile('valid', {'mode':'external','url':'http://localhost:8080'})
        self.assertEqual(core.profiles(),['valid'])

    def test_profile_duplicate_credentials_never_replace_an_existing_profile(self):
        core.save_profile('test', {'mode':'external','url':'http://localhost:8080','api_key':'old'})
        path=core.profile_path('test'); before=path.read_bytes()
        with self.assertRaises(core.StrataError):
            core.save_profile('test',core.json_loads('{"mode":"external","url":"http://localhost:8080","api_key":"a","api_key":"b"}'))
        self.assertEqual(path.read_bytes(),before)

    def test_profile_extension_booleans_and_nested_unicode_round_trip_without_keys_in_connection(self):
        extra={'enabled':False,'values':[None,True,{'中文':'内容'}]}
        core.save_profile('test',{'mode':'external','url':'https://example.invalid/v1','api_key':'fake-secret','extra':extra})
        self.assertEqual(core.read_profile('test')['extra'],extra)
        self.assertNotIn('fake-secret',repr(nodes.StrataConnection().run('test')))


class PanelOriginContracts(unittest.TestCase):
    setUp=fixture.Panel.setUp
    request=fixture.Panel.request

    def test_origin_must_be_a_serialized_origin_without_path_query_or_fragment(self):
        for origin in ('http://localhost:8188/path','http://localhost:8188?x=1','http://localhost:8188#fragment'):
            with self.subTest(origin=origin),self.assertRaises(self.http_error):
                asyncio.run(self.handlers['/strata_t8/profile'](self.request({},headers={'Origin':origin})))

    def test_ipv6_loopback_origin_and_read_only_status_are_accepted(self):
        with mock.patch.object(core,'control',return_value={'service':'strata'}) as control:
            response=asyncio.run(self.handlers['/strata_t8/control'](self.request({'name':'test','action':'status'},
                host='[::1]:8188',remote='::1',headers={'Origin':'http://[::1]:8188'})))
        self.assertEqual(response.status,200);self.assertIsNone(control.call_args.args[2]())

    def test_profile_http_body_uses_the_same_strict_duplicate_member_parser(self):
        request=self.request()
        async def duplicate_body(**kwargs):
            return kwargs['loads']('{"name":"test","profile":{"mode":"external","url":"http://localhost:8080","api_key":"a","api_key":"b"}}')
        request.json=duplicate_body
        with mock.patch.object(core,'save_profile') as save:
            response=asyncio.run(self.handlers['/strata_t8/profile'](request))
        self.assertEqual(response.status,400);save.assert_not_called()


class BrowserActionContracts(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is needed for the save/action concurrency contract')
    def test_save_freezes_actions_and_action_completion_cannot_unfreeze_a_running_save(self):
        script=r'''
const fs=require('fs'),vm=require('vm');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.checked=false;this.style={cssText:''};}
 append(...v){this.children.push(...v);if(this.tag==='select'&&!this.value&&v[0]?.value)this.value=v[0].value;}
 replaceChildren(...v){this.children=v;if(this.tag==='select')this.value='';}
 setAttribute(){}
}
let ext,panel,saveResolve,actionResolve;const saved={test:{mode:'external',url:'http://localhost:8080'}};
const response=data=>({ok:true,json:async()=>data});
const api={fetchApi:async(path,options)=>{
 if(path.endsWith('/profiles'))return response({profiles:saved,home:'test'});
 if(path.endsWith('/profile'))return new Promise(resolve=>saveResolve=resolve);
 if(path==='/prompt')return new Promise(resolve=>actionResolve=resolve);
 return response({service:'strata'});
}};
const app={registerExtension:v=>ext=v,extensionManager:{registerSidebarTab:v=>panel=v}};
const context=vm.createContext({app,api,document:{createElement:t=>new Element(t)},console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,''),context);
(async()=>{
 await ext.setup();const root=new Element('root');panel.render(root);await new Promise(r=>setImmediate(r));
 const actions=root.children.find(e=>e.tag==='div').children,load=actions.find(e=>e.textContent==='加载');
 const save=root.children.find(e=>e.textContent==='保存配置');
 const loading=load.onclick();await new Promise(r=>setImmediate(r));
 const saving=save.onclick();await new Promise(r=>setImmediate(r));
 if(actions.some(button=>!button.disabled))throw Error('actions can race an unfinished profile save');
 actionResolve(response({prompt_id:'queued'}));await loading;
 if(actions.some(button=>!button.disabled))throw Error('completed action unfroze the ongoing save');
 saveResolve(response({saved:'test'}));await saving;
 if(actions.some(button=>button.disabled)||save.disabled)throw Error('save did not restore the completed actions');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(fixture.NODE_ROOT/'web/strata.js')],capture_output=True,encoding='utf8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


class WireJsonContracts(unittest.TestCase):
    def test_duplicate_http_status_flags_cannot_authorize_a_last_member_override(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                raw=b'{"loaded":true,"loaded":false}'
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with self.assertRaisesRegex(core.StrataError,'JSON'):
            core.Client({'url':f'http://127.0.0.1:{server.server_port}'}).request('/test',check=None)


class OutputAndImageContracts(unittest.TestCase):
    def test_empty_extract_lists_and_nested_non_string_fields_keep_typed_outputs(self):
        self.assertEqual(nodes.StrataExtract().run('[]',expected_type='array'),('[]',[],[]))
        value,items,custom=nodes.StrataExtract().run('[{"n":{"x":1}},{"n":false}]',item_field='n',expected_type='array')
        self.assertEqual(items,['{"x": 1}','false']);self.assertEqual(custom,items)
        self.assertEqual(json.loads(value),[{'n':{'x':1}},{'n':False}])

    def test_batch_duplicate_inputs_preserve_index_identity_and_one_transaction(self):
        answers=[('first','r1','{"total_tokens":1}'),('second','r2','{"total_tokens":2}')]
        with mock.patch.object(core,'generate',return_value=answers) as generate:
            result=nodes.StrataBatch().run(core.Connection('test'),['same','same'])
        generate.assert_called_once();paired=json.loads(result[1])
        self.assertEqual([(v['index'],v['input'],v['output']) for v in paired],[(0,'same','first'),(1,'same','second')])
        self.assertEqual(result[0],result[2])

    def test_transparent_images_keep_one_encoded_image_per_batch_entry(self):
        import numpy as np
        from PIL import Image
        image=np.zeros((2,2,3,4),dtype=np.float32);image[1,:,:,0]=1;image[1,:,:,3]=1
        encoded=core.encode_images(fixture.Images.Tensor(image))
        pixels=[Image.open(io.BytesIO(base64.b64decode(value.split(',')[1]))).getpixel((0,0)) for value in encoded]
        self.assertEqual(pixels,[(255,255,255),(255,0,0)])

    def test_literal_numeric_object_keys_and_long_array_indices_are_not_confused(self):
        self.assertEqual(nodes.pointer({'01':'key','-1':'other'},'/01'),'key')
        with self.assertRaisesRegex(core.StrataError,'existing field'):nodes.pointer([1],'/'+('9'*5000))


@unittest.skipUnless(fixture.SOURCE_ROOT,'Set STRATA_SOURCE_DIR for actual dialect HTTP integration')
class StructuredDialectWire(unittest.TestCase):
    setUp=fixture.BatchHTTP.setUp

    def test_draft4_invalid_output_repairs_then_extracts_and_releases_each_request(self):
        from serve.server import MockEngine
        self.engine.reply=MockEngine(fixture.ByteTokenizer(),['{"value":0}','{"value":1}'],max_context=4096)
        schema={'$schema':'http://json-schema.org/draft-04/schema#','type':'object','required':['value'],
                'properties':{'value':{'type':'number','minimum':0,'exclusiveMinimum':True}}}
        result=nodes.StrataStructured().run(core.Connection('test'),'number',schema=json.dumps(schema),
            repair_attempts=1,max_tokens=128,reasoning_effort='none')
        self.assertEqual(nodes.StrataNumber().run(result[0],'/value'),(1.0,))
        self.assertEqual((self.engine.starts,self.engine.closes),(2,2));self.assertFalse(self.engine.alive())

    def test_draft2019_nested_output_survives_both_http_and_local_validation(self):
        from serve.server import MockEngine
        answer={'value':1,'next':{'value':2}}
        self.engine.reply=MockEngine(fixture.ByteTokenizer(),json.dumps(answer),max_context=4096)
        schema={'$schema':'https://json-schema.org/draft/2019-09/schema','$recursiveAnchor':True,
                'type':'object','properties':{'value':{'type':'integer'},'next':{'$recursiveRef':'#'}}}
        result=nodes.StrataStructured().run(core.Connection('test'),'tree',schema=json.dumps(schema),max_tokens=128,reasoning_effort='none')
        self.assertEqual(json.loads(result[0]),answer)
        self.assertEqual(nodes.StrataNumber().run(result[0],'/next/value'),(2.0,))
        self.assertEqual((self.engine.starts,self.engine.closes),(1,1));self.assertFalse(self.engine.alive())


if __name__=='__main__':unittest.main()
