"""Submit the public video workflow to a separately started isolated Comfy queue."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--comfy-url', required=True)
parser.add_argument('--profile', required=True)
parser.add_argument('--video-file', required=True)
parser.add_argument('--evidence', type=Path, required=True)
options = parser.parse_args()
root = Path(__file__).resolve().parents[1]
prompt = json.loads((root / 'examples/video-analysis.api.json').read_text(encoding='utf-8'))
prompt['1']['inputs']['profile'] = options.profile
prompt['2']['inputs']['file'] = options.video_file
prompt['3']['inputs'].update(max_frames=8, max_pixels=65536)
prompt['5'] = prompt.pop('4')
prompt['4'] = {'class_type': 'StrataAcceptanceLoadTinyModel', 'inputs': {
    'question': 'Describe background color progression and white circle direction. Keep report to one sentence; '
                'events to one per segment, shots and prompt empty, limitations to one short sentence. '
                'Use only first-to-last provided timestamps for events.'}}
prompt['5']['inputs'].update(question=['4', 0], mode='native', frames_per_segment=4,
                            max_pixels=65536, max_tokens=1536, temperature=0)
prompt['6'] = {'class_type': 'StrataT8Extract', 'inputs': {
    'json_text': ['5', 1], 'pointer': '/analysis/report', 'item_field': '', 'expected_type': 'string'}}
prompt['7'] = {'class_type': 'StrataAcceptanceSaveResult', 'inputs': {
    'report': ['6', 0], 'analysis': ['5', 1], 'evidence': ['5', 2], 'coverage': ['5', 3], 'usage': ['5', 4]}}
prompt['8'] = {'class_type': 'SaveImage', 'inputs': {'images': ['3', 0], 'filename_prefix': 'strata_video_frames'}}
options.evidence.mkdir(parents=True, exist_ok=True)
(options.evidence / 'queue-prompt.json').write_text(json.dumps(prompt, indent=2, ensure_ascii=False), encoding='utf-8')
def request(path, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(options.comfy_url.rstrip('/') + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)
created = request('/prompt', {'prompt': prompt, 'client_id': 'strata-video-private-acceptance'})
assert not created.get('node_errors'), created
identifier = created['prompt_id']
print('Queued actual Comfy prompt ' + identifier, flush=True)
deadline = time.monotonic() + 1800
while time.monotonic() < deadline:
    histories = request('/history/' + identifier)
    if identifier in histories:
        history = histories[identifier]
        (options.evidence / 'queue-history.json').write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
        assert history['status']['status_str'] == 'success' and history['status']['completed'], history['status']
        assert len(history['outputs']['8']['images']) == 8, history['outputs']
        assert history['outputs']['7']['text'], history['outputs']
        print(json.dumps({'prompt_id': identifier, 'status': 'success', 'output_images': 8,
                          'downstream_extract_and_save': True, 'actual_same_gpu_handoff': True}), flush=True)
        break
    time.sleep(2)
else:
    raise TimeoutError('Actual Comfy queue did not complete within the acceptance deadline')
