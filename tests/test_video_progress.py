"""Opt-in stage reuse after interruption, adaptive coverage and boundary budgets."""
import copy
import json
import importlib
import unittest
from unittest import mock
import test_video_nodes as fixture
import test_video_stream

core,nodes,video=fixture.core,fixture.nodes,fixture.video
video_progress=importlib.import_module(core.__package__+'.video_progress')


class Progress(unittest.TestCase):
    setUp=test_video_stream.DenseVideo.setUp
    client=test_video_stream.DenseVideo.client
    def cached_client(self,fail_after=None,signature='a'*64):
        client,seen=self.client(fail_after=fail_after)
        previous=client.request.side_effect
        def response(path,*args,**kwargs):
            if path=='/v1/video/capabilities':return {'runtime_context':signature}
            return previous(path,*args,**kwargs)
        client.request.side_effect=response
        return client,seen
    def run_saved(self,client,**kwargs):
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'),mock.patch.object(video_progress,'source_fingerprint',return_value='source-a'):
            return nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,duration_s=2,
                                                    sampling_fps=24,max_tokens=512,save_progress=True,**kwargs)
    def test_interruption_commits_only_validated_requests_then_skips_them(self):
        client,seen=self.cached_client(fail_after=2)
        with self.assertRaises(InterruptedError):self.run_saved(client)
        self.assertEqual(len(seen),2)
        records=list((core.HOME/'video-progress').glob('*.json'))
        self.assertEqual(len(records),1)
        journal=json.loads(records[0].read_text(encoding='utf-8'))
        self.assertEqual(len(journal['responses']),2)
        self.assertNotIn('data:image',records[0].read_text(encoding='utf-8'))
        restarted,seen=self.cached_client()
        output=self.run_saved(restarted)
        usage=json.loads(output['result'][4])
        self.assertEqual(usage['checkpoint']['reused_requests'],2)
        self.assertEqual(json.loads(output['result'][3])['sample_count'],48)
        again,seen=self.cached_client()
        repeat=self.run_saved(again)
        self.assertEqual(len(seen),0)
        self.assertGreater(json.loads(repeat['result'][4])['checkpoint']['reused_requests'],2)
    def test_changed_runtime_blocks_reuse_and_clear_allows_new_run(self):
        client,_=self.cached_client();self.run_saved(client)
        changed,seen=self.cached_client(signature='b'*64)
        with self.assertRaisesRegex(core.StrataError,'changed'):self.run_saved(changed)
        self.assertEqual(seen,[])
        self.run_saved(changed,clear_progress=True)
        self.assertTrue(seen)
    def test_cross_window_boundary_does_not_inflate_unique_coverage(self):
        client,seen=self.client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,duration_s=2,
                sampling_fps=24,max_tokens=512,continuity=True)
        evidence=json.loads(output['result'][2]);self.assertEqual(len(evidence),48)
        chunks=[r['messages'][-1]['content'] for r in seen if isinstance(r['messages'][-1]['content'],list)]
        self.assertTrue(any(any('Boundary reference' in p.get('text','') for p in content) for content in chunks[1:]))
        self.assertTrue(all(sum(p.get('type')=='image_url' for p in content)<=8 for content in chunks))
    def test_invalid_progress_is_rejected_before_external_gpu_handoff(self):
        profile=core.read_profile('test');profile['same_gpu']=True;core.save_profile('test',profile)
        client=mock.Mock();client.request.return_value=self.status
        cache=mock.Mock();cache.bind.side_effect=core.StrataError('runtime changed')
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'gpu_handoff') as handoff,mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError,'changed'):
                core.generate_video_pipeline(self.connection,[nodes.request('question')],lambda *_:None,stage_cache=cache)
        handoff.assert_not_called();cleanup.assert_not_called()
    def test_adaptive_image_input_preserves_entire_window_and_actual_timestamps(self):
        sampled=video.sample_input(images=fixture.images(288),fps=24,start_s=2,end_s=12,sampling_fps=1,
                                   max_frames=8,max_pixels=65536,sampling_strategy='adaptive')
        self.assertEqual(sampled.manifest['sampling']['start_s'],2)
        self.assertEqual(sampled.manifest['sampling']['end_s'],12)
        self.assertEqual(len(sampled.frames),8)
        self.assertGreater(sampled.frames[-1].source_timestamp_s,11)
        video.shared().validate_manifest(sampled.manifest,8)

