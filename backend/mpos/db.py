"""SQL Server access for datav2: connections and plain table reads."""
import warnings

import pandas as pd
import pyodbc

from .config import settings

# Source tables in datav2.dbo
TABLES = {
    "BILL": "BILL_WISE_ORDER_DATA",
    "PAYMENT": "PAYMENT_WISE_TRANSACTIONS",
    "ITEM": "ITEM_WISE_TRANSACTIONS",
}


def connect(database=None):
    if not settings.db_password:
        raise RuntimeError("SQL28_PWD is not set - add it to the .env file in the project root.")
    conn_str = (
        f"DRIVER={{{settings.db_driver}}};SERVER={settings.db_server},{settings.db_port};"
        f"DATABASE={database or settings.db_name};UID={settings.db_user};PWD={settings.db_password};"
        "Encrypt=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, timeout=15)


def read_sql(sql, conn, params=()):
    """pandas.read_sql on a pyodbc connection, without pandas' SQLAlchemy warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
        return pd.read_sql(sql, conn, params=list(params) or None)


def read_table(table, top=None, where=None, params=(), conn=None):
    """Read one of TABLES into a DataFrame, e.g.
    read_table("BILL", top=100) or read_table("PAYMENT", where="STORE = ?", params=["HD22"])."""
    name = TABLES.get(table.upper(), table)
    sql = f"SELECT {f'TOP {int(top)} ' if top else ''}* FROM dbo.[{name}]"
    if where:
        sql += f" WHERE {where}"
    own = conn is None
    conn = conn or connect()
    try:
        return read_sql(sql, conn, params)
    finally:
        if own:
            conn.close()
