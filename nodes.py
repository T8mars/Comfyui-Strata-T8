"""Stable node IDs and shared synchronous execution for V1/V3 ComfyUI."""
import json
import hashlib
import math
import re
from pathlib import Path
from . import core

CONNECTION = 'STRATA_CONNECTION'
TEXT_LIST = 'STRATA_TEXT_LIST'
STORY_SCHEMA = {
    'type': 'object', 'required': ['shots'], 'additionalProperties': False,
    'properties': {'shots': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {
        'type': 'object', 'required': ['subject', 'action', 'scene', 'camera', 'duration', 'prompt'],
        'additionalProperties': False, 'properties': {
            **{k: {'type': 'string', 'minLength': 1} for k in ['subject', 'action', 'scene', 'camera', 'prompt']},
            'duration': {'type': 'number', 'minimum': .1, 'maximum': 600}}}}}}
PRESETS = {
    'expand': 'Expand the input into a clear, concrete prompt. Preserve the subject and intent. Output only the prompt.',
    'translate': 'Translate the input accurately to English. Preserve technical terms. Output only the translation.',
    'rewrite': 'Rewrite the input for clarity and specificity, preserving all constraints. Output only the rewrite.',
    'image': 'Write an English image-generation prompt: subject, action, composition, setting, lighting, visual style. Preserve intent. Output only the prompt.',
    'video': 'Write an English video-generation prompt with visible actions, camera movement, temporal progression, and continuity constraints. Output only the prompt.',
    'positive_negative': 'Produce JSON with positive and negative strings for image generation. Positive: desired concrete visible content. Negative: unwanted artifacts. Preserve the input intent.'}
IMAGE_PRESETS = {
    'caption': 'Describe only visible content in this image: subjects, actions, composition, setting, light and colors.',
    'reverse_prompt': 'Write an English image-generation prompt to recreate visible subjects, composition, style, lighting and camera view. Output only the prompt.',
    'ocr': 'Transcribe readable text in reading order. Preserve original language and line breaks. Mark illegible text; do not invent characters.',
    'question': 'Answer the user question using the image. Separate visible evidence from inference.',
    'evaluate': 'Evaluate this image under the user rubric. State visible evidence, uncertainties and concrete improvements. Any score is your subjective judgment.'}


def sampling():
    return {'max_tokens': ('INT', {'default': 1024, 'min': 1, 'max': 131072}),
            'temperature': ('FLOAT', {'default': .7, 'min': 0, 'max': 2, 'step': .05}),
            'top_p': ('FLOAT', {'default': .95, 'min': .01, 'max': 1, 'step': .01}),
            'top_k': ('INT', {'default': 20, 'min': 1, 'max': 64}),
            'seed': ('INT', {'default': 0, 'min': 0, 'max': 2147483647}),
            'reasoning_effort': (['none', 'low', 'medium', 'high', 'xhigh'],),
            'refresh': ('INT', {'default': 0, 'min': 0, 'max': 2147483647})}


def request(prompt, system='', history='[]', **options):
    if not isinstance(prompt, str) or not isinstance(system, str) or not isinstance(history, str):
        raise core.StrataError('Prompt, system and history must be text strings')
    for text in (prompt, system, history):
        core.text_unicode(text)
    messages = core.json_loads(history)
    if not isinstance(messages, list) or len(messages) > 256 or any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant') or not isinstance(m.get('content'), str) for m in messages):
        raise core.StrataError('History must be a JSON array of at most 256 role/content text messages')
    messages = ([{'role': 'system', 'content': system}] if system else []) + messages + [{'role': 'user', 'content': prompt}]
    options.pop('refresh', None)
    bounds = {'max_tokens': (1, 131072, True), 'temperature': (0, 2, False),
              'top_p': (0, 1, False), 'top_k': (1, 64, True), 'seed': (0, 2147483647, True)}
    for key, (low, high, integer) in bounds.items():
        if key not in options:
            continue
        value = options[key]
        if (isinstance(value, bool) or not isinstance(value, int if integer else (int, float))
                or not low <= value <= high or not math.isfinite(value) or (key == 'top_p' and value == 0)):
            raise core.StrataError(f'Invalid sampling value: {key}')
    if options.get('reasoning_effort', 'none') not in ('none', 'low', 'medium', 'high', 'xhigh'):
        raise core.StrataError('Unknown reasoning effort')
    return {'messages': messages, **options}


