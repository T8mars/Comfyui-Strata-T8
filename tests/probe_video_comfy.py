"""Actual official VIDEO trim/crop and V3 schemas, CPU only; no model/service work."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
from fractions import Fraction
from unittest import mock

source = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(source))
if len(sys.argv) > 2:
    sys.path.insert(0, str(Path(sys.argv[2]).resolve()))
sys.argv = [sys.argv[0]]
import av
import numpy as np
import torch
sys.modules['triton'] = None
from comfy_api.latest import InputImpl

server = types.ModuleType('server')
mm = types.ModuleType('comfy.model_management')
mm.throw_exception_if_processing_interrupted = lambda: None
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strata_video_real_comfy', root / '__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules, {'server': server, 'comfy.model_management': mm, spec.name: package}):
    spec.loader.exec_module(package)
    mapping = {cls.__name__: cls for cls in package.V3_NODES}
    sampler, analysis = mapping['StrataT8VideoSample'], mapping['StrataT8VideoAnalyze']
    sample_schema = sampler.GET_NODE_INFO_V1()
    assert sample_schema['input']['optional']['video'][0] == 'VIDEO'
    assert sample_schema['input']['optional']['images'][0] == 'IMAGE'
    path = Path(directory) / 'video.mp4'
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=4)
        stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
        for index in range(12):
            pixels = np.zeros((48, 64, 3), np.uint8)
            pixels[:, :32, 0] = 255
            pixels[:, 32:, 1] = 255
            pixels[:, :, 2] = index * 15
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 4)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    if not hasattr(InputImpl.VideoFromFile, 'as_trimmed'):
        frames, text = sampler.execute(video=InputImpl.VideoFromFile(str(path)), fps=24,
            timestamps_json='', start_s=.5, end_s=2., sampling_fps=2,
            max_frames=4, max_pixels=65536).args
        assert tuple(frames.shape) == (3, 48, 64, 3), frames.shape
        manifest = package.nodes.video_tools.shared().validate_manifest(json.loads(text), 3)
        assert manifest['source']['source_pts_preserved'] is True
        assert [f['source_timestamp_s'] for f in manifest['frames']] == [.5, 1., 1.5]
        print(json.dumps({'nodes': len(mapping), 'old_file_only_layout': True,
                          'frames': list(frames.shape), 'v3_schemas': True, 'gpu_model_loaded': False}))
        raise SystemExit(0)
    video = InputImpl.VideoFromFile(str(path)).as_trimmed(.5, 1.5, False).as_cropped(0, 0, 16, 24)
    outputs = sampler.execute(video=video, fps=24, timestamps_json='', start_s=0, end_s=0,
                              sampling_fps=2, max_frames=4, max_pixels=65536)
    frames, text = outputs.args
    assert tuple(frames.shape) == (3, 24, 16, 3), frames.shape
    assert frames.device.type == 'cpu'
    assert float(frames[..., 0].mean()) > .9 and float(frames[..., 1].mean()) < .1
    manifest = package.nodes.video_tools.shared().validate_manifest(json.loads(text), len(frames))
    assert manifest['source']['source_pts_preserved'] is True
    assert manifest['source']['original_media_time_origin_s'] == .5
    assert [round(frame['original_media_timestamp_s'], 2) for frame in manifest['frames']] == [.5, 1, 1.5]
    analysis_schema = analysis.GET_NODE_INFO_V1()
    assert analysis_schema['output'] == ['STRING'] * 5
    assert 'native' in analysis_schema['input']['required']['mode'][1]['options']
    rotated_path = Path(directory) / 'rotated.mp4'
    print('rotation probe: encoding display-matrix fixture', flush=True)
    with av.open(str(rotated_path), 'w') as container:
        stream = container.add_stream('libx264', rate=4)
        stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
        stream.set_display_rotation(90)
        print('rotation probe: display matrix configured', flush=True)
        for index in range(4):
            pixels = np.zeros((48, 64, 3), np.uint8)
            pixels[:, :32, 0] = 255
            pixels[:, 32:, 1] = 255
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    print('rotation probe: file written; adapting official crop', flush=True)
    rotated = InputImpl.VideoFromFile(str(rotated_path)).as_trimmed(.25, .5, False).as_cropped(0, 40, 16, 16)
    outputs = sampler.execute(video=rotated, fps=4, timestamps_json='', start_s=0, end_s=0,
                              sampling_fps=4, max_frames=4, max_pixels=65536)
    rotated_frames, rotated_text = outputs.args
    assert tuple(rotated_frames.shape) == (2, 16, 16, 3), rotated_frames.shape
    assert float(rotated_frames[..., 0].mean()) > .9 and float(rotated_frames[..., 1].mean()) < .1
    rotated_manifest = package.nodes.video_tools.shared().validate_manifest(json.loads(rotated_text), 2)
    assert [f['source_timestamp_s'] for f in rotated_manifest['frames']] == [.25, .5]
    assert [f['timestamp_s'] for f in rotated_manifest['frames']] == [0, .25]
    accumulated = InputImpl.VideoFromList([video, video])
    concatenated, concatenated_text = sampler.execute(video=accumulated, fps=4, timestamps_json='',
        start_s=0, end_s=0, sampling_fps=2, max_frames=4, max_pixels=65536).args
    concat_manifest = package.nodes.video_tools.shared().validate_manifest(json.loads(concatenated_text), 4)
    assert [f['component_index'] for f in concat_manifest['frames']] == [0, 0, 1, 1]
    assert [f['timestamp_s'] for f in concat_manifest['frames']] == [0, .75, 1.5, 2.25]
    assert tuple(concatenated.shape) == (4, 24, 16, 3)
    from comfy_api.latest import VideoComponents
    components = InputImpl.VideoFromComponents(VideoComponents(images=frames, frame_rate=Fraction(2), audio=None))
    component_frames, _ = sampler.execute(video=components, fps=4, timestamps_json='',
        start_s=0, end_s=0, sampling_fps=2, max_frames=4, max_pixels=65536).args
    assert tuple(component_frames.shape) == (3, 24, 16, 3)
    unknown_layout = InputImpl.VideoFromFile(str(path))
    unknown_layout.future_transform = 'unreviewed'
    with mock.patch.object(unknown_layout, 'get_components') as decoded:
        try:
            sampler.execute(video=unknown_layout, fps=4, timestamps_json='', start_s=0, end_s=0,
                            sampling_fps=2, max_frames=4, max_pixels=65536)
        except package.core.StrataError as error:
            assert 'layout' in str(error)
        else:
            raise AssertionError('Unreviewed official object layout was accepted')
        decoded.assert_not_called()
    print(json.dumps({'nodes': len(mapping), 'frames': list(frames.shape), 'crop_and_trim': True,
                      'original_media_timestamps': [f['original_media_timestamp_s'] for f in manifest['frames']],
                      'v3_schemas': True, 'display_matrix_rotation_crop_trim': True,
                      'concatenated_file_sampling': True, 'component_tensor_sampling': True,
                      'unknown_layout_rejected': True, 'gpu_model_loaded': False}))
