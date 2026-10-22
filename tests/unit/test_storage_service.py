"""Unit tests for local FileStorageService — upload, download, delete, list."""
import hashlib
import io
from pathlib import Path

import pytest

from app.services.file_storage import FileStorageService


class TestFileStorageService:
    """Test the local filesystem storage backend."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch, temp_data_dir):
        """Override DATA_DIR to a temp directory and create a fresh FileStorageService."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "DATA_DIR", temp_data_dir)
        # Create a fresh instance that picks up the patched settings
        self.storage = FileStorageService()
        self.root = Path(temp_data_dir)

    def test_upload_file(self):
        """Upload should write file to disk and return metadata."""
        content = b"Hello, openIndu!"
        result = self.storage.upload_file(io.BytesIO(content), "test.pdf", "documents/siemens")

        assert result["oss_key"].startswith("documents/siemens/")
        assert result["oss_key"].endswith(".pdf")
        assert result["file_hash"] == hashlib.sha256(content).hexdigest()
        assert result["file_size"] == len(content)

        # Verify file actually exists on disk
        file_path = self.root / result["oss_key"]
        assert file_path.exists()
        assert file_path.read_bytes() == content

    def test_upload_file_no_extension(self):
        """Upload with no extension should default to .bin."""
        content = b"binary data"
        result = self.storage.upload_file(io.BytesIO(content), "noext", "data")

        assert result["oss_key"].endswith(".bin")

    def test_upload_file_preserves_prefix_structure(self):
        """Upload should create nested directories for prefix."""
        content = b"test"
        result = self.storage.upload_file(io.BytesIO(content), "manual.pdf", "documents/siemens/s7-1200")

        assert "documents/siemens/s7-1200/" in result["oss_key"]
        assert (self.root / result["oss_key"]).exists()

    def test_delete_file(self):
        """Delete should remove file from disk."""
        content = b"temporary content"
        result = self.storage.upload_file(io.BytesIO(content), "temp.pdf", "tmp")
        oss_key = result["oss_key"]

        assert (self.root / oss_key).exists()

        self.storage.delete_file(oss_key)
        assert not (self.root / oss_key).exists()

    def test_delete_nonexistent_file(self):
        """Deleting a nonexistent file should not raise an error."""
        self.storage.delete_file("nonexistent/file.pdf")

    def test_get_file_path(self):
        """get_file_path should return the full path to an existing file."""
        content = b"path test"
        result = self.storage.upload_file(io.BytesIO(content), "test.pdf", "uploads")
        oss_key = result["oss_key"]

        path = self.storage.get_file_path(oss_key)
        assert isinstance(path, Path)
        assert path.exists()
        assert path.read_bytes() == content

    def test_get_file_path_not_found(self):
        """get_file_path should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError, match="File not found"):
            self.storage.get_file_path("does/not/exist.pdf")

    def test_list_objects(self):
        """list_objects should return all files under a prefix."""
        self.storage.upload_file(io.BytesIO(b"file1"), "a.pdf", "prefix")
        self.storage.upload_file(io.BytesIO(b"file2"), "b.pdf", "prefix")
        self.storage.upload_file(io.BytesIO(b"file3"), "c.pdf", "other")

        result = self.storage.list_objects("prefix")
        assert len(result) == 2
        for obj in result:
            # On Windows, paths may use backslash; normalize for assertion
            key = obj["key"].replace("\\", "/")
            assert key.startswith("prefix/")
            assert "size" in obj
            assert "last_modified" in obj

    def test_list_objects_empty(self):
        """list_objects should return empty list for nonexistent prefix."""
        result = self.storage.list_objects("nonexistent")
        assert result == []
