"""Helpers shared by the slip formats."""


def num(v):
    """Unsigned number without trailing zeros: 450.0 -> '450', -324.50 -> '324.5', None -> '0'."""
    v = abs(float(v or 0))
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def header_line(store, day, stars):
    """'1 <store> <YYYYMMDD> * * ...' header used by the S, R and C slips."""
    return "\t".join(["1", store, day.replace("-", "")] + ["*"] * stars)
