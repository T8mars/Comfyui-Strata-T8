"""Third audit: fresh ownership, framing, recovery and browser transaction boundaries."""
import base64
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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


class SchemaRecovery(unittest.TestCase):
    def test_unproductive_local_cycles_fail_as_bounded_structured_errors(self):
        for schema in ({'$ref':'#'}, {'allOf':[{'$ref':'#'}]},
                       {'$defs':{'a':{'$ref':'#/$defs/b'},'b':{'$ref':'#/$defs/a'}},'$ref':'#/$defs/a'}):
            with self.subTest(schema=schema), self.assertRaises(nodes.StructuredOutputError):
                nodes.validated('{}', schema)
        with mock.patch.object(core, 'generate', return_value=[('{}','','{}')]) as generate:
            with self.assertRaises(nodes.StructuredOutputError):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema='{"type":"object","$ref":"#"}', repair_attempts=1)
            self.assertEqual(generate.call_count, 2)

    def test_structured_schema_requires_text_before_any_model_work(self):
        for schema in (None, {}, [], False, 0):
            with self.subTest(schema=schema), mock.patch.object(core, 'generate') as generate:
                with self.assertRaisesRegex(core.StrataError, 'must be text'):
                    nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=schema)
                generate.assert_not_called()
        with mock.patch.object(core, 'generate', return_value=[('{"shots":[]}', '', '{}')]):
            with self.assertRaises(nodes.StructuredOutputError):
                nodes.StrataStructured().run(core.Connection('test'), 'hi', schema=' \n ')

    def test_extract_invalid_expected_type_is_a_node_error(self):
        for expected in ([], {}, None):
            with self.subTest(expected=expected), self.assertRaisesRegex(core.StrataError, 'expected JSON type'):
                nodes.StrataExtract().run('{}', expected_type=expected)

    def test_dynamic_ref_scopes_and_ref_like_data_keep_their_meaning(self):
        schema = {'$dynamicAnchor':'node','type':'object','properties':{'next':{'$dynamicRef':'#node'}}}
        self.assertEqual(nodes.validated('{"next":{}}', schema), {'next':{}})
        self.assertEqual(nodes.validated('"$ref"', {'enum':['$ref','https://literal.invalid']}), '$ref')


