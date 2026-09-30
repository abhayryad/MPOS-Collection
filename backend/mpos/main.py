"""MPOS Collection web app: the /api endpoints plus the built React frontend.

    cd frontend && npm install && npm run build        (once, and after frontend changes)
    python -m uvicorn mpos.main:app --app-dir backend --port 8028   -> http://localhost:8028

or start_lan.bat to serve it on the LAN.
"""
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .api import routers
from .config import settings

app = FastAPI(title="MPOS Collection")
for router in routers:
    app.include_router(router)


class FrontendFiles(StaticFiles):
    """index.html is always revalidated so a rebuild reaches every browser;
    hashed files under assets/ never change and can be cached for a year."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("assets/"):  # Windows passes assets\...
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


# Mounted last so the /api routes above take precedence.
if settings.frontend_dist.is_dir():
    app.mount("/", FrontendFiles(directory=settings.frontend_dist, html=True), name="frontend")
else:
    @app.get("/")
    def frontend_missing():
        return Response("Frontend not built. Run:  cd frontend && npm install && npm run build",
                        media_type="text/plain", status_code=503)
