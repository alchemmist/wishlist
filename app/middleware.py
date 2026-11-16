"""Keep small API-body limits; authenticated photo streams have no size/count cap."""
import re
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path = scope.get('path', '')
        photo_upload = scope.get('method') in {'POST', 'PUT'} and re.fullmatch(
            r'/api/items/\d+/(?:image|images/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', path)
        photo_order = scope.get('method') == 'PATCH' and re.fullmatch(r'/api/items/\d+/images', path)
        if (photo_upload or photo_order) and not scope.get('session', {}).get('sub'):
            return await JSONResponse({'detail': 'Sign in first'}, status_code=401)(scope, receive, send)
        unlimited = photo_order or (photo_upload and headers.get('content-type', '').lower().startswith('multipart/form-data'))
        limit = None if unlimited else 262_144
        try:
            declared = int(headers.get('content-length', '0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)(scope, receive, send)
        if limit is not None and declared > limit:
            return await JSONResponse({'detail': 'Request body is too large'}, status_code=413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if limit is not None and received > limit:
                    raise HTTPException(413, 'Request body is too large')
            return message

        await self.app(scope, limited_receive, send)
