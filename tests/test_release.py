"""Release metadata and packaging cannot leak local files."""
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('strata_node_release', ROOT/'tools/build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_tracked_binary_or_onnx_weights_cannot_enter_public_web_or_examples(self):
        for name in ('web/experts.bin', 'examples/accidental.onnx', 'examples/accidental.pth'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for shipping in builder.SHIPPING:
                    (root/shipping).write_text('public shipping file')
                (root/'pyproject.toml').write_text((ROOT/'pyproject.toml').read_text())
                (root/'meta.json').write_text((ROOT/'meta.json').read_text())
                (root/'version.json').write_text((ROOT/'version.json').read_text())
                weight = root/name
                weight.parent.mkdir(parents=True, exist_ok=True)
                weight.write_bytes(b'accidental tensor fixture')
                tracked = '\0'.join([*builder.SHIPPING, name])+'\0'
                with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder.subprocess, 'check_output', return_value=tracked.encode()):
                    with self.assertRaisesRegex(ValueError, 'Model'): builder.main()

    def test_metadata_uses_independent_version_and_publisher(self):
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())
        meta = json.loads((ROOT/'meta.json').read_text())
        self.assertEqual(project['project']['version'], meta['version'])
        self.assertEqual(project['project']['name'], 'strata-t8')
        self.assertEqual(project['tool']['comfy']['PublisherId'], 't8star')
        self.assertEqual(project['project']['urls']['Repository'], 'https://github.com/T8mars/Comfyui-Strata-T8')
        self.assertFalse(meta['models_included'])

    def test_zip_excludes_untracked_credentials_and_development_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in builder.SHIPPING:
                (root/name).write_text('public shipping file')
            (root/'pyproject.toml').write_text((ROOT/'pyproject.toml').read_text())
            (root/'meta.json').write_text((ROOT/'meta.json').read_text())
            (root/'version.json').write_text((ROOT/'version.json').read_text())
            (root/'.env').write_text('private-key')
            (root/'profiles').mkdir()
            (root/'profiles/private.json').write_text('private-key')
            tracked = '\0'.join([*builder.SHIPPING, 'profiles/private.json', 'meta.json'])+'\0'
            with mock.patch.object(builder, 'ROOT', root), mock.patch.object(builder.subprocess, 'check_output', return_value=tracked.encode()):
                builder.main()
            with zipfile.ZipFile(next((root/'dist').glob('*.zip'))) as archive:
                self.assertFalse(any('.env' in name or 'profiles/' in name or 'meta.json' in name for name in archive.namelist()))
                self.assertFalse(any(b'private-key' in archive.read(name) for name in archive.namelist()))


if __name__ == '__main__':
    unittest.main()
