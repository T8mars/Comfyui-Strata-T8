"""HTTP and owned-service supervision. Importing this module performs no GPU or network work."""
from __future__ import annotations
import base64
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import uuid

HOME = Path(os.environ.get('STRATA_COMFY_HOME') or Path(os.environ.get('LOCALAPPDATA', Path.home()/'.config'))/'Strata-T8-ComfyUI')


class StrataError(RuntimeError):
    pass


def json_loads(text):
    """Reject ambiguous members, non-JSON constants and numbers that overflow float."""
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('Non-finite JSON number')
        return parsed
    def constant(value):
        raise ValueError('Non-finite JSON number')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON member')
            value[key] = item
        return value
    try:
        value = json.loads(text, parse_float=number, parse_constant=constant, object_pairs_hook=pairs)
        # Escaped lone UTF-16 surrogates parse successfully but cannot be sent to ComfyUI as UTF-8.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        return value
    except (TypeError, ValueError, RecursionError):
        raise StrataError('Invalid JSON: use unique member names, valid Unicode and finite numbers') from None


def text_unicode(value):
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise StrataError('Text must contain valid Unicode characters') from None


@dataclass(frozen=True)
class Connection:
    profile: str


def profile_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'[\w-]{1,64}', name):
        raise StrataError('Profile name: 1–64 letters, digits, underscores or hyphens')
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', name, re.IGNORECASE):
        raise StrataError('Profile name must not be a reserved Windows device name')
    return HOME/'profiles'/f'{name}.json'


def profiles():
    names = []
    for path in (HOME/'profiles').glob('*.json'):
        if not path.is_file():
            continue
        try:
            profile_path(path.stem)
        except StrataError:
            continue
        names.append(path.stem)
    return sorted(names) or ['default']


def read_profile(name):
    path = profile_path(name)
    if not path.is_file():
        raise StrataError('Create this local profile in the Strata panel first; workflows contain only its name.')
    try:
        return normalize_profile(json_loads(path.read_text(encoding='utf-8')))
    except (OSError, UnicodeError):
        raise StrataError('Saved profile could not be read') from None


