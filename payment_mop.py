"""Read STORE, AMOUNTTENDERED and MOP_TYPE from PAYMENT_WISE_TRANSACTIONS
and write them to payment_mop_output.txt, followed by totals per store and MOP.

    python payment_mop.py
"""
import os

from db_v2 import read_table

COLUMNS = ["STORE", "AMOUNTTENDERED", "MOP_TYPE"]


def main():
    df = read_table("PAYMENT")[COLUMNS]
    df["STORE"] = df["STORE"].str.strip()
    df["MOP_TYPE"] = df["MOP_TYPE"].str.strip()
    df["AMOUNTTENDERED"] = df["AMOUNTTENDERED"].astype(float)
    df = df.sort_values(["STORE", "MOP_TYPE"]).reset_index(drop=True)

    totals = (df.groupby(["STORE", "MOP_TYPE"])["AMOUNTTENDERED"]
                .agg(COUNT="count", TOTAL="sum").reset_index())

    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, "payment_mop_output.txt")
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"PAYMENT_WISE_TRANSACTIONS - {len(df)} rows\n\n")
        out.write(df.to_string(index=False, float_format="{:.2f}".format))
        out.write("\n\n=== Totals by store and MOP ===\n")
        out.write(totals.to_string(index=False, float_format="{:.2f}".format))
        out.write(f"\n\nGRAND TOTAL: {df['AMOUNTTENDERED'].sum():.2f}\n")
    print(f"Done. {len(df)} rows written to {out_path}")


if __name__ == "__main__":
    main()
