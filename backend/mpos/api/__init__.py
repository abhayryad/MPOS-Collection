"""HTTP API. Each module is one router; main.py includes them all."""
from . import dashboard, reports, slips

routers = [dashboard.router, slips.router, reports.router]
