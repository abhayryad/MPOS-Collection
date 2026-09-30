"""User locations: 'HO' (head office) sees every store; otherwise a user sees only their store codes.

Every data endpoint resolves the requested store through stores_for() so the rule is
enforced on the server, not just hidden in the page.
"""
from fastapi import HTTPException

HO = "HO"


def clean(locations):
    """['ho', ' hd22', 'HD22'] -> ['HO'];  ['hd22', 'dh24'] -> ['DH24', 'HD22']. HO wins over store codes."""
    codes = {c.strip().upper() for c in locations if c and c.strip()}
    return [HO] if HO in codes else sorted(codes)


def parse(value):
    return clean((value or "").split(","))


def allowed_stores(user):
    """None = every store (HO / admin); else the set of store codes this user may see."""
    if user.is_admin or HO in user.locations:
        return None
    return set(user.locations)


def stores_for(user, store=""):
    """Stores a request may read: [store] if one is asked for (and allowed), else None for
    HO users (all stores) or the user's own store list."""
    allowed = allowed_stores(user)
    store = (store or "").strip().upper()
    if allowed is not None and not allowed:
        raise HTTPException(403, "No store is assigned to your account - ask your admin to set your location")
    if store:
        if allowed is not None and store not in allowed:
            raise HTTPException(403, f"You do not have access to store {store}")
        return [store]
    return None if allowed is None else sorted(allowed)


def filter_stores(user, stores):
    """Subset of a store list this user may see (for pickers)."""
    allowed = allowed_stores(user)
    return list(stores) if allowed is None else [s for s in stores if s.upper() in allowed]