class ProfileTransactions(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_extension_values_cannot_save_a_self_corrupting_profile(self):
        original = {'mode':'external','url':'http://localhost:8080','api_key':'test-key','extra':{'title':'ok'}}
        core.save_profile('test', original)
        before = core.profile_path('test').read_bytes()
        for extension in ({'nested':[float('nan')]}, {'value':float('inf')}, {'value':object()}):
            with self.subTest(extension=extension), self.assertRaisesRegex(core.StrataError, 'JSON data'):
                core.save_profile('test', dict(original, extra=extension))
            self.assertEqual(core.profile_path('test').read_bytes(), before)
        self.assertEqual(core.read_profile('test')['extra'], {'title':'ok'})
        self.assertEqual(list(core.profile_path('test').parent.glob('*.tmp')), [])

    def test_unicode_encoding_failure_preserves_old_profile_without_private_temp(self):
        profile={'mode':'external','url':'http://localhost:8080','api_key':'test-key'}
        core.save_profile('test', profile)
        before=core.profile_path('test').read_bytes()
        with self.assertRaises(UnicodeEncodeError):
            core.save_profile('test', dict(profile, extra='\ud800'))
        self.assertEqual(core.profile_path('test').read_bytes(), before)
        self.assertEqual(list(core.profile_path('test').parent.glob('*.tmp')), [])

    def test_blank_external_key_repairs_a_corrupt_unauthenticated_profile(self):
        path=core.profile_path('test');path.parent.mkdir(parents=True)
        path.write_text('{broken',encoding='utf8')
        core.save_profile('test',{'mode':'external','url':'http://localhost:8080','api_key':''})
        self.assertEqual(core.read_profile('test')['api_key'],'')

    def test_managed_blank_key_rotates_identity_without_leaking_into_connection(self):
        profile={'mode':'managed','runtime':self.temp.name,'data_dir':self.temp.name,'api_key':''}
        core.save_profile('test',profile)
        previous=core.read_profile('test')['api_key']
        core.save_profile('test',profile)
        current=core.read_profile('test')['api_key']
        self.assertTrue(previous and current)
        self.assertNotEqual(previous,current)
        self.assertNotIn(current,repr(nodes.StrataConnection().run('test')))


class OwnershipRecovery(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp
    prepared_manager = fixture.OwnedProcesses.prepared_manager

    def test_duplicate_config_options_cannot_identify_a_foreign_effective_config(self):
        manager=core.Managed('test',{'runtime':self.temp.name})
        manager.state_path.write_text(json.dumps({'pid':123,'created':7,'python':sys.executable,'server':str(fixture.SERVER_FILE)}),encoding='utf8')
        proc=mock.Mock();proc.exe.return_value=sys.executable;proc.create_time.return_value=7
        prefix=[sys.executable,str(fixture.SERVER_FILE),'--config',str(manager.dir/'service.json')]
        with mock.patch('psutil.Process',return_value=proc):
            for suffix in (['--config',str(Path(self.temp.name)/'foreign.json')],['--config='+str(Path(self.temp.name)/'foreign.json')]):
                proc.cmdline.return_value=prefix+suffix
                self.assertIsNone(manager.owned())
            proc.cmdline.return_value=prefix
            self.assertIs(manager.owned(),proc)

    def started_manager(self):
        manager=self.prepared_manager()
        config, child, proc=mock.Mock(),mock.Mock(),mock.Mock()
        config.poll.return_value,config.returncode=0,0
        child.pid=123;proc.create_time.return_value=7;proc.is_running.return_value=True
        return manager,config,child,proc

    def test_incompatible_ready_response_stops_new_owned_server(self):
        manager,config,child,proc=self.started_manager()
        client=mock.Mock()
        def status(*args,**kwargs):
            state=json.loads(manager.state_path.read_text(encoding='utf8'))
            return dict(round2.idle_status(protocol_version=2),instance_id=state['instance'])
        client.request.side_effect=status
        with mock.patch.object(core.socket,'socket'),mock.patch.object(core.subprocess,'Popen',side_effect=[config,child]), \
                mock.patch('psutil.Process',return_value=proc),mock.patch.object(manager,'stop',return_value=True) as stop:
            with self.assertRaisesRegex(core.StrataError,'protocol version 1'):
                manager.ensure(client,check=lambda:None)
            stop.assert_called_once()
        self.assertEqual(client.request.call_count,1)

    def test_damaged_instance_record_rolls_back_the_owned_process(self):
        manager,config,child,proc=self.started_manager()
        def damage():
            state=json.loads(manager.state_path.read_text(encoding='utf8'))
            state['instance']=False
            manager.state_path.write_text(json.dumps(state),encoding='utf8')
        real_replace=core.os.replace
        def replace(*args):
            real_replace(*args);damage()
        with mock.patch.object(core.socket,'socket'),mock.patch.object(core.subprocess,'Popen',side_effect=[config,child]), \
                mock.patch('psutil.Process',return_value=proc),mock.patch.object(core.os,'replace',side_effect=replace), \
                mock.patch.object(manager,'stop',return_value=True) as stop:
            with self.assertRaisesRegex(core.StrataError,'instance record'):
                manager.ensure(mock.Mock(),check=lambda:None)
            stop.assert_called_once()

    def test_stop_access_denied_during_enumeration_never_claims_release(self):
        import psutil
        manager=core.Managed('test',{'runtime':self.temp.name})
        proc=mock.Mock();proc.children.side_effect=psutil.AccessDenied(123)
        with mock.patch.object(manager,'owned',return_value=proc), self.assertRaises(psutil.AccessDenied):
            manager.stop()
        proc.terminate.assert_not_called()


class ReleaseRecovery(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_unload_fallback_can_report_success_when_own_server_had_to_stop(self):
        profile={'mode':'managed','runtime':self.temp.name,'allow_lifecycle':True,'url':'http://localhost:8082','cleanup_timeout_s':.001}
        manager=mock.Mock();manager.stop.return_value=True
        client=mock.Mock();client.request.side_effect=core.StrataError('unreachable')
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'Managed',return_value=manager):
            result=core.control(core.Connection('test'),'unload',check=lambda:None)
        self.assertEqual(result,{'service':'strata','stopped':True,'released':True})
        self.assertTrue(client.request.call_count)
        self.assertTrue(all(call.args[0]=='/v1/status' for call in client.request.call_args_list))
        manager.stop.assert_called_once()

    def test_process_loaded_flag_is_released_before_same_gpu_baseline(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':True}
        status=round2.idle_status();status['processes']['vision']['loaded']=True
        client=mock.Mock();client.request.side_effect=[status,{'choices':[{'message':{'content':'ok'}}]}]
        order=[]
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'cleanup',side_effect=lambda *a:order.append('cleanup')), \
                mock.patch.object(core,'gpu_handoff',side_effect=lambda *a:order.append('handoff')):
            result=core.generate(core.Connection('test'),[nodes.request('hi')])
        self.assertEqual(result[0][0],'ok')
        self.assertEqual(order,['cleanup','handoff','cleanup'])

    def test_cancelled_request_keeps_cleanup_independent_of_cancel_hook(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True}
        client=mock.Mock();client.request.side_effect=[round2.idle_status(),InterruptedError('cancelled')]
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaises(InterruptedError):core.generate(core.Connection('test'),[nodes.request('hi')])
        cleanup.assert_called_once_with(client,profile,None,None)


class WireRecovery(unittest.TestCase):
    def setUp(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200)
                if outer.declared is not None:self.send_header('Content-Length',str(outer.declared))
                self.send_header('Connection','close');self.end_headers()
                self.wfile.write(outer.body);self.wfile.flush()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.client=core.Client({'url':f'http://127.0.0.1:{self.server.server_port}'})

    def test_valid_json_in_a_truncated_content_length_is_not_a_complete_response(self):
        self.body=b'{"ok":true}';self.declared=1000
        with self.assertRaisesRegex(core.StrataError,'Incomplete service response'):
            self.client.request('/test',check=None)
        self.declared=len(self.body)
        self.assertEqual(self.client.request('/test',check=None),{'ok':True})

    def test_close_delimited_json_remains_supported(self):
        self.body=b'{"ok":true}';self.declared=None
        self.assertEqual(self.client.request('/test',check=None),{'ok':True})

    def test_explicit_huge_timeouts_fail_before_socket_or_overflow(self):
        for timeout in (10**1000,86401,float('inf'),True):
            with mock.patch('http.client.HTTPConnection') as connect,self.assertRaisesRegex(core.StrataError,'Request timeout'):
                self.client.request('/test',timeout=timeout,check=None)
            connect.assert_not_called()

    def test_invalid_request_json_is_never_sent_after_connect(self):
        conn,_,_=fixture.Transport.connection(self)
        with mock.patch('http.client.HTTPConnection',return_value=conn),self.assertRaises(core.StrataError):
            self.client.request('/test',{'bad':float('nan')},check=None)
        conn.request.assert_not_called()
        conn.close.assert_called()


class ImageGeometry(unittest.TestCase):
    def test_extreme_aspect_ratio_obeys_the_actual_encoded_pixel_limit(self):
        import numpy as np
        from PIL import Image
        for shape in ((1,1,1048576,3),(1,1048576,1,3)):
            with self.subTest(shape=shape):
                encoded=core.encode_images(fixture.Images.Tensor(np.zeros(shape,dtype=np.float32)),max_pixels=65536)
                image=Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',',1)[1])))
                self.assertLessEqual(image.width*image.height,65536)
                self.assertEqual(min(image.size),1)


