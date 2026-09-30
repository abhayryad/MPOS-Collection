"""Command-line tools. Run from the project root:

    python -m mpos export-slips                          all dates, all stores, M S R C
    python -m mpos export-slips --date 2026-09-28        one day
    python -m mpos export-slips --from 2026-09-01 --to 2026-09-28 --store HD22 --kinds SR
    python -m mpos check-db                              test the connection, show table sizes
    python -m mpos payment-mop                           STORE / AMOUNTTENDERED / MOP_TYPE listing

(needs backend/ on the path: `set PYTHONPATH=backend` first, or use run_cli.bat)

export-slips writes the same slip text as the website into output/, one file per
store, day and slip type. Unlike the website it does not hold back today's slips.
"""
import argparse
import sys
from datetime import date

from .config import settings
from .db import TABLES, connect, read_table
from .slips.service import KINDS, build_all, load


def export_slips(args):
    if args.date and (args.start or args.end):
        sys.exit("Use either --date or --from/--to, not both.")
    start = end = args.date
    if args.start or args.end:
        start, end = args.start or args.end, args.end or args.start
    for d in (start, end):
        if d:
            try:
                date.fromisoformat(d)
            except ValueError:
                sys.exit(f"Invalid date {d!r} - use YYYY-MM-DD, e.g. 2026-09-28")
    kinds = [k for k in args.kinds.upper() if k in KINDS]
    if not kinds:
        sys.exit(f"--kinds must use the letters {''.join(KINDS)}")

    slips = build_all(*load(start, end, args.store))
    settings.output_dir.mkdir(exist_ok=True)
    written = []
    for kind in kinds:
        for (store, day), text in sorted(slips[kind].items()):
            path = settings.output_dir / f"{store}_{day}_{kind}.txt"
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path.name)
    if not written:
        sys.exit("No slips for that filter.")
    print(f"Wrote {len(written)} file(s) to {settings.output_dir}")
    for name in written:
        print("  " + name)


def check_db(_args):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT @@SERVERNAME, DB_NAME(), SUSER_SNAME(), GETDATE()")
        server, db, login, now = cur.fetchone()
        print(f"Connected: server={server} db={db} login={login} time={now}\n")
        for key, name in TABLES.items():
            cur.execute("SELECT SUM(rows) FROM sys.partitions WHERE object_id = OBJECT_ID(?) AND index_id IN (0, 1)",
                        f"dbo.{name}")
            print(f"{key:<8} {name:<30} rows={cur.fetchone()[0]}")


def payment_mop(_args):
    """STORE, AMOUNTTENDERED and MOP_TYPE for every payment, then totals per store and MOP."""
    df = read_table("PAYMENT")[["STORE", "AMOUNTTENDERED", "MOP_TYPE"]]
    df["STORE"] = df["STORE"].str.strip()
    df["MOP_TYPE"] = df["MOP_TYPE"].str.strip()
    df["AMOUNTTENDERED"] = df["AMOUNTTENDERED"].astype(float)
    df = df.sort_values(["STORE", "MOP_TYPE"]).reset_index(drop=True)
    totals = (df.groupby(["STORE", "MOP_TYPE"])["AMOUNTTENDERED"]
                .agg(COUNT="count", TOTAL="sum").reset_index())

    settings.output_dir.mkdir(exist_ok=True)
    out_path = settings.output_dir / "payment_mop_output.txt"
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"PAYMENT_WISE_TRANSACTIONS - {len(df)} rows\n\n")
        out.write(df.to_string(index=False, float_format="{:.2f}".format))
        out.write("\n\n=== Totals by store and MOP ===\n")
        out.write(totals.to_string(index=False, float_format="{:.2f}".format))
        out.write(f"\n\nGRAND TOTAL: {df['AMOUNTTENDERED'].sum():.2f}\n")
    print(f"Done. {len(df)} rows written to {out_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m mpos", description="MPOS Collection command-line tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export-slips", help="write slip files to output/")
    p.add_argument("--date", help="one day, YYYY-MM-DD")
    p.add_argument("--from", dest="start", help="first day, YYYY-MM-DD")
    p.add_argument("--to", dest="end", help="last day, YYYY-MM-DD")
    p.add_argument("--store", help="one store code, e.g. HD22 (default: all)")
    p.add_argument("--kinds", default="".join(KINDS), help="slip types, e.g. MS (default: MSRC)")
    p.set_defaults(func=export_slips)

    sub.add_parser("check-db", help="test the database connection").set_defaults(func=check_db)
    sub.add_parser("payment-mop", help="payment listing with totals per store and MOP").set_defaults(func=payment_mop)

    args = parser.parse_args(argv)
    args.func(args)
