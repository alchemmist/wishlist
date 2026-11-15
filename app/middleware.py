"""Request size limits apply while receiving, before multipart spools a file."""
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
        # Allow multipart overhead, while the endpoint independently enforces 8 MB/file.
        limit = 9_000_000 if headers.get('content-type', '').lower().startswith('multipart/form-data') else 262_144
        try:
            declared = int(headers.get('content-length', '0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)(scope, receive, send)
        if declared > limit:
            return await JSONResponse({'detail': 'Request body is too large'}, status_code=413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    raise HTTPException(413, 'Request body is too large')
            return message

        await self.app(scope, limited_receive, send)
