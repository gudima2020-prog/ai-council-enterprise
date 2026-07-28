from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class HumanControlBackupCreate(BaseModel):
    backup_key: str = Field(min_length=3, max_length=160)
    workspace_id: str | None = None
    created_by: str
    include_database: bool = True
    include_evidence: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

class HumanControlRestoreRequest(BaseModel):
    requested_by: str
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=3, max_length=255)
    dry_run: bool = True
    force: bool = False
