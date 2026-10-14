"""Default-off speech, imported timing evidence and no fabricated visual/audio citations."""
import json
import io
import base64
import hashlib
import types
import sys
import numpy as np
import unittest
from unittest import mock
import test_video_nodes as fixture
import test_video_stream as dense

core,nodes,video=fixture.core,fixture.nodes,fixture.video
from importlib import import_module
speech=import_module(nodes.__package__+'.speech')
contract=import_module(nodes.__package__+'.speech_contract')


def timed():
    return {'contract_version':1,'timeline':'input','status':'complete','cues':[
        {'id':'u1','start_s':0,'end_s':1,'text':'向右移动','timing_method':'vad_segment'}]}


class SpeechEvidence(unittest.TestCase):
    setUp = fixture.VideoLease.setUp
    def audio_client(self, forged=False):
        client=mock.Mock();seen=[]
        def response(path,body=None,**kwargs):
            if path=='/v1/status':return self.status
            seen.append(body)
            content=body['messages'][-1]['content']
            value=fixture.result()
            value['dialogue']=[]
            if isinstance(content,list):
                info=json.loads(content[0]['text'])
                frames=info['frames']
                value['events']=[{'start_s':frames[0]['timestamp_s'],'end_s':frames[-1]['timestamp_s'],
                    'description':'observed action','evidence_frames':list(dict.fromkeys([frames[0]['index'],frames[-1]['index']]))}]
                evidence=json.loads(content[1]['text'].split('\n',1)[1])
                if evidence['cues']:value['dialogue']=[{'cue_ids':['forged'] if forged else [cue['id'] for cue in evidence['cues']],
                    'observation':'Quoted dialogue and sampled image evidence.'}]
            else:
                info=json.loads(content);frames=info['evidence_frames']
                value['events']=[{'start_s':frames[0]['timestamp_s'],'end_s':frames[-1]['timestamp_s'],
                    'description':'merged visual evidence','evidence_frames':list(dict.fromkeys([frames[0]['index'],frames[-1]['index']]))}]
                ids={identity for segment in info['segments'] for item in segment['dialogue'] for identity in item['cue_ids']}
                value['dialogue']=[{'cue_ids':sorted(ids),'observation':'Merged dialogue.'}] if ids else []
            return fixture.completion(value)
        client.request.side_effect=response
        return client,seen

    def test_default_off_and_explicit_subtitle_do_not_call_the_asr_service(self):
        with mock.patch.object(core,'read_profile',side_effect=AssertionError('default-off profile I/O')):
            self.assertIsNone(speech.prepare(self.connection))
            self.assertEqual(speech.prepare(self.connection,timed_transcript=json.dumps(timed()))['cues'][0]['text'],'向右移动')
        self.assertFalse(nodes.StrataVideoAnalyzeFull.INPUT_TYPES()['optional']['enable_asr'][1]['default'])
        self.assertFalse(nodes.StrataSpeechTranscribe.INPUT_TYPES()['required']['enable_asr'][1]['default'])

    def test_full_video_receives_only_overlapping_cues_and_preserves_the_transcript(self):
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,
                duration_s=2,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(timed()))
        result=json.loads(output['result'][1]);self.assertEqual(result['speech'],timed())
        chunks=[r for r in seen if isinstance(r['messages'][-1]['content'],list)]
        self.assertEqual(len(chunks),6)
        for i,req in enumerate(chunks):
            payload=json.loads(req['messages'][-1]['content'][1]['text'].split('\n',1)[1])
            self.assertEqual(bool(payload['cues']),i<3)
        self.assertEqual(result['analysis']['dialogue'][0]['cue_ids'],['u1'])

    def test_forged_dialogue_id_is_rejected(self):
        client,seen=self.audio_client(forged=True)
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            with self.assertRaisesRegex(nodes.StructuredOutputError,'cue'):
                nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(24),fps=24,
                    duration_s=1,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(timed()))

    def test_imported_srt_uses_exact_provided_times_and_no_speaker_guess(self):
        value=speech.prepare(self.connection,timed_transcript='1\n00:00:01,200 --> 00:00:02,500\n你好\n')
        self.assertEqual(value['cues'][0]['start_s'],1.2)
        self.assertIsNone(value['cues'][0]['speaker'])
        self.assertIn('00:00:01,200 --> 00:00:02,500',speech.srt(value))

    def test_merge_receives_complete_utterance_once_instead_of_only_model_paraphrases(self):
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,
                duration_s=2,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(timed()))
        merges=[json.loads(req['messages'][-1]['content']) for req in seen if isinstance(req['messages'][-1]['content'],str)]
        self.assertTrue(merges)
        for info in merges:
            expected=any(segment['dialogue'] for segment in info['segments'])
            self.assertEqual([cue['id'] for cue in info['speech_evidence']['cues']],['u1'] if expected else [])
            if expected:self.assertEqual(info['speech_evidence']['cues'][0]['text'],'向右移动')
        cleanup.assert_called_once()

    def test_selected_window_subtitle_origin_is_added_once(self):
        value=timed();value['timeline']='selected_window'
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(144),fps=24,
                start_s=4,duration_s=2,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(value))
        first=json.loads(seen[0]['messages'][-1]['content'][1]['text'].split('\n',1)[1])
        self.assertEqual(first['cues'][0]['start_s'],4.)
        self.assertEqual(first['cues'][0]['end_s'],5.)

    def test_merge_orders_deduplicated_cues_by_timestamp_instead_of_lexical_id(self):
        value=timed();value['cues'][0]['id']='speech-2'
        value['cues'].append({'id':'speech-10','start_s':1,'end_s':2,'text':'后续对白','timing_method':'vad_segment'})
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,
                duration_s=2,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(value))
        merges=[json.loads(req['messages'][-1]['content']) for req in seen if isinstance(req['messages'][-1]['content'],str)]
        combined=[info['speech_evidence']['cues'] for info in merges if len(info['speech_evidence']['cues'])==2]
        self.assertTrue(combined)
        self.assertTrue(all([cue['id'] for cue in cues]==['speech-2','speech-10'] for cues in combined))

    def test_sparse_frames_keep_dialogue_in_the_requested_sampling_gap(self):
        value=timed();value['cues'][0].update(start_s=.27,end_s=.32)
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(8),fps=4,
                duration_s=1,sampling_fps=24,max_tokens=512,timed_transcript=json.dumps(value))
        first=json.loads(seen[0]['messages'][-1]['content'][1]['text'].split('\n',1)[1])
        self.assertEqual([cue['id'] for cue in first['cues']],['u1'])

    def test_large_import_rejected_before_json_parsing_or_profile_io(self):
        with mock.patch.object(core,'json_loads',side_effect=AssertionError('parsed too large')), \
             mock.patch.object(core,'read_profile',side_effect=AssertionError('profile I/O')):
            with self.assertRaisesRegex(core.StrataError,'2 MiB'):
                speech.prepare(self.connection,timed_transcript='{'+'x'*(contract.MAX_BYTES+1))

    def test_speech_node_disabled_does_not_read_profile_and_retains_empty_outputs(self):
        with mock.patch.object(core,'read_profile',side_effect=AssertionError('profile I/O')):
            self.assertEqual(nodes.StrataSpeechTranscribe().run(self.connection),('','',''))

    def test_shared_speech_copies_match_their_shipping_provenance_and_runtime(self):
        from pathlib import Path
        import test_nodes as context
        root=Path(nodes.__file__).resolve().parent
        proof=json.loads((root/'speech-shared-source.json').read_text(encoding='utf-8'))
        for name,record in proof['files'].items():
            payload=(root/name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(),record['sha256'])
            if context.SOURCE_ROOT:
                source=(context.SOURCE_ROOT/record['source_path']).read_bytes()
                self.assertEqual(hashlib.sha256(source).hexdigest(),record['source_sha256'])
                expected=source.decode('utf-8').replace('from .contract import number','from .speech_contract import number')
                expected=expected.replace('from tools.strata_video import video_duration','from .video_shared import video_duration')
                self.assertEqual(payload,expected.encode('utf-8'))

    def test_forced_confucius_boundaries_preserve_review_status_and_whole_text(self):
        raw={'status':'requires_review','audio_samples_16k':16000,'text':'需复核',
             'segments':[{'segment_id':0,'start_sample':0,'end_sample':16000,
                          'speech_start_sample':1600,'speech_end_sample':14400,
                          'text':'需复核','timing_method':'vad_segment','end_reason':'forced'}]}
        value=speech.prepare(self.connection,timed_transcript=json.dumps(raw))
        self.assertEqual(value['cues'][0]['text'],'需复核')
        self.assertTrue(value['cues'][0]['requires_review'])
        self.assertEqual((value['cues'][0]['start_s'],value['cues'][0]['end_s']),(.1,.9))

    def test_legacy_video_analyze_preserves_outputs_and_merges_real_cue_text(self):
        sampled=video.sample_input(images=fixture.images(48),fps=24,start_s=0,end_s=2,sampling_fps=24,max_frames=48)
        frames,manifest=video.tensor_result(sampled)
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            output=nodes.StrataVideoAnalyze().run(self.connection,frames,manifest,
                frames_per_segment=8,timed_transcript=json.dumps(timed()),max_tokens=512)
        self.assertEqual(len(output['result']),5)
        self.assertEqual(json.loads(output['result'][1])['speech'],timed())
        merges=[json.loads(req['messages'][-1]['content']) for req in seen if isinstance(req['messages'][-1]['content'],str)]
        self.assertTrue(any(cue['text']=='向右移动' for info in merges for cue in info['speech_evidence']['cues']))
        cleanup.assert_called_once()

    def test_examples_have_valid_required_node_inputs_and_output_links(self):
        from pathlib import Path
        root=Path(nodes.__file__).resolve().parent
        for name in ('video-speech-analysis.api.json','video-speech-reuse.api.json','video-subtitle-analysis.api.json'):
            graph=json.loads((root/'examples'/name).read_text(encoding='utf-8'))
            for identity,entry in graph.items():
                if entry['class_type']=='LoadVideo':continue
                definition=nodes.NODE_CLASS_MAPPINGS[entry['class_type']].INPUT_TYPES()
                self.assertTrue(set(definition.get('required',{}))<=set(entry['inputs']),name)
                self.assertTrue(set(entry['inputs'])<=set(definition.get('required',{}))|set(definition.get('optional',{})),name)
                for value in entry['inputs'].values():
                    if isinstance(value,list):
                        upstream=graph[value[0]]
                        if upstream['class_type']!='LoadVideo':
                            self.assertLess(value[1],len(nodes.NODE_CLASS_MAPPINGS[upstream['class_type']].RETURN_TYPES))

    def test_legacy_uniform_sampling_keeps_dialogue_between_visual_chunks(self):
        sampled=video.sample_input(images=fixture.images(240),fps=24,start_s=0,end_s=10,sampling_fps=24,max_frames=8)
        frames,manifest=video.tensor_result(sampled)
        value=timed();value['cues'][0].update(start_s=2.,end_s=2.1)
        client,seen=self.audio_client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            nodes.StrataVideoAnalyze().run(self.connection,frames,manifest,frames_per_segment=2,
                timed_transcript=json.dumps(value),max_tokens=512)
        first=json.loads(seen[0]['messages'][-1]['content'][1]['text'].split('\n',1)[1])
        self.assertEqual([cue['id'] for cue in first['cues']],['u1'])


