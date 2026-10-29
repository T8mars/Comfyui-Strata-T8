"""Generation must not publish an answer cancelled during its final local work."""
import threading
import time
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2

core, nodes = fixture.core, fixture.nodes


class GenerationFinalCancellation(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def profile(self, lifecycle):
        profile = {'mode':'external','url':'http://127.0.0.1:8082','allow_lifecycle':lifecycle}
        core.save_profile('test', profile)
        return core.Connection('test')

    def client(self, usage=None):
        client = mock.Mock()
        client.request.side_effect = [round2.idle_status(),
            {'choices':[{'message':{'content':'answer'}}], 'usage':usage or {}}]
        return client

    def test_cancel_during_cleanup_never_publishes_final_answer(self):
        connection = self.profile(True)
        for kind in ('generate', 'generate_pipeline', 'generate_video_pipeline'):
            cancel = threading.Event()
            def check():
                if cancel.is_set():
                    raise InterruptedError('cancelled during final release')
            with self.subTest(kind=kind), mock.patch.object(core, 'Client', return_value=self.client()), \
                    mock.patch.object(core, 'interrupted', side_effect=check), \
                    mock.patch.object(core, 'cleanup', side_effect=lambda *args: cancel.set()) as cleanup:
                with self.assertRaisesRegex(InterruptedError, 'final release'):
                    arguments = () if kind == 'generate' else (lambda answers,status: None,)
                    getattr(core, kind)(connection, [nodes.request('hello')], *arguments)
            cleanup.assert_called_once()
            deadline = time.monotonic()+1
            def lock_check():
                if time.monotonic() >= deadline:
                    raise AssertionError('final cancellation retained a transaction lease')
            with core.profile_lock('test', check=lock_check), core.service_lock(core.read_profile('test'), check=lock_check):
                pass

    def test_cancel_during_final_usage_conversion_never_publishes_answer(self):
        connection = self.profile(False)
        usage = {'total_tokens':23}
        cancel = threading.Event()
        dumps = core.json.dumps
        def encode(value, *args, **kwargs):
            result = dumps(value, *args, **kwargs)
            if value is usage:
                cancel.set()
            return result
        def check():
            if cancel.is_set():
                raise InterruptedError('cancelled during usage conversion')
        with mock.patch.object(core, 'Client', return_value=self.client(usage)), \
                mock.patch.object(core, 'interrupted', side_effect=check), \
                mock.patch.object(core.json, 'dumps', side_effect=encode), \
                mock.patch.object(core, 'cleanup') as cleanup:
            with self.assertRaisesRegex(InterruptedError, 'usage conversion'):
                core.generate(connection, [nodes.request('hello')])
        cleanup.assert_not_called()

    def test_cleanup_failure_remains_visible_even_when_cancelled(self):
        connection = self.profile(True)
        cancel = threading.Event()
        def check():
            if cancel.is_set():
                raise InterruptedError('cancelled')
        def release(*args):
            cancel.set()
            raise core.StrataError('release unconfirmed')
        with mock.patch.object(core, 'Client', return_value=self.client()), \
                mock.patch.object(core, 'interrupted', side_effect=check), \
                mock.patch.object(core, 'cleanup', side_effect=release):
            with self.assertRaisesRegex(core.StrataError, 'release unconfirmed'):
                core.generate(connection, [nodes.request('hello')])

    def test_original_request_failure_is_not_replaced_by_cleanup_cancellation(self):
        connection = self.profile(True)
        client = self.client()
        client.request.side_effect = [round2.idle_status(), core.StrataError('original request failure')]
        cancel = threading.Event()
        def check():
            if cancel.is_set():
                raise InterruptedError('cancelled')
        with mock.patch.object(core, 'Client', return_value=client), \
                mock.patch.object(core, 'interrupted', side_effect=check), \
                mock.patch.object(core, 'cleanup', side_effect=lambda *args: cancel.set()):
            with self.assertRaisesRegex(core.StrataError, 'original request failure'):
                core.generate(connection, [nodes.request('hello')])


if __name__ == '__main__':
    unittest.main()
