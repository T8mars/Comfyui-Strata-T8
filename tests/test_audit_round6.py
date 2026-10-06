"""Sixth audit: preflight, resource postconditions and profile persistence."""
import asyncio
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2
import test_audit_round5 as round5

core, nodes = fixture.core, fixture.nodes


class PrivateProfilePreflight(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def test_profile_unicode_is_rejected_before_the_existing_profile_is_replaced(self):
        original = {'mode':'external','url':'http://localhost:8080','api_key':'original-test-key'}
        core.save_profile('test', original)
        before = core.profile_path('test').read_bytes()
        for changes in ({'url':'http://\ud800.invalid:8080'}, {'extension':{'label':'\ud800'}},
                        {'extension':{'\ud800':'value'}}):
            with self.subTest(field=next(iter(changes))):
                with self.assertRaises(core.StrataError):
                    core.normalize_profile(dict(original, **changes))
                with self.assertRaises(core.StrataError):
                    core.save_profile('test', dict(original, **changes))
                self.assertEqual(core.profile_path('test').read_bytes(), before)
                self.assertFalse(list(core.profile_path('test').parent.glob('*.tmp')))

    def test_non_string_extension_members_cannot_write_a_duplicate_or_changed_json_key(self):
        core.save_profile('test', {'mode':'external','url':'http://localhost:8080'})
        before = core.profile_path('test').read_bytes()
        for extension in ({1:'number','1':'text'}, {'nested':[{False:'boolean'}]}, {None:'null'}):
            with self.subTest(extension=repr(extension)):
                with self.assertRaises(core.StrataError):
                    core.save_profile('test', {'mode':'external','url':'http://localhost:8080','extension':extension})
                self.assertEqual(core.profile_path('test').read_bytes(), before)
                self.assertEqual(core.read_profile('test')['url'], 'http://localhost:8080')

    def test_connection_fingerprint_treats_a_non_file_profile_as_missing(self):
        path = core.profile_path('directory')
        path.mkdir(parents=True)
        self.assertEqual(nodes.StrataConnection.IS_CHANGED('directory'), 'missing')
        with self.assertRaisesRegex(core.StrataError, 'Create this local profile'):
            nodes.StrataConnection().run('directory')

    def test_connection_fingerprint_read_failure_is_a_controlled_error(self):
        core.save_profile('test', {'mode':'external','url':'http://localhost:8080'})
        with mock.patch.object(Path, 'read_bytes', side_effect=PermissionError('private path')):
            with self.assertRaisesRegex(core.StrataError, 'profile'):
                nodes.StrataConnection.IS_CHANGED('test')


class ManagedMetadataPreflight(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def manager(self):
        manager = fixture.OwnedProcesses.prepared_manager(self)
        current = json.loads((fixture.NODE_ROOT/'meta.json').read_text(encoding='utf-8'))['runtime_min_version']
        (manager.root/'meta.json').write_text(json.dumps({'version':current,'protocol_version':1}), encoding='utf-8')
        return manager

    def test_damaged_runtime_metadata_is_controlled_before_identity_or_gpu_work(self):
        manager = self.manager()
        for raw in ('[]', '{', '{"version":12,"protocol_version":1}',
                    '{"version":"0.1.39-t8.12","protocol_version":true}',
                    '{"version":"0.1.39-t8.12","version":"0.1.39-t8.99","protocol_version":1}'):
            (manager.root/'meta.json').write_text(raw, encoding='utf-8')
            with self.subTest(raw=raw), mock.patch.object(manager,'owned',return_value=None) as owned, \
                 mock.patch.object(core.socket,'socket') as socket, mock.patch.object(core.subprocess,'Popen') as popen:
                with self.assertRaisesRegex(core.StrataError, 'metadata|compatible'):
                    manager.ensure(mock.Mock(), check=lambda:None, prepare_check=lambda:self.fail('GPU preparation started'))
            owned.assert_not_called(); socket.assert_not_called(); popen.assert_not_called()

    def test_old_or_missing_managed_metadata_cannot_start_native_preparation(self):
        manager = self.manager()
        for version in ('0.1.39-t8.11', None):
            if version is None: (manager.root/'meta.json').unlink()
            else: (manager.root/'meta.json').write_text(json.dumps({'version':version,'protocol_version':1}), encoding='utf-8')
            with self.subTest(version=version), mock.patch.object(manager,'owned',return_value=None) as owned, \
                 mock.patch.object(core.subprocess,'Popen') as popen:
                with self.assertRaisesRegex(core.StrataError, 'metadata|compatible|version'):
                    manager.ensure(mock.Mock(), check=lambda:None, prepare_check=lambda:self.fail('GPU preparation started'))
            owned.assert_not_called(); popen.assert_not_called()

    def test_a_newer_upstream_base_with_a_t8_revision_is_compatible(self):
        manager = self.manager()
        (manager.root/'meta.json').write_text('{"version":"0.2.0-t8.1","protocol_version":1}', encoding='utf-8')
        order = []
        def spawn(*args, **kwargs): order.append('spawn'); raise OSError('test stops before process creation')
        with mock.patch.object(manager,'owned',return_value=None), mock.patch.object(core.socket,'socket'), \
             mock.patch.object(core.subprocess,'Popen',side_effect=spawn):
            with self.assertRaises(OSError): manager.ensure(mock.Mock(), check=lambda:None, prepare_check=lambda:order.append('handoff'))
        self.assertEqual(order, ['handoff','spawn'])

    def test_disappearing_preparation_parent_does_not_claim_known_children_have_exited(self):
        import psutil
        manager = self.manager()
        config = mock.Mock(); config.pid = 123; config.poll.return_value = None
        checks = 0
        def cancel():
            nonlocal checks
            checks += 1
            if checks > 1: raise InterruptedError('cancel preparation')
        with mock.patch.object(manager,'owned',return_value=None), mock.patch.object(core.socket,'socket'), \
             mock.patch.object(core.subprocess,'Popen',return_value=config), \
             mock.patch('psutil.Process',side_effect=psutil.NoSuchProcess(123)), \
             mock.patch('psutil.wait_procs',return_value=([],[])):
            with self.assertRaisesRegex(core.StrataError, 'preparation children'):
                manager.ensure(mock.Mock(), check=cancel)
        config.kill.assert_called_once(); config.wait.assert_called_once_with(timeout=20)
        self.assertFalse(manager.state_path.exists())


class SocketAndTransactionChecks(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def wire(self, status=200, interim=False):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if interim: self.wfile.write(b'HTTP/1.1 100 Continue\r\n\r\n')
                raw=b'{"ok":true}'
                self.send_response(status); self.send_header('Content-Length',str(len(raw))); self.end_headers()
                self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return core.Client({'url':f'http://127.0.0.1:{server.server_port}'})

    def test_json_redirects_are_not_success_and_are_not_followed(self):
        for status in (301,302,307,308):
            with self.subTest(status=status):
                with self.assertRaisesRegex(core.StrataError, f'HTTP {status}'):
                    self.wire(status).request('/v1/status',check=None)

    def test_interim_continue_then_a_created_json_response_is_consumed_once(self):
        self.assertEqual(self.wire(201,True).request('/v1/status',check=None), {'ok':True})

    def test_ipv6_v1_base_and_https_select_one_canonical_api_path(self):
        profile=core.normalize_profile({'mode':'external','url':'https://[::1]:443/v1///'})
        self.assertEqual(profile['url'],'https://[::1]:443/v1')
        conn=mock.Mock(); response=mock.Mock(); response.status=200; response.length=11
        response.getheaders.return_value=[('Content-Length','11')];response.read.return_value=b'{"ok":true}'
        conn.getresponse.return_value=response
        with mock.patch.object(core.http.client,'HTTPSConnection',return_value=conn) as create:
            self.assertEqual(core.Client(profile).request('/v1/status',check=None), {'ok':True})
        self.assertEqual(create.call_args.args, ('::1',443))
        self.assertEqual(conn.request.call_args.args[:2], ('GET','/v1/status'))
        response.close.assert_called_once(); conn.close.assert_called_once()

    def test_non_contention_lock_errors_fail_before_entering_the_transaction(self):
        module = __import__('msvcrt' if os.name=='nt' else 'fcntl')
        function = 'locking' if os.name=='nt' else 'flock'
        checks=0
        def check():
            nonlocal checks
            checks+=1
            if checks>2: raise InterruptedError('old implementation keeps retrying a broken lock')
        with mock.patch.object(module,function,side_effect=OSError(errno.EBADF,'private lock path')) as lock:
            with self.assertRaisesRegex(core.StrataError,'lock'):
                with core.file_lock('fatal-lock',check): self.fail('entered a broken transaction')
        self.assertEqual(lock.call_count,1)

    def test_a_real_other_process_lock_is_cancelled_without_entering_or_growing_the_file(self):
        script="""import sys,time
from pathlib import Path
p=Path(sys.argv[1]); f=open(p,'r+b'); f.seek(0)
if sys.platform=='win32':
 import msvcrt; msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
else:
 import fcntl; fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
print('locked',flush=True); sys.stdin.readline()
"""
        import hashlib
        path=core.HOME/'locks'/(hashlib.sha256(b'cross-process').hexdigest()+'.lock')
        path.parent.mkdir(parents=True);path.write_bytes(b'0')
        child=subprocess.Popen([sys.executable,'-B','-c',script,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            checks=0
            def cancel():
                nonlocal checks
                checks+=1
                if checks>=3: raise InterruptedError('cancel lock wait')
            with self.assertRaisesRegex(InterruptedError,'lock wait'):
                with core.file_lock('cross-process',cancel): self.fail('entered while another process held the lock')
        finally:
            child.communicate('\n',timeout=10)
        self.assertEqual(path.read_bytes(),b'0')


class LifecyclePreflightAndPostconditions(unittest.TestCase):
    setUp=fixture.OwnedProcesses.setUp

    def test_a_new_managed_profile_with_vision_off_never_reaches_native_preparation_or_handoff(self):
        manager=fixture.OwnedProcesses.prepared_manager(self)
        minimum=json.loads((fixture.NODE_ROOT/'meta.json').read_text(encoding='utf-8'))['runtime_min_version']
        (manager.root/'meta.json').write_text(json.dumps({'version':minimum,'protocol_version':1}),encoding='utf-8')
        core.save_profile('test',dict(manager.profile,mode='managed',vision='no',same_gpu=True))
        self.assertFalse(manager.state_path.exists())
        req=nodes.request('look');req['messages'][-1]['content']=[{'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]
        with mock.patch.object(core.socket,'socket'),mock.patch.object(core.subprocess,'Popen') as popen, \
             mock.patch.object(core,'gpu_handoff',side_effect=AssertionError('disabled vision reached native preparation handoff')) as handoff:
            with self.assertRaisesRegex(core.StrataError,'vision'):core.generate(core.Connection('test'),[req])
        popen.assert_not_called();handoff.assert_not_called();self.assertFalse(manager.state_path.exists())

    def test_disabled_vision_is_rejected_before_cleanup_or_comfy_handoff(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':True}
        status=round2.idle_status(vision={'enabled':False},loaded=True)
        client=mock.Mock();client.request.return_value=status
        req=nodes.request('look');req['messages'][-1]['content']=[{'type':'image_url','image_url':{'url':'data:image/png;base64,fixture'}}]
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(core,'cleanup',return_value=False) as cleanup,mock.patch.object(core,'gpu_handoff',return_value=None) as handoff:
            with self.assertRaisesRegex(core.StrataError,'vision'):core.generate(core.Connection('test'),[req])
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/status'])
        cleanup.assert_not_called();handoff.assert_not_called()

    def test_non_same_gpu_automatic_cleanup_does_not_adopt_an_existing_active_request(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':False}
        client=mock.Mock();client.request.side_effect=[round2.idle_status(activity={'in_flight':1}),{'choices':[{'message':{'content':'new text'}}]}]
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError,'busy|idle'):core.generate(core.Connection('test'),[nodes.request('new')])
        cleanup.assert_not_called()
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/status'])

    def test_a_shared_external_service_can_generate_without_claiming_automatic_cleanup(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':False,'same_gpu':False}
        client=mock.Mock();client.request.side_effect=[round2.idle_status(activity={'in_flight':1},concurrency={'serving':2}),
                                                     {'choices':[{'message':{'content':'shared answer'}}]}]
        with mock.patch.object(core,'read_profile',return_value=profile),mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            self.assertEqual(core.generate(core.Connection('test'),[nodes.request('hi')]),[('shared answer','','{}')])
        cleanup.assert_not_called()

    def test_control_final_release_snapshot_cannot_reintroduce_an_incompatible_or_active_service(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':True}
        for changed in (round2.idle_status(protocol_version=2),round2.idle_status(loaded=True),
                        round2.idle_status(activity={'in_flight':1}),
                        round2.idle_status(processes={'engine':{'running':False,'loaded':False,'starting':True},'vision':{'running':False,'loaded':False,'starting':False}})):
            client=mock.Mock();client.request.side_effect=[round2.idle_status(),{},round2.idle_status(),changed]
            with self.subTest(changed=changed),mock.patch.object(core,'read_profile',return_value=profile), \
                 mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'gpu_handoff',return_value=None), \
                 mock.patch.object(core,'cleanup',return_value=False) as cleanup:
                with self.assertRaises(core.StrataError):core.control(core.Connection('test'),'load',check=lambda:None)
            self.assertEqual(cleanup.call_count,2)

    def test_background_comfy_allocation_blocks_generation_and_still_cleans_up(self):
        profile={'mode':'external','url':'http://localhost:8080','allow_lifecycle':True,'same_gpu':True}
        client=mock.Mock();client.request.return_value=round2.idle_status()
        torch=types.SimpleNamespace(cuda=types.SimpleNamespace(memory_allocated=lambda device:129*1024**2))
        with mock.patch.dict(sys.modules,{'torch':torch}),mock.patch.object(core,'read_profile',return_value=profile), \
             mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'gpu_handoff',return_value=('device',2**34,0)), \
             mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError,'background GPU'):core.generate(core.Connection('test'),[nodes.request('hi')])
        cleanup.assert_called_once_with(client,profile,None,('device',2**34,0))
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/status'])


class InputAndTypedOutputs(unittest.TestCase):
    setUp=fixture.OwnedProcesses.setUp

    def test_control_text_is_validated_before_any_lifecycle_action(self):
        for text in (None,7,['text'],'\ud800'):
            with self.subTest(text=repr(text)),mock.patch.object(core,'control',return_value={'service':'strata'}) as control:
                with self.assertRaises(core.StrataError):nodes.StrataControl().run(core.Connection('test'),'stop',text=text)
            control.assert_not_called()

    def test_large_image_cpu_conversion_is_rejected_before_touching_a_tensor(self):
        tensor=mock.Mock();tensor.shape=(8,4096,4097,4);tensor.numel.return_value=8*4096*4097*4
        with self.assertRaisesRegex(core.StrataError,'CPU conversion limit'):core.encode_images(tensor)
        tensor.detach.assert_not_called();tensor.to.assert_not_called()

    def test_structured_repair_without_a_system_turn_keeps_the_original_user_prompt(self):
        requests=[]
        def generate(connection, reqs):
            requests.append(json.loads(json.dumps(reqs)))
            return [('not JSON' if len(requests)==1 else '{"ok":true}','','{}')]
        with mock.patch.object(core,'generate',side_effect=generate):
            output=nodes.StrataStructured().run(core.Connection('test'),'保留这个原始请求',system='',
                                               schema='{"type":"object","required":["ok"]}',repair_attempts=1)
        self.assertEqual(output[0],'{"ok": true}')
        self.assertEqual(requests[0][0]['messages'],[{'role':'user','content':'保留这个原始请求'}])
        repaired=requests[1][0]['messages'][0]
        self.assertEqual(repaired['role'],'user');self.assertTrue(repaired['content'].startswith('保留这个原始请求\n'))


class RawProfilePanel(unittest.TestCase):
    setUp=fixture.Panel.setUp
    request=fixture.Panel.request

    def test_raw_profile_json_keeps_duplicate_members_visible_to_the_server(self):
        original={'mode':'external','url':'http://localhost:8080','api_key':'original-test-key'}
        with mock.patch.object(core,'save_profile') as save:
            for raw in ('{"mode":"external","mode":"managed","url":"http://localhost:8080"}',
                        '{"mode":"external","url":"http://localhost:8080","extension":{"x":1,"x":2}}'):
                response=asyncio.run(self.handlers['/strata_t8/profile'](self.request({'name':'test','profile_json':raw,'api_key':'__KEEP__'})))
                self.assertEqual(response.status,400)
                self.assertIn('unique member',response.body['error'])
        save.assert_not_called()

    def test_raw_profile_save_and_legacy_object_save_both_preserve_the_key_contract(self):
        for body in ({'name':'test','profile_json':'{"mode":"external","url":"http://localhost:8080"}','api_key':'__KEEP__'},
                     {'name':'test','profile':{'mode':'external','url':'http://localhost:8080','api_key':'__KEEP__'}}):
            with self.subTest(body=body),mock.patch.object(core,'save_profile') as save:
                response=asyncio.run(self.handlers['/strata_t8/profile'](self.request(body)))
            self.assertEqual(response.status,200)
            self.assertEqual(save.call_args.args,('test',{'mode':'external','url':'http://localhost:8080','api_key':'__KEEP__'}))

    def test_raw_profile_payload_wrong_types_do_not_reach_profile_persistence(self):
        for raw in ('[]','null','7',[],'"string"'):
            with self.subTest(raw=raw),mock.patch.object(core,'save_profile') as save:
                response=asyncio.run(self.handlers['/strata_t8/profile'](self.request({'name':'test','profile_json':raw,'api_key':''})))
            self.assertEqual(response.status,400);save.assert_not_called()


class BrowserRawProfile(unittest.TestCase):
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
const watchdog=setTimeout(()=>{console.error('Browser test did not complete');process.exit(2)},5000);
(async()=>{
 await ext.setup();const root=new Element('root');panel.render(root);await flush();
 const select=root.children.find(e=>e.tag==='select');
 const name=root.children.find(e=>e.tag==='input'&&e.placeholder==='配置名称');
 const fields=root.children.find(e=>e.tag==='textarea');
 const key=root.children.find(e=>e.tag==='input'&&e.type==='password');
 const refresh=root.children.find(e=>e.textContent==='刷新配置');
 const actions=root.children.find(e=>e.tag==='div').children;
 const status=root.children.find(e=>e.tag==='pre');const action=label=>actions.find(e=>e.textContent===label);
''' + body + r'''
 console.log('AUDIT6_BROWSER_COMPLETE');
})().then(()=>clearTimeout(watchdog)).catch(error=>{clearTimeout(watchdog);console.error(error);process.exitCode=1;});
'''
        result=subprocess.run([shutil.which('node'),'-e',harness,str(fixture.NODE_ROOT/'web/strata.js')],
                              capture_output=True,encoding='utf-8',timeout=12)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('AUDIT6_BROWSER_COMPLETE',result.stdout)

    def test_save_transports_raw_profile_json_without_erasing_duplicate_fields(self):
        self.run_browser(r'''
 const save=root.children.find(e=>e.textContent==='保存配置');
 fields.value='{"mode":"external","url":"http://localhost:8080","context":1024,"context":4096}';
 key.value='replace-test-key';
 const saving=save.onclick();await flush();
 const sent=calls.find(c=>c[0]==='/strata_t8/profile')[1];
 if(sent.profile_json!==fields.value || sent.api_key!=='replace-test-key')throw Error('JSON.parse erased duplicate profile fields before server validation');
 pending.find(p=>p.path==='/strata_t8/profile').resolve(response({error:'Invalid JSON: use unique member names'},false));await saving;
 if(key.value!=='replace-test-key' || save.disabled)throw Error('failed save discarded draft or left panel disabled');
''')

    def test_non_object_profile_drafts_are_rejected_without_a_post(self):
        self.run_browser(r'''
 const save=root.children.find(e=>e.textContent==='保存配置');
 for(const raw of ['[]','null','7','"text"']) {
   fields.value=raw;const saving=save.onclick();await flush();
   if(calls.some(c=>c[0]==='/strata_t8/profile'))throw Error('non-object draft was posted');
   await saving;
   if(!status.textContent.includes('JSON') || save.disabled)throw Error('missing controlled profile diagnostic');
 }
''')

    def test_a_profile_with_a_javascript_prototype_member_name_stays_a_literal_profile(self):
        self.run_browser(r'''
 Object.defineProperty(saved,'__proto__',{value:{mode:'external',url:'http://localhost:8123',context:8192},enumerable:true});
 await refresh.onclick();select.value='__proto__';select.onchange();
 if(name.value!=='__proto__' || JSON.parse(fields.value).context!==8192 || key.value!=='')throw Error('literal profile name was confused with a prototype');
 const showing=action('状态').onclick();await flush();
 const sent=calls.find(c=>c[0]==='/strata_t8/control')[1];
 if(sent.name!=='__proto__')throw Error('wrong selected profile identity');
 pending.find(p=>p.path.endsWith('/control')).resolve(response({service:'strata'}));await showing;
''')


if __name__=='__main__':unittest.main()
