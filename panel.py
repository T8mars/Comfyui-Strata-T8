"""Local-only configuration/status endpoints; credentials never returned to the browser."""
import asyncio
import json
from urllib.parse import urlsplit
from . import core


def register():
    try:
        from server import PromptServer
        from aiohttp import web
        server = PromptServer.instance
    except (ImportError, AttributeError):
        return

    def local(request, post=False):
        try:
            host = urlsplit('http://'+request.host).hostname
            origin = request.headers.get('Origin')
            parsed_origin = urlsplit(origin) if origin else None
        except ValueError:
            raise web.HTTPForbidden(text='Invalid local Origin') from None
        if request.remote not in ('127.0.0.1', '::1') or host not in ('127.0.0.1', 'localhost', '::1'):
            raise web.HTTPForbidden(text='Strata profile controls require a loopback ComfyUI connection')
        if parsed_origin and (parsed_origin.netloc != request.host or parsed_origin.scheme != request.scheme
                              or parsed_origin.path or parsed_origin.query or parsed_origin.fragment):
            raise web.HTTPForbidden(text='Foreign Origin')
        if post and request.content_type != 'application/json':
            raise web.HTTPUnsupportedMediaType(text='Use application/json')

    @server.routes.get('/strata_t8/profiles')
    async def get_profiles(request):
        local(request)
        values, errors = {}, {}
        for name in core.profiles():
            if core.profile_path(name).is_file():
                try:
                    value = core.read_profile(name)
                except core.StrataError:
                    errors[name] = 'Saved profile could not be read; provide a complete replacement profile and API key'
                    continue
                values[name] = {k: v for k, v in value.items() if k != 'api_key'}
                values[name]['key_configured'] = bool(value.get('api_key'))
        return web.json_response({'profiles': values, 'profile_errors': errors, 'home': str(core.HOME)})

    @server.routes.post('/strata_t8/profile')
    async def set_profile(request):
        local(request, True)
        try:
            body = await request.json(loads=core.json_loads)
            if not isinstance(body, dict) or not isinstance(body.get('name'), str) or not isinstance(body.get('profile'), dict):
                raise core.StrataError('Provide a profile name and JSON profile object')
            await asyncio.to_thread(core.save_profile, body['name'], body['profile'])
            return web.json_response({'saved': body['name']})
        except core.StrataError as error:
            return web.json_response({'error': str(error)}, status=400)
        except Exception as error:
            return web.json_response({'error': f'Profile save failed: {type(error).__name__}'}, status=400)

    @server.routes.post('/strata_t8/control')
    async def control(request):
        local(request, True)
        try:
            body = await request.json(loads=core.json_loads)
            if not isinstance(body, dict) or not isinstance(body.get('name'), str) or body.get('action') not in ('start', 'status', 'load', 'unload', 'stop'):
                raise core.StrataError('Provide a profile name and known control action')
            if body['action'] != 'status':
                return web.json_response({'error': 'Service lifecycle controls must run through a Strata Control workflow in the ComfyUI queue.'}, status=409)
            status = await asyncio.to_thread(core.control, core.Connection(body['name']), body['action'], lambda: None)
            return web.json_response(status)
        except core.StrataError as error:
            return web.json_response({'error': str(error)}, status=400)
        except Exception as error:
            return web.json_response({'error': f'Service control failed: {type(error).__name__}'}, status=400)
