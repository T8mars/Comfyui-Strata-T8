"""Optional speech adapter. No native imports or runtime actions on the disabled path."""
from __future__ import annotations
import base64
import io
import json
import math
import struct
from . import core, speech_contract as contract

# core.request_payload enforces 64 MiB including base64/JSON. Reject before
# allocating a waveform or constructing a much larger encoded request.
MAX_PCM_BYTES = (64 * 1024**2 - 4096) * 3 // 4 - 44


def pcm_budget(samples, channels=1):
    if samples * channels * 4 > MAX_PCM_BYTES:
        raise core.StrataError('Selected speech exceeds the 64 MiB API upload budget; use a shorter window or lower AUDIO sample rate')


def asr_gpu_handoff():
    """Release Comfy allocations; the API releases its own Strata engines.

    A resident Strata model may still own most RAM/VRAM here. Its main-model
    thresholds cannot authorize the much smaller ASR stage before gpu_scope
    enters the server FIFO and releases that residency atomically.
    """
    import comfy.model_management as mm
    import torch
    device=mm.get_torch_device()
    if device.type!='cuda' or getattr(getattr(torch,'version',None),'hip',None):
        raise core.StrataError('CUDA ASR same-GPU handoff requires NVIDIA CUDA')
    mm.unload_all_models()
    mm.soft_empty_cache()
    return device


def authorize_cuda(profile, status, owned_status=None):
    status=core.service_status(status)
    if not profile.get('allow_lifecycle') or status['activity']['in_flight']:
        raise core.StrataError('CUDA ASR requires an idle service and allow_lifecycle; CPU ASR preserves residency')
    if status['concurrency']['serving']!=1:
        raise core.StrataError('CUDA ASR requires serial Strata serving')
    expected=profile.get('instance_id')
    if profile['mode']=='managed':
        expected=owned_status.get('instance_id') if isinstance(owned_status,dict) else None
        if not isinstance(expected,str) or not expected:
            raise core.StrataError('Managed CUDA ASR ownership cannot be confirmed')
    if expected is not None:
        if not isinstance(expected,str) or not expected or status.get('instance_id')!=expected:
            raise core.StrataError('CUDA ASR port belongs to a different service instance')
    # External profiles without a pin retain their explicit lifecycle authority
    # for the configured URL. Never search for or unload a different service.
    return status


class Cancellation:
    def is_set(self):
        core.interrupted()
        return False


def waveform_window(audio,start,end,offset=0):
    import numpy as np
    start=contract.number(start,'audio window start');end=contract.number(end,'audio window end')
    offset=contract.number(offset,'audio offset')
    if start<0 or end<=start or end-start>3600 or abs(offset)>86400 or end>86400:
        raise core.StrataError('Select a finite AUDIO window of at most 3600 seconds')
    core.interrupted()
    if not isinstance(audio,dict) or type(audio.get('sample_rate')) is not int:
        raise core.StrataError('Connect AUDIO with waveform and integer sample_rate')
    waveform=audio.get('waveform'); rate=audio['sample_rate']
    if not 8000<=rate<=192000 or not hasattr(waveform,'shape') or len(waveform.shape)!=3 or waveform.shape[0]!=1 or not 1<=waveform.shape[1]<=8 or waveform.shape[-1]<1:
        raise core.StrataError('AUDIO requires one batch, 1–8 channels and 8–192 kHz')
    count=math.ceil((end-start)*rate-1e-8)
    pcm_budget(count,waveform.shape[1])
    out=np.zeros((waveform.shape[1],count),dtype=np.float32)
    left=max(0,round((start-offset)*rate));right=min(waveform.shape[-1],round((end-offset)*rate))
    if right>left:
        value=waveform[0,:,left:right]
        if hasattr(value,'detach'):value=value.detach().to('cpu').float().numpy()
        value=np.asarray(value,dtype=np.float32)
        if not np.isfinite(value).all():raise core.StrataError('AUDIO contains NaN or infinity')
        destination=max(0,round((offset+left/rate-start)*rate))
        length=min(value.shape[-1],count-destination)
        out[:,destination:destination+length]=value[:,:length]
    core.interrupted()
    return out,rate


