"""Optional read-only probe against an installed ComfyUI API and real CPU PyTorch.

Run with the ComfyUI Python, passing its source checkout and (optionally) dependency directory.
This probe never starts ComfyUI, an inference server, or a CUDA context.
"""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest import mock

source = Path(sys.argv[1]).resolve()
dependencies = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
sys.argv = [sys.argv[0]]
sys.path.insert(0, str(source))
if dependencies:
    sys.path.insert(0, str(dependencies))
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import torch
from PIL import Image
# Disable optional GPU kernel discovery in this CPU probe. The official io classes stay real.
sys.modules['triton'] = None
from comfy_api.latest import io as comfy_io

server = types.ModuleType('server')  # Importing real server.py could initialize GPU management.
mm = types.ModuleType('comfy.model_management')
mm.throw_exception_if_processing_interrupted = lambda: None
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strata_round2_real_comfy', root/'__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, mock.patch.dict(sys.modules, {'server':server, 'comfy.model_management':mm, spec.name:package}):
    spec.loader.exec_module(package)
    core, nodes = package.nodes.core, package.nodes
    core.HOME = Path(home)
    mapping = {cls.__name__:cls for cls in package.V3_NODES}
    assert set(mapping) == set(nodes.NODE_CLASS_MAPPINGS)
    for node_id, cls in mapping.items():
        info = cls.GET_NODE_INFO_V1()  # Official validation, finalization and V1 conversion.
        legacy = nodes.NODE_CLASS_MAPPINGS[node_id]
        assert tuple(info['output']) == legacy.RETURN_TYPES
        assert tuple(info['output_is_list']) == getattr(legacy, 'OUTPUT_IS_LIST', (False,)*len(legacy.RETURN_TYPES))
        assert info['output_node'] == getattr(legacy, 'OUTPUT_NODE', False)
    result = mapping['StrataT8Extract'].execute(json_text='{"shots":["a","b"]}', pointer='/shots', item_field='', expected_type='array')
    assert isinstance(result, comfy_io.NodeOutput) and result.result[1:] == (['a','b'], ['a','b'])
    with mock.patch.object(core, 'control', return_value={'service':'strata'}):
        result = mapping['StrataT8Control'].execute(connection=core.Connection('test'), action='status')
        assert result.result[0] == core.Connection('test') and result.ui['text']
    assert mapping['StrataT8Control'].fingerprint_inputs() != mapping['StrataT8Control'].fingerprint_inputs()
    for dtype in (torch.float16, torch.bfloat16, torch.float32):
        # Transpose produces a non-contiguous, gradient-bearing IMAGE tensor.
        tensor = torch.zeros((2,3,4,4), dtype=dtype, requires_grad=True).transpose(1,2)
        encoded = core.encode_images(tensor, max_pixels=65536)
        assert len(encoded) == 2
        image = Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',',1)[1])))
        assert image.mode == 'RGB' and image.size == (3,4) and image.getpixel((0,0)) == (255,255,255)
    assert not torch.cuda.is_initialized(), 'Probe must remain CPU-only'
    print(json.dumps({'official_v3_nodes':len(mapping), 'python':sys.version.split()[0], 'torch':torch.__version__,
                      'cpu_tensor_dtypes':['float16','bfloat16','float32'], 'cuda_initialized':torch.cuda.is_initialized()}))
