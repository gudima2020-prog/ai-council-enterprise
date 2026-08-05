from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets


class DocumentStorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StoredDocument:
    storage_key: str
    path: Path
    size_bytes: int
    content_sha256: str
    created: bool


class ManagedDocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    @classmethod
    def from_environment(
        cls,
        project_root: Path,
    ) -> "ManagedDocumentStorage":
        configured = os.getenv(
            "AI_STUDIO_DOCUMENT_STORAGE_ROOT",
            "",
        ).strip()
        root = (
            Path(configured)
            if configured
            else project_root / "data" / "documents"
        )
        return cls(root)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def storage_key(
        content_sha256: str,
        extension: str,
    ) -> str:
        digest = content_sha256.casefold()
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_HASH_INVALID",
                "Document SHA-256 is invalid.",
            )
        normalized_extension = extension.strip().casefold()
        if (
            not normalized_extension
            or len(normalized_extension) > 16
            or not normalized_extension.isalnum()
        ):
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_EXTENSION_INVALID",
                "Document extension is invalid.",
            )
        return (
            f"sha256/{digest[:2]}/{digest[2:4]}/"
            f"{digest}.{normalized_extension}"
        )

    def put(
        self,
        *,
        content: bytes,
        expected_sha256: str,
        extension: str,
    ) -> StoredDocument:
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256.casefold():
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_HASH_MISMATCH",
                "Document content does not match the expected SHA-256.",
            )

        storage_key = self.storage_key(
            actual_sha256,
            extension,
        )
        path = self.resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            self._verify_file(
                path,
                expected_sha256=actual_sha256,
                expected_size=len(content),
            )
            return StoredDocument(
                storage_key=storage_key,
                path=path,
                size_bytes=len(content),
                content_sha256=actual_sha256,
                created=False,
            )

        temp_path = path.with_name(
            f".{path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            with temp_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temp_path.chmod(0o600)
            except OSError:
                pass
            os.replace(temp_path, path)
        except FileExistsError:
            temp_path.unlink(missing_ok=True)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        self._verify_file(
            path,
            expected_sha256=actual_sha256,
            expected_size=len(content),
        )
        return StoredDocument(
            storage_key=storage_key,
            path=path,
            size_bytes=len(content),
            content_sha256=actual_sha256,
            created=True,
        )

    def read_verified(
        self,
        *,
        storage_key: str,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        path = self.resolve(storage_key)
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_MISSING",
                "Managed document content is missing.",
            ) from exc

        if len(content) != expected_size:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_SIZE_MISMATCH",
                "Managed document size does not match the registry.",
            )
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_HASH_MISMATCH",
                "Managed document hash does not match the registry.",
            )
        return content

    def delete(
        self,
        *,
        storage_key: str,
        expected_sha256: str,
    ) -> bool:
        path = self.resolve(storage_key)
        if not path.exists():
            return False
        self._verify_file(
            path,
            expected_sha256=expected_sha256,
            expected_size=None,
        )
        path.unlink()
        self._remove_empty_parents(path.parent)
        return True

    def exists(self, storage_key: str) -> bool:
        return self.resolve(storage_key).is_file()

    def resolve(self, storage_key: str) -> Path:
        if not storage_key or "\\" in storage_key:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_KEY_UNSAFE",
                "Document storage key is unsafe.",
            )
        pure = PurePosixPath(storage_key)
        if (
            pure.is_absolute()
            or any(
                part in {"", ".", ".."}
                for part in pure.parts
            )
            or pure.parts[0] != "sha256"
        ):
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_KEY_UNSAFE",
                "Document storage key is unsafe.",
            )

        candidate = self._root.joinpath(*pure.parts).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_KEY_UNSAFE",
                "Document storage key escapes the managed root.",
            ) from exc
        return candidate

    @staticmethod
    def _verify_file(
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int | None,
    ) -> None:
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_MISSING",
                "Managed document content is missing.",
            ) from exc
        if expected_size is not None and len(content) != expected_size:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_SIZE_MISMATCH",
                "Managed document size is inconsistent.",
            )
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise DocumentStorageError(
                "DOCUMENT_STORAGE_HASH_MISMATCH",
                "Managed document hash is inconsistent.",
            )

    def _remove_empty_parents(self, start: Path) -> None:
        current = start
        while current != self._root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
