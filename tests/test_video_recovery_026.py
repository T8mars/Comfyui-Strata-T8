"""Bounded node checkpoint retention and atomic replacement accounting."""
import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import test_nodes as fixture

core = fixture.core
progress = importlib.import_module(core.__package__+'.video_progress')


class JournalRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, 'HOME', Path(self.temp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def saved(self):
        journal = progress.Journal('a'*64, {'fixture':True})
        journal.record({'prompt':'first'}, {'text':'observation'})
        journal.commit()
        return journal

    def test_restored_mtime_cannot_revive_an_expired_journal(self):
        journal = self.saved()
        value = json.loads(journal.path.read_text(encoding='utf-8'))
        value['expires'] = time.time()-1
        journal.path.write_text(json.dumps(value), encoding='utf-8')
        # The rewrite has a fresh mtime, as a copied/restored file would.
        loaded = progress.Journal('a'*64, {'fixture':True})
        self.assertEqual(loaded.value['responses'], {})
        self.assertFalse(loaded.path.exists())

    def test_unexpired_journal_keeps_valid_responses(self):
        journal = self.saved()
        loaded = progress.Journal('a'*64, {'fixture':True})
        self.assertEqual(loaded.value['responses'], journal.value['responses'])

    def test_malformed_expiry_is_not_accepted(self):
        journal = self.saved()
        for expiry in (None, True, 'tomorrow'):
            value = dict(journal.value, expires=expiry)
            journal.path.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(expiry=expiry), self.assertRaisesRegex(core.StrataError, 'Invalid saved'):
                progress.Journal('a'*64, {'fixture':True})

    def test_replacing_existing_journal_does_not_double_count_its_bytes(self):
        journal = self.saved()
        journal.record({'prompt':'second'}, {'text':'second observation'})
        now = time.time()
        expected = {**journal.value, 'responses':{**journal.value['responses'], **journal.pending}, 'expires':now+86400}
        size = len(json.dumps(expected, ensure_ascii=False, allow_nan=False).encode())
        other = journal.directory/('b'*64+'.json')
        other.write_bytes(b'x'*256)
        with mock.patch.object(progress, 'MAX_TOTAL', size+256), mock.patch.object(progress.time, 'time', return_value=now):
            journal.commit()
        self.assertEqual(len(journal.value['responses']), 2)
        self.assertEqual(journal.path.stat().st_size+other.stat().st_size, size+256)

    def test_replacement_still_rejects_genuinely_full_storage(self):
        journal = self.saved()
        original = journal.path.read_bytes()
        journal.record({'prompt':'second'}, {'text':'second observation'})
        with mock.patch.object(progress, 'MAX_TOTAL', 1), self.assertRaisesRegex(core.StrataError, 'storage is full'):
            journal.commit()
        self.assertEqual(journal.path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