def check_schema(schema):
    from jsonschema import Draft202012Validator, SchemaError
    from jsonschema.validators import validator_for
    from referencing import Registry
    from referencing.exceptions import Unresolvable, NoSuchResource
    from referencing.jsonschema import specification_with, UnknownDialect
    if not isinstance(schema, (dict, bool)):
        raise core.StrataError('Invalid JSON Schema')
    # No HTTP/file reference retrieval in schemas supplied by a workflow.
    def no_remote(uri):
        raise NoSuchResource(ref=uri)

    def schema_class(node, inherited):
        if not isinstance(node, dict) or '$schema' not in node:
            return inherited
        if not isinstance(node['$schema'], str) or not node['$schema']:
            raise core.StrataError('JSON Schema $schema must be a nonempty string')
        selected = validator_for(node, default=None)
        if selected is None:
            raise core.StrataError('Unsupported JSON Schema dialect')
        return selected

    checked = {}
    def check_tree(node, inherited):
        current_cls = schema_class(node, inherited)
        key = (id(node), current_cls)
        if key in checked:
            return checked[key]
        specification = specification_with(current_cls.META_SCHEMA['$schema'])
        try:
            children = list(specification.subresources_of(node))
        except (TypeError, AttributeError):
            current_cls.check_schema(node)
            raise core.StrataError('Invalid JSON Schema child container') from None
        if isinstance(node, dict) and 'dependencies' in current_cls.VALIDATORS:
            dependencies = node.get('dependencies')
            if isinstance(dependencies, dict):
                # Older referencing drafts choose dependencies from the first value.
                # Property-name lists are data; inspect every schema dependency independently.
                property_dependencies = {id(value) for value in dependencies.values() if isinstance(value, list)}
                children = [child for child in children if id(child) not in property_dependencies]
                children.extend(value for value in dependencies.values() if isinstance(value, (dict, bool)))
        child_ids = {id(child) for child in children if isinstance(child, dict)}
        def mask(value):
            # Parent meta-schemas must not reinterpret an embedded resource's different draft.
            # Preserve containers, booleans and invalid scalar shapes for the parent's checks.
            if isinstance(value, dict):
                if id(value) in child_ids:
                    return {}
                return {key:{} if isinstance(part,dict) and id(part) in child_ids else part
                        for key,part in value.items()}
            if isinstance(value, list):
                return [{} if isinstance(part,dict) and id(part) in child_ids else part for part in value]
            return value
        shallow = {key:mask(value) for key,value in node.items()} if isinstance(node,dict) else node
        current_cls.check_schema(shallow)
        result = current_cls, specification, children
        checked[key] = result
        for child in children:
            check_tree(child, current_cls)
        return result
    try:
        validator_cls, specification, _ = check_tree(schema, Draft202012Validator)
        registry = Registry(retrieve=no_remote)
        root = specification.create_resource(schema)
        resolver = registry.resolver_with_root(root)
        visited = set()
        def local_refs(resource, scoped, inherited):
            value = resource.contents
            current_cls, current_specification, children = check_tree(value, inherited)
            identity = (id(value), current_cls, scoped._base_uri)
            if identity in visited:
                return
            visited.add(identity)
            if isinstance(value, dict):
                active = dict(current_cls._APPLICABLE_VALIDATORS(value))
                for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                    if key not in active or key not in current_cls.VALIDATORS:
                        continue
                    reference = active[key]
                    if not reference.startswith('#'):
                        raise core.StrataError('JSON Schema references must be local fragments')
                    try:
                        resolved = scoped.lookup(reference)
                    except Unresolvable:
                        raise core.StrataError('JSON Schema local reference does not identify an existing schema') from None
                    except (AttributeError, TypeError):
                        raise core.StrataError('JSON Schema local reference with mixed legacy dependencies is unsupported; use a JSON Pointer reference (#/...)') from None
                    try:
                        _, target_specification, _ = check_tree(resolved.contents, current_cls)
                    except SchemaError:
                        raise core.StrataError('JSON Schema local reference must identify a valid schema') from None
                    local_refs(target_specification.create_resource(resolved.contents), resolved.resolver, current_cls)
                if '$ref' in active and len(active) == 1:
                    return  # Older drafts ignore sibling assertions, while the target remains checked.
            for contents in children:
                _, child_specification, _ = check_tree(contents, current_cls)
                child = child_specification.create_resource(contents)
                local_refs(child, scoped.in_subresource(child), current_cls)
        local_refs(root, resolver, validator_cls)
    except SchemaError:
        raise core.StrataError('Invalid JSON Schema') from None
    except RecursionError:
        raise core.StrataError('JSON Schema is too deeply nested') from None
    except UnknownDialect:
        raise core.StrataError('Unsupported JSON Schema dialect') from None
    return validator_cls(schema, registry=registry)


