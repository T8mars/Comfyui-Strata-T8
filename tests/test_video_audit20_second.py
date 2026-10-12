"""Second local CPU video audit: adapters, evidence geometry and output boundaries."""
import io
import json
from fractions import Fraction
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import numpy as np
from PIL import Image
import test_nodes as fixture
import test_video_nodes as vf

core, nodes = fixture.core, fixture.nodes
video = nodes.video_tools
MODULE = 'comfy_api.latest._input_impl.video_types'


def official_file(source=None, duration=2., trim=0., crop=None):
    source = source if source is not None else io.BytesIO(b'fixture')
    cls = type('VideoFromFile', (), {'__module__': MODULE,
        'get_stream_source': lambda self: source, 'get_duration': lambda self: duration,
        'get_active_trim_window': lambda self: (trim, duration),
        'get_components': mock.Mock(side_effect=AssertionError('unbounded decode'))})
    obj = cls()
    obj.__dict__.update({'_VideoFromFile__file': source, '_VideoFromFile__start_time': trim,
                        '_VideoFromFile__duration': duration, '_VideoFromFile__crop': crop})
    return obj, cls


def official_list(count=2):
    first, file_cls = official_file(duration=1.)
    files = [first] + [file_cls() for _ in range(count - 1)]
    for item in files[1:]: item.__dict__.update(first.__dict__)
    list_cls = type('VideoFromList', (), {'__module__': MODULE})
    obj = list_cls()
    obj.__dict__.update(videos=files, complete_audio=None, _VideoFromList__buffer=None)
    return obj, types.SimpleNamespace(VideoFromFile=file_cls, VideoFromList=list_cls)


def official_components(images, fps=Fraction(1), alpha=None, color_space='sRGB'):
    components = types.SimpleNamespace(images=images, frame_rate=fps, alpha=alpha)
    cls = type('VideoFromComponents', (), {'__module__': MODULE,
        'get_components': lambda self: components, 'get_color_space': lambda self: color_space})
    obj = cls()
    obj.__dict__.update({'_VideoFromComponents__components': components,
        '_VideoFromComponents__bit_depth': 8, '_VideoFromComponents__color_space': color_space})
    return obj, types.SimpleNamespace(VideoFromComponents=cls)


