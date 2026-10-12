"""Explicit opt-in live-service acceptance; never part of unittest discovery."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--comfy-source', type=Path, required=True)
parser.add_argument('--comfy-deps', type=Path)
parser.add_argument('--video', type=Path, required=True)
parser.add_argument('--evidence', type=Path, required=True)
parser.add_argument('--url', default='http://127.0.0.1:8084')
options = parser.parse_args()
sys.path.insert(0, str(options.comfy_source.resolve()))
if options.comfy_deps:
    sys.path.insert(0, str(options.comfy_deps.resolve()))
sys.argv = [sys.argv[0]]
sys.modules['triton'] = None
from comfy_api.latest import InputImpl

cancel = threading.Event()
server = types.ModuleType('server')
mm = types.ModuleType('comfy.model_management')
def interrupted():
    if cancel.is_set():
        raise InterruptedError('Live acceptance cancelled the pending Comfy node')
mm.throw_exception_if_processing_interrupted = interrupted
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strata_video_inference', root / '__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
options.evidence.mkdir(parents=True, exist_ok=True)
with mock.patch.dict(sys.modules, {'server': server, 'comfy.model_management': mm, spec.name: package}):
    spec.loader.exec_module(package)
    core = package.core
    core.HOME = options.evidence / 'private-profiles'
    profile = {'mode': 'external', 'url': options.url, 'allow_lifecycle': True,
               'same_gpu': False, 'timeout_s': 1800, 'cleanup_timeout_s': 120}
    core.save_profile('live-video', profile)
    connection = core.Connection('live-video')
    mapping = {cls.__name__: cls for cls in package.V3_NODES}
    frames, manifest_json = mapping['StrataT8VideoSample'].execute(
        video=InputImpl.VideoFromFile(str(options.video.resolve())), fps=24, timestamps_json='',
        start_s=0, end_s=0, sampling_fps=1, max_frames=8, max_pixels=65536).args
    manifest = core.json_loads(manifest_json)
    assert len(frames) == 8, len(frames)
    (options.evidence / 'manifest.json').write_text(manifest_json, encoding='utf-8')
    original_request = core.Client.request
    trace = []
    def request(client, path, body=None, **kwargs):
        entry = {'path': path, 'started_at': time.time()}
        trace.append(entry)
        try:
            value = original_request(client, path, body, **kwargs)
            entry['finished_at'] = time.time()
            if path == '/v1/chat/completions':
                entry['processes_after'] = original_request(client, '/v1/status', check=None, timeout=5).get('processes')
            return value
        except BaseException as error:
            entry['error'] = type(error).__name__ + ': ' + str(error)
            raise
    client = core.Client(profile)
    def assert_released():
        status = original_request(client, '/v1/status', check=None, timeout=5)
        assert status['loaded'] is False and status['activity']['in_flight'] == 0, status
        assert not any(p['running'] or p['loaded'] or p['starting'] for p in status['processes'].values()), status
        return status
    reports = {}
    try:
        with mock.patch.object(core.Client, 'request', request):
            for mode in ('frames', 'native'):
                print(f'Live {mode}: two chunks and one merge', flush=True)
                beginning = len(trace)
                output = mapping['StrataT8VideoAnalyze'].execute(connection=connection, frames=frames,
                    manifest_json=manifest_json, task='timeline', mode=mode, frames_per_segment=4,
                    question='Describe background color progression and white circle direction. Keep report to one sentence; '
                             'events to one per segment, shots and prompt empty, limitations to one short sentence. '
                             'Use only first-to-last provided timestamps for events.',
                    max_pixels=65536, max_tokens=1536, temperature=0, top_p=.95, top_k=20,
                    seed=0, reasoning_effort='none', refresh=0)
                results = output.args
                parsed = core.json_loads(results[1])
                usage = core.json_loads(results[4])
                assert len(parsed['segments']) == 2 and usage['requests'] == 3, usage
                assert len(core.json_loads(results[2])) == 8
                assert core.json_loads(results[3])['continuous_observation'] is False
                processes = [t['processes_after'] for t in trace[beginning:] if 'processes_after' in t]
                assert len(processes) == 3
                assert all(p == processes[0] for p in processes), processes
                reports[mode] = {'analysis': parsed, 'usage': usage, 'same_processes_across_stages': True,
                                 'released_status': assert_released()}
                (options.evidence / f'{mode}-node-result.json').write_text(json.dumps(reports[mode], indent=2, ensure_ascii=False), encoding='utf-8')
                print(f'Live {mode}: passed; three stages and final release', flush=True)
            print('Live original Text and Vision nodes', flush=True)
            text = package.nodes.StrataText().run(connection, 'Reply with only STRATA_OK', max_tokens=64,
                                                 temperature=0, reasoning_effort='none')[0]
            assert 'STRATA_OK' in text
            assert_released()
            image = package.nodes.StrataImage().run(connection, frames[:1], task='question',
                question='What is the background color? Reply in a short sentence.', max_pixels=65536,
                max_tokens=128, temperature=0, reasoning_effort='none')[0]
            assert 'red' in image.lower(), image
            assert_released()
            reports['original_nodes'] = {'text': text, 'image': image, 'released': True}
            print('Live cancellation while HTTP inference is pending', flush=True)
            timer = threading.Timer(1, cancel.set)
            timer.start()
            began = time.monotonic()
            try:
                package.nodes.StrataVideoAnalyze().run(connection, frames, manifest_json, mode='native',
                    frames_per_segment=4, max_tokens=4096, temperature=0, reasoning_effort='none')
            except InterruptedError:
                reports['cancellation'] = {'raised': 'InterruptedError', 'elapsed_s': time.monotonic() - began,
                                           'released_status': assert_released()}
            else:
                raise AssertionError('Pending video inference did not observe cancellation')
            finally:
                timer.cancel()
                cancel.clear()
        (options.evidence / 'acceptance.json').write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'passed': list(reports), 'actual_v3_execution': True,
                          'same_gpu_handoff_retested': False, 'service_wrapper_stopped': False}), flush=True)
    finally:
        cancel.clear()
        (options.evidence / 'http-trace.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
        # A failed assertion still releases the explicitly authorized idle local service.
        core.cleanup(client, profile, None, None)
