"""Copy only the pure CPU sampling module from an explicitly selected runtime checkout."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime', type=Path)
    options = parser.parse_args()
    source = options.runtime.resolve() / 'tools/strata_video.py'
    data = source.read_bytes().replace(b'\r\n', b'\n')
    compile(data, str(source), 'exec')
    (ROOT / 'video_shared.py').write_bytes(data)
    proof = {'contract_version': 1, 'runtime_repository': 'T8mars/Strata-T8',
             'source_path': 'tools/strata_video.py', 'sha256': hashlib.sha256(data).hexdigest()}
    (ROOT / 'video-shared-source.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