def check_service_schema(schema):
    check_schema(schema)
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise core.StrataError('Strata protocol 1 requires a JSON Schema root with type object')


class StructuredOutputError(core.StrataError):
    pass


def validated(text, schema):
    from jsonschema import ValidationError
    from referencing.exceptions import Unresolvable
    validator = check_schema(schema)
    try:
        value = core.json_loads(text)
        validator.validate(value)
    except (core.StrataError, ValueError, ValidationError, RecursionError, Unresolvable):
        raise StructuredOutputError('Model output is not valid JSON matching the schema') from None
    return value


class Base:
    CATEGORY = 'Strata-T8'
    FUNCTION = 'run'


def preset(task, choices):
    if not isinstance(task, str) or task not in choices:
        raise core.StrataError('Unknown Strata task preset')
    return choices[task]


class StrataConnection(Base):
    RETURN_TYPES, RETURN_NAMES = (CONNECTION,), ('connection',)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'profile': (core.profiles(),)}}

    @classmethod
    def IS_CHANGED(cls, profile):
        path = core.profile_path(profile)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'missing'

    def run(self, profile):
        core.read_profile(profile)
        return (core.Connection(profile),)


class StrataText(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('text', 'reasoning', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'prompt': ('STRING', {'multiline': True}),
                             'system': ('STRING', {'multiline': True, 'default': 'You are a helpful assistant.'}),
                             'history': ('STRING', {'multiline': True, 'default': '[]'}), **sampling()}}

    def run(self, connection, prompt, system='', history='[]', **options):
        return core.generate(connection, [request(prompt, system, history, **options)])[0]


class StrataPrompt(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('positive', 'negative', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'text': ('STRING', {'multiline': True}),
                             'task': (list(PRESETS),), 'template': ('STRING', {'multiline': True, 'default': ''}), **sampling()}}

    def run(self, connection, text, task='image', template='', **options):
        selected = preset(task, PRESETS)
        if not isinstance(template, str):
            raise core.StrataError('Prompt template must be text')
        req = request(text, template.strip() or selected, **options)
        schema = {'type': 'object', 'required': ['positive', 'negative'], 'additionalProperties': False,
                  'properties': {'positive': {'type': 'string'}, 'negative': {'type': 'string'}}}
        if task == 'positive_negative':
            req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'prompts', 'schema': schema}}
        output, _, usage = core.generate(connection, [req])[0]
        if task == 'positive_negative':
            data = validated(output, schema)
            return data['positive'], data['negative'], usage
        return output, '', usage


class StrataStructured(Base):
    RETURN_TYPES = ('STRING', 'STRING', TEXT_LIST, 'STRING')
    RETURN_NAMES = ('json', 'shot_prompts', 'text_list', 'usage_json')
    OUTPUT_IS_LIST = (False, True, False, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'prompt': ('STRING', {'multiline': True}),
                             'schema': ('STRING', {'multiline': True, 'default': json.dumps(STORY_SCHEMA)}),
                             'system': ('STRING', {'multiline': True, 'default': 'Convert the story into concrete sequential shots. Each prompt describes one visible shot. Return the requested JSON.'}),
                             'repair_attempts': ('INT', {'default': 0, 'min': 0, 'max': 2}), **sampling()}}

    def run(self, connection, prompt, schema='', system='', repair_attempts=0, **options):
        if type(repair_attempts) is not int or not 0 <= repair_attempts <= 2:
            raise core.StrataError('Repair attempts must be an integer between 0 and 2')
        if not isinstance(schema, str):
            raise core.StrataError('JSON Schema must be text')
        schema = core.json_loads(schema) if schema.strip() else STORY_SCHEMA
        # Validate schema before contacting/loading a model.
        check_service_schema(schema)
        req = request(prompt, system, **options)
        req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'strata_output', 'schema': schema}}
        for attempt in range(repair_attempts+1):
            try:
                text, _, usage = core.generate(connection, [req])[0]
                value = validated(text, schema)
                break
            except core.StrataError as error:
                if attempt == repair_attempts or (not isinstance(error, StructuredOutputError) and 'structured_output_failed' not in str(error)):
                    raise
                req['messages'][0]['content'] += '\nReturn valid JSON only, matching every required field and its type.'
        prompts = [shot['prompt'] for shot in value.get('shots', [])] if isinstance(value, dict) and isinstance(value.get('shots'), list) and all(isinstance(s, dict) and isinstance(s.get('prompt'), str) for s in value['shots']) else []
        return json.dumps(value, ensure_ascii=False), prompts, prompts, usage


