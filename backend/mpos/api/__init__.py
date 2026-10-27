"""HTTP API. Each module is one router; main.py includes them all.
Every route except /api/auth/login requires a logged-in user (see auth/deps.py) - apart from
/api/device/*, which the mPOS phones call with their own token (see device_api.py)."""
from . import admin, auth, dashboard, device_api, reports, slips

routers = [auth.router, admin.router, dashboard.router, slips.router, reports.router, device_api.router]