def wav_bytes(array,rate):
    import numpy as np
    if array.ndim!=2 or not 1<=array.shape[0]<=8 or type(rate) is not int or not 8000<=rate<=192000:
        raise core.StrataError('Expected finite AUDIO channels by samples and a valid sample rate')
    pcm_budget(array.shape[1],array.shape[0])
    if not np.isfinite(array).all():raise core.StrataError('AUDIO contains NaN or infinity')
    channels=array.shape[0]
    payload=np.ascontiguousarray(array.T,dtype='<f4').tobytes()
    fmt=struct.pack('<HHIIHH',3,channels,rate,rate*channels*4,channels*4,32)
    return b'RIFF'+struct.pack('<I',36+len(payload))+b'WAVEfmt '+struct.pack('<I',16)+fmt+b'data'+struct.pack('<I',len(payload))+payload


def extract_video_audio(video,start,end,settings):
    from . import video as adapter, speech_media as media
    import numpy as np
    kind=adapter.official_type(video)
    pcm_budget(math.ceil((end-start)*16000-1e-8))
    if kind=='VideoFromFile':
        state,_,source=adapter.file_source(video)
        trim,duration=adapter.file_timing(state,adapter.metadata_guard(source))
        result=media.extract(source,start_s=trim+start,end_s=min(trim+end,trim+duration),
                             audio_track=settings.get('audio_track',0),channel=settings.get('channel','mean'),cancel=Cancellation())
        return result['pcm'],16000,{key:value for key,value in result.items() if key!='pcm'}
    if kind=='VideoFromComponents':
        state=set(vars(video))
        if state not in ({'_VideoFromComponents__components'},
                        {'_VideoFromComponents__components','_VideoFromComponents__bit_depth','_VideoFromComponents__color_space'}):
            raise core.StrataError('Unsupported VIDEO component layout for audio')
        # Existing tensors, not a full-file decode. File/List never call get_components().
        audio=getattr(video.get_components(),'audio',None)
        if audio is None:return None,16000,{'status':'no_track'}
        array,rate=waveform_window(audio,start,end)
        return array,rate,{'status':'ready','clock':'component_audio'}
    if kind=='VideoFromList':
        if (set(vars(video))!={'videos','complete_audio','_VideoFromList__buffer'} or not isinstance(video.videos,list)
                or not 1<=len(video.videos)<=64):
            raise core.StrataError('Unsupported accumulated VIDEO audio layout')
        if video.complete_audio is not None:
            array,rate=waveform_window(video.complete_audio,start,end)
            return array,rate,{'status':'ready','clock':'composite_complete_audio'}
        count=math.ceil((end-start)*16000-1e-8)
        out=np.zeros(count,dtype=np.float32);offset=0;has_track=False;parts=[]
        for item in video.videos:
            core.interrupted()
            if adapter.official_type(item)!='VideoFromFile':raise core.StrataError('Accumulated audio requires official file components')
            state,_,source=adapter.file_source(item)
            duration=adapter.file_timing(state,adapter.metadata_guard(source))[1]
            left,right=max(start,offset),min(end,offset+duration)
            if right>left:
                pcm,rate,meta=extract_video_audio(item,left-offset,right-offset,settings)
                if pcm is not None:
                    has_track=True;dest=round((left-start)*16000);n=min(len(pcm),count-dest);out[dest:dest+n]=pcm[:n]
                parts.append({'component_start_s':offset,**meta})
            offset+=duration
        return (out if has_track else None),16000,{'status':'ready' if has_track else 'no_track','clock':'composite','components':parts}
    raise core.StrataError('Unsupported VIDEO audio source; connect AUDIO or a timed transcript')


