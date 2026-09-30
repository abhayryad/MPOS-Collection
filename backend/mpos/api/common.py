from datetime import date

from fastapi import HTTPException


def check_date(value, name):
    """Validate a YYYY-MM-DD query parameter, returning it normalised."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be YYYY-MM-DD")
