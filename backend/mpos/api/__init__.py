"""HTTP API. Each module is one router; main.py includes them all.
Every route except /api/auth/login requires a logged-in user (see auth/deps.py)."""
from . import admin, auth, dashboard, reports, slips

routers = [auth.router, admin.router, dashboard.router, slips.router, reports.router]
