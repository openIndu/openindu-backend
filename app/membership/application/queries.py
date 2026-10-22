"""Read use cases for membership administration."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserListCriteria:
    page: int
    size: int
    keyword: str | None
    role: str | None
    apply_status: str | None
    sort_by: str
    sort_order: str


class UserDirectory(Protocol):
    def list_users(self, criteria: UserListCriteria) -> dict: ...


class ListUsers:
    def __init__(self, directory: UserDirectory) -> None:
        self.directory = directory

    def execute(self, criteria: UserListCriteria) -> dict:
        return self.directory.list_users(criteria)
