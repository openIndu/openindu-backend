"""Resource tag use cases: create, rename with cascades, delete with guards."""
from typing import Protocol

from app.catalog.domain import tag_rules
from app.domain.catalog import CatalogCode, CatalogError


class TagEntry(Protocol):
    id: int
    type: str
    value: str
    label_zh: str
    parent_value: str | None
    brand_value: str | None


class TagBoard(Protocol):
    def find(self, tag_id: int) -> TagEntry | None: ...

    def exists(self, tag_type: str, value: str, parent_value: str | None, scoped: bool) -> bool: ...

    def create(self, *, tag_type: str, value: str, label_zh: str, parent_value: str | None, brand_value: str | None, sort_order: int) -> TagEntry: ...

    def rename_references(self, old_value: str, new_value: str, tag_type: str) -> None: ...

    def in_use(self, tag: TagEntry) -> bool: ...

    def delete_child_series(self, parent_value: str) -> None: ...

    def save(self, tag: TagEntry) -> TagEntry: ...

    def delete(self, tag: TagEntry) -> None: ...


class CreateTag:
    def __init__(self, board: TagBoard) -> None:
        self.board = board

    def execute(self, *, tag_type: str, value: str, label_zh: str, parent_value: str | None = None, brand_value: str | None = None, sort_order: int = 0) -> TagEntry:
        tag_rules.require_known_tag_type(tag_type)
        stripped_value, stripped_label = tag_rules.require_tag_fields(value, label_zh)
        tag_rules.require_series_parent(tag_type, parent_value, brand_value)
        # Series values are globally unique; other types are unique per parent.
        scoped = tag_type not in tag_rules.SERIES_TYPES
        if self.board.exists(tag_type, stripped_value, parent_value or None, scoped):
            raise CatalogError(CatalogCode.TAG_ALREADY_EXISTS, tag_type=tag_type, value=value)
        return self.board.create(
            tag_type=tag_type,
            value=stripped_value,
            label_zh=stripped_label,
            parent_value=parent_value or None,
            brand_value=brand_value or None,
            sort_order=sort_order,
        )


class UpdateTag:
    """Renaming a value cascades to every software/document and child tag
    referencing the old value — the repository owns those updates."""

    def __init__(self, board: TagBoard) -> None:
        self.board = board

    def execute(self, tag_id: int, *, value: str | None = None, label_zh: str | None = None, is_active: bool | None = None, sort_order: int | None = None) -> TagEntry:
        tag = self.board.find(tag_id)
        if not tag:
            raise CatalogError(CatalogCode.TAG_NOT_FOUND)
        if value is not None:
            new_value = tag_rules.require_new_value(value)
            old_value = tag.value
            tag.value = new_value
            self.board.rename_references(old_value, new_value, tag.type)
        if label_zh is not None:
            tag.label_zh = tag_rules.require_new_label(label_zh)
        if is_active is not None:
            tag.is_active = is_active
        if sort_order is not None:
            tag.sort_order = sort_order
        return self.board.save(tag)


class DeleteTag:
    """A tag still referenced by documents/software cannot be deleted. An
    unused doc_category takes its child series along."""

    def __init__(self, board: TagBoard) -> None:
        self.board = board

    def execute(self, tag_id: int) -> None:
        tag = self.board.find(tag_id)
        if not tag:
            raise CatalogError(CatalogCode.TAG_NOT_FOUND)
        if self.board.in_use(tag):
            raise CatalogError(CatalogCode.TAG_IN_USE)
        if tag.type == "doc_category":
            self.board.delete_child_series(tag.value)
        self.board.delete(tag)