def pointer(value, path):
    if not isinstance(path, str):
        raise core.StrataError('JSON Pointer must be a string')
    if not path:
        return value
    if not path.startswith('/'):
        raise core.StrataError('Use a JSON Pointer, for example /shots/0/prompt')
    for component in path[1:].split('/'):
        if re.search(r'~(?![01])', component):
            raise core.StrataError('JSON Pointer escapes must be ~0 or ~1')
        key = component.replace('~1', '/').replace('~0', '~')
        try:
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', key):
                    raise core.StrataError('JSON Pointer array indices must be non-negative decimal integers without leading zeroes')
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise core.StrataError('JSON Pointer cannot descend into a scalar value')
        except (KeyError, IndexError, ValueError):
            raise core.StrataError('JSON Pointer does not identify an existing field') from None
    return value


def as_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class StrataExtract(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', TEXT_LIST), ('value', 'items', 'text_list')
    OUTPUT_IS_LIST = (False, True, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'json_text': ('STRING', {'multiline': True, 'forceInput': True}),
                             'pointer': ('STRING', {'default': '/shots'}),
                             'item_field': ('STRING', {'default': 'prompt'}),
                             'expected_type': (['any', 'string', 'number', 'array', 'object', 'boolean'],)}}

    def run(self, json_text, pointer='', item_field='', expected_type='any'):
        value = globals()['pointer'](core.json_loads(json_text), pointer)
        types = {'string': str, 'number': (int, float), 'array': list, 'object': dict, 'boolean': bool}
        if not isinstance(expected_type, str) or (expected_type != 'any' and expected_type not in types):
            raise core.StrataError('Unknown expected JSON type')
        if expected_type != 'any' and (not isinstance(value, types[expected_type]) or (expected_type == 'number' and isinstance(value, bool))):
            raise core.StrataError(f'Extracted value is not {expected_type}')
        items = value if isinstance(value, list) else [value]
        if not isinstance(item_field, str):
            raise core.StrataError('Item field must be a string')
        if item_field:
            if any(not isinstance(item, dict) or item_field not in item for item in items):
                raise core.StrataError('Every list item must contain the requested item field')
            items = [item[item_field] for item in items]
        items = [as_text(item) for item in items]
        return as_text(value), items, items


class StrataNumber(Base):
    RETURN_TYPES, RETURN_NAMES = ('FLOAT',), ('number',)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'json_text': ('STRING', {'forceInput': True}), 'pointer': ('STRING', {'default': '/shots/0/duration'})}}

    def run(self, json_text, pointer):
        value = globals()['pointer'](core.json_loads(json_text), pointer)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise core.StrataError('The selected JSON field must be a number')
        try:
            number = float(value)
        except OverflowError:
            raise core.StrataError('The selected JSON number exceeds the FLOAT range') from None
        if not math.isfinite(number):
            raise core.StrataError('The selected JSON field must be a finite number')
        return (number,)


class StrataImage(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', 'STRING'), ('text', 'reasoning', 'usage_json')

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'images': ('IMAGE',), 'task': (list(IMAGE_PRESETS),),
                             'question': ('STRING', {'multiline': True, 'default': ''}),
                             'schema': ('STRING', {'multiline': True, 'default': ''}),
                             'max_pixels': ('INT', {'default': 1048576, 'min': 65536, 'max': 4194304}), **sampling()}}

    def run(self, connection, images, task='caption', question='', schema='', max_pixels=1048576, **options):
        selected = preset(task, IMAGE_PRESETS)
        req = request(question, selected, **options)
        if not isinstance(schema, str):
            raise core.StrataError('JSON Schema must be text')
        parsed = core.json_loads(schema) if schema.strip() else None
        if schema.strip():
            check_service_schema(parsed)
            req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'image_analysis', 'schema': parsed}}
        encoded = core.encode_images(images, max_pixels=max_pixels)
        req['messages'][-1]['content'] = [{'type': 'text', 'text': question or selected},
                                       *({'type': 'image_url', 'image_url': {'url': image}} for image in encoded)]
        result = core.generate(connection, [req])[0]
        if parsed is not None:
            validated(result[0], parsed)
        return result


