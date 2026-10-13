"""Dense opt-in VIDEO/IMAGE analysis with bounded CPU chunks and a single lease."""
import copy
import base64
import io
import json
import math
from . import core

MAX_SEGMENTS = 256


def run(connection, *, video=None, images=None, fps=24, timestamps_json='', start_s=0,
        duration_s=0, sampling_fps=24, task='overview', question='', mode='frames',
        frames_per_segment=8, max_pixels=589824, transcript='', **options):
    from . import nodes
    # The input name shadows the module; keep the audited adapter explicit.
    from . import video as adapter
    selected = nodes.preset(task, nodes.VIDEO_PRESETS)
    options.setdefault('temperature', 0)
    options.setdefault('reasoning_effort', 'none')
    if mode not in ('frames', 'native') or type(frames_per_segment) is not int or not 2 <= frames_per_segment <= (8 if mode == 'frames' else 16):
        raise core.StrataError('Use 2–8 frames per segment in frames mode, or 2–16 in native mode')
    duration_s = adapter.number(duration_s, 'duration_s', high=3600)
    start_s = adapter.number(start_s, 'start_s', high=86400)
    sampling_fps = adapter.number(sampling_fps, 'sampling_fps', high=30, positive=True)
    if sampling_fps < .01:
        raise core.StrataError('Video sampling_fps must be at least 0.01')
    for text, limit, name in ((question,4096,'question'), (transcript,8192,'transcript')):
        if not isinstance(text, str) or len(text.encode('utf-8','surrogatepass')) > limit:
            raise core.StrataError(f'Video {name} exceeds its text budget')
        core.text_unicode(text)
    nodes.request(question, selected, **options)
    if options.get('max_tokens',2048) > 4096:
        raise core.StrataError('Video output is limited to 4096 tokens per stage')
    first_end = start_s + min(frames_per_segment/sampling_fps, duration_s or math.inf)
    def sample(begin, end):
        return adapter.sample_input(video=video, images=images, fps=fps, timestamps_json=timestamps_json,
            start_s=begin, end_s=end, sampling_fps=sampling_fps,
            max_frames=frames_per_segment, max_pixels=max_pixels)
    first = sample(start_s, first_end)
    source_duration = first.manifest['source'].get('input_duration_s', first.manifest['source'].get('duration_s'))
    source_duration = adapter.number(source_duration, 'source duration', high=86400, positive=True)
    end_s = min(source_duration, start_s + duration_s) if duration_s else source_duration
    if end_s <= start_s or end_s-start_s > 3600:
        raise core.StrataError('Select a nonempty VIDEO window of at most 3600 seconds')
    count = max(1, math.ceil((end_s-start_s)*sampling_fps/frames_per_segment - 1e-9))
    if count > MAX_SEGMENTS:
        raise core.StrataError(f'Video needs {count} segments; select a shorter duration or lower sampling_fps (max {MAX_SEGMENTS} segments)')
    schema = copy.deepcopy(nodes.VIDEO_SCHEMA)
    schema['properties']['events']['items']['properties']['evidence_frames']['items']['maximum'] = MAX_SEGMENTS*16-1
    nodes.check_service_schema(schema)
    evidence, initial, final, times = [], [], [], []
    instruction = (selected + ' Use the supplied evidence IDs and selected-window-relative timestamps. '
        'Record visible action changes and camera changes. Distinguish observation from inference. '
        'These are dense sampled visual chunks; do not claim continuous playback or hearing audio. '
        'Analyze ONLY this attached chunk, not an imagined complete video. Compare the FIRST and LAST frame explicitly. '
        'If a color stays constant in this chunk, say so; the overall question does not imply a color change in every chunk. '
        'For direction compare the object positions in image coordinates from first to last; '
        'do not invent a reversal or return motion without intermediate-frame evidence. '
        'Optional pixel_measurements are measured CPU RGB statistics and normalized foreground-region centers, not object classifications. '
        'Use them to cross-check colors and image-plane displacement when present. A constant center y is not evidence of vertical movement. '
        'Coordinate changes smaller than center_tolerance_xy are sampling/compression uncertainty, not evidence of motion in that axis. '
        'A foreground region is not necessarily one tracked object; do not use its centroid to invent object identity or unseen actions. '
        'The optional transcript is user-supplied text. Return the JSON schema, with gaps and uncertainty in limitations.')
    if task == 'prompt': instruction += ' Return a nonempty English generation prompt in the final answer.'
    merge_instruction = (selected + ' You are now summarizing the COMPLETE selected window from ordered chunk reports. '
        'Use ALL chunk reports in chronological order. A constant color within one chunk does NOT mean the color is constant in the whole video. '
        'Preserve observed differences between chunks and preserve cited IDs and time ranges. '
        'Do not add new visual facts. If reports conflict, state the conflict and uncertainty. '
        'Do not claim continuous playback or verified audio. Return the JSON schema.')
    if task == 'prompt': merge_instruction += ' Return a nonempty English generation prompt.'
    next_segment, scopes, phase = 1, [], 'observe'
    def coverage():
        return {'sample_count':len(times), 'window_start_s':start_s, 'window_end_s':end_s,
            'first_sample_s':times[0] if times else None, 'last_sample_s':times[-1] if times else None,
            'max_sample_gap_s':max((b-a for a,b in zip(times,times[1:])),default=0),
            'target_sampling_fps':sampling_fps, 'estimated_frames':math.ceil((end_s-start_s)*sampling_fps-1e-9),
            'completed_segments':len(initial), 'total_segments':count,
            'analysis_completed':len(initial)==count, 'continuous_observation':False}
    def observation(sampled):
        adapter.shared().validate_manifest(sampled.manifest,len(sampled.frames))
        tensor, _ = adapter.tensor_result(sampled)
        encoded = core.encode_images(tensor,max_images=16,max_pixels=max_pixels)
        chunk=[]
        for frame, image_source in zip(sampled.manifest['frames'], encoded):
            core.interrupted()
            input_time=frame.get('input_timestamp_s',sampled.manifest['sampling']['start_s']+frame['timestamp_s'])
            relative=input_time-start_s
            if relative < -1e-6 or input_time >= end_s+1e-6 or times and input_time <= times[-1]+1e-9:
                raise core.StrataError('Dense video sampling returned invalid or duplicate timestamps')
            item=dict(frame,index=len(evidence),timestamp_s=max(0,relative))
            from PIL import Image
            with Image.open(io.BytesIO(base64.b64decode(image_source.split(',',1)[1]))) as image:
                measurements = adapter.frame_measurements(image, frame.get('tensor_content_box'))
            if measurements: item['pixel_measurements'] = measurements
            evidence.append(item);chunk.append(item);times.append(input_time)
        req=nodes.request(question,instruction,**options)
        content=[{'type':'text','text':json.dumps({'task':task,'question':question,
            'scope':'Observe this chunk only; the whole-video question will be answered later.',
            'frames':[{'index':f['index'],'timestamp_s':f['timestamp_s'],
                **({'pixel_measurements':f['pixel_measurements']} if 'pixel_measurements' in f else {})} for f in chunk],
            'transcript':transcript},ensure_ascii=False)}]
        if mode=='native':
            normalized={'sampling':{'start_s':0,'end_s':end_s-start_s,'sampling_fps':sampling_fps},
                'source':{'time_origin_s':chunk[0]['source_timestamp_s']-chunk[0]['timestamp_s']}}
            content.append(adapter.native_part(encoded,chunk,normalized))
        else:
            for source,frame in zip(encoded,chunk):
                content.extend([{'type':'text','text':f"Frame {frame['index']} at {frame['timestamp_s']:.6f} seconds"},
                    {'type':'image_url','image_url':{'url':source}}])
        req['messages'][-1]['content']=content
        req['response_format']={'type':'json_schema','json_schema':{'name':'video_analysis_full','schema':schema}}
        return req,{f['index']:f for f in chunk}
    first_req, first_scope=observation(first)
    scopes=[first_scope]
    def parsed(answer,scope):
        value=nodes.validated(answer[0],schema)
        for event in value['events']:
            ids=event['evidence_frames']
            if any(i not in scope for i in ids):
                raise nodes.StructuredOutputError('Dense video event cites evidence outside this stage')
            pts=[scope[i]['timestamp_s'] for i in ids]
            if event['start_s']>event['end_s'] or event['start_s']<min(pts)-1e-6 or event['end_s']>max(pts)+1e-6:
                raise nodes.StructuredOutputError('Dense video event time is outside its cited evidence')
        return value
    def sparse_scope(value,scope):
        cited={i for e in value['events'] for i in e['evidence_frames']}
        if not cited and scope: cited={min(scope),max(scope)}
        return {i:scope[i] for i in sorted(cited)}
    all_scopes=[]
    def continuation(answers,status):
        nonlocal next_segment,scopes,phase
        if len(answers)!=len(scopes): raise core.StrataError('Dense video stage count mismatch')
        values=[parsed(answer,scope) for answer,scope in zip(answers,scopes)]
        if phase=='observe':
            initial.extend(values);all_scopes.extend(sparse_scope(v,s) for v,s in zip(values,scopes))
            if next_segment<count:
                begin=start_s+next_segment*frames_per_segment/sampling_fps
                stop=min(end_s,start_s+(next_segment+1)*frames_per_segment/sampling_fps)
                req,scope=observation(sample(begin,stop));next_segment+=1;scopes=[scope]
                return [req]
            values=list(initial);scopes=list(all_scopes);phase='merge'
        if len(values)==1:
            if task=='prompt' and not values[0]['prompt'].strip():
                raise nodes.StructuredOutputError('Video prompt task returned an empty prompt')
            final.append(values[0]);return None
        def text(group,scope):
            timed=[]
            for value in group:
                entry=dict(value)
                relevant=[stage_scope for original,stage_scope in zip(values,scopes) if original is value]
                if relevant and relevant[0]:
                    pts=[f['timestamp_s'] for f in relevant[0].values()]
                    entry['evidence_start_s']=min(pts);entry['evidence_end_s']=max(pts)
                timed.append(entry)
            return json.dumps({'task':task,'question':question,'segments':timed,
                'evidence_frames':[{'index':i,'timestamp_s':frame['timestamp_s']} for i,frame in scope.items()],
                'coverage':coverage()},ensure_ascii=False)
        groups=[];group=[];scope={}
        for value,stage_scope in zip(values,scopes):
            stage_scope=sparse_scope(value,stage_scope)
            candidate=group+[value];candidate_scope={**scope,**stage_scope}
            if group and (len(candidate)>4 or len(text(candidate,candidate_scope).encode())>12000):
                groups.append((group,scope));group=[value];scope=dict(stage_scope)
            else:group=candidate;scope=candidate_scope
        if group:groups.append((group,scope))
        if len(groups)>=len(values):raise core.StrataError('Dense video summaries exceed the merge budget; reduce output length')
        requests=[]
        for group,scope in groups:
            prompt=text(group,scope)
            if len(prompt.encode())>12000:raise core.StrataError('Dense video summary exceeds the merge budget')
            req=nodes.request(prompt,merge_instruction,**options)
            req['response_format']={'type':'json_schema','json_schema':{'name':'video_summary_full','schema':schema}}
            requests.append(req)
        scopes=[scope for _,scope in groups];return requests
    answers=core.generate_video_pipeline(connection,[first_req],continuation,
        capability_check=adapter.native_capability if mode=='native' else None)
    result={'contract_version':1,'mode':mode,'task':task,'analysis':final[0],'segments':initial,
        'source':first.manifest['source'],'coverage':coverage()}
    report=final[0]['prompt'] if task=='prompt' else final[0]['report']
    outputs=(report,json.dumps(result,ensure_ascii=False),json.dumps(evidence,ensure_ascii=False),
        json.dumps(coverage(),ensure_ascii=False),json.dumps({'requests':len(answers),'stages':[core.json_loads(a[2]) for a in answers]},ensure_ascii=False))
    return {'ui':{'text':[report]},'result':outputs}
