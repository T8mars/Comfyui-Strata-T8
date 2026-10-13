"""Opt-in dense sampling does not discard a 240/360-frame video before inference."""
import json
import unittest
from unittest import mock
import test_video_nodes as fixture

core,nodes,video=fixture.core,fixture.nodes,fixture.video


class DenseVideo(unittest.TestCase):
    setUp = fixture.VideoLease.setUp
    def client(self, *, fail_after=None, native=False):
        client=mock.Mock();seen=[]
        status=dict(self.status)
        status['capabilities']={'video':{'contract_version':1,'modes':['frames','native'] if native else ['frames']}}
        def response(path,body=None,**kwargs):
            if path=='/v1/status':return status
            if fail_after is not None and len(seen)>=fail_after:raise InterruptedError('cancel dense video')
            seen.append(body)
            content=body['messages'][-1]['content']
            if isinstance(content,list):
                span=json.loads(content[0]['text'])['frames']
                events=[{'start_s':span[0]['timestamp_s'],'end_s':span[-1]['timestamp_s'],
                    'description':'observed action','evidence_frames':[span[0]['index'],span[-1]['index']]}]
            else:
                info=json.loads(content)
                # Compress merge results while preserving real endpoint evidence.
                scope=info['evidence_frames']
                events=[{'start_s':scope[0]['timestamp_s'],'end_s':scope[-1]['timestamp_s'],
                    'description':'merged actions','evidence_frames':[scope[0]['index'],scope[-1]['index']]}]
            return fixture.completion(fixture.result('observed',events))
        client.request.side_effect=response
        return client,seen

    def test_10_seconds_at_24_fps_analyzes_240_frames_with_global_evidence_and_one_cleanup(self):
        client,seen=self.client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(360),fps=24,
                duration_s=10,sampling_fps=24,max_tokens=512)
        evidence=json.loads(output['result'][2]);coverage=json.loads(output['result'][3])
        self.assertEqual(len(evidence),240);self.assertEqual([f['index'] for f in evidence],list(range(240)))
        self.assertEqual(coverage['completed_segments'],30);self.assertTrue(coverage['analysis_completed'])
        self.assertAlmostEqual(coverage['max_sample_gap_s'],1/24)
        chunks=[r for r in seen if isinstance(r['messages'][-1]['content'],list)]
        self.assertEqual(len(chunks),30);self.assertEqual(len(json.loads(output['result'][1])['segments']),30)
        self.assertTrue(all(len(json.loads(r['messages'][-1]['content'][0]['text'])['frames'])==8 for r in chunks))
        cleanup.assert_called_once()

    def test_15_seconds_native_has_360_frames_but_no_request_has_more_than_16(self):
        client,seen=self.client(native=True)
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(360),fps=24,
                sampling_fps=24,mode='native',frames_per_segment=16,max_tokens=512)
        coverage=json.loads(output['result'][3]);self.assertEqual(coverage['sample_count'],360)
        self.assertEqual(coverage['total_segments'],23)
        parts=[r['messages'][-1]['content'][1] for r in seen if isinstance(r['messages'][-1]['content'],list)]
        self.assertEqual(sum(len(p['frames']) for p in parts),360)
        self.assertTrue(all(p['type']=='strata_video' and len(p['frames'])<=16 for p in parts))
        for part in parts:
            for frame in part['frames']:
                self.assertAlmostEqual(part['metadata']['time_origin_s']+frame['timestamp_s'],frame['source_timestamp_s'])
        cleanup.assert_called_once()

    def test_selected_window_times_remain_relative_and_source_clock_is_preserved(self):
        client,seen=self.client(native=True)
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(360),fps=24,
                start_s=5,duration_s=2,sampling_fps=24,mode='native',frames_per_segment=16,max_tokens=512)
        evidence=json.loads(output['result'][2]);coverage=json.loads(output['result'][3])
        self.assertEqual(len(evidence),48);self.assertEqual(evidence[0]['timestamp_s'],0)
        self.assertEqual(evidence[0]['source_timestamp_s'],5);self.assertEqual(coverage['first_sample_s'],5)

    def test_low_rate_source_reports_actual_count_instead_of_requested_fps(self):
        client,seen=self.client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            output=nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(120),fps=12,
                duration_s=10,sampling_fps=24,max_tokens=512)
        coverage=json.loads(output['result'][3]);self.assertEqual(coverage['sample_count'],120)
        self.assertEqual(coverage['estimated_frames'],240);self.assertAlmostEqual(coverage['max_sample_gap_s'],1/12)

    def test_cancel_and_late_sampling_failure_cleanup_once(self):
        for cause in ('network','decode'):
            client,seen=self.client(fail_after=2 if cause=='network' else None)
            original=video.sample_input;calls=[]
            def sampling(*args,**kwargs):
                calls.append(kwargs)
                if cause=='decode' and len(calls)==3:raise InterruptedError('cancel decode')
                return original(*args,**kwargs)
            with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup,\
                    mock.patch.object(video,'sample_input',side_effect=sampling):
                with self.assertRaises(InterruptedError):
                    nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(240),fps=24,max_tokens=512)
                cleanup.assert_called_once()
            self.assertEqual(len(seen),2)

    def test_unknown_native_capability_rejects_before_model_generation(self):
        client,seen=self.client(native=False)
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(core.StrataError,'native video'):
                nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(240),fps=24,mode='native',max_tokens=512)
            cleanup.assert_not_called()
        self.assertEqual(seen,[])

    def test_invalid_seconds_and_excessive_plan_fail_before_lease(self):
        for params in ({'duration_s':True},{'duration_s':-1},{'duration_s':3601},{'sampling_fps':0},
                       {'start_s':False},{'frames_per_segment':16},{'duration_s':0,'sampling_fps':30,'frames_per_segment':2}):
            with self.subTest(params=params),mock.patch.object(core,'Client') as client:
                with self.assertRaises((core.StrataError,ValueError)):
                    nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(2400),fps=24,**params)
                client.assert_not_called()

    def test_node_id_and_existing_defaults_are_preserved(self):
        self.assertIn('StrataT8VideoAnalyzeFull',nodes.NODE_CLASS_MAPPINGS)
        self.assertEqual(nodes.StrataVideoSample.INPUT_TYPES()['required']['max_frames'][1]['default'],64)
        self.assertEqual(nodes.StrataVideoAnalyze.INPUT_TYPES()['required']['frames_per_segment'][1]['default'],8)

    def test_full_schema_does_not_allow_invented_global_evidence(self):
        client,seen=self.client()
        good=client.request.side_effect
        def fabricated(path,body=None,**kwargs):
            if path=='/v1/status':return good(path,body,**kwargs)
            return fixture.completion(fixture.result(events=[{'start_s':0,'end_s':0,
                'description':'invented','evidence_frames':[4000]}]))
        client.request.side_effect=fabricated
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup') as cleanup:
            with self.assertRaisesRegex(nodes.StructuredOutputError,'outside this stage'):
                nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(240),fps=24,max_tokens=512)
            cleanup.assert_called_once()

    def test_observation_scope_is_not_reused_for_complete_video_summary(self):
        client,seen=self.client()
        with mock.patch.object(core,'Client',return_value=client),mock.patch.object(core,'cleanup'):
            nodes.StrataVideoAnalyzeFull().run(self.connection,images=fixture.images(48),fps=24,max_tokens=512)
        observations=[r for r in seen if isinstance(r['messages'][-1]['content'],list)]
        summaries=[r for r in seen if isinstance(r['messages'][-1]['content'],str)]
        self.assertTrue(observations and summaries)
        self.assertTrue(all('Analyze ONLY this attached chunk' in r['messages'][0]['content'] for r in observations))
        self.assertTrue(all('COMPLETE selected window' in r['messages'][0]['content'] for r in summaries))
        self.assertTrue(all('Analyze ONLY this attached chunk' not in r['messages'][0]['content'] for r in summaries))
        self.assertTrue(all(r['temperature']==0 for r in seen))

    def test_pixel_measurements_report_only_qualified_regions_without_direction_labels(self):
        from PIL import Image,ImageDraw
        first=Image.new('RGB',(128,72),(220,0,0));last=first.copy()
        ImageDraw.Draw(first).ellipse((12,28,28,44),fill='white')
        ImageDraw.Draw(last).ellipse((92,28,108,44),fill='white')
        a,b=video.frame_measurements(first),video.frame_measurements(last)
        self.assertEqual(a['dominant_uniform_rgb'],[220,0,0])
        self.assertLess(a['foreground_region_center_xy'][0],b['foreground_region_center_xy'][0])
        self.assertAlmostEqual(a['foreground_region_center_xy'][1],b['foreground_region_center_xy'][1])
        import numpy as np
        rng=np.random.default_rng(4)
        textured=Image.fromarray(rng.integers(0,256,(72,128,3),dtype=np.uint8))
        self.assertEqual(video.frame_measurements(textured),{})
        self.assertNotIn('object',str(a));self.assertNotIn('direction',str(a))

    def test_pixel_statistics_ignore_normalization_padding_and_keep_frame_coordinates(self):
        from PIL import Image,ImageDraw
        canvas=Image.new('RGB',(128,128),'white')
        canvas.paste(Image.new('RGB',(16,128),(0,0,220)),(56,0))
        ImageDraw.Draw(canvas).ellipse((61,60,67,66),fill='white')
        measured=video.frame_measurements(canvas,[56,0,16,128])
        self.assertEqual(measured['dominant_uniform_rgb'],[0,0,220])
        self.assertAlmostEqual(measured['foreground_region_center_xy'][0],.5039,places=3)
        self.assertEqual(measured['rgb_region'],'content_inside_white_padding')


if __name__=='__main__':unittest.main()
