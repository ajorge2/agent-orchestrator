"""Sample-only public MCP endpoints alongside the existing web application."""
from contextlib import AsyncExitStack, asynccontextmanager
import logging
import tempfile
from pathlib import Path
from flask import send_from_directory, jsonify
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount
from mcp.server.transport_security import TransportSecuritySettings
from .mcp_server import create_server as create_orcview
from sqlharness.mcp_server import create_server as create_sqlharness, create_demo_database

PUBLIC=Path(__file__).resolve().parent.parent/'mcp-public'

def create_public_app(flask_app):
    @flask_app.get('/mcp/orcview/')
    @flask_app.get('/mcp/orcview')
    def mcp_page():return send_from_directory(PUBLIC,'orcview.html')
    @flask_app.get('/mcp/assets/<path:name>')
    def mcp_assets(name):return send_from_directory(PUBLIC/'assets',name)
    @flask_app.get('/mcp/install/<path:name>')
    def mcp_install(name):return send_from_directory(PUBLIC/'install',name)
    @flask_app.get('/mcp/health')
    def mcp_health():return jsonify(ok=True,release='mcp-2026-10-23',sample_only=True,model_calls=False)

    samples=tempfile.TemporaryDirectory(prefix='sqlharness-public-synthetic-')
    sql=create_sqlharness(create_demo_database(Path(samples.name)),max_rows=100)
    orc=create_orcview()
    security=TransportSecuritySettings(allowed_hosts=['site--orcview--764vq9x779wv.code.run','127.0.0.1:*','localhost:*'],allowed_origins=['https://site--orcview--764vq9x779wv.code.run','https://site--codearchitect--74wvcb9qzxr6.code.run','http://127.0.0.1:*','http://localhost:*'])
    paths={'/mcp/orcview/server':orc,'/mcp/sqlharness/server':sql}
    apps={p:s.streamable_http_app(streamable_http_path=p,stateless_http=True,json_response=True,max_request_body_size=64000,transport_security=security) for p,s in paths.items()}
    legacy=WSGIMiddleware(flask_app)
    active=0
    async def gateway(scope,receive,send):
        nonlocal active
        app=apps.get(scope.get('path'))
        if app is None:return await legacy(scope,receive,send)
        if active>=8:return await JSONResponse({'error':'Demo busy; retry shortly'},status_code=429,headers={'Retry-After':'2'})(scope,receive,send)
        active+=1
        async def no_store(message):
            if message['type']=='http.response.start':
                message['headers']=[*message.get('headers',[]),(b'cache-control',b'no-store')]
            await send(message)
        try:await app(scope,receive,no_store)
        finally:active-=1
    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with AsyncExitStack() as stack:
                for app in apps.values():await stack.enter_async_context(app.router.lifespan_context(app))
                yield
        finally:samples.cleanup()
    # No request/body logging, telemetry or model calls in this MCP path.
    logging.disable(logging.CRITICAL)
    return Starlette(routes=[Mount('/',app=gateway)],lifespan=lifespan)
