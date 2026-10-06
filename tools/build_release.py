"""Build the independent node ZIP from tracked public shipping paths."""
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SHIPPING = {'__init__.py', 'core.py', 'nodes.py', 'panel.py', 'README.md', 'LICENSE',
            'requirements.txt', 'pyproject.toml', 'version.json', 'profile.example.json'}


def main():
    meta = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
    version = meta['project']['version']
    assert json.loads((ROOT/'meta.json').read_text())['version'] == version
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    names = sorted(name for name in paths if name and (name in SHIPPING or name.startswith(('web/', 'examples/'))))
    if not SHIPPING.issubset(names):
        raise ValueError('Missing tracked shipping files')
    out = ROOT/'dist'
    out.mkdir(exist_ok=True)
    archive = out/f'ComfyUI-Strata-T8-{version}.zip'
    manifest = {'version': version, 'repository': meta['project']['urls']['Repository'], 'models_included': False, 'files': []}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for name in names:
            file = ROOT/name
            if file.is_symlink() or not file.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError(f'Linked shipping file: {name}')
            if file.suffix.lower() in ('.gguf', '.safetensors', '.pt', '.pth', '.ckpt'):
                raise ValueError(f'Model in node package: {name}')
            data = file.read_bytes()
            manifest['files'].append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            package.writestr('Comfyui-Strata-T8/'+name, data)
        package.writestr('Comfyui-Strata-T8/NODE-MANIFEST.json', json.dumps(manifest, indent=2))
        assert package.testzip() is None
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'sha256': digest, 'files': len(names), 'models_included': False}))


if __name__ == '__main__':
    main()
