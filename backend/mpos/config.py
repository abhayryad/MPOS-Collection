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

    # Snowflake: where site users and the authentication history are stored
    sf_account: str = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    sf_user: str = os.environ.get("SNOWFLAKE_USER", "")
    sf_password: str = os.environ.get("SNOWFLAKE_PASSWORD", "")  # used only if no private key is set
    # Key-pair authentication (preferred): path to the .p8 private key, and its passphrase if encrypted
    sf_private_key_path: str = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "")
    sf_private_key_passphrase: str = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    sf_role: str = os.environ.get("SNOWFLAKE_ROLE", "")
    sf_warehouse: str = os.environ.get("SNOWFLAKE_WAREHOUSE", "")
    sf_database: str = os.environ.get("SNOWFLAKE_DATABASE", "V2RETAIL")
    sf_schema: str = os.environ.get("SNOWFLAKE_SCHEMA", "BRONZE")

    # Site login. The built-in "admin" account's password lives only here, not in Snowflake.
    admin_username: str = "admin"
    admin_password: str = os.environ.get("MPOS_ADMIN_PASSWORD", "")
    secret_key: str = os.environ.get("MPOS_SECRET_KEY", "")  # signs session cookies
    # Pre-filled temporary password for new users / resets; a user given it must change it at first login
    default_user_password: str = os.environ.get("MPOS_DEFAULT_PASSWORD", "")
    session_hours: int = int(os.environ.get("MPOS_SESSION_HOURS", "8"))
    max_failed_logins: int = 5          # per username ...
    lockout_minutes: int = 15           # ... within this many minutes locks the login this long

    root: Path = ROOT
    frontend_dist: Path = ROOT / "frontend" / "dist"
    sql_dir: Path = ROOT / "sql"
    output_dir: Path = ROOT / "output"  # where the command-line export writes slip files


settings = Settings()
