"""Store master: the store codes an admin can assign as a user's location.

Source: Snowflake V2RETAIL.GOLD.STORE_PLANT_MASTER, active stores only (ST_TYP = 'STORE',
ST_STAT = 'ACT'). Cached for 10 minutes - the list changes rarely.
"""
import time

from .auth import store as sf

STORE_MASTER = "V2RETAIL.GOLD.STORE_PLANT_MASTER"
_CACHE_SECONDS = 600
_cache = {"until": 0.0, "rows": []}


def store_master():
    """[{code, name, zone, region, state}] for every active store, sorted by code."""
    if _cache["until"] > time.time():
        return _cache["rows"]
    rows = sf._run(
        f"SELECT UPPER(TRIM(ST_CD)) AS CODE, TRIM(ST_FULL_NM) AS NAME, TRIM(ZONE) AS ZONE, "
        f"TRIM(REG) AS REGION, TRIM(STATE) AS STATE FROM {STORE_MASTER} "
        f"WHERE UPPER(TRIM(ST_TYP)) = 'STORE' AND UPPER(TRIM(ST_STAT)) = 'ACT' "
        f"AND ST_CD IS NOT NULL AND TRIM(ST_CD) <> '' ORDER BY CODE")
    seen, out = set(), []
    for r in rows:
        if r["CODE"] not in seen:
            seen.add(r["CODE"])
            out.append({"code": r["CODE"], "name": r["NAME"] or "", "zone": r["ZONE"] or "",
                        "region": r["REGION"] or "", "state": r["STATE"] or ""})
    _cache.update(until=time.time() + _CACHE_SECONDS, rows=out)
    return out
