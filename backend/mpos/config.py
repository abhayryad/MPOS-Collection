"""All settings in one place. Values come from the environment, normally via the
.env file in the project root (see .env.example); the defaults are server 28 / datav2.

Never commit .env - it holds the database password.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # project root: backend/mpos/config.py -> ../..
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_server: str = os.environ.get("SQL28_SERVER", "192.168.151.28")
    db_port: int = int(os.environ.get("SQL28_PORT", "1433"))
    db_name: str = os.environ.get("SQL28_DATABASE", "datav2")
    db_user: str = os.environ.get("SQL28_USER", "ssis_new")
    db_password: str = os.environ.get("SQL28_PWD", "")
    db_driver: str = os.environ.get("SQL28_DRIVER", "ODBC Driver 18 for SQL Server")

    root: Path = ROOT
    frontend_dist: Path = ROOT / "frontend" / "dist"
    sql_dir: Path = ROOT / "sql"
    output_dir: Path = ROOT / "output"  # where the command-line export writes slip files


settings = Settings()
