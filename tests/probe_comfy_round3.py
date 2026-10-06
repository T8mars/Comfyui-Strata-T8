"""Optional third-audit probe: live V3 profile schemas and real CPU-only passthrough/images."""
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

source=Path(sys.argv[1]).resolve()
dependencies=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else None
sys.argv=[sys.argv[0]]
sys.path.insert(0,str(source))
if dependencies:sys.path.insert(0,str(dependencies))
os.environ['CUDA_VISIBLE_DEVICES']='-1'
import torch
from PIL import Image
sys.modules['triton']=None
from comfy_api.latest import io as comfy_io

server=types.ModuleType('server')
mm=types.ModuleType('comfy.model_management')
mm.throw_exception_if_processing_interrupted=lambda:None
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('strata_round3_real_comfy',root/'__init__.py',submodule_search_locations=[str(root)])
package=importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home,mock.patch.dict(sys.modules,{'server':server,'comfy.model_management':mm,spec.name:package}):
    spec.loader.exec_module(package)
    core,nodes=package.nodes.core,package.nodes
    core.HOME=Path(home)
    mapping={cls.__name__:cls for cls in package.V3_NODES}
    connection=mapping['StrataT8Connection']
    def options():
        spec=connection.GET_NODE_INFO_V1()['input']['required']['profile']
        return spec[0] if isinstance(spec[0],list) else spec[1]['options']
    assert options()==['default']
    profile={'mode':'external','url':'http://localhost:8080','api_key':'probe-test-key'}
    core.save_profile('alpha',profile)
    assert options()==['alpha']
    core.save_profile('beta',profile)
    assert options()==['alpha','beta']
    core.profile_path('alpha').unlink()
    assert options()==['beta']
    output=connection.execute(profile='beta')
    assert isinstance(output,comfy_io.NodeOutput) and output.result==(core.Connection('beta'),)
    assert 'probe-test-key' not in repr(output.result)
    tensor=torch.tensor([[[[-1,2,.5]]]],dtype=torch.float32,requires_grad=True)
    with mock.patch.object(core,'control',return_value={'service':'strata','stopped':True,'released':True}):
        output=mapping['StrataT8Control'].execute(connection=core.Connection('beta'),action='unload',text='downstream',images=tensor)
    assert output.result[2]=='downstream' and output.result[3] is tensor
    assert json.loads(output.result[1])['released'] is True
    encoded=core.encode_images(tensor,max_pixels=65536)
    image=Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',',1)[1])))
    assert image.getpixel((0,0))==(0,255,128)
    sizes=[]
    for shape in ((1,1,1048576,3),(1,1048576,1,3)):
        encoded=core.encode_images(torch.zeros(shape),max_pixels=65536)
        image=Image.open(io.BytesIO(base64.b64decode(encoded[0].split(',',1)[1])))
        assert image.width*image.height<=65536
        sizes.append(image.size)
    assert not torch.cuda.is_initialized()
    print(json.dumps({'official_v3_profile_options':'default -> alpha -> alpha,beta -> beta',
                      'control_passthrough_identity':True,'clipped_pixel':[0,255,128],
                      'extreme_aspect_png_sizes':sizes,'cuda_initialized':torch.cuda.is_initialized(),
                      'python':sys.version.split()[0],'torch':torch.__version__}))