class SpeechMediaAndTransport(unittest.TestCase):
    setUp=fixture.VideoLease.setUp

    @staticmethod
    def audio(samples=16000,channels=1,rate=16000):
        return {'waveform':np.ones((1,channels,samples),dtype=np.float32),'sample_rate':rate}

    def client(self, *, returned=None, settings=None, supported=True):
        client=mock.Mock()
        config={'device':'cpu','audio_track':0,'channel':'mean',**(settings or {})}
        result=returned or timed()
        def response(path,body=None,**kwargs):
            if path=='/v1/asr/settings':return {'settings':config,'capabilities':{'supported':supported,'missing':['model']}}
            if path=='/v1/status':return self.status
            if path=='/v1/audio/transcribe':return json.loads(json.dumps(result))
            raise AssertionError('Unexpected request '+path)
        client.request.side_effect=response
        return client

    def test_numpy_audio_and_positive_offset_preserve_silence_and_samples(self):
        source=self.audio(samples=8000)
        out,rate=speech.waveform_window(source,0,1,.25)
        self.assertEqual(out.shape,(1,16000));self.assertEqual(rate,16000)
        self.assertFalse(out[:,:4000].any());self.assertTrue((out[:,4000:12000]==1).all())
        self.assertFalse(out[:,12000:].any())

    def test_negative_audio_offset_trims_by_input_clock(self):
        source=self.audio(samples=24000)
        source['waveform'][0,0,:8000]=2
        out,_=speech.waveform_window(source,0,1,-.5)
        self.assertTrue((out==1).all())

    def test_audio_invalid_shape_rate_nonfinite_and_window_are_rejected(self):
        for source in (self.audio(channels=9),self.audio(rate=4000),self.audio(samples=0),
                       {'waveform':np.ones((2,1,10)),'sample_rate':16000},
                       {'waveform':np.full((1,1,10),np.nan),'sample_rate':16000}):
            with self.subTest(source_shape=source['waveform'].shape),self.assertRaises(core.StrataError):
                speech.waveform_window(source,0,.1)
        for start,end,offset in ((0,0,0),(-1,1,0),(0,float('inf'),0),(0,1,float('nan'))):
            with self.subTest(window=(start,end,offset)),self.assertRaises((core.StrataError,ValueError)):
                speech.waveform_window(self.audio(),start,end,offset)

    def test_audio_metadata_is_ignored_without_opening_private_paths(self):
        source=self.audio(samples=8);source['filename']='do-not-open.wav'
        with mock.patch('builtins.open',side_effect=AssertionError('AUDIO metadata I/O')):
            array,_=speech.waveform_window(source,0,.001)
        self.assertTrue((array[:,:8]==1).all())

    def test_wav_uses_float32_channel_interleave_and_wire_budget(self):
        pcm=np.array([[1,2],[3,4]],dtype=np.float32)
        data=speech.wav_bytes(pcm,16000)
        self.assertEqual(data[:4],b'RIFF');self.assertEqual(len(data),60)
        self.assertEqual(np.frombuffer(data[44:],dtype='<f4').tolist(),[1,3,2,4])
        with mock.patch.object(speech,'MAX_PCM_BYTES',15):
            with self.assertRaisesRegex(core.StrataError,'upload budget'):speech.wav_bytes(pcm,16000)

    def test_large_window_fails_before_profile_or_waveform_allocation(self):
        with mock.patch.object(core,'read_profile',side_effect=AssertionError('profile I/O')), \
             mock.patch.object(np,'zeros',side_effect=AssertionError('large allocation')):
            with self.assertRaisesRegex(core.StrataError,'upload budget'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=3600)

    def test_runtime_selected_window_response_is_normalized_without_double_offset(self):
        result=timed();result['timeline']='selected_window'
        client=self.client(returned=result)
        with mock.patch.object(core,'Client',return_value=client):
            output=speech.prepare(self.connection,audio=self.audio(samples=6*16000),
                enable_asr=True,start_s=4,end_s=5)
        self.assertEqual(output['timeline'],'input')
        self.assertEqual(output['cues'][0]['start_s'],4.)
        self.assertEqual(contract.relevant(output,4,5,selected_start_s=4)['cues'][0]['start_s'],4.)

    def test_runtime_out_of_window_cues_are_rejected(self):
        result=timed();result['cues'][0]['end_s']=3
        client=self.client(returned=result)
        with mock.patch.object(core,'Client',return_value=client):
            with self.assertRaisesRegex(core.StrataError,'outside'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)

    def test_digitally_silent_audio_does_not_load_or_transcribe_missing_models(self):
        audio=self.audio();audio['waveform'][:]=0
        client=self.client(supported=False)
        with mock.patch.object(core,'Client',return_value=client):
            output=speech.prepare(self.connection,audio=audio,enable_asr=True,start_s=0,end_s=1)
        self.assertEqual(output['status'],'silence')
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/asr/settings'])

    def test_cpu_transcription_preserves_resident_model_and_channel_choice(self):
        client=self.client(settings={'channel':'right'})
        with mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(core,'gpu_handoff',side_effect=AssertionError('CPU handoff')):
            speech.prepare(self.connection,audio=self.audio(channels=2),enable_asr=True,start_s=0,end_s=1)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/asr/settings','/v1/audio/transcribe'])
        self.assertEqual(client.request.call_args.args[1]['channel'],'right')

    def test_cuda_busy_service_does_not_handoff_or_start_asr(self):
        core.save_profile('test',{'mode':'external','url':'http://127.0.0.1:18402','allow_lifecycle':True,'same_gpu':True})
        self.status['activity']['in_flight']=1
        client=self.client(settings={'device':'cuda'})
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(speech,'asr_gpu_handoff') as handoff:
            with self.assertRaisesRegex(core.StrataError,'idle'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        handoff.assert_not_called()
        self.assertNotIn('/v1/audio/transcribe',[call.args[0] for call in client.request.call_args_list])

    def test_cuda_without_lifecycle_authority_does_not_start_transcription(self):
        core.save_profile('test',{'mode':'external','url':'http://127.0.0.1:18402','allow_lifecycle':False,'same_gpu':False})
        client=self.client(settings={'device':'cuda'})
        with mock.patch.object(core,'Client',return_value=client):
            with self.assertRaisesRegex(core.StrataError,'allow_lifecycle'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/asr/settings','/v1/status'])

    def test_cuda_resident_main_model_does_not_apply_main_model_ram_or_vram_thresholds(self):
        core.save_profile('test',{'mode':'external','url':'http://127.0.0.1:18402',
            'allow_lifecycle':True,'same_gpu':True,'min_free_ram_gib':60,'min_free_vram_mib':12288})
        self.status['loaded']=True
        for process in self.status['processes'].values():process.update(running=True,loaded=True)
        client=self.client(settings={'device':'cuda'})
        order=[];request=client.request.side_effect
        def response(path,body=None,**kwargs):order.append(path);return request(path,body,**kwargs)
        client.request.side_effect=response
        mm=types.ModuleType('comfy.model_management')
        mm.throw_exception_if_processing_interrupted=lambda:None
        mm.get_torch_device=lambda:types.SimpleNamespace(type='cuda')
        mm.unload_all_models=mock.Mock(side_effect=lambda:order.append('comfy_unload'))
        mm.soft_empty_cache=mock.Mock(side_effect=lambda:order.append('comfy_empty'))
        comfy=types.ModuleType('comfy');comfy.model_management=mm
        torch=types.SimpleNamespace(version=types.SimpleNamespace(hip=None),
            cuda=types.SimpleNamespace(mem_get_info=mock.Mock(return_value=(1024,16*1024**3))))
        with mock.patch.dict(sys.modules,{'comfy':comfy,'comfy.model_management':mm,'torch':torch}), \
             mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(core,'gpu_handoff',side_effect=AssertionError('main-model threshold reused')), \
             mock.patch('psutil.virtual_memory',return_value=types.SimpleNamespace(available=1024)) as memory:
            result=speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(order,['/v1/asr/settings','/v1/status','comfy_unload','comfy_empty','/v1/audio/transcribe'])
        memory.assert_not_called();torch.cuda.mem_get_info.assert_not_called()
        self.assertNotIn('/v1/unload',order)

    def test_cuda_managed_owner_mismatch_rejects_before_handoff_or_post(self):
        profile=core.read_profile('test');profile.update(mode='managed',runtime=self.directory.name,same_gpu=True)
        self.status['instance_id']='wrong-owner'
        manager=mock.Mock();manager.ensure.return_value={**self.status,'instance_id':'owned-instance'}
        client=self.client(settings={'device':'cuda'})
        with mock.patch.object(core,'read_profile',return_value=profile), \
             mock.patch.object(core,'Managed',return_value=manager),mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(speech,'asr_gpu_handoff') as handoff:
            with self.assertRaisesRegex(core.StrataError,'different service instance'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        handoff.assert_not_called()
        self.assertNotIn('/v1/audio/transcribe',[call.args[0] for call in client.request.call_args_list])

    def test_cuda_external_pin_and_managed_missing_identity_are_rejected(self):
        profile=core.read_profile('test');profile['instance_id']='expected-instance'
        with self.assertRaisesRegex(core.StrataError,'different service instance'):
            speech.authorize_cuda(profile,{**self.status,'instance_id':'changed'})
        profile.update(mode='managed')
        with self.assertRaisesRegex(core.StrataError,'ownership cannot be confirmed'):
            speech.authorize_cuda(profile,self.status,{'instance_id':None})

    def test_cuda_matching_managed_identity_and_parallel_gate(self):
        profile=core.read_profile('test');profile.update(mode='managed',runtime=self.directory.name,same_gpu=True)
        self.status['instance_id']='owned-instance'
        manager=mock.Mock();manager.ensure.return_value=dict(self.status)
        client=self.client(settings={'device':'cuda'})
        with mock.patch.object(core,'read_profile',return_value=profile), \
             mock.patch.object(core,'Managed',return_value=manager),mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(speech,'asr_gpu_handoff') as handoff:
            speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        handoff.assert_called_once()
        self.status['concurrency']['serving']=2
        with self.assertRaisesRegex(core.StrataError,'serial'):
            speech.authorize_cuda(profile,self.status,manager.ensure.return_value)

    def test_cuda_handoff_failure_blocks_transcription_post(self):
        core.save_profile('test',{'mode':'external','url':'http://127.0.0.1:18402','allow_lifecycle':True,'same_gpu':True})
        client=self.client(settings={'device':'cuda'})
        with mock.patch.object(core,'Client',return_value=client), \
             mock.patch.object(speech,'asr_gpu_handoff',side_effect=core.StrataError('Comfy release failed')):
            with self.assertRaisesRegex(core.StrataError,'release failed'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/asr/settings','/v1/status'])

    def test_cuda_api_engine_release_failure_is_not_hidden_or_retried(self):
        client=self.client(settings={'device':'cuda'});request=client.request.side_effect
        def response(path,body=None,**kwargs):
            if path=='/v1/audio/transcribe':raise core.StrataError('Owned Strata engine release failed')
            return request(path,body,**kwargs)
        client.request.side_effect=response
        with mock.patch.object(core,'Client',return_value=client):
            with self.assertRaisesRegex(core.StrataError,'release failed'):
                speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],['/v1/asr/settings','/v1/status','/v1/audio/transcribe'])

    def test_cuda_handoff_rejects_cpu_or_hip_before_releasing_comfy(self):
        for device,hip in (('cpu',None),('cuda','6.4')):
            mm=types.ModuleType('comfy.model_management')
            mm.get_torch_device=lambda:types.SimpleNamespace(type=device)
            mm.unload_all_models=mock.Mock();mm.soft_empty_cache=mock.Mock()
            comfy=types.ModuleType('comfy');comfy.model_management=mm
            torch=types.SimpleNamespace(version=types.SimpleNamespace(hip=hip))
            with self.subTest(device=device,hip=hip),mock.patch.dict(sys.modules,{'comfy':comfy,'comfy.model_management':mm,'torch':torch}):
                with self.assertRaisesRegex(core.StrataError,'NVIDIA'):speech.asr_gpu_handoff()
            mm.unload_all_models.assert_not_called();mm.soft_empty_cache.assert_not_called()

    def test_cancellation_prevents_late_profile_or_network_requests(self):
        with mock.patch.object(core,'interrupted',side_effect=InterruptedError('cancelled')),mock.patch.object(core,'Client') as client:
            with self.assertRaises(InterruptedError):speech.prepare(self.connection,audio=self.audio(),enable_asr=True,start_s=0,end_s=1)
        client.assert_not_called()

    def test_file_audio_applies_original_trim_once_and_never_decodes_components(self):
        from importlib import import_module
        media=import_module(nodes.__package__+'.speech_media')
        source=io.BytesIO(b'fixture')
        obj=types.SimpleNamespace(get_components=mock.Mock(side_effect=AssertionError('full decode')))
        state={'_VideoFromFile__start_time':2.,'_VideoFromFile__duration':5.}
        with mock.patch.object(video,'official_type',return_value='VideoFromFile'), \
             mock.patch.object(video,'file_source',return_value=(state,None,source)), \
             mock.patch.object(video,'metadata_guard',return_value=12.), \
             mock.patch.object(media,'extract',return_value={'pcm':np.ones(16000),'clock':'original_pts'}) as extract:
            _,_,clock=speech.extract_video_audio(obj,1,2,{'channel':'right','audio_track':1})
        self.assertEqual((extract.call_args.kwargs['start_s'],extract.call_args.kwargs['end_s']),(3.,4.))
        self.assertEqual((extract.call_args.kwargs['channel'],extract.call_args.kwargs['audio_track']),('right',1))
        obj.get_components.assert_not_called()

    def test_components_audio_uses_existing_audio_and_missing_track_returns_empty(self):
        cls=type('ComponentFixture',(),{'get_components':lambda self:self.__dict__['_VideoFromComponents__components']})
        obj=cls();obj.__dict__={'_VideoFromComponents__components':types.SimpleNamespace(audio=self.audio())}
        with mock.patch.object(video,'official_type',return_value='VideoFromComponents'):
            pcm,rate,clock=speech.extract_video_audio(obj,0,1,{})
            self.assertEqual(pcm.shape,(1,16000));self.assertEqual(clock['clock'],'component_audio')
            obj.__dict__['_VideoFromComponents__components'].audio=None
            pcm,_,clock=speech.extract_video_audio(obj,0,1,{})
        self.assertIsNone(pcm);self.assertEqual(clock['status'],'no_track')

    def test_composite_complete_audio_overrides_component_soundtracks(self):
        obj=types.SimpleNamespace(videos=[object()],complete_audio=self.audio(),_VideoFromList__buffer=None)
        with mock.patch.object(video,'official_type',return_value='VideoFromList'), \
             mock.patch.object(video,'file_source',side_effect=AssertionError('decoded overridden soundtrack')):
            pcm,rate,clock=speech.extract_video_audio(obj,0,1,{})
        self.assertEqual(clock['clock'],'composite_complete_audio');self.assertTrue((pcm==1).all())

    def test_composite_component_audio_keeps_the_join_and_local_trim_clocks(self):
        from importlib import import_module
        media=import_module(nodes.__package__+'.speech_media')
        first,second=object(),object()
        composite=types.SimpleNamespace(videos=[first,second],complete_audio=None,_VideoFromList__buffer=None)
        state={'_VideoFromFile__start_time':2.,'_VideoFromFile__duration':1.}
        def source(item):return state,None,item
        def extract(source,**options):
            count=round((options['end_s']-options['start_s'])*16000)
            return {'pcm':np.full(count,2 if source is first else 3,dtype=np.float32),'clock':'original_pts'}
        with mock.patch.object(video,'official_type',side_effect=lambda item:'VideoFromList' if item is composite else 'VideoFromFile'), \
             mock.patch.object(video,'file_source',side_effect=source),mock.patch.object(video,'metadata_guard',return_value=6.), \
             mock.patch.object(media,'extract',side_effect=extract) as decoder:
            pcm,rate,clock=speech.extract_video_audio(composite,.5,1.5,{})
        self.assertEqual(rate,16000);self.assertTrue((pcm[:8000]==2).all());self.assertTrue((pcm[8000:]==3).all())
        self.assertEqual([(call.kwargs['start_s'],call.kwargs['end_s']) for call in decoder.call_args_list],[(2.5,3.),(2.,2.5)])
        self.assertEqual([part['component_start_s'] for part in clock['components']],[0.,1.])


if __name__=='__main__':unittest.main()
