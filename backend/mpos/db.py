"""Connect to the datav2 database on server 28 (SQL Server).

The password is read from SQL28_PWD in the .env file next to this script
(or from the environment). Never commit or share .env.

    python db_v2.py            -> tests the connection
    from db_v2 import connect  -> use in other scripts
"""
import os
import warnings

import pyodbc
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

SERVER = "192.168.151.28"
PORT = 1433
DATABASE = "datav2"
USER = "ssis_new"
PASSWORD = os.environ.get("SQL28_PWD", "")  # loaded from .env
DRIVER = "ODBC Driver 18 for SQL Server"

# Source tables in datav2.dbo
TABLES = {
    "BILL": "BILL_WISE_ORDER_DATA",
    "PAYMENT": "PAYMENT_WISE_TRANSACTIONS",
    "ITEM": "ITEM_WISE_TRANSACTIONS",
}


def connect(database=DATABASE):
    password = PASSWORD or os.environ.get("SQL28_PWD", "")
    if not password:
        raise RuntimeError("Set PASSWORD in db_v2.py or the SQL28_PWD environment variable.")
    conn_str = (
        f"DRIVER={{{DRIVER}}};SERVER={SERVER},{PORT};DATABASE={database};"
        f"UID={USER};PWD={password};Encrypt=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, timeout=15)


def read_table(table, top=None, where=None, params=(), conn=None):
    """Read one of TABLES into a DataFrame, e.g.
    read_table("BILL", top=100) or read_table("PAYMENT", where="STORE_CODE = ?", params=["S01"])."""
    import pandas as pd

    name = TABLES.get(table.upper(), table)
    sql = f"SELECT {f'TOP {int(top)} ' if top else ''}* FROM dbo.[{name}]"
    if where:
        sql += f" WHERE {where}"
    own = conn is None
    conn = conn or connect()
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
            return pd.read_sql(sql, conn, params=list(params) or None)
    finally:
        if own:
            conn.close()


def row_counts(conn):
    cur = conn.cursor()
    for key, name in TABLES.items():
        cur.execute(
            "SELECT SUM(rows) FROM sys.partitions WHERE object_id = OBJECT_ID(?) AND index_id IN (0, 1)",
            f"dbo.{name}")
        print(f"{key:<8} {name:<30} rows={cur.fetchone()[0]}")


if __name__ == "__main__":
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT @@SERVERNAME, DB_NAME(), SUSER_SNAME(), GETDATE()")
        server, db, login, now = cur.fetchone()
        print(f"Connected: server={server} db={db} login={login} time={now}\n")
        row_counts(conn)
        for key in TABLES:
            df = read_table(key, top=5, conn=conn)
            print(f"\n{key}: {len(df.columns)} columns\n{df.head().to_string()}")
