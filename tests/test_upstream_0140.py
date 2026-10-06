"""Protocol 1 compatibility when Strata-T8 adopts upstream 0.1.40.1.

These exercise real loopback HTTP and the public node entry points, without
downloading a model or constructing a native GPU encoder.
"""
import json
from pathlib import Path
import unittest
from unittest import mock

import test_nodes as fixture

core, nodes = fixture.core, fixture.nodes


class ManagedUpstreamCompatibility(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def manager(self):
        return fixture.OwnedProcesses.prepared_manager(self)

    def write_metadata(self, manager, version):
        (manager.root/'meta.json').write_text(json.dumps({
            'version': version, 'protocol_version': 1,
            'upstream_release_tag': 'v0.1.40.1', 'engine_version': '0.1.40',
        }), encoding='utf-8')

    def test_hotfix_source_tag_does_not_require_a_four_part_portable_version(self):
        manager = self.manager()
        self.write_metadata(manager, '0.1.40-t8.1')
        self.assertEqual(manager.runtime_version(), '0.1.40-t8.1')
        # A source tag is metadata, not a replacement for the portable contract.
        self.write_metadata(manager, '0.1.40.1-t8.1')
        with self.assertRaisesRegex(core.StrataError, 'metadata'):
            manager.runtime_version()

    def test_upgrade_replaces_its_old_owned_instance_before_native_preparation(self):
        import hashlib
        manager = self.manager()
        old_version = '0.1.39-t8.14'
        self.write_metadata(manager, old_version)
        startup = {key: manager.profile.get(key) for key in
                   ('runtime', 'data_dir', 'port', 'context', 'vision', 'api_key')}
        startup['runtime_version'] = old_version
        old_fingerprint = hashlib.sha256(json.dumps(startup, sort_keys=True).encode()).hexdigest()
        manager.state_path.write_text(json.dumps({'fingerprint': old_fingerprint}), encoding='utf-8')
        self.write_metadata(manager, '0.1.40-t8.1')
        order = []
        def stop():
            order.append('stop-owned-old-version')
            return True
        def spawn(*args, **kwargs):
            order.append('prepare-new-version')
            raise OSError('test stops before spawning a process')
        with mock.patch.object(manager, 'owned', return_value=mock.Mock()), \
             mock.patch.object(manager, 'stop', side_effect=stop), \
             mock.patch.object(core.socket, 'socket'), \
             mock.patch.object(core.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaises(OSError):
                manager.ensure(mock.Mock(), check=lambda: None,
                               prepare_check=lambda: order.append('gpu-handoff'))
        self.assertEqual(order, ['stop-owned-old-version', 'gpu-handoff', 'prepare-new-version'])


@unittest.skipUnless(fixture.SOURCE_ROOT, 'Set STRATA_SOURCE_DIR for Strata HTTP compatibility tests')
class UpstreamHTTPCompatibility(unittest.TestCase):
    setUp = fixture.BatchHTTP.setUp

    def replies(self, *scripts):
        self.engine.reply = self.engine.reply.__class__(self.svc.tok, list(scripts), max_context=4096)

    def assert_released(self):
        status = core.control(core.Connection('test'), 'status', check=lambda: None)
        self.assertEqual(status['protocol_version'], 1)
        self.assertFalse(status['loaded'])
        self.assertFalse(status['activity']['in_flight'])
        self.assertTrue(all(not any(process[key] for key in ('running', 'loaded', 'starting'))
                            for process in status['processes'].values()))

    def test_unadapted_upstream_status_is_rejected_before_inference_or_unload(self):
        status = self.svc.v1_status()
        for key in ('protocol_version', 'package_version', 'instance_id', 'processes'):
            status.pop(key, None)
        with mock.patch.object(self.svc, 'v1_status', return_value=status), \
             mock.patch.object(self.svc, 'unload', wraps=self.svc.unload) as unload:
            with self.assertRaisesRegex(core.StrataError, 'protocol version 1 status'):
                nodes.StrataText().run(core.Connection('test'), 'question',
                                      max_tokens=64, reasoning_effort='none')
        self.assertEqual(self.engine.starts, 0)
        self.assertEqual(self.svc.totals['requests'], 0)
        unload.assert_not_called()

    def test_text_node_preserves_stop_option_through_real_server_and_releases(self):
        self.replies('before STOP after')
        answer = nodes.StrataText().run(core.Connection('test'), 'question', stop=['STOP'],
                                       max_tokens=64, reasoning_effort='none')
        self.assertEqual(answer[0], 'before ')
        self.assert_released()
        self.assertEqual(self.engine.starts, 1)

    def test_actual_structured_server_failure_authorizes_only_one_bounded_repair(self):
        self.replies('{"answer":7}', '{"answer":"fixed"}')
        schema = {'type': 'object', 'required': ['answer'],
                  'properties': {'answer': {'type': 'string'}}, 'additionalProperties': False}
        # A real 502 from Strata, rather than a locally synthesized exception,
        # must preserve the typed repair authorization and cleanup each attempt.
        answer = nodes.StrataStructured().run(core.Connection('test'), 'question',
                    schema=json.dumps(schema), repair_attempts=1,
                    max_tokens=128, reasoning_effort='none')
        self.assertEqual(json.loads(answer[0]), {'answer': 'fixed'})
        self.assertEqual(self.engine.reply.turns, 2)
        self.assertEqual(self.engine.starts, 2)
        self.assertEqual(self.engine.closes, 2)
        self.assert_released()

    def test_batch_keeps_order_and_new_stop_behavior_in_one_load_transaction(self):
        self.replies('first STOP unwanted', 'second STOP unwanted')
        result, paired, custom = nodes.StrataBatch().run(core.Connection('test'), ['one', 'two'],
                    stop='STOP', max_tokens=64, reasoning_effort='none')
        self.assertEqual(result, ['first ', 'second '])
        self.assertEqual(custom, result)
        self.assertEqual([item['input'] for item in json.loads(paired)], ['one', 'two'])
        self.assertEqual(self.engine.starts, 1)
        self.assertEqual(self.engine.closes, 1)
        self.assert_released()

    def test_image_batch_preserves_lazy_vision_and_releases_both_resources(self):
        import numpy as np
        directory = Path(self.temp.name)/'vision'
        directory.mkdir()
        class VisionStub:
            starting = False
            def __init__(self):
                self.running = False
                self.calls = self.starts = self.closes = 0
                self.dir = directory
            def alive(self): return self.running
            def restart(self, cancel=None):
                self.running = True
                self.starts += 1
            def unload(self):
                self.running = False
                self.closes += 1
            def encode(self, source, cancel=None):
                self.calls += 1
                filename = self.dir/f'{self.calls}.emb'
                filename.write_bytes(b'\0'*8)
                return filename, 1
        vision = VisionStub()
        self.svc.vision = vision
        self.replies('red square', 'blue square')
        self.assertFalse(self.svc.v1_status()['vision']['loaded'])
        images = fixture.Images.Tensor(np.zeros((2, 2, 2, 3), dtype=np.float32))
        outputs = nodes.StrataImageBatch().run(core.Connection('test'), images,
                    question='Describe the colors.', max_tokens=64, reasoning_effort='none')
        self.assertEqual(outputs[0], ['red square', 'blue square'])
        self.assertEqual(vision.calls, 2)
        self.assertEqual(vision.starts, 1)
        self.assertEqual(vision.closes, 1)
        self.assertEqual(self.engine.starts, 1)
        self.assert_released()
        self.assertFalse(self.svc.v1_status()['vision']['loaded'])
        # Vision intentionally caches each image on disk across unloads. Only
        # the request's combined embeddings must be discarded after inference.
        self.assertFalse(list(directory.glob('req-*.sve')))


if __name__ == '__main__':
    unittest.main()