class VideoAudit20Second(unittest.TestCase):
    setUp = vf.VideoLease.setUp

    def sample(self, count=2, **kwargs):
        return nodes.StrataVideoSample().run(images=vf.images(count), fps=1, **kwargs)

    def analyze(self, prompt='valid generation prompt', count=2, **kwargs):
        frames, manifest = self.sample(count)
        value = vf.result('This is an analysis report, not a generation prompt.')
        value['prompt'] = prompt
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(value)]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            output = nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', **kwargs)
        return output, client, cleanup

    def test_01_huge_integer_numeric_options_raise_domain_error(self):
        for kwargs in ({'fps': 10**1000}, {'sampling_fps': 10**1000}, {'start_s': 10**1000}, {'end_s': 10**1000}):
            with self.subTest(option=next(iter(kwargs))), mock.patch.object(video, 'pil_frame') as copied:
                with self.assertRaises(core.StrataError):
                    nodes.StrataVideoSample().run(images=vf.images(2), **kwargs)
                copied.assert_not_called()

    def test_02_subnormal_fps_is_rejected_before_cpu_conversion(self):
        with mock.patch.object(video, 'pil_frame') as copied:
            with self.assertRaises(core.StrataError):
                nodes.StrataVideoSample().run(images=vf.images(1), fps=1e-320, end_s=1)
            copied.assert_not_called()

    def test_03_long_image_window_is_rejected_before_cpu_conversion(self):
        with mock.patch.object(video, 'pil_frame') as copied:
            with self.assertRaises(core.StrataError):
                nodes.StrataVideoSample().run(images=vf.images(4), fps=.001)
            copied.assert_not_called()

    def test_04_subnormal_sampling_rate_on_accumulated_video_is_domain_error(self):
        accumulated, module = official_list()
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'metadata_guard'), mock.patch.object(video, 'file_adapter') as decode:
            with self.assertRaises(core.StrataError):
                nodes.StrataVideoSample().run(video=accumulated, sampling_fps=1e-320)
            decode.assert_not_called()

    def test_05_manifest_dimensions_must_match_actual_tensor(self):
        frames, text = self.sample()
        manifest = json.loads(text)
        manifest['frames'][0].update(width=31, height=24)
        with mock.patch.object(core, 'encode_images') as copied, mock.patch.object(core, 'Client') as client:
            with self.assertRaisesRegex(core.StrataError, 'dimensions'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, json.dumps(manifest))
            copied.assert_not_called()
            client.assert_not_called()

    def test_06_mixed_frame_sizes_keep_aspect_and_record_tensor_geometry(self):
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4), 'red'), Image.new('RGB', (4, 2), 'blue')], fps=1)
        frames, text = video.tensor_result(sampled)
        manifest = json.loads(text)
        self.assertEqual(frames.shape, (2, 4, 4, 3))
        self.assertEqual([(f['width'], f['height']) for f in manifest['frames']], [(4, 4), (4, 4)])
        self.assertEqual(manifest['frames'][1]['pre_tensor_size'], [4, 2])
        self.assertEqual(manifest['frames'][1]['tensor_content_box'], [0, 1, 4, 2])
        self.assertTrue(np.all(frames.array[1, 0] == 1.))
        self.assertTrue(np.all(frames.array[1, 1:3, :, 2] == 1.))
        self.assertTrue(np.all(frames.array[1, 1:3, :, :2] == 0.))

    def test_07_mixed_size_canvas_stays_within_sampling_pixel_budget(self):
        sampled = video.shared().sample_images([Image.new('RGB', (512, 512)), Image.new('RGB', (512, 128))], fps=1, max_pixels=65536)
        frames, text = video.tensor_result(sampled)
        manifest = video.shared().validate_manifest(json.loads(text), 2)
        self.assertLessEqual(frames.shape[1] * frames.shape[2], 65536)
        self.assertTrue(all((f['width'], f['height']) == (frames.shape[2], frames.shape[1]) for f in manifest['frames']))

    def test_08_empty_prompt_cannot_fall_back_to_report(self):
        with self.assertRaisesRegex(nodes.StructuredOutputError, 'prompt'):
            self.analyze(prompt='')

    def test_09_whitespace_prompt_cannot_reach_downstream(self):
        with self.assertRaisesRegex(nodes.StructuredOutputError, 'prompt'):
            self.analyze(prompt=' \t\n\u3000')

    def test_10_nonempty_prompt_retains_exact_user_visible_output(self):
        output, _, cleanup = self.analyze(prompt='A red ball rolls to the right.')
        self.assertEqual(output['result'][0], 'A red ball rolls to the right.')
        self.assertEqual(output['ui']['text'], ['A red ball rolls to the right.'])
        cleanup.assert_called_once()

    def test_11_official_trim_resolves_negative_start_without_get_components(self):
        obj, cls = official_file(duration=1., trim=-1.)
        cls.get_active_trim_window = lambda self: (3., 1.)
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4))], timestamps=[3.], source_duration_s=4., start_s=3., end_s=4., preselected=True)
        with mock.patch.dict(sys.modules, {MODULE: types.SimpleNamespace(VideoFromFile=cls)}), mock.patch.object(video, 'metadata_guard', return_value=4.), mock.patch.object(video.shared(), 'sample_video', return_value=sampled) as decode:
            _, text = nodes.StrataVideoSample().run(video=obj, end_s=.5)
        self.assertEqual(decode.call_args.kwargs['start_s'], 3.)
        self.assertEqual(decode.call_args.kwargs['end_s'], 3.5)
        self.assertEqual(json.loads(text)['frames'][0]['original_media_timestamp_s'], 3.)
        cls.get_components.assert_not_called()
        # A short later window is valid even when its absolute end exceeds 3600.
        _, later_text = nodes.StrataVideoSample().run(images=vf.images(4), fps=4,
            timestamps_json='[5000,5000.25,5000.5,5000.75]', start_s=5000., end_s=5001., sampling_fps=4)
        later = json.loads(later_text)
        self.assertEqual([f['timestamp_s'] for f in later['frames']], [0., .25, .5, .75])
        self.assertEqual(later['sampling']['start_s'], 5000.)

    def test_12_accumulated_boundary_target_belongs_to_next_component(self):
        accumulated, module = official_list()
        seen = []
        def sample_file(component, **kwargs):
            seen.append((component, kwargs))
            return video.shared().sample_images([Image.new('RGB', (4, 4))], timestamps=[kwargs['start_s']], source_duration_s=1., preselected=True, start_s=kwargs['start_s'], end_s=kwargs['end_s'])
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'metadata_guard', return_value=1.), mock.patch.object(video, 'file_adapter', side_effect=sample_file):
            _, text = nodes.StrataVideoSample().run(video=accumulated, start_s=1., end_s=2., max_frames=1)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], accumulated.videos[1])
        self.assertEqual(json.loads(text)['frames'][0]['component_index'], 1)

    def test_13_accumulated_metadata_loop_observes_cancellation_between_files(self):
        accumulated, module = official_list(8)
        calls = []
        def metadata(_): calls.append('read')
        def cancel():
            if calls: raise InterruptedError('cancel after first metadata')
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'metadata_guard', side_effect=metadata), mock.patch.object(core, 'interrupted', side_effect=cancel):
            with self.assertRaisesRegex(InterruptedError, 'cancel after first'):
                nodes.StrataVideoSample().run(video=accumulated)
        self.assertEqual(len(calls), 1)

    def test_14_accumulated_metadata_time_is_in_total_cpu_deadline(self):
        accumulated, module = official_list(8)
        clock, reads = [0.], []
        def metadata(_): reads.append(1); clock[0] += 181.
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'metadata_guard', side_effect=metadata), mock.patch.object(video.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(core.StrataError, 'CPU time limit'):
                nodes.StrataVideoSample().run(video=accumulated)
        self.assertEqual(len(reads), 1)

    def test_15_component_fraction_overflow_has_domain_error(self):
        obj, module = official_components(vf.images(2), fps=Fraction(10**1000))
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'pil_frame') as copied:
            with self.assertRaises(core.StrataError):
                nodes.StrataVideoSample().run(video=obj)
            copied.assert_not_called()

    def test_16_component_alpha_is_composited_for_selected_frames(self):
        source = vf.Tensor(np.zeros((2, 4, 4, 3), np.float32))
        alpha = vf.Tensor(np.zeros((2, 4, 4), np.float32))
        obj, module = official_components(source, alpha=alpha)
        with mock.patch.dict(sys.modules, {MODULE: module}):
            frames, text = nodes.StrataVideoSample().run(video=obj, max_frames=1)
        self.assertTrue(np.all(frames.array == 1.))
        self.assertEqual(len(source.transfers), 1)
        self.assertEqual(len(alpha.transfers), 1)
        self.assertEqual(json.loads(text)['source']['alpha_compositing'], 'white')

    def test_17_component_hdr_transfer_is_rejected_before_copy(self):
        obj, module = official_components(vf.images(2), color_space='HDR PQ')
        with mock.patch.dict(sys.modules, {MODULE: module}), mock.patch.object(video, 'pil_frame') as copied:
            with self.assertRaisesRegex(core.StrataError, 'color space'):
                nodes.StrataVideoSample().run(video=obj)
            copied.assert_not_called()

    def test_18_explicit_vfr_times_ignore_cfr_sampling_but_keep_source_indices(self):
        frames, text = nodes.StrataVideoSample().run(images=vf.images(4), fps=10,
            timestamps_json='[0,0.11,0.51,0.93]', start_s=.1, end_s=1., sampling_fps=2)
        manifest = json.loads(text)
        self.assertEqual([f['input_frame_index'] for f in manifest['frames']], [1, 3])
        self.assertEqual([round(f['timestamp_s'], 2) for f in manifest['frames']], [.01, .83])
        self.assertEqual(manifest['source']['timestamp_basis'], 'provided')

    def test_19_tensor_stack_checks_cancel_between_individual_frame_copies(self):
        sampled = video.shared().sample_images([Image.new('RGB', (4, 4))] * 3, fps=1)
        calls = []
        original = np.asarray
        def copied(*args, **kwargs): calls.append(1); return original(*args, **kwargs)
        def cancel():
            if calls: raise InterruptedError('cancel after one stack conversion')
        with mock.patch.object(video.core, 'interrupted', side_effect=cancel), mock.patch.object(np, 'asarray', side_effect=copied):
            with self.assertRaisesRegex(InterruptedError, 'one stack conversion'):
                video.tensor_result(sampled)
        self.assertEqual(len(calls), 1)

    def test_20_final_empty_prompt_is_rejected_and_original_lease_cleaned(self):
        frames, manifest = self.sample(4)
        value = vf.result('non-prompt report')
        value['prompt'] = 'segment generation prompt'
        summary = vf.result('must not be returned as prompt')
        client = mock.Mock()
        client.request.side_effect = [self.status, vf.completion(value), vf.completion(value), vf.completion(summary)]
        with mock.patch.object(core, 'Client', return_value=client), mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(nodes.StructuredOutputError, 'prompt'):
                nodes.StrataVideoAnalyze().run(self.connection, frames, manifest, task='prompt', frames_per_segment=2)
            cleanup.assert_called_once()
        self.assertEqual(len(client.request.call_args_list), 4)


if __name__ == '__main__':
    unittest.main()
