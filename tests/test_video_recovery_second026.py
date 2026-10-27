"""A journal queued behind the profile lease must see newly committed stages."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_nodes as fixture

core = fixture.core
progress = importlib.import_module(core.__package__+'.video_progress')


class QueuedJournal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start(); self.addCleanup(patch.stop)
        self.client = mock.Mock()
        self.client.request.return_value = {'runtime_context':'b'*64}
        self.status = {'model':'fixture', 'package_version':'fixture'}

    def bind(self, journal):
        journal.bind(self.status, self.client, lambda: None)

    def test_queued_reader_reloads_stages_committed_before_its_lease(self):
        first = progress.Journal('a'*64, {'fixture':True})
        queued = progress.Journal('a'*64, {'fixture':True})
        request = {'messages':[{'role':'user','content':'first stage'}]}
        response = {'choices':[{'message':{'content':'evidence'}}]}
        self.bind(first)
        first.record(request, response); first.commit()
        self.bind(queued)
        self.assertEqual(queued.get(request), response)
        queued.record({'stage':2}, {'text':'second'}); queued.commit()
        self.assertEqual(len(progress.Journal('a'*64, {'fixture':True}).value['responses']), 2)

    def test_clear_while_queued_does_not_reuse_deleted_evidence(self):
        first = progress.Journal('a'*64, {'fixture':True})
        self.bind(first); first.record({'stage':1}, {'text':'evidence'}); first.commit()
        queued = progress.Journal('a'*64, {'fixture':True})
        queued.path.unlink()
        self.bind(queued)
        self.assertIsNone(queued.get({'stage':1}))

    def test_config_replaced_while_queued_fails_before_reusing_evidence(self):
        queued = progress.Journal('a'*64, {'fixture':True})
        replacement = progress.Journal('a'*64, {'different':True})
        self.bind(replacement)
        replacement.record({'stage':1}, {'text':'other video'}); replacement.commit()
        with self.assertRaisesRegex(core.StrataError, 'changed'):
            self.bind(queued)


if __name__ == '__main__':
    unittest.main()
