"""Only expired owned journal files may be removed during unrelated task startup."""
import os
from pathlib import Path
import time
import unittest
from unittest import mock

import test_video_recovery_second026 as fixture

core, progress = fixture.core, fixture.progress


class JournalStartupCleanup(unittest.TestCase):
    setUp = fixture.QueuedJournal.setUp
    bind = fixture.QueuedJournal.bind

    def expired(self):
        journal = progress.Journal('b'*64, {'fixture':True})
        self.bind(journal)
        journal.record({'stage':1}, {'text':'old evidence'})
        journal.commit()
        stamp = time.time()-2*86400
        os.utime(journal.path, (stamp, stamp))
        return journal.path

    def test_unrecognized_old_json_is_not_deleted(self):
        directory = core.HOME/'video-progress'
        directory.mkdir()
        note = directory/'notes.json'
        note.write_text('private user note', encoding='utf-8')
        stamp = time.time()-2*86400
        os.utime(note, (stamp, stamp))
        progress.Journal('a'*64, {'fixture':True})
        self.assertEqual(note.read_text('utf-8'), 'private user note')

    def test_old_directory_with_journal_suffix_does_not_block_new_task(self):
        directory = core.HOME/'video-progress'
        directory.mkdir()
        folder = directory/('c'*64+'.json')
        folder.mkdir()
        stamp = time.time()-2*86400
        os.utime(folder, (stamp, stamp))
        progress.Journal('a'*64, {'fixture':True})
        self.assertTrue(folder.is_dir())

    def test_unreadable_other_journal_does_not_block_new_task(self):
        other = self.expired()
        stat = Path.stat
        def inaccessible(path, *args, **kwargs):
            if path == other:
                raise PermissionError('fixture unavailable other journal')
            return stat(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', new=inaccessible):
            current = progress.Journal('a'*64, {'fixture':True})
        self.assertEqual(current.value['responses'], {})
        self.assertTrue(other.exists())

    def test_locked_expired_journal_is_retained_without_blocking_another_task(self):
        other = self.expired()
        unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == other:
                raise PermissionError('fixture locked old journal')
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', new=locked):
            current = progress.Journal('a'*64, {'fixture':True})
        self.assertEqual(current.value['responses'], {})
        self.assertTrue(other.exists())

    def test_old_owned_journal_still_reclaimed(self):
        other = self.expired()
        progress.Journal('a'*64, {'fixture':True})
        self.assertFalse(other.exists())


if __name__ == '__main__':
    unittest.main()
