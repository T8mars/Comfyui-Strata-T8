"""CPU video adapters and ordered-frame requests; no imports perform I/O or GPU work."""
from __future__ import annotations
import bisect
import io
import json
import math
from pathlib import Path
import sys
import time
from . import core

MAX_FRAMES = 64
MAX_INPUT_BYTES = 256 * 1024**2
MAX_DURATION = 3600


def shared():
    from . import video_shared
    return video_shared


def number(value, name, low=0, high=MAX_DURATION, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high or positive and value <= low):
        raise core.StrataError(f'Invalid video {name}')
    return float(value)


def timestamps(text, count, fps):
    if not isinstance(text, str):
        raise core.StrataError('Video timestamps must be a JSON array in seconds')
    values = core.json_loads(text) if text.strip() else [i / fps for i in range(count)]
    if not isinstance(values, list) or len(values) != count:
        raise core.StrataError('Supply one timestamp for each input frame')
    values = [number(v, 'timestamp', high=86400) for v in values]
    if any(a >= b for a, b in zip(values, values[1:])):
        raise core.StrataError('Video timestamps must be strictly increasing')
    return values


def pil_frame(tensor, max_pixels):
    import numpy as np
    from PIL import Image
    core.interrupted()
    array = tensor.detach().to('cpu').float().numpy()
    if not np.isfinite(array).all():
        raise core.StrataError('IMAGE contains non-finite pixel values')
    image = Image.fromarray((np.clip(array, 0, 1) * 255).round().astype(np.uint8))
    if image.mode == 'RGBA':
        rgb = Image.new('RGB', image.size, 'white')
        rgb.paste(image, mask=image.getchannel('A'))
        image = rgb
    else:
        image = image.convert('RGB')
    scale = min(1, (max_pixels / (image.width * image.height))**.5)
    if scale < 1:
        width, height = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        if width * height > max_pixels:
            if width >= height:
                width = max(1, max_pixels // height)
            else:
                height = max(1, max_pixels // width)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    core.interrupted()
    return image


def sample_tensor(images, *, fps, timestamps_json='', start_s=0, end_s=None,
                  sampling_fps=1, max_frames=64, max_pixels=589824):
    if (not hasattr(images, 'shape') or len(images.shape) != 4 or images.shape[-1] not in (3, 4)
            or not 1 <= images.shape[0] <= 180000 or images.shape[1] < 1 or images.shape[2] < 1
            or images.shape[1] * images.shape[2] > 16 * 1048576):
        raise core.StrataError('Video IMAGE must be a bounded [frames, height, width, RGB/RGBA] tensor')
    values = timestamps(timestamps_json, images.shape[0], fps)
    source_duration = values[-1] + 1 / fps
    stop = source_duration if end_s is None else min(end_s, source_duration)
    if stop <= start_s:
        raise core.StrataError('Video time range contains no frames')
    candidates = [i for i, t in enumerate(values) if start_s <= t < stop]
    if not candidates:
        raise core.StrataError('Video time range contains no frames')
    # Only selected tensor slices reach CPU; never .cpu() the entire video batch.
    selected = []
    for target in shared().sampling_schedule(start_s, stop, sampling_fps, max_frames):
        index = bisect.bisect_left(values, target)
        if index < len(values) and values[index] < stop and (not selected or index != selected[-1]):
            selected.append(index)
    if not selected:
        raise core.StrataError('Video time range contains no frames')
    pictures = [pil_frame(images[i], max_pixels) for i in selected]
    result = shared().sample_images(pictures, fps=fps, timestamps=[values[i] for i in selected],
                                   start_s=start_s, end_s=stop, sampling_fps=sampling_fps,
                                   max_frames=max_frames, max_pixels=max_pixels,
                                   max_duration_s=MAX_DURATION, cancel=core.interrupted,
                                   source_duration_s=source_duration, source_indices=selected, preselected=True)
    # Tensor batches cannot recover original decoder PTS unless supplied explicitly.
    result.manifest.setdefault('source', {}).update({'timestamp_basis': 'provided' if timestamps_json.strip() else 'estimated_cfr',
                                                    'input_frame_count': int(images.shape[0]),
                                                    'duration_s': source_duration})
    result.manifest.setdefault('coverage', {}).update({'requested_start_s': start_s, 'requested_end_s': stop})
    for frame, index in zip(result.manifest['frames'], selected):
        frame['input_frame_index'] = index
        frame['timestamp_kind'] = 'provided' if timestamps_json.strip() else 'estimated'
    result.manifest['coverage']['timestamp_kind'] = 'provided' if timestamps_json.strip() else 'estimated'
    return result


def official_type(video):
    cls = type(video)
    module = sys.modules.get(cls.__module__)
    if (cls.__module__ not in ('comfy_api.latest._input_impl.video_types', 'comfy_api.input_impl.video_types')
            or cls.__name__ not in ('VideoFromFile', 'VideoFromComponents', 'VideoFromList')
            or getattr(module, cls.__name__, None) is not cls):
        raise core.StrataError('Unsupported VIDEO implementation; use official Load Video or an IMAGE adapter')
    return cls.__name__


def file_source(video):
    """Read the audited official crop state; all other state uses public getters.

    The abstract VIDEO API has no crop getter. Both recognized layouts are exact:
    old file-only objects, or the current file/trim/crop object. Unknown layouts
    fail before decoding instead of guessing or calling unbounded get_components.
    """
    state = vars(video)
    old = {'_VideoFromFile__file'}
    current = old | {'_VideoFromFile__start_time', '_VideoFromFile__duration', '_VideoFromFile__crop'}
    if set(state) not in (old, current):
        raise core.StrataError('Unsupported official VIDEO state layout; update the adapter or use IMAGE frames')
    crop = state.get('_VideoFromFile__crop')
    if crop is not None and (not isinstance(crop, tuple) or len(crop) != 4
            or any(type(value) is not int for value in crop) or min(crop[:2]) < 0 or min(crop[2:]) < 1):
        raise core.StrataError('Unsupported official VIDEO crop layout')
    source = video.get_stream_source()
    if isinstance(source, (str, Path)):
        if '://' in str(source) or not Path(source).is_file():
            raise core.StrataError('VIDEO source must be a local regular file')
        size = Path(source).stat().st_size
    elif isinstance(source, io.BytesIO):
        size = source.getbuffer().nbytes
    else:
        raise core.StrataError('VIDEO source must be a bounded local file or BytesIO')
    if size > MAX_INPUT_BYTES:
        raise core.StrataError('VIDEO source exceeds 256 MiB; select a smaller source file')
    return state, crop, source


def metadata_guard(source):
    """Ensure official duration getters take their metadata-only branches.

    Without duration/frame metadata, their documented fallback counts an entire
    decoded stream. Reject that branch before a getter can open or decode it.
    """
    import av
    core.interrupted()
    try:
        with av.open(source, mode='r', timeout=(10, 10),
                     options={'protocol_whitelist': 'file,pipe',
                              'format_whitelist': 'mov,matroska,webm,avi,flv,mpeg,mpegts,ogg,gif'}) as container:
            stream = next(iter(container.streams.video), None)
            if stream is None:
                raise core.StrataError('VIDEO source has no video stream')
            if container.duration is not None:
                number(float(container.duration / av.time_base), 'source duration', high=86400, positive=True)
            elif stream.frames and stream.average_rate:
                number(float(stream.frames / stream.average_rate), 'source duration', high=86400, positive=True)
            else:
                raise core.StrataError('VIDEO duration metadata is missing; export a bounded video file or use IMAGE with timestamps')
    except (av.FFmpegError, OSError) as error:
        raise core.StrataError(f'VIDEO metadata could not be read: {error}') from None
    core.interrupted()


def file_adapter(video, *, start_s, end_s, sampling_fps, max_frames, max_pixels, cancel):
    state, crop, source = file_source(video)
    metadata_guard(source)
    duration = number(video.get_duration(), 'duration', high=86400, positive=True)
    stop = duration if end_s is None else min(duration, end_s)
    if stop <= start_s or stop - start_s > MAX_DURATION:
        raise core.StrataError('Select a nonempty VIDEO window of at most 3600 seconds')
    original_start = number(video.get_active_trim_window()[0], 'source trim', high=86400) if len(state) == 4 else 0
    result = shared().sample_video(source, start_s=original_start + start_s,
                                   end_s=original_start + stop, sampling_fps=sampling_fps,
                                   max_frames=max_frames, max_pixels=max_pixels, max_duration_s=MAX_DURATION,
                                   max_input_bytes=MAX_INPUT_BYTES, cancel=cancel, crop=crop)
    result.manifest['source'].update({'adapter': 'official_file_state_v1', 'input_time_origin_s': start_s,
                                     'input_duration_s': duration, 'source_pts_preserved': True,
                                     'original_media_time_origin_s': original_start + start_s,
                                     'crop_after_rotation': list(crop) if crop is not None else None})
    result.manifest['coverage'].update({'input_window_start_s': start_s, 'input_window_end_s': stop})
    for frame in result.manifest['frames']:
        frame['input_timestamp_s'] = start_s + frame['timestamp_s']
        frame['original_media_timestamp_s'] = original_start + start_s + frame['timestamp_s']
    return result


def list_adapter(video, *, start_s, end_s, sampling_fps, max_frames, max_pixels):
    state = vars(video)
    if (set(state) != {'videos', 'complete_audio', '_VideoFromList__buffer'} or not isinstance(video.videos, list)
            or not 1 <= len(video.videos) <= 64):
        raise core.StrataError('Unsupported accumulated VIDEO layout')
    for item in video.videos:
        if official_type(item) != 'VideoFromFile':
            raise core.StrataError('Accumulated VIDEO must contain bounded official file videos')
        _, _, source = file_source(item)  # Check bounds before official metadata getters open anything.
        metadata_guard(source)
    durations = [number(item.get_duration(), 'component duration', high=86400, positive=True) for item in video.videos]
    duration = sum(durations)
    stop = duration if end_s is None else min(end_s, duration)
    if stop <= start_s or stop - start_s > MAX_DURATION:
        raise core.StrataError('Select an accumulated VIDEO window of at most 3600 seconds')
    targets = shared().sampling_schedule(start_s, stop, sampling_fps, max_frames)
    step = targets[1] - targets[0] if len(targets) > 1 else max(1 / sampling_fps, stop - start_s)
    pictures, times, details = [], [], []
    offset, deadline = 0., time.monotonic() + 180
    def check():
        core.interrupted()
        if time.monotonic() >= deadline:
            raise core.StrataError('Accumulated VIDEO sampling exceeded its CPU time limit')
    for component_index, (component, component_duration) in enumerate(zip(video.videos, durations)):
        check()
        component_targets = [target - offset for target in targets if offset <= target < offset + component_duration]
        if component_targets:
            local_start = component_targets[0]
            local_end = min(component_duration, stop - offset, component_targets[-1] + step)
            sampled = file_adapter(component, start_s=local_start, end_s=local_end, sampling_fps=1 / step,
                                   max_frames=len(component_targets), max_pixels=max_pixels, cancel=check)
            for frame in sampled.frames:
                pictures.append(frame.image)
                times.append(offset + local_start + frame.timestamp_s)
                details.append({'component_index': component_index,
                                'component_source_timestamp_s': frame.source_timestamp_s})
        offset += component_duration
    result = shared().sample_images(pictures, fps=sampling_fps, timestamps=times, source_duration_s=duration,
                                    preselected=True, start_s=start_s, end_s=stop, sampling_fps=sampling_fps,
                                    max_frames=max_frames, max_pixels=max_pixels, cancel=check)
    result.manifest['source'].update({'kind': 'video_list', 'timestamp_basis': 'composite_media_clock'})
    result.manifest['coverage']['timestamp_kind'] = 'composite'
    for frame, detail in zip(result.manifest['frames'], details):
        frame.update(detail, timestamp_kind='composite')
    return result


def sample_input(video=None, images=None, *, fps=24, timestamps_json='', start_s=0, end_s=None,
                 sampling_fps=1, max_frames=64, max_pixels=589824):
    fps = number(fps, 'fps', high=240, positive=True)
    sampling_fps = number(sampling_fps, 'sampling_fps', high=30, positive=True)
    if not isinstance(timestamps_json, str):
        raise core.StrataError('Video timestamps must be JSON text')
    core.text_unicode(timestamps_json)
    start_s = number(start_s, 'start_s', high=86400)
    if end_s is not None:
        end_s = number(end_s, 'end_s', high=86400, positive=True)
        if end_s <= start_s:
            raise core.StrataError('Video end must be later than start')
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_FRAMES:
        raise core.StrataError('Video max_frames must be between 1 and 64')
    if type(max_pixels) is not int or not 65536 <= max_pixels <= 1048576:
        raise core.StrataError('Video max_pixels must be between 65536 and 1048576')
    if (video is None) == (images is None):
        raise core.StrataError('Connect exactly one VIDEO or IMAGE input')
    if images is not None:
        return sample_tensor(images, fps=fps, timestamps_json=timestamps_json, start_s=start_s,
                             end_s=end_s, sampling_fps=sampling_fps, max_frames=max_frames, max_pixels=max_pixels)
    if timestamps_json.strip():
        raise core.StrataError('Explicit timestamps apply to IMAGE input only')
    name = official_type(video)
    if name == 'VideoFromComponents':
        state = set(vars(video))
        if state not in ({'_VideoFromComponents__components'},
                         {'_VideoFromComponents__components', '_VideoFromComponents__bit_depth', '_VideoFromComponents__color_space'}):
            raise core.StrataError('Unsupported official VIDEO components state layout')
        components = video.get_components()  # Already existing tensors; performs no full-video decode.
        component_fps = number(float(components.frame_rate), 'component fps', high=240, positive=True)
        return sample_tensor(components.images, fps=component_fps, start_s=start_s,
                             end_s=end_s, sampling_fps=sampling_fps, max_frames=max_frames, max_pixels=max_pixels)
    if name == 'VideoFromList':
        return list_adapter(video, start_s=start_s, end_s=end_s, sampling_fps=sampling_fps,
                            max_frames=max_frames, max_pixels=max_pixels)
    return file_adapter(video, start_s=start_s, end_s=end_s, sampling_fps=sampling_fps,
                        max_frames=max_frames, max_pixels=max_pixels, cancel=core.interrupted)


def tensor_result(sampled):
    import numpy as np
    import torch
    if not sampled.frames:
        raise core.StrataError('Video produced no decodable frames')
    size = sampled.frames[0].image.size
    pictures = [frame.image.resize(size) if frame.image.size != size else frame.image for frame in sampled.frames]
    # Samples were capped and downscaled before this CPU-only stack.
    tensors = [torch.from_numpy(np.asarray(image, dtype=np.float32).copy() / 255) for image in pictures]
    core.interrupted()
    return torch.stack(tensors), json.dumps(sampled.manifest, ensure_ascii=False, allow_nan=False)


def native_capability(status):
    capabilities = status.get('capabilities')
    capability = capabilities.get('video') if isinstance(capabilities, dict) else None
    if (not isinstance(capability, dict) or type(capability.get('contract_version')) is not int or capability['contract_version'] != 1
            or not isinstance(capability.get('modes'), list) or 'native' not in capability['modes']):
        raise core.StrataError('This Strata service does not advertise native video contract 1; select frames mode or upgrade it')


def native_part(encoded, frames, manifest):
    sampling = manifest['sampling']
    source = manifest['source']
    start_s = sampling.get('start_s', 0)
    return {'type': 'strata_video', 'mode': 'native', 'contract_version': 1,
            'frames': [dict(source=source, timestamp_s=frame['timestamp_s'],
                            source_timestamp_s=frame['source_timestamp_s'], index=frame['index'])
                       for source, frame in zip(encoded, frames)],
            'metadata': {'duration_s': sampling['end_s'] - start_s,
                         'sampling_fps': sampling.get('sampling_fps', 1),
                         'time_origin_s': source.get('time_origin_s', 0) + start_s}}
