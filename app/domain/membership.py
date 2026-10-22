"""Membership outcomes independent of HTTP and presentation language."""
from enum import StrEnum


class MemberCode(StrEnum):
    ALREADY_MEMBER = "MEMBER_ALREADY"
    APPLICATION_PENDING = "MEMBER_APPLICATION_PENDING"
    APPLICATION_NOT_PENDING = "MEMBER_APPLICATION_NOT_PENDING"
    USER_NOT_FOUND = "MEMBER_USER_NOT_FOUND"
    USER_ALREADY_DELETED = "MEMBER_USER_ALREADY_DELETED"
    CANNOT_DELETE_SELF = "MEMBER_CANNOT_DELETE_SELF"
    INVALID_ROLE = "MEMBER_INVALID_ROLE"


class MemberError(Exception):
    """Transport-independent membership failure."""

    def __init__(self, code: MemberCode, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(code.value)
