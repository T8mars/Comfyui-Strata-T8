"""Private acceptance-only Comfy nodes. Copy into an isolated custom_nodes folder.

These classes are never exported by Strata-T8 or included in its release ZIP.
"""
import json
import os
from pathlib import Path
import urllib.request

STATE = {}


class LoadTinyModel:
    RETURN_TYPES = ('STRING',)
    FUNCTION = 'run'
    CATEGORY = 'Strata acceptance only'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'question': ('STRING', {'multiline': True})}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    def run(self, question):
        import torch
        import comfy.model_management as mm
        from comfy.model_patcher import ModelPatcher

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.linear = torch.nn.Linear(1024, 1024)
            def get_dtype(self):
                return torch.float32

        device = mm.get_torch_device()
        assert device.type == 'cuda', device
        baseline = torch.cuda.memory_allocated(device)
        model = Tiny()
        patcher = ModelPatcher(model, device, torch.device('cpu'))
        mm.load_models_gpu([patcher], force_full_load=True)
        assert model.linear.weight.device.type == 'cuda'
        STATE.update(model=model, patcher=patcher, before={
            'device': str(model.linear.weight.device),
            'allocated_bytes': torch.cuda.memory_allocated(device),
            'baseline_allocated_bytes': baseline,
            'parameter_bytes': sum(p.numel() * p.element_size() for p in model.parameters()),
            'registered_models': len(mm.current_loaded_models)})
        assert STATE['before']['allocated_bytes'] - baseline >= 4 * 1024**2
        return (question,)


class SaveAcceptanceResult:
    RETURN_TYPES = ('STRING',)
    FUNCTION = 'run'
    CATEGORY = 'Strata acceptance only'
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {name: ('STRING', {'forceInput': True})
                             for name in ('report', 'analysis', 'evidence', 'coverage', 'usage')}}

    def run(self, report, analysis, evidence, coverage, usage):
        import torch
        import comfy.model_management as mm
        base = os.environ['STRATA_ACCEPTANCE_SERVICE'].rstrip('/')
        with urllib.request.urlopen(base + '/v1/status', timeout=5) as response:
            status = json.load(response)
        assert not status['loaded'] and not status['activity']['in_flight'], status
        assert not any(p['running'] or p['loaded'] or p['starting'] for p in status['processes'].values()), status
        assert STATE['model'].linear.weight.device.type == 'cpu'
        assert len(mm.current_loaded_models) == 0
        allocated = torch.cuda.memory_allocated(mm.get_torch_device())
        assert allocated <= STATE['before']['baseline_allocated_bytes'] + 1024**2
        analysis, evidence, coverage, usage = map(json.loads, (analysis, evidence, coverage, usage))
        assert len(analysis['segments']) == 2 and usage['requests'] == 3
        assert len(evidence) == 8 and coverage['continuous_observation'] is False
        lowered = report.lower()
        assert all(word in lowered for word in ('red', 'green', 'blue', 'right')), report
        proof = {'report': report, 'analysis': analysis, 'evidence': evidence,
                 'coverage': coverage, 'usage': usage, 'model_before_handoff': STATE['before'],
                 'model_after_handoff': {'device': 'cpu', 'allocated_bytes': allocated,
                                         'registered_models': len(mm.current_loaded_models)},
                 'service_after': status, 'actual_queue_and_same_gpu_handoff': True}
        target = Path(os.environ['STRATA_ACCEPTANCE_DIR']) / 'queue-result.json'
        target.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'ui': {'text': [report]}, 'result': (report,)}


NODE_CLASS_MAPPINGS = {'StrataAcceptanceLoadTinyModel': LoadTinyModel,
                       'StrataAcceptanceSaveResult': SaveAcceptanceResult}
