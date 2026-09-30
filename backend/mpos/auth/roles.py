"""Site roles. A role gives access to one tab; the built-in admin account has every role
plus the Admin tab. Add a role here and in frontend/src/App.tsx to protect a new tab."""

SALE_POSTING = "SALE_POSTING"
REPORTS = "REPORTS"
ADMIN = "ADMIN"  # only the built-in admin account; cannot be given to users

ROLES = {
    SALE_POSTING: "Sale Posting Download",
    REPORTS: "Reports",
}


def clean(roles):
    """Valid, de-duplicated role list in ROLES order."""
    wanted = {r.strip().upper() for r in roles if r and r.strip()}
    return [r for r in ROLES if r in wanted]
