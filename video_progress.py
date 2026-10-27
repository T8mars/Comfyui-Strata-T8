"""Opt-in, private, bounded response journal; no images or API keys on disk."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import re
from . import core

MAX_TOTAL = 128 * 1024**2


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def source_fingerprint(video,images,audio=None):
    from . import video as adapter
    hasher=hashlib.sha256()
    def tensor(value):
        hasher.update(str(tuple(value.shape)).encode());hasher.update(str(value.dtype).encode())
        for item in value:
            core.interrupted()
            flat=item.detach().reshape(-1)
            for offset in range(0,flat.numel(),65536):
                core.interrupted()
                import torch
                hasher.update(flat[offset:offset+65536].cpu().contiguous().view(torch.uint8).numpy().tobytes())
    def source(item):
        name=adapter.official_type(item);hasher.update(name.encode())
        if name=='VideoFromList':
            for child in item.videos:source(child)
        elif name=='VideoFromComponents':
            components=item.get_components();hasher.update(str(components.frame_rate).encode());tensor(components.images)
            if getattr(components,'alpha',None) is not None:tensor(components.alpha)
        else:
            state,crop,local=adapter.file_source(item)
            hasher.update(fingerprint({k:v for k,v in state.items() if isinstance(v,(str,int,float,bool,type(None)))}).encode())
            hasher.update(str(crop).encode())
            if isinstance(local,(str,Path)):
                with Path(local).open('rb') as stream:
                    for block in iter(lambda:stream.read(4*1024**2),b''):
                        core.interrupted();hasher.update(block)
            else:
                buffer=local.getbuffer()
                try:
                    for offset in range(0,len(buffer),4*1024**2):core.interrupted();hasher.update(buffer[offset:offset+4*1024**2])
                finally:buffer.release()
    if video is not None:source(video)
    else:tensor(images)
    if audio is not None:
        hasher.update(str(audio.get('sample_rate')).encode());tensor(audio['waveform'])
    return hasher.hexdigest()


class Journal:
    def __init__(self,key,config,*,clear=False):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise core.StrataError('Invalid private video progress identity')
        if type(clear) is not bool:raise core.StrataError('Invalid video progress clear setting')
        self.key=key;self.pending={};self.hits=0;self.bound=False
        self.clear_on_bind=clear
        self.directory=core.HOME/'video-progress'
        for path in [self.directory,*self.directory.parents]:
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise core.StrataError('Linked video progress directory')
        self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/(key+'.json')
        now=time.time()
        for path in self.directory.glob('*.json'):
            if path.is_symlink():continue
            if now-path.stat().st_mtime>86400:path.unlink()
        if len(list(self.directory.glob('*.json')))>=128 and not self.path.exists():raise core.StrataError('Clear older saved video progress before starting another task')
        self.value={'contract_version':1,'config':config,'responses':{},'expires':now+86400}
        if not clear:self._reload(now)

    def _reload(self,now=None):
        now=time.time() if now is None else now
        config=self.value['config']
        self.value={'contract_version':1,'config':config,'responses':{},'expires':now+86400}
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size>8*1024**2:raise core.StrataError('Invalid saved video progress')
            saved=core.json_loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(saved,dict) or saved.get('contract_version')!=1
                    or type(saved.get('expires')) not in (int,float)
                    or not isinstance(saved.get('responses'),dict)
                    or len(saved['responses'])>768 or any(not re.fullmatch('[0-9a-f]{64}',k) or not isinstance(v,dict) for k,v in saved['responses'].items())):
                raise core.StrataError('Invalid saved video response journal')
            if saved['expires']<=now:
                # File copies and restores can refresh mtime; the persisted
                # expiration still bounds reuse to the promised 24 hours.
                self.path.unlink()
            else:
                if saved.get('config')!=config:raise core.StrataError('Saved video source or analysis options changed')
                self.value=saved

    def validate_profile(self,profile):
        # Called with the actual profile under its lease, before service startup.
        expected=self.value['config'].get('profile_context')
        if expected is not None and fingerprint({k:v for k,v in profile.items() if k!='api_key'})!=expected:
            raise core.StrataError('Service profile changed while video analysis was queued; run it again')

    def bind(self,status,client,check):
        # Runtime signature excludes uptime and instance ID, so restarting the
        # same software/model can reuse progress; a software/config change cannot.
        # Construction happens before acquiring the profile/service lease. A
        # queued run must re-read stages (or a clear) committed while it waited.
        if not self.bound:
            # Clearing before the lease lets an older running task resurrect
            # its stages. Defer until it has finished and this run owns the lease.
            if self.clear_on_bind:
                if self.path.is_symlink() or getattr(self.path,'is_junction',lambda:False)():
                    raise core.StrataError('Linked video progress path')
                self.path.unlink(missing_ok=True)
                self.clear_on_bind=False
            self._reload()
        cap=client.request('/v1/video/capabilities',check=check,timeout=5)
        signature=cap.get('runtime_context')
        if not isinstance(signature,str) or len(signature)!=64:raise core.StrataError('Saving progress requires a runtime with a video configuration fingerprint')
        context=fingerprint({'runtime':signature,'model':status['model'],'package':status.get('package_version')})
        if 'context' in self.value and self.value['context']!=context:raise core.StrataError('Model or runtime changed; clear saved progress or use a new progress key')
        self.value['context']=context;self.bound=True

    def get(self,request):
        if not self.bound:raise core.StrataError('Video journal has not been bound to the runtime')
        value=self.value['responses'].get(fingerprint(request))
        if value is not None:self.hits+=1
        return value

    def record(self,request,response):
        raw=json.dumps(response,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>128*1024:raise core.StrataError('Video response exceeds the checkpoint budget')
        self.pending[fingerprint(request)]=response

    def commit(self):
        if not self.pending:return
        merged={**self.value['responses'],**self.pending}
        if len(merged)>768:raise core.StrataError('Video checkpoint exceeds the stage limit')
        value={**self.value,'responses':merged,'expires':time.time()+86400}
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        if len(raw)>8*1024**2:raise core.StrataError('Video checkpoint exceeds 8 MiB')
        # The atomic replacement supersedes this journal's previous bytes.
        # Counting both versions rejects valid updates near the total quota.
        if sum(p.stat().st_size for p in self.directory.glob('*.json') if p!=self.path and not p.is_symlink())+len(raw)>MAX_TOTAL:raise core.StrataError('Local video checkpoint storage is full')
        temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self.path)
        finally:temp.unlink(missing_ok=True)
        self.value=value;self.pending.clear()