class StrataBatch(Base):
    RETURN_TYPES, RETURN_NAMES = ('STRING', 'STRING', TEXT_LIST), ('results', 'paired_json', 'text_list')
    OUTPUT_IS_LIST = (True, False, False)

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'texts': (TEXT_LIST,),
                             'system': ('STRING', {'multiline': True, 'default': 'Rewrite this into an English image-generation prompt. Output only the prompt.'}), **sampling()}}

    def run(self, connection, texts, system='', **options):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 64 or any(not isinstance(t, str) for t in texts):
            raise core.StrataError('Provide a list of 1–64 text strings')
        answers = core.generate(connection, [request(text, system, **options) for text in texts])
        outputs = [a[0] for a in answers]
        paired = [{'index': i, 'input': text, 'output': a[0], 'reasoning': a[1], 'usage': json.loads(a[2])} for i, (text, a) in enumerate(zip(texts, answers))]
        return outputs, json.dumps(paired, ensure_ascii=False), outputs


class StrataImageBatch(Base):
    RETURN_TYPES, RETURN_NAMES = StrataBatch.RETURN_TYPES, StrataBatch.RETURN_NAMES
    OUTPUT_IS_LIST = StrataBatch.OUTPUT_IS_LIST

    @classmethod
    def INPUT_TYPES(cls):
        definition = StrataImage.INPUT_TYPES()
        definition['required'].pop('schema')
        return definition

    def run(self, connection, images, task='caption', question='', max_pixels=1048576, **options):
        selected = preset(task, IMAGE_PRESETS)
        request(question, selected, **options)  # Validate inputs before copying images to CPU.
        encoded = core.encode_images(images, max_pixels=max_pixels)
        requests = []
        for image in encoded:
            req = request('', selected, **options)
            req['messages'][-1]['content'] = [{'type': 'text', 'text': question or selected},
                                           {'type': 'image_url', 'image_url': {'url': image}}]
            requests.append(req)
        answers = core.generate(connection, requests)
        outputs = [a[0] for a in answers]
        return outputs, json.dumps([{'index': i, 'output': a[0], 'reasoning': a[1], 'usage': json.loads(a[2])} for i, a in enumerate(answers)], ensure_ascii=False), outputs


class StrataControl(Base):
    RETURN_TYPES, RETURN_NAMES = (CONNECTION, 'STRING', 'STRING', 'IMAGE'), ('connection', 'status_json', 'text', 'images')
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'connection': (CONNECTION,), 'action': (['status', 'start', 'load', 'unload', 'stop'],)},
                'optional': {'text': ('STRING', {'forceInput': True}), 'images': ('IMAGE',)}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float('nan')

    def run(self, connection, action='status', text='', images=None):
        status = core.control(connection, action)
        return {'ui': {'text': [json.dumps(status, ensure_ascii=False, indent=2)]},
                'result': (connection, json.dumps(status, ensure_ascii=False), text, images)}


NODE_CLASS_MAPPINGS = {
    'StrataT8Connection': StrataConnection, 'StrataT8Text': StrataText, 'StrataT8Prompt': StrataPrompt,
    'StrataT8Structured': StrataStructured, 'StrataT8Extract': StrataExtract, 'StrataT8Number': StrataNumber,
    'StrataT8Image': StrataImage, 'StrataT8Batch': StrataBatch, 'StrataT8ImageBatch': StrataImageBatch,
    'StrataT8Control': StrataControl}
NODE_DISPLAY_NAME_MAPPINGS = {
    'StrataT8Connection': 'Strata 连接 / Connection', 'StrataT8Text': 'Strata 文本生成 / Text',
    'StrataT8Prompt': 'Strata 提示词助手 / Prompt', 'StrataT8Structured': 'Strata 结构化分镜 / JSON',
    'StrataT8Extract': 'Strata JSON 提取 / Extract', 'StrataT8Number': 'Strata JSON 数值 / Number',
    'StrataT8Image': 'Strata 图片分析 / Vision', 'StrataT8Batch': 'Strata 文本批量 / Batch',
    'StrataT8ImageBatch': 'Strata 图片批量 / Image Batch', 'StrataT8Control': 'Strata 服务控制 / Control'}
