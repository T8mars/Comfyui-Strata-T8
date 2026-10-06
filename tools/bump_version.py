"""Update the independent node version before committing a new Registry release."""
import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Semantic version, e.g. 1.0.1')
    version = parser.parse_args().version
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        parser.error('Use a stable X.Y.Z version')
    path = ROOT/'pyproject.toml'
    text = path.read_text(encoding='utf-8')
    old = tomllib.loads(text)['project']['version']
    if tuple(map(int, version.split('.'))) <= tuple(map(int, old.split('.'))):
        parser.error('The new version must be higher; Registry versions are immutable')
    text = text.replace(f'version = "{old}"', f'version = "{version}"', 1)
    path.write_text(text, encoding='utf-8')
    for name in ('meta.json', 'version.json'):
        path = ROOT/name
        data = json.loads(path.read_text(encoding='utf-8'))
        data['version'] = version
        path.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    print(f'Node version {old} -> {version}; update release notes, commit and push main to publish')


if __name__ == '__main__':
    main()