def normalize_profile(profile):
    if not isinstance(profile, dict):
        raise StrataError('Profile must be a JSON object')
    profile = dict(profile)
    if profile.get('mode') not in ('managed', 'external'):
        raise StrataError('Profile mode must be managed or external')
    if profile['mode'] == 'managed':
        for key in ('runtime', 'data_dir'):
            value = profile.get(key)
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise StrataError('Managed profiles require runtime and data_dir paths without control characters')
            try:
                profile[key] = str(Path(value).expanduser().resolve())
            except (OSError, ValueError):
                raise StrataError(f'Invalid managed path: {key}') from None
        port = profile.get('port', 8082)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise StrataError('Managed port must be an integer between 1 and 65535')
        profile['url'] = f"http://127.0.0.1:{int(profile.get('port', 8082))}"
        profile['allow_lifecycle'] = True
        profile.setdefault('same_gpu', True)
    address = profile.get('url')
    if not isinstance(address, str) or any(ord(c) <= 32 or ord(c) == 127 for c in address):
        raise StrataError('Service URL must be text without whitespace or control characters')
    try:
        url = urllib.parse.urlsplit(address)
        port = url.port if url.port is not None else (443 if url.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError('port')
    except ValueError:
        raise StrataError('Invalid service URL or port') from None
    if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
        raise StrataError('Use an HTTP(S) service URL without embedded credentials, query or fragment')
    if url.path.rstrip('/') not in ('', '/v1'):
        raise StrataError('Service URL path must be empty or /v1')
    if profile.get('same_gpu') and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise StrataError('GPU handoff requires a local service')
    for key in ('allow_lifecycle', 'same_gpu'):
        if key in profile and not isinstance(profile[key], bool):
            raise StrataError(f'{key} must be a JSON boolean')
    api_key = profile.get('api_key', '')
    if not isinstance(api_key, str) or any(ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise StrataError('API key must be text without control characters')
    try:
        api_key.encode('latin-1')
    except UnicodeEncodeError:
        raise StrataError('API key must fit the HTTP header character set') from None
    if profile.get('same_gpu') and not profile.get('allow_lifecycle'):
        raise StrataError('Same-GPU mode requires explicit lifecycle control')
    context = profile.get('context', 32768)
    if isinstance(context, bool) or not isinstance(context, int) or not 1024 <= context <= 131072:
        raise StrataError('Context must be an integer between 1024 and 131072')
    for key, default, low, high in [('timeout_s', 1800, 0, 86400), ('cleanup_timeout_s', 90, 0, 3600),
                                    ('min_free_vram_mib', 12288, 0, 1048576), ('min_free_ram_gib', 60, 0, 1048576)]:
        value = profile.get(key, default)
        minimum_ok = value >= low if key.startswith('min_free_') and isinstance(value, (int, float)) else isinstance(value, (int, float)) and value > low
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum_ok or value > high or not math.isfinite(value):
            raise StrataError(f'Invalid numeric profile value: {key}')
    if profile.get('vision', 'gpu') not in ('gpu', 'cpu', 'no'):
        raise StrataError('Vision mode must be gpu, cpu or no')
    # Extension fields are retained, but they must also remain valid on the next read.
    try:
        json.dumps(profile, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise StrataError('Profile values must be JSON data with finite numbers') from None
    host = '['+url.hostname+']' if ':' in url.hostname else url.hostname
    profile['url'] = urllib.parse.urlunsplit((url.scheme, f'{host}:{port}', url.path.rstrip('/'), '', ''))
    return profile


def save_profile(name, profile):
    with profile_lock(name, check=lambda: None):
        _save_profile(name, profile)


def _save_profile(name, profile):
    # Credentials live here, never in node inputs, PNG metadata or status responses.
    path = profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(profile, dict):
        raise StrataError('Profile must be a JSON object')
    profile = dict(profile)
    if profile.get('api_key') == '__KEEP__':
        try:
            old = json_loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if not isinstance(old, dict):
                raise StrataError('Saved profile must be an object')
        except (StrataError, OSError, UnicodeError):
            raise StrataError('Saved profile is damaged; provide a replacement API key before saving') from None
        profile['api_key'] = old.get('api_key', '')
    if profile.get('mode') == 'managed' and not profile.get('api_key'):
        profile['api_key'] = uuid.uuid4().hex + uuid.uuid4().hex
    profile = normalize_profile(profile)
    temp = path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
            json.dump(profile, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def interrupted():
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def encode_images(images, max_images=8, max_pixels=1048576):
    import numpy as np
    from PIL import Image
    if type(max_pixels) is not int or not 65536 <= max_pixels <= 4194304:
        raise StrataError('max_pixels must be an integer between 65536 and 4194304')
    if not hasattr(images, 'shape') or len(images.shape) != 4 or images.shape[-1] not in (3, 4) or images.shape[1] < 1 or images.shape[2] < 1:
        raise StrataError('IMAGE must have shape [batch, height, width, 3 or 4]')
    if not 1 <= images.shape[0] <= max_images:
        raise StrataError(f'Use at most {max_images} images per request')
    if images.numel() > max_images*16*1048576*4:
        raise StrataError('Image input exceeds the CPU conversion limit; resize it upstream')
    result = []
    for tensor in images:
        interrupted()
        # Float32 supports bfloat16/float16 input too; detect NaN/Inf before clipping hides them.
        array = tensor.detach().to('cpu').float().numpy()
        if not np.isfinite(array).all():
            raise StrataError('IMAGE contains non-finite pixel values')
        array = (np.clip(array, 0, 1)*255).round().astype(np.uint8)
        image = Image.fromarray(array)
        if image.mode == 'RGBA':
            background = Image.new('RGB', image.size, 'white')
            background.paste(image, mask=image.getchannel('A'))
            image = background
        else:
            image = image.convert('RGB')
        scale = min(1, (max_pixels/(image.width*image.height))**.5)
        if scale < 1:
            width, height = max(1, int(image.width*scale)), max(1, int(image.height*scale))
            # Clamping a thin dimension to one can otherwise exceed the requested pixel cap.
            if width*height > max_pixels:
                if width >= height:
                    width = max_pixels//height
                else:
                    height = max_pixels//width
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format='PNG')
        result.append('data:image/png;base64,' + base64.b64encode(out.getvalue()).decode())
    return result


class Client:
    def __init__(self, profile):
        self.profile = profile

    def request(self, path, body=None, check=interrupted, timeout=None):
        endpoint = urllib.parse.urlsplit(self.profile['url'])
        cls = http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection
        wait_s = timeout if timeout is not None else self.profile.get('timeout_s', 1800)
        if (isinstance(wait_s, bool) or not isinstance(wait_s, (int, float))
                or not 0 < wait_s <= 86400 or not math.isfinite(wait_s)):
            raise StrataError('Request timeout must be finite, above zero and at most 86400 seconds')
        if check:
            check()
        conn = cls(endpoint.hostname, endpoint.port, timeout=min(5, wait_s))
        conn.auto_open = 0  # A cancelled/closed socket must never reconnect and send a late request.
        result, finished, cancelled = {}, threading.Event(), threading.Event()
        def work():
            response = None
            try:
                headers = {'Content-Type': 'application/json'}
                if self.profile.get('api_key'):
                    headers['Authorization'] = 'Bearer '+self.profile['api_key']
                payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8') if body is not None else None
                if payload is not None and len(payload) > 64*1024**2:
                    raise StrataError('Service request exceeds 64 MiB; resize images or reduce the request')
                if cancelled.is_set():
                    return
                conn.connect()
                result['socket'] = conn.sock
                if cancelled.is_set():
                    return
                conn.sock.settimeout(wait_s)
                conn.request('POST' if body is not None else 'GET', path, payload, headers)
                response = conn.getresponse()
                if cancelled.is_set():
                    return
                # http.client accepts the first of duplicate lengths and ignores a length beside chunked.
                wire_headers = response.getheaders()
                lengths = [value.strip() for key,value in wire_headers if key.lower() == 'content-length']
                transfers = [value.strip().lower() for key,value in wire_headers if key.lower() == 'transfer-encoding']
                if len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers):
                    raise StrataError('Ambiguous service response framing')
                if lengths:
                    if not re.fullmatch(r'[0-9]+', lengths[0]):
                        raise StrataError('Invalid service response Content-Length')
                    length = lengths[0].lstrip('0') or '0'
                    if len(length) > 8 or int(length) > 16*1024**2:
                        raise StrataError('Service response exceeds 16 MiB')
                if transfers and transfers != ['chunked']:
                    raise StrataError('Unsupported service response Transfer-Encoding')
                expected_length = getattr(response, 'length', None)
                raw = response.read(16*1024**2+1)
                if len(raw) > 16*1024**2:
                    raise StrataError('Service response exceeds 16 MiB')
                if type(expected_length) is int and len(raw) != expected_length:
                    raise StrataError('Incomplete service response: Content-Length was not received')
                try:
                    data = json_loads(raw)
                except StrataError:
                    if response.status >= 400:
                        raise StrataError(f'HTTP {response.status}: service rejected request (invalid JSON response)') from None
                    raise
                if not isinstance(data, dict):
                    if response.status >= 400:
                        raise StrataError(f'HTTP {response.status}: service rejected request (non-object response)')
                    raise StrataError('Service response must be a JSON object')
                if response.status >= 400:
                    error = data.get('error') or {}
                    if not isinstance(error, dict):
                        error = {'message': str(error)}
                    raise StrataError(f"HTTP {response.status} {error.get('code') or error.get('type') or ''}: {error.get('message', 'service rejected request')}")
                result['data'] = data
            except Exception as error:
                result['error'] = error
            finally:
                try:
                    if response is not None:
                        response.close()
                finally:
                    try:
                        conn.close()
                    finally:
                        finished.set()
        worker = threading.Thread(target=work, daemon=True, name='strata-http')
        deadline = time.monotonic()+wait_s
        worker.start()
        try:
            while not finished.wait(min(.1, max(0, deadline-time.monotonic()))):
                if time.monotonic() >= deadline:
                    raise StrataError('Strata request exceeded its total timeout')
                if check:
                    check()
            if time.monotonic() >= deadline:
                raise StrataError('Strata request exceeded its total timeout')
            if check:
                check()
            if 'error' in result:
                error = result['error']
                if isinstance(error, StrataError):
                    message = str(error)
                    key = self.profile.get('api_key')
                    raise StrataError(message.replace(key, '[redacted]') if key else message) from None
                raise StrataError(f'Strata connection failed: {type(error).__name__}') from None
            return result['data']
        except BaseException:
            cancelled.set()
            transport = result.get('socket') or conn.sock
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    transport.close()
                except OSError:
                    pass
            # Windows buffered header/body reads may outlive shutdown. Only their worker closes
            # HTTPResponse and HTTPConnection, so cancellation never waits for a buffered-reader lock.
            worker.join(timeout=.2)
            raise


def service_status(data):
    """Require the resource state used to authorize load/release, rather than treating gaps as idle."""
    try:
        if data['service'] != 'strata' or type(data['protocol_version']) is not int or data['protocol_version'] != 1 or not isinstance(data['model'], str) or not data['model']:
            raise ValueError()
        if type(data['loaded']) is not bool or type(data['vision']['enabled']) is not bool:
            raise ValueError()
        if type(data['activity']['in_flight']) is not int or data['activity']['in_flight'] < 0:
            raise ValueError()
        if type(data['concurrency']['serving']) is not int or data['concurrency']['serving'] < 1:
            raise ValueError()
        processes = data['processes']
        if not isinstance(processes, dict) or not {'engine', 'vision'}.issubset(processes):
            raise ValueError()
        for process in processes.values():
            if not isinstance(process, dict) or any(type(process[key]) is not bool for key in ('running', 'loaded', 'starting')):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise StrataError('Incomplete or incompatible Strata-T8 protocol version 1 status') from None
    return data


@contextmanager
def file_lock(key, check=interrupted):
    if check:
        check()
    path = HOME/'locks'/(hashlib.sha256(key.encode()).hexdigest()+'.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), 'r+b') as lock:
        if not path.stat().st_size:
            lock.write(b'0')
            lock.flush()
        while True:
            lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if check:
                    check()
                time.sleep(.1)
        try:
            if check:
                check()
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def profile_lock(name, check=interrupted):
    # Windows profiles and instance directories are case-insensitive, so their locks must be too.
    path = os.path.normcase(str(profile_path(name).resolve()))
    return file_lock('profile:'+path, check)


@contextmanager
def service_lock(profile, check=interrupted):
    # Endpoint locking also protects profiles that use different same_gpu flags.
    url = urllib.parse.urlsplit(profile['url'])
    host = 'loopback' if url.hostname in ('localhost', '127.0.0.1', '::1') else url.hostname
    keys = [f'endpoint:{url.scheme}:{host}:{url.port or (443 if url.scheme == "https" else 80)}']
    if profile.get('same_gpu'):
        keys.append('gpu:0')
    if profile['mode'] == 'managed':
        keys.append('runtime:'+str(Path(profile['runtime']).expanduser().resolve()).casefold())
    with ExitStack() as stack:
        for key in sorted(keys):
            stack.enter_context(file_lock(key, check))
        yield


class Managed:
    def __init__(self, name, profile):
        self.name, self.profile = name, profile
        self.root = Path(profile['runtime']).expanduser().resolve()
        self.dir = HOME/'instances'/name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir/'owner.json'
        self.python = self.root/'runtime/python/python.exe'

    def owned(self):
        import psutil
        if not self.state_path.is_file():
            return None
        try:
            state = json_loads(self.state_path.read_text(encoding='utf-8'))
            if (type(state['pid']) is not int or not 0 < state['pid'] <= 2**32-1 or isinstance(state['created'], bool)
                    or not isinstance(state['created'], (int, float)) or not 0 <= state['created'] <= 10**12
                    or not math.isfinite(state['created'])):
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError, StrataError):
            raise StrataError('Invalid owned-service record; resource ownership cannot be confirmed') from None
        try:
            proc = psutil.Process(state['pid'])
            recorded_python = Path(state.get('python') or self.python).resolve()
            server = Path(state.get('server') or recorded_python.parents[2]/'serve/server.py').resolve()
            command = proc.cmdline()
            config = self.dir/'service.json'
            # The first script argument must be the owned server, not a path passed to another script.
            index = 1
            while index < len(command) and command[index].startswith('-'):
                if command[index] in ('-X', '-W') and index+1 < len(command):
                    index += 2
                elif command[index] in ('-u', '-B', '-E', '-I', '-s', '-S', '-q', '-b', '-bb', '-O', '-OO') or command[index].startswith(('-X', '-W')):
                    index += 1
                else:
                    break
            has_server = index < len(command) and Path(command[index]).resolve() == server
            config_options = [index for index, arg in enumerate(command) if arg == '--config' or arg.startswith('--config=')]
            has_config = (len(config_options) == 1 and command[config_options[0]] == '--config'
                          and config_options[0]+1 < len(command)
                          and Path(command[config_options[0]+1]).resolve() == config.resolve())
            created = proc.create_time()
            if (not math.isfinite(created) or abs(created-state['created']) > .01 or Path(proc.exe()).resolve() != recorded_python
                    or not has_server or not has_config):
                return None
            return proc
        except psutil.AccessDenied:
            raise StrataError('Cannot verify owned service identity; resource ownership cannot be confirmed') from None
        except (psutil.Error, OSError, ValueError, TypeError, IndexError, OverflowError):
            return None

    def stop(self):
        import psutil
        proc = self.owned()
        if proc is None:
            return False
        try:
            children = proc.children(recursive=True)
        except psutil.NoSuchProcess:
            raise StrataError('Owned parent exited before its children could be verified; resource release is unconfirmed') from None
        targets = [proc, *children]
        for target in targets:
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                pass  # Still attempt every target; wait_procs below must confirm that they all exited.
        _, alive = psutil.wait_procs(targets, timeout=15)
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(alive, timeout=15)
        if alive:
            raise StrataError('Owned service processes are still exiting; downstream GPU work is blocked')
        self.state_path.unlink(missing_ok=True)
        return True

    def ensure(self, client, check=interrupted, prepare_check=None):
        import psutil
        config_keys = ('runtime', 'data_dir', 'port', 'context', 'vision', 'api_key')
        startup = {key: self.profile.get(key) for key in config_keys}
        startup['runtime_version'] = json.loads((self.root/'meta.json').read_text(encoding='utf-8')).get('version') if (self.root/'meta.json').is_file() else None
        fingerprint = hashlib.sha256(json.dumps(startup, sort_keys=True).encode()).hexdigest()
        proc = self.owned()
        if proc is not None and json.loads(self.state_path.read_text(encoding='utf-8')).get('fingerprint') != fingerprint:
            self.stop()
            proc = None
        if proc is None:
            if not self.python.is_file() or not (self.root/'tools/managed_config.py').is_file():
                raise StrataError('Select a compatible Strata-T8 portable runtime')
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', int(self.profile.get('port', 8082))))
                except OSError:
                    raise StrataError('Managed port is occupied; select another port or use an external profile') from None
            # Offline configuration can launch native GPU probes/calibration before the HTTP server.
            check()
            if prepare_check:
                prepare_check()
                check()
            config_cmd = [str(self.python), '-X', 'utf8', str(self.root/'tools/managed_config.py'),
                          '--data-dir', self.profile['data_dir'], '--output', str(self.dir/'service.json'),
                          '--vision', self.profile.get('vision', 'gpu'), '--context', str(self.profile.get('context', 32768))]
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            log = open(self.dir/'service.log', 'ab', buffering=0)
            config = None
            try:
                config = subprocess.Popen(config_cmd, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
                deadline = time.monotonic()+900
                while config.poll() is None:
                    check()
                    if time.monotonic() > deadline:
                        raise StrataError('Offline model preparation timed out; see the local service log')
                    time.sleep(.1)
                if config.returncode:
                    raise StrataError(f'Offline profile preparation failed; see {self.dir / "service.log"}')
            except BaseException:
                if config is not None and config.poll() is None:
                    children, failure = [], None
                    try:
                        task = psutil.Process(config.pid)
                        children = task.children(recursive=True)
                        for child in children:
                            try:
                                child.kill()
                            except psutil.NoSuchProcess:
                                pass
                            except psutil.Error as error:
                                failure = error
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.Error as error:
                        failure = error
                    finally:
                        config.kill()
                        config.wait(timeout=20)
                    _, alive = psutil.wait_procs(children, timeout=20)
                    if alive or failure:
                        raise StrataError('Model preparation children have not exited')
                raise
            finally:
                log.close()
            check()
            instance = uuid.uuid4().hex
            env = dict(os.environ, STRATA_API_KEY=self.profile['api_key'], STRATA_INSTANCE_ID=instance)
            with open(self.dir/'service.log', 'ab', buffering=0) as log:
                child = subprocess.Popen([str(self.python), '-X', 'utf8', '-u', str(self.root/'serve/server.py'),
                                         '--engine', 'strata', '--config', str(self.dir/'service.json'),
                                         '--port', str(self.profile.get('port', 8082))], cwd=self.root, env=env,
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
            temp = self.state_path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
            try:
                proc = psutil.Process(child.pid)
                temp.write_text(json.dumps({'pid': child.pid, 'created': proc.create_time(), 'instance': instance,
                                            'python': str(self.python), 'server': str(self.root/'serve/server.py'),
                                            'fingerprint': fingerprint}), encoding='utf-8')
                os.replace(temp, self.state_path)
            except BaseException:
                child.kill()
                child.wait(timeout=20)
                raise
            finally:
                temp.unlink(missing_ok=True)
        try:
            state = json_loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(state, dict) or not isinstance(state.get('instance'), str) or not state['instance']:
                raise StrataError('Invalid owned-service instance record; readiness cannot be confirmed')
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                check()
                if not proc.is_running():
                    raise StrataError(f'Managed server exited; see {self.dir / "service.log"}')
                try:
                    status = client.request('/v1/status', timeout=2, check=check)
                except StrataError:
                    time.sleep(.2)
                    continue
                if status.get('instance_id') != state['instance']:
                    raise StrataError('Port belongs to a different server instance')
                service_status(status)
                return status
            raise StrataError('Managed HTTP server did not become ready within 90 seconds')
        except BaseException:
            self.stop()
            raise


def gpu_handoff(profile):
    import psutil
    import comfy.model_management as mm
    import torch
    device = mm.get_torch_device()
    if device.type != 'cuda' or getattr(torch.version, 'hip', None):
        raise StrataError('Same-GPU mode currently requires the verified NVIDIA path')
    mm.unload_all_models()
    mm.soft_empty_cache()
    free, total = torch.cuda.mem_get_info(device)
    if free < int(profile.get('min_free_vram_mib', 12288))*1024**2:
        raise StrataError(f'GPU conflict: only {free//1024**2} MiB free after ComfyUI unload')
    if psutil.virtual_memory().available < float(profile.get('min_free_ram_gib', 60))*1024**3:
        raise StrataError('Insufficient available RAM after ComfyUI offload; close other models or lower the profile threshold only after measurement')
    return device, free, torch.cuda.memory_allocated(device)


def cleanup(client, profile, manager, baseline):
    deadline = time.monotonic()+float(profile.get('cleanup_timeout_s', 90))
    last, stopped = None, False
    def remaining(limit):
        budget = deadline-time.monotonic()
        if budget <= 0:
            raise StrataError('Resource release timed out')
        return min(limit, budget)
    while time.monotonic() < deadline:
        try:
            status = service_status(client.request('/v1/status', check=None, timeout=remaining(3)))
            if not status['activity']['in_flight']:
                client.request('/v1/unload', {}, check=None, timeout=remaining(5))
                status = service_status(client.request('/v1/status', check=None, timeout=remaining(3)))
                if not status['activity']['in_flight'] and not status['loaded'] and not any(p['running'] or p['loaded'] or p['starting'] for p in status['processes'].values()):
                    break
        except (StrataError, KeyError) as error:
            last = error
        time.sleep(min(.2, max(0, deadline-time.monotonic())))
    else:
        if manager:
            if not manager.stop():
                raise StrataError('Resource release could not be confirmed and no owned service can be stopped')
            stopped = True
        else:
            raise StrataError(f'External service release could not be confirmed: {last}')
    if baseline:
        import torch
        end = time.monotonic()+15
        while torch.cuda.mem_get_info(baseline[0])[0] < baseline[1]-256*1024**2:
            if time.monotonic() > end:
                raise StrataError('GPU memory has not returned after Strata unload; downstream work is blocked')
            time.sleep(.2)
    return stopped


def generate(connection, requests):
    with profile_lock(connection.profile):
        return _generate(connection, requests)


def _generate(connection, requests):
    profile = read_profile(connection.profile)
    client = Client(profile)
    manager = Managed(connection.profile, profile) if profile['mode'] == 'managed' else None
    with service_lock(profile):
        baseline = None
        cleanup_required = False
        def check():
            interrupted()
            if baseline:
                import torch
                if torch.cuda.memory_allocated(baseline[0]) > baseline[2]+128*1024**2:
                    raise StrataError('ComfyUI background GPU work appeared during the Strata request')
        try:
            status = manager.ensure(client, check=check, prepare_check=lambda: gpu_handoff(profile) if profile.get('same_gpu') else None) if manager else client.request('/v1/status', check=check, timeout=5)
            service_status(status)
            if profile.get('same_gpu') and status['concurrency']['serving'] != 1:
                raise StrataError('Same-GPU mode requires parallel=1')
            if profile.get('same_gpu') and status['activity']['in_flight']:
                raise StrataError('Strata is busy with another request; retry after it finishes')
            cleanup_required = bool(profile.get('allow_lifecycle'))
            if profile.get('same_gpu') and (status['loaded'] or any(p['running'] or p['loaded'] or p['starting'] for p in status['processes'].values())):
                if cleanup(client, profile, manager, None):
                    # A confirmed fallback stop invalidates the old HTTP/model identity.
                    status = manager.ensure(client, check=check, prepare_check=lambda: gpu_handoff(profile))
                    service_status(status)
                    if status['activity']['in_flight']:
                        cleanup_required = False  # Do not unload another active request.
                        raise StrataError('Strata is busy with another request; retry after it finishes')
                    if status['concurrency']['serving'] != 1:
                        raise StrataError('Same-GPU mode requires parallel=1')
            baseline = gpu_handoff(profile) if profile.get('same_gpu') else None
            answers = []
            for request in requests:
                check()
                request = dict(request, model=status['model'], stream=False)
                if any(isinstance(m.get('content'), list) for m in request['messages']) and not status['vision']['enabled']:
                    raise StrataError('Enable the bundled vision encoder in this profile')
                result = client.request('/v1/chat/completions', request, check=check)
                choices = result.get('choices')
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get('message'), dict):
                    raise StrataError('Malformed chat completion: missing choices/message')
                message = choices[0]['message']
                text = message.get('content') or ''
                reasoning = message.get('reasoning_content')
                reasoning = '' if reasoning is None else reasoning
                usage = result.get('usage')
                usage = {} if usage is None else usage
                if not isinstance(text, str) or not isinstance(reasoning, str) or not isinstance(usage, dict):
                    raise StrataError('Malformed chat completion: text/reasoning/usage types')
                if not text:
                    raise StrataError('Model returned no final answer; increase max_tokens or reduce reasoning effort')
                answers.append((text, reasoning, json.dumps(usage, ensure_ascii=False)))
            return answers
        finally:
            if cleanup_required:
                cleanup(client, profile, manager, baseline)


def control(connection, action, check=interrupted):
    if action == 'status':
        return _control(connection, action, check)
    with profile_lock(connection.profile, check):
        return _control(connection, action, check)


def _control(connection, action, check=interrupted):
    if action not in ('status', 'start', 'load', 'unload', 'stop'):
        raise StrataError('Unknown control action')
    profile = read_profile(connection.profile)
    client = Client(profile)
    if action == 'status':
        return service_status(client.request('/v1/status', timeout=5, check=check))
    with service_lock(profile, check=check):
        manager = Managed(connection.profile, profile) if profile['mode'] == 'managed' else None
        if action == 'stop':
            if not manager:
                raise StrataError('Only a managed profile can stop its owned server')
            stopped = manager.stop()
            return {'service': 'strata', 'stopped': stopped}
        if action != 'status' and not profile.get('allow_lifecycle'):
            raise StrataError('Enable lifecycle control explicitly for this external profile')
        if manager and action in ('start', 'load'):
            manager.ensure(client, check=check, prepare_check=lambda: gpu_handoff(profile) if profile.get('same_gpu') else None)
        if action == 'load':
            status = service_status(client.request('/v1/status', timeout=5, check=check))
            if profile.get('same_gpu') and status['concurrency']['serving'] != 1:
                raise StrataError('Same-GPU mode requires parallel=1')
            if status.get('protocol_version') != 1 or status['activity']['in_flight']:
                raise StrataError('Load requires an idle compatible Strata-T8 service')
            if profile.get('same_gpu') and cleanup(client, profile, manager, None):
                status = service_status(manager.ensure(client, check=check, prepare_check=lambda: gpu_handoff(profile)))
                if status['concurrency']['serving'] != 1 or status['activity']['in_flight']:
                    raise StrataError('Load requires an idle compatible Strata-T8 service with parallel=1')
        baseline = gpu_handoff(profile) if action == 'load' and profile.get('same_gpu') else None
        status = None
        try:
            if action == 'load':
                client.request('/v1/load', {}, check=check)
            elif action == 'unload':
                if cleanup(client, profile, manager, None):
                    return {'service': 'strata', 'stopped': True, 'released': True}
            status = service_status(client.request('/v1/status', timeout=5, check=check))
        finally:
            if action == 'load' and (profile.get('same_gpu') or status is None) and profile.get('allow_lifecycle'):
                stopped = cleanup(client, profile, manager, baseline)
                if status is not None:
                    status['after_release'] = ({'service':'strata','stopped':True,'released':True} if stopped
                                               else client.request('/v1/status', timeout=5, check=None))
        return status
