"""Input-shape rules for visit tracking events."""
PAGE_VIEW = "page_view"


def safe_visitor_id(value: str | None) -> str | None:
    """Client-supplied visitor id: trimmed, None when empty, at most 64 chars."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:64]


def clamp_user_agent(user_agent: str) -> str:
    return user_agent[:512]


def clamp_path(path: str) -> str:
    return path[:512] or "/"
