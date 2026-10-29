"""Queued controls must not publish passthrough after cancellation during release."""
import threading
import time
import unittest
from unittest import mock

import test_nodes as fixture
import test_audit_round2 as round2

core = fixture.core


class FinalControlCancellation(unittest.TestCase):
    setUp = fixture.OwnedProcesses.setUp

    def profile(self, managed=False, same_gpu=False):
        profile = {'mode':'external','url':'http://127.0.0.1:8082',
                   'allow_lifecycle':True,'same_gpu':same_gpu}
        if managed:
            profile.update(mode='managed',runtime=str(core.HOME/'runtime'),data_dir=str(core.HOME/'model'))
        core.save_profile('test',profile)
        return core.Connection('test')

    def checker(self, cancelled):
        def check():
            if cancelled.is_set():
                raise InterruptedError('cancelled during final control release')
        return check

    def test_load_cancellation_during_final_release_blocks_passthrough_after_release(self):
        connection = self.profile(same_gpu=True)
        cancelled = threading.Event()
        client = mock.Mock()
        loaded = round2.idle_status(loaded=True)
        loaded['processes']['engine'].update(running=True,loaded=True)
        client.request.side_effect = [round2.idle_status(),{},loaded,round2.idle_status()]
        releases = []
        def release(*args):
            releases.append(1)
            if len(releases) == 2:
                cancelled.set()
            return False
        with mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'gpu_handoff',return_value=None), \
                mock.patch.object(core,'cleanup',side_effect=release):
            with self.assertRaisesRegex(InterruptedError,'final control release'):
                core.control(connection,'load',check=self.checker(cancelled))
        self.assertEqual(len(releases),2)
        self.assertEqual([call.args[0] for call in client.request.call_args_list],
                         ['/v1/status','/v1/load','/v1/status','/v1/status'])
        # The last status still confirms release without interruption; the
        # cancellation must not skip resource cleanup or retain either lease.
        self.assertIsNone(client.request.call_args_list[-1].kwargs['check'])
        deadline=time.monotonic()+1
        def lock_check():
            if time.monotonic()>=deadline:
                raise AssertionError('cancelled control retained its lease')
        with core.profile_lock('test',check=lock_check), core.service_lock(core.read_profile('test'),check=lock_check):
            pass

    def test_managed_stop_and_unload_fallback_observe_cancel_after_finishing(self):
        connection=self.profile(managed=True)
        for action in ('stop','unload'):
            cancelled=threading.Event()
            manager=mock.Mock()
            def stopped(*args):
                cancelled.set()
                return True
            with self.subTest(action=action), mock.patch.object(core,'Managed',return_value=manager), \
                    mock.patch.object(core,'Client') as client, \
                    mock.patch.object(core,'cleanup',side_effect=stopped) as release:
                manager.stop.side_effect=stopped
                with self.assertRaisesRegex(InterruptedError,'final control release'):
                    core.control(connection,action,check=self.checker(cancelled))
                if action=='stop':
                    manager.stop.assert_called_once();release.assert_not_called()
                else:
                    release.assert_called_once()
                client.return_value.request.assert_not_called()

    def test_final_cleanup_failure_keeps_priority_over_cancel(self):
        connection=self.profile(same_gpu=True)
        cancelled=threading.Event()
        client=mock.Mock()
        client.request.side_effect=[round2.idle_status(),{},round2.idle_status(loaded=True)]
        releases=[]
        def release(*args):
            releases.append(1)
            if len(releases)==2:
                cancelled.set()
                raise core.StrataError('resource release unconfirmed')
            return False
        with mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'gpu_handoff',return_value=None), \
                mock.patch.object(core,'cleanup',side_effect=release):
            with self.assertRaisesRegex(core.StrataError,'release unconfirmed'):
                core.control(connection,'load',check=self.checker(cancelled))

    def test_original_load_failure_is_not_replaced_by_cleanup_cancellation(self):
        connection=self.profile(same_gpu=True)
        cancelled=threading.Event()
        client=mock.Mock()
        client.request.side_effect=[round2.idle_status(),core.StrataError('original load failure')]
        releases=[]
        def release(*args):
            releases.append(1)
            if len(releases)==2:
                cancelled.set()
            return False
        with mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'gpu_handoff',return_value=None), \
                mock.patch.object(core,'cleanup',side_effect=release):
            with self.assertRaisesRegex(core.StrataError,'original load failure'):
                core.control(connection,'load',check=self.checker(cancelled))
        self.assertEqual(len(releases),2)

    def test_explicit_status_callback_ignores_unrelated_queue_cancel(self):
        connection=self.profile()
        client=mock.Mock();client.request.return_value=round2.idle_status()
        with mock.patch.object(core,'Client',return_value=client), \
                mock.patch.object(core,'interrupted',side_effect=InterruptedError('unrelated queue')):
            self.assertEqual(core.control(connection,'status',check=lambda:None)['service'],'strata')


if __name__=='__main__':
    unittest.main()
