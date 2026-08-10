from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class AgentPolicyProfileVersionModel(Base):
    __tablename__ = "agent_policy_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "profile_id",
            "version",
            name="uq_agent_policy_profile_versions_workspace_profile_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("agent_profile_version")
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class AgentPolicyProfileSelectionModel(Base):
    __tablename__ = "agent_policy_profile_selections"
    __table_args__ = (
        CheckConstraint(
            "source IN ('built_in', 'custom')",
            name="ck_agent_policy_profile_selections_source",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )
