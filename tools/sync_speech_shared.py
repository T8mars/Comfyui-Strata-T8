"""Copy the pure transcript contract/audio-clock adapter from a reviewed Strata checkout."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def sync(source,check=False):
    source=Path(source)
    mappings={'speech_contract.py':('serve/asr/contract.py',()),
              'speech_media.py':('serve/asr/media.py',(('from .contract import number','from .speech_contract import number'),
                                                      ('from tools.strata_video import video_duration','from .video_shared import video_duration')))}
    files={}
    for destination,(relative,replacements) in mappings.items():
        raw=(source/relative).read_bytes()
        text=raw.decode('utf-8')
        for old,new in replacements:text=text.replace(old,new)
        adapted=text.encode('utf-8')
        files[destination]={'source_path':relative,'source_sha256':hashlib.sha256(raw).hexdigest(),'sha256':hashlib.sha256(adapted).hexdigest()}
        if check:
            if (ROOT/destination).read_bytes()!=adapted:raise ValueError('Speech shared source differs: '+destination)
        else:(ROOT/destination).write_bytes(adapted)
    if not check:
        commit=subprocess.check_output(['git','-c','index.threads=1','rev-parse','HEAD'],cwd=source,text=True).strip()
        changed=bool(subprocess.check_output(['git','-c','index.threads=1','status','--porcelain','--','serve/asr/contract.py','serve/asr/media.py'],cwd=source,text=True).strip())
        (ROOT/'speech-shared-source.json').write_text(json.dumps({'repository':'T8mars/Strata-T8','commit':commit,
            'source_working_tree_modified':changed,'files':files},indent=2)+'\n',encoding='utf-8')
    return files


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--strata-dir',type=Path,required=True);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();sync(args.strata_dir,args.check)