class BrowserTransactions(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js is needed for actual browser transaction handlers')
    def test_refresh_preserves_drafts_and_late_responses_and_blank_key_can_be_cleared(self):
        script=r'''
const fs=require('fs'),vm=require('vm');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.value='';this.checked=false;this.style={cssText:''};}
  append(...v){this.children.push(...v);if(this.tag==='select' && !this.value && v[0]?.value)this.value=v[0].value;}
  replaceChildren(...v){this.children=v;if(this.tag==='select')this.value='';}
  setAttribute(){}
}
let ext,panel,fail=false;const pending=[],saves=[];
const api={fetchApi:async(path,options)=>{
 if(path.endsWith('/profiles'))return await new Promise(resolve=>pending.push(resolve));
 if(fail)return {ok:false,status:400,json:async()=>({error:'bad profile'})};
 saves.push(JSON.parse(options.body));return {ok:true,json:async()=>({saved:'draft'})};
}};
const app={registerExtension:v=>ext=v,extensionManager:{registerSidebarTab:v=>panel=v}};
const context=vm.createContext({app,api,document:{createElement:t=>new Element(t)},console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;\r?\n/gm,''),context);
const reply=profiles=>({ok:true,json:async()=>({profiles,home:'test'})});
(async()=>{
 await ext.setup();const root=new Element('root');panel.render(root);
 const name=root.children.find(e=>e.placeholder==='配置名称'),fields=root.children.find(e=>e.tag==='textarea');
 const key=root.children.find(e=>e.type==='password'),clear=root.children.find(e=>e.tag==='label').children[0];
 const refresh=root.children.find(e=>e.textContent==='刷新配置'),save=root.children.find(e=>e.textContent==='保存配置');
 name.value='draft';fields.value='{"mode":"external","url":"http://localhost:8080"}';key.value='draft-key';
 pending.shift()(reply({default:{mode:'external',url:'http://localhost:8080'}}));await new Promise(r=>setImmediate(r));
 if(name.value!=='draft'||key.value!=='draft-key')throw Error('initial refresh replaced edited draft');
 let older=refresh.onclick(),newer=refresh.onclick();
 pending[1](reply({newer:{mode:'external',url:'http://localhost:8081'}}));await newer;
 pending[0](reply({older:{mode:'external',url:'http://localhost:8082'}}));await older;pending.length=0;
 if(name.value!=='draft'||fields.value.includes('8082'))throw Error('refresh lost an unsaved profile');
 const select=root.children.find(e=>e.tag==='select');if(select.children.some(e=>e.value==='older'))throw Error('stale response overwrote newest profiles');
 clear.checked=true;key.value='';const saving=save.onclick();await new Promise(r=>setImmediate(r));
 if([save,refresh,name,fields,key,clear].some(e=>!e.disabled))throw Error('save was not protected while in progress');
 pending.shift()(reply({draft:{mode:'external',url:'http://localhost:8080'}}));await saving;
 if(saves.length!==1||saves[0].profile.api_key!=='')throw Error('clear key serialized KEEP instead of empty');
 if([save,refresh,name,fields,key,clear].some(e=>e.disabled)||clear.checked)throw Error('save busy/clear state remained');
 fail=true;key.value='replacement';await save.onclick();
 if([save,refresh,name,fields,key,clear].some(e=>e.disabled)||key.value!=='replacement')throw Error('failed save lost editable recovery inputs');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(fixture.NODE_ROOT/'web/strata.js')],
                              capture_output=True,encoding='utf8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