def prepare(connection,*,video=None,audio=None,start_s=0,end_s=None,audio_offset_s=0,enable_asr=False,timed_transcript=''):
    if type(enable_asr) is not bool:raise core.StrataError('enable_asr must be boolean')
    if not isinstance(timed_transcript,str):raise core.StrataError('Timed transcript must be JSON/SRT/VTT text')
    if timed_transcript.strip():
        core.text_unicode(timed_transcript)
        if len(timed_transcript.encode('utf-8'))>contract.MAX_BYTES:raise core.StrataError('Transcript exceeds 2 MiB')
        value=timed_transcript.lstrip('\ufeff \t\r\n').strip()
        try:
            if value.startswith('{'):
                data=core.json_loads(value)
                return contract.transcript(data) if 'cues' in data else contract.from_confucius(data)
            return contract.subtitle_text(value)
        except (ValueError,TypeError,KeyError) as error:raise core.StrataError(str(error)) from error
    if not enable_asr:return None
    if audio is None and video is None:raise core.StrataError('Automatic ASR requires VIDEO or AUDIO; IMAGE alone has no audio')
    try:
        start_s=contract.number(start_s,'audio window start');end_s=contract.number(end_s,'audio window end')
    except ValueError as error:raise core.StrataError(str(error)) from error
    if start_s<0 or end_s<=start_s or end_s-start_s>3600 or end_s>86400:raise core.StrataError('Select a bounded speech window within one day')
    pcm_budget(math.ceil((end_s-start_s)*16000-1e-8))
    core.interrupted()
    profile=core.read_profile(connection.profile);client=core.Client(profile)
    manager=core.Managed(connection.profile,profile) if profile['mode']=='managed' else None
    with core.service_lock(profile):
        owned_status=manager.ensure(client,check=core.interrupted) if manager else None
        response=client.request('/v1/asr/settings',check=core.interrupted)
        settings=response['settings']; capability=response['capabilities']
        if audio is not None:
            from . import video as adapter
            offset=adapter.number(audio_offset_s,'audio_offset_s',low=-86400,high=86400)
            array,rate=waveform_window(audio,start_s,end_s,offset)
            clock={'status':'ready','clock':'provided_audio','audio_offset_s':offset}
        else:
            array,rate,clock=extract_video_audio(video,start_s,end_s,settings)
        if array is None:
            return contract.transcript({'contract_version':1,'timeline':'input','status':'no_track','source':'confucius','cues':[],'audio':clock})
        import numpy as np
        if array.ndim==1:array=array[None,:]
        if not np.any(array):
            return contract.transcript({'contract_version':1,'timeline':'input','status':'silence','source':'confucius','cues':[],'audio':clock})
        if not capability.get('supported'):raise core.StrataError('Configure the optional ASR model/runtime in Strata Model Settings: '+', '.join(capability.get('missing',[])))
        if settings.get('device')=='cuda':
            authorize_cuda(profile,client.request('/v1/status',check=core.interrupted),owned_status)
            if profile.get('same_gpu'):asr_gpu_handoff()
        payload=base64.b64encode(wav_bytes(array,rate)).decode('ascii')
        prepared_file = audio is None and clock.get('clock') not in ('component_audio','composite_complete_audio')
        result=contract.transcript(client.request('/v1/audio/transcribe',{'data':payload,'start_s':0,'end_s':(end_s-start_s),
                                                  'audio_track':0,'channel':'mean' if prepared_file else settings.get('channel','mean'),
                                                  'offset_ms':0},
                                                  check=core.interrupted,timeout=max(180,(end_s-start_s)*60)))
        # Both service timelines start at this freshly uploaded WAV's zero.
        # Normalize before adding input origin so selected_window is not added twice.
        if any(cue['end_s']>end_s-start_s+1e-6 for cue in result['cues']):
            raise core.StrataError('ASR returned cues outside the uploaded audio window')
        result['timeline']='input'
        # The uploaded WAV is relative to this selection. Apply manual timing
        # correction on the original input clock, so a negative correction is
        # not prematurely clipped at the temporary WAV's zero timestamp.
        offset_ms=settings.get('offset_ms',0)
        if type(offset_ms) is not int or abs(offset_ms)>86400000:
            raise core.StrataError('Invalid ASR timing correction from service settings')
        origin=start_s+offset_ms/1000
        result['cues']=[{**cue,'start_s':max(0,cue['start_s']+origin),'end_s':cue['end_s']+origin}
                        for cue in result['cues'] if cue['end_s']+origin>0]
        result['audio_mapping']=clock
        return contract.transcript(result)


def srt(value):
    def stamp(seconds):
        milliseconds=round(seconds*1000);hours,remainder=divmod(milliseconds,3600000);minutes,remainder=divmod(remainder,60000);secs,ms=divmod(remainder,1000)
        return f'{hours:02}:{minutes:02}:{secs:02},{ms:03}'
    return '\n\n'.join(f"{i+1}\n{stamp(cue['start_s'])} --> {stamp(cue['end_s'])}\n{cue['text']}" for i,cue in enumerate(contract.transcript(value)['cues']))
