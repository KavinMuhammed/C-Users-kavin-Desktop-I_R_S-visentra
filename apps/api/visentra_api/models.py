"""Relational schema. Every value the UI shows is read from these tables."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


TS = DateTime(timezone=True)


class TextEvidence(Base):
    """Extracted document evidence; never a defect prediction or aircraft identity."""
    __tablename__ = "text_evidence"
    __table_args__ = (
        UniqueConstraint("inspection_id", "sha256", name="uq_text_evidence_source"),
        CheckConstraint("status IN ('completed','no_text','failed')", name="ck_text_evidence_status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    document_revision: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    engine: Mapped[str] = mapped_column(String(120))
    pages: Mapped[list] = mapped_column(JSON, default=list)
    original_text: Mapped[str] = mapped_column(Text, default="")
    corrected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    project_type: Mapped[str] = mapped_column(String(40), default="image_classification")
    industry: Mapped[str] = mapped_column(String(80), default="")
    image_size: Mapped[int] = mapped_column(Integer, default=224)
    model_family: Mapped[str] = mapped_column(String(40), default="resnet18")
    training_strategy: Mapped[str] = mapped_column(String(40), default="transfer_learning")
    val_ratio: Mapped[float] = mapped_column(Float, default=0.15)
    test_ratio: Mapped[float] = mapped_column(Float, default=0.15)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    use_case: Mapped[str] = mapped_column(String(200), default="")
    production_model_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=now, onupdate=now)
    archived_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)

    classes: Mapped[list["ProjectClass"]] = relationship(order_by="ProjectClass.index", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | editor | reviewer | viewer
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class ProjectClass(Base):
    __tablename__ = "project_classes"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # upload | zip | huggingface | url_list
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    license: Mapped[str] = mapped_column(String(200), default="")
    license_url: Mapped[str] = mapped_column(String(500), default="")
    attribution: Mapped[str] = mapped_column(Text, default="")
    usage_restrictions: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle | running | success | partial | failed | unavailable
    images_collected: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class Job(Base):
    """Background job (collection, processing, training). Persisted so work survives restarts."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)  # collect | train | evaluate
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | running | success | partial | failed | cancelled
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    started_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=now, onupdate=now)


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (Index("ix_images_project_sha", "project_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    object_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    thumb_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    phash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    mime: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="valid", index=True)  # valid | invalid
    invalid_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    near_dup_of: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_dataset_id: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source_record_id: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    license: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


class ImageLabel(Base):
    __tablename__ = "image_labels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    image_id: Mapped[str] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("project_classes.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # import | manual | review
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    val_ratio: Mapped[float] = mapped_column(Float)
    test_ratio: Mapped[float] = mapped_column(Float)
    classes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    # immutable snapshot: manifest (JSON in object storage) + its SHA-256, frozen at finalization
    manifest_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    readiness_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class DatasetVersionItem(Base):
    __tablename__ = "dataset_version_items"
    version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[str] = mapped_column(String(32))
    class_index: Mapped[int] = mapped_column(Integer)
    split: Mapped[str] = mapped_column(String(8), index=True)  # train | val | test


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seed: Mapped[int] = mapped_column(Integer)
    device: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    environment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    model_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    started_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"))
    version: Mapped[int] = mapped_column(Integer)
    architecture: Mapped[str] = mapped_column(String(40))
    pretrained: Mapped[bool] = mapped_column(Boolean, default=False)
    classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    preprocessing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seed: Mapped[int] = mapped_column(Integer)
    framework: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    device: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    artifact_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED | TRAINING | COMPLETED | FAILED | ARCHIVED
    val_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"))
    split: Mapped[str] = mapped_column(String(8), default="test")
    n_samples: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    per_class: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confusion: Mapped[list[list[int]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(300))
    thumb_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    explanation_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    explanation_status: Mapped[str] = mapped_column(String(30), default="not_requested")
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    predicted_class: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float)
    topk: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    latency_ms: Mapped[float] = mapped_column(Float)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(20), default="none", index=True)
    threshold_used: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # AUTO_ACCEPT | REVIEW_REQUIRED
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | batch | device | camera
    device_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    explanation_method: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    explanation_layer: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    latency_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    prediction_id: Mapped[Optional[str]] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), nullable=True, index=True)
    image_id: Mapped[Optional[str]] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(40))  # low_confidence | manual | model_disagreement | quality_flag
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | in_review | escalated | accepted | corrected | rejected (uncertain: legacy escalation)
    corrected_class_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    reviewer_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_image_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    at: Mapped[datetime] = mapped_column(TS, default=now, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(20), default="success")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=now, onupdate=now)


# ====================================================================== workspaces & RBAC
class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    personal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    archived_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20))  # OWNER | ADMIN | ML_ENGINEER | REVIEWER | OPERATOR | VIEWER
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


# ====================================================================== readiness history
class ReadinessAnalysis(Base):
    """One readiness evaluation. Never overwritten: re-analysis creates a new row."""
    __tablename__ = "readiness_analyses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(10))  # project | version
    policy_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    blocking_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(TS, default=now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True, index=True)


# ====================================================================== model governance
class ModelPromotion(Base):
    """Append-only production history: promotions, rollbacks and demotions."""
    __tablename__ = "model_promotions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # promote | rollback | demote
    model_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    previous_model_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    checksum_verified: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


# ====================================================================== review history
class ReviewEvent(Base):
    __tablename__ = "review_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    review_id: Mapped[str] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(20))
    from_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


# ====================================================================== jobs: events, worker liveness
class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(TS, default=now)
    level: Mapped[str] = mapped_column(String(10), default="info")
    stage: Mapped[str] = mapped_column(String(40), default="")
    message: Mapped[str] = mapped_column(Text)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    host: Mapped[str] = mapped_column(String(120))
    pid: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(TS, default=now)
    heartbeat_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


# ====================================================================== notifications
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "read_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info | success | warning | error
    link: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)


# ====================================================================== devices
class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    device_type: Mapped[str] = mapped_column(String(30))  # USB_CAMERA | IP_CAMERA | RTSP_CAMERA | INDUSTRIAL_CAMERA | EDGE_DEVICE | CUSTOM_DEVICE
    adapter: Mapped[str] = mapped_column(String(30), default="agent")
    status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED", index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    model_policy: Mapped[str] = mapped_column(String(20), default="production")  # production | pinned | none
    model_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    token_prefix: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    last_telemetry: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    telemetry_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=now)
    updated_at: Mapped[datetime] = mapped_column(TS, default=now, onupdate=now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class DeviceEvent(Base):
    __tablename__ = "device_events"
    __table_args__ = (UniqueConstraint("device_id", "event_id", name="uq_device_event"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(30))
    device_time: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TS, default=now, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prediction_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


# ====================================================================== idempotency
class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    scope: Mapped[str] = mapped_column(String(120))  # "<user|device>:<id>:<endpoint>"
    key: Mapped[str] = mapped_column(String(120))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Phase 10: SHA-256 of the request (method, path, body digest). The same key with a different request is
    # refused (409 IDEMPOTENCY_CONFLICT). NULL on rows written before Phase 10 (they replay as before).
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


# ====================================================================== inspections (Phase 10 = Phase 5 release R2)
# Implements docs/architecture/schema/010_new_tables.sql (inspection_ref_counters, inspections, inspection_media,
# media_renditions, storage_objects) column for column. Status changes go only through inspection/lifecycle.py.
JSONX = JSON().with_variant(JSONB(), "postgresql")
COMPONENTS = ("fuselage", "wing_upper", "wing_lower", "empennage", "nacelle", "control_surface", "landing_gear", "other")
INSPECTION_STATUSES = ("created", "analysing", "analysis_failed", "awaiting_review", "in_review", "reviewed")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class InspectionRefCounter(Base):
    __tablename__ = "inspection_ref_counters"
    __table_args__ = (CheckConstraint("year BETWEEN 2000 AND 2999", name="ck_refctr_year"),
                      CheckConstraint("last_value BETWEEN 0 AND 999999", name="ck_refctr_value"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class Inspection(Base):
    __tablename__ = "inspections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "ref", name="uq_inspections_ws_ref"),
        CheckConstraint("length(ref) = 15", name="ck_inspections_ref"),
        CheckConstraint(_in("component", COMPONENTS), name="ck_inspections_component"),
        CheckConstraint(_in("status", INSPECTION_STATUSES), name="ck_inspections_status"),
        CheckConstraint(_in("analysis_mode", ("model", "none")), name="ck_inspections_mode"),
        CheckConstraint("revision >= 0", name="ck_inspections_revision"),
        CheckConstraint("signoff_outcome IS NULL OR " + _in("signoff_outcome", ("findings_recorded", "no_confirmed_findings")), name="ck_inspections_outcome"),
        CheckConstraint("(status = 'reviewed') = (signed_off_at IS NOT NULL AND signed_off_by IS NOT NULL AND signoff_outcome IS NOT NULL)", name="ck_inspections_signoff"),
        CheckConstraint("status <> 'analysis_failed' OR last_error_code IS NOT NULL", name="ck_inspections_failure"),
        CheckConstraint("(deleted_at IS NULL) = (deleted_by IS NULL)", name="ck_inspections_deleted"),
        Index("ix_inspections_ws_created", "workspace_id", "created_at", sqlite_where=text("deleted_at IS NULL"), postgresql_where=text("deleted_at IS NULL")),
        Index("ix_inspections_ws_status", "workspace_id", "status", "created_at", sqlite_where=text("deleted_at IS NULL"), postgresql_where=text("deleted_at IS NULL")),
        Index("ix_inspections_creator", "created_by", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    ref: Mapped[str] = mapped_column(String(20))
    component: Mapped[str] = mapped_column(String(30))
    zone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    aircraft_ref: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    notes: Mapped[str] = mapped_column(String(1000), default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(String(20))
    analysis_mode: Mapped[str] = mapped_column(String(10), default="none", server_default=text("'none'"))
    last_error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    signed_off_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    signed_off_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    signoff_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    signoff_outcome: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    deleted_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)


class InspectionMedia(Base):
    __tablename__ = "inspection_media"
    __table_args__ = (
        UniqueConstraint("inspection_id", "ordinal", name="uq_media_inspection_ordinal"),
        UniqueConstraint("id", "inspection_id", name="uq_media_id_inspection"),
        CheckConstraint("ordinal >= 1", name="ck_media_ordinal"),
        CheckConstraint(_in("kind", ("image", "video", "video_frame")), name="ck_media_kind"),
        CheckConstraint("frame_time_ms IS NULL OR frame_time_ms >= 0", name="ck_media_frame_time"),
        CheckConstraint(_in("mime", ("image/jpeg", "image/png", "image/webp", "video/mp4")), name="ck_media_mime"),
        CheckConstraint("size_bytes > 0", name="ck_media_size"),
        CheckConstraint("(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)", name="ck_media_positive_dims"),
        CheckConstraint("duration_ms IS NULL OR duration_ms > 0", name="ck_media_duration"),
        CheckConstraint("length(sha256) = 64", name="ck_media_sha"),
        CheckConstraint(_in("source", ("upload", "device", "video_extraction")), name="ck_media_source"),
        CheckConstraint(_in("integrity", ("ok", "missing", "checksum_mismatch")), name="ck_media_integrity"),
        CheckConstraint(_in("retention_state", ("active", "purged")), name="ck_media_retention"),
        CheckConstraint("(kind = 'video_frame') = (parent_media_id IS NOT NULL AND frame_time_ms IS NOT NULL)", name="ck_media_frame"),
        CheckConstraint("kind = 'video' OR (width IS NOT NULL AND height IS NOT NULL)", name="ck_media_dims"),
        CheckConstraint("(kind = 'video') = (mime = 'video/mp4')", name="ck_media_video"),
        CheckConstraint("(retention_state = 'purged') = (purged_at IS NOT NULL)", name="ck_media_purge"),
        Index("ix_media_inspection", "inspection_id"),
        Index("ix_media_ws_sha", "workspace_id", "sha256"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))
    parent_media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("inspection_media.id", ondelete="RESTRICT"), nullable=True)
    frame_time_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(300), unique=True)
    capture: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict, server_default=text("'{}'"))
    quality: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict, server_default=text("'{}'"))
    source: Mapped[str] = mapped_column(String(20), default="upload", server_default=text("'upload'"))
    device_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    integrity: Mapped[str] = mapped_column(String(20), default="ok", server_default=text("'ok'"))
    integrity_checked_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    retention_state: Mapped[str] = mapped_column(String(10), default="active", server_default=text("'active'"))
    purged_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class MediaRendition(Base):
    __tablename__ = "media_renditions"
    __table_args__ = (
        CheckConstraint(_in("kind", ("display", "thumb", "evidence_crop")), name="ck_rendition_kind"),
        CheckConstraint(_in("mime", ("image/jpeg", "image/png")), name="ck_rendition_mime"),
        CheckConstraint("length(sha256) = 64", name="ck_rendition_sha"),
        CheckConstraint("size_bytes > 0 AND width > 0 AND height > 0", name="ck_rendition_positive"),
        CheckConstraint("(kind = 'evidence_crop') = (crop IS NOT NULL)", name="ck_rendition_crop"),
        Index("ux_rendition_single", "media_id", "kind", unique=True,
              sqlite_where=text("kind IN ('display', 'thumb')"), postgresql_where=text("kind IN ('display', 'thumb')")),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    media_id: Mapped[str] = mapped_column(ForeignKey("inspection_media.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(String(300), unique=True)
    mime: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    crop: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONX, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))


STORAGE_STATES = ("pending", "committed", "delete_pending", "deleted", "missing")


class StorageObject(Base):
    """Registry and write-intent log of every object written by the inspection module (DATA_FLOW_AND_CONSISTENCY §3)."""
    __tablename__ = "storage_objects"
    __table_args__ = (
        CheckConstraint(_in("store", ("objects", "models")), name="ck_storage_store"),
        CheckConstraint(_in("purpose", ("media_original", "media_rendition", "report", "dataset_media", "manifest", "model_artifact", "model_card")), name="ck_storage_purpose"),
        CheckConstraint(_in("state", STORAGE_STATES), name="ck_storage_state"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_storage_sha"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_storage_size"),
        CheckConstraint("state NOT IN ('committed', 'delete_pending') OR (committed_at IS NOT NULL AND sha256 IS NOT NULL AND owner_id IS NOT NULL)", name="ck_storage_committed"),
        CheckConstraint("(state = 'deleted') = (deleted_at IS NOT NULL)", name="ck_storage_deleted"),
        Index("ix_storage_state_created", "state", "created_at",
              sqlite_where=text("state IN ('pending', 'delete_pending', 'missing')"), postgresql_where=text("state IN ('pending', 'delete_pending', 'missing')")),
        Index("ix_storage_owner", "owner_type", "owner_id"),
    )
    object_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    store: Mapped[str] = mapped_column(String(10))
    purpose: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(15))
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    owner_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    committed_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


# ====================================================================== experimental runs (Phase 10, ADR-014)
# Output of the development-only experimental model (Phase 8, VT-DET-0001). Deliberately NOT analysis_runs /
# defect_predictions: those hold operational model output and require a model that passed VT-GATE-OP-01.
# The CHECKs make an experimental row unable to claim operational use.
class ExperimentalRun(Base):
    __tablename__ = "experimental_runs"
    __table_args__ = (
        CheckConstraint(_in("source", ("upload", "inspection_media")), name="ck_exprun_source"),
        CheckConstraint("(source = 'inspection_media') = (inspection_id IS NOT NULL AND media_id IS NOT NULL)", name="ck_exprun_link"),
        CheckConstraint(_in("status", ("completed", "failed")), name="ck_exprun_status"),
        CheckConstraint("outcome IS NULL OR " + _in("outcome", ("detections", "no_detections")), name="ck_exprun_outcome_values"),
        CheckConstraint("(status = 'completed') = (outcome IS NOT NULL)", name="ck_exprun_outcome"),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="ck_exprun_error"),
        CheckConstraint("experimental AND NOT operational_use", name="ck_exprun_experimental"),
        CheckConstraint("prediction_count >= 0", name="ck_exprun_count"),
        CheckConstraint("image_sha256 IS NULL OR length(image_sha256) = 64", name="ck_exprun_sha"),
        ForeignKeyConstraint(["media_id", "inspection_id"], ["inspection_media.id", "inspection_media.inspection_id"], name="fk_exprun_media_inspection", ondelete="RESTRICT"),
        Index("ix_exprun_user_created", "user_id", "created_at"),
        Index("ix_exprun_inspection", "inspection_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=True)
    inspection_id: Mapped[Optional[str]] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"), nullable=True)
    media_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(40))
    model_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    weights_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    readiness: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    experimental: Mapped[bool] = mapped_column(Boolean, default=True)
    operational_use: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10))
    outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    image_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    image_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    image_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    image_format: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    prediction_count: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict)
    timing_ms: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    audit_event_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=now)


class ExperimentalPrediction(Base):
    __tablename__ = "experimental_predictions"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_exppred_run_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_exppred_ordinal"),
        CheckConstraint("kind = 'ai_suggestion'", name="ck_exppred_kind"),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_exppred_score"),
        CheckConstraint("x_min >= 0 AND y_min >= 0 AND x_max > x_min AND y_max > y_min", name="ck_exppred_box"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("experimental_runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    class_id: Mapped[int] = mapped_column(Integer)
    class_name: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20), default="ai_suggestion")
    score: Mapped[float] = mapped_column(Float)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    x_min: Mapped[float] = mapped_column(Float)
    y_min: Mapped[float] = mapped_column(Float)
    x_max: Mapped[float] = mapped_column(Float)
    y_max: Mapped[float] = mapped_column(Float)
    clipped: Mapped[bool] = mapped_column(Boolean, default=False)


# ====================================================================== review and reports (Phase 11 = rest of Phase 5 release R2)
# Implements docs/architecture/schema/010_new_tables.sql (human_findings, finding_decisions, inspection_reports) and, as
# schema only, the R3 tables they reference (analysis_runs, analysis_media, defect_predictions). No product code writes the
# R3 tables: no operational detection model exists (VT-GATE-OP-01). Append-only rules: ORM guards below + DB triggers.
REVIEW_CATEGORIES = ("crack", "dent", "corrosion", "other")
DECISIONS = ("pending", "confirmed", "rejected", "further_review")
DECISION_KINDS = ("decision", "reclassify", "note", "delete", "signoff", "reopen")
REPORT_STATUSES = ("queued", "generating", "available", "failed")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("inspection_id", "attempt", name="uq_run_attempt"),
        UniqueConstraint("id", "inspection_id", "model_version_id", name="uq_run_provenance"),
        CheckConstraint("attempt >= 1", name="ck_run_attempt"),
        CheckConstraint("model_checksum IS NULL OR length(model_checksum) = 64", name="ck_run_checksum_len"),
        CheckConstraint("score_threshold >= 0 AND score_threshold <= 1", name="ck_run_score"),
        CheckConstraint("iou_threshold > 0 AND iou_threshold <= 1", name="ck_run_iou"),
        CheckConstraint("review_threshold >= 0 AND review_threshold <= 1", name="ck_run_review"),
        CheckConstraint(_in("status", ("queued", "running", "succeeded", "failed", "cancelled")), name="ck_run_status"),
        CheckConstraint("(status IN ('failed', 'cancelled')) = (error_code IS NOT NULL)", name="ck_run_error"),
        CheckConstraint("(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)", name="ck_run_finished"),
        CheckConstraint("status <> 'succeeded' OR model_checksum IS NOT NULL", name="ck_run_checksum"),
        Index("ux_run_one_active", "inspection_id", unique=True,
              sqlite_where=text("status IN ('queued', 'running')"), postgresql_where=text("status IN ('queued', 'running')")),
        Index("ix_run_inspection", "inspection_id", "attempt"),
        Index("ix_run_model", "model_version_id", "finished_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    attempt: Mapped[int] = mapped_column(Integer)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"))
    model_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    taxonomy_version: Mapped[str] = mapped_column(String(20))
    score_threshold: Mapped[float] = mapped_column(Float)
    iou_threshold: Mapped[float] = mapped_column(Float)
    review_threshold: Mapped[float] = mapped_column(Float)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(12))
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    suppressed: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict, server_default=text("'{}'"))
    timings: Mapped[dict[str, Any]] = mapped_column(JSONX, default=dict, server_default=text("'{}'"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    started_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class AnalysisMedia(Base):
    __tablename__ = "analysis_media"
    __table_args__ = (
        CheckConstraint("oriented_width > 0 AND oriented_height > 0 AND input_width > 0 AND input_height > 0", name="ck_am_dims"),
        CheckConstraint(_in("resize_mode", ("letterbox", "stretch")), name="ck_am_resize"),
        CheckConstraint("scale_x > 0 AND scale_y > 0 AND pad_x >= 0 AND pad_y >= 0", name="ck_am_scale"),
        CheckConstraint("exif_orientation BETWEEN 1 AND 8", name="ck_am_orientation"),
        CheckConstraint(_in("status", ("succeeded", "failed")), name="ck_am_status"),
        CheckConstraint("n_predictions >= 0", name="ck_am_count"),
        CheckConstraint("inference_ms IS NULL OR inference_ms >= 0", name="ck_am_ms"),
    )
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("inspection_media.id", ondelete="RESTRICT"), primary_key=True)
    oriented_width: Mapped[int] = mapped_column(Integer)
    oriented_height: Mapped[int] = mapped_column(Integer)
    input_width: Mapped[int] = mapped_column(Integer)
    input_height: Mapped[int] = mapped_column(Integer)
    resize_mode: Mapped[str] = mapped_column(String(10))
    scale_x: Mapped[float] = mapped_column(Float)
    scale_y: Mapped[float] = mapped_column(Float)
    pad_x: Mapped[float] = mapped_column(Float, default=0, server_default=text("0"))
    pad_y: Mapped[float] = mapped_column(Float, default=0, server_default=text("0"))
    exif_orientation: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(10))
    n_predictions: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    inference_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class DefectPrediction(Base):
    """ENT-05: an immutable AI proposal. A reviewer's decision is a finding_decisions row, never an update here."""
    __tablename__ = "defect_predictions"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "media_id", "ordinal", name="uq_pred_ordinal"),
        UniqueConstraint("id", "inspection_id", name="uq_pred_inspection"),
        ForeignKeyConstraint(["analysis_run_id", "inspection_id", "model_version_id"], ["analysis_runs.id", "analysis_runs.inspection_id", "analysis_runs.model_version_id"],
                             name="fk_pred_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(["media_id", "inspection_id"], ["inspection_media.id", "inspection_media.inspection_id"], name="fk_pred_media", ondelete="RESTRICT"),
        CheckConstraint("ordinal >= 1", name="ck_pred_ordinal"),
        CheckConstraint("model_class_index >= 0", name="ck_pred_class_index"),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_pred_score"),
        CheckConstraint("box_w > 0 AND box_h > 0 AND box_x >= 0 AND box_y >= 0 AND box_x + box_w <= 1.000001 AND box_y + box_h <= 1.000001", name="ck_pred_box"),
        Index("ix_pred_inspection", "inspection_id", "analysis_run_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(String(32))
    analysis_run_id: Mapped[str] = mapped_column(String(32))
    model_version_id: Mapped[str] = mapped_column(String(32))
    media_id: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer)
    class_id: Mapped[str] = mapped_column(String(40))
    model_class_index: Mapped[int] = mapped_column(Integer)
    taxonomy_version: Mapped[str] = mapped_column(String(20))
    score: Mapped[float] = mapped_column(Float)
    box_x: Mapped[float] = mapped_column(Float)
    box_y: Mapped[float] = mapped_column(Float)
    box_w: Mapped[float] = mapped_column(Float)
    box_h: Mapped[float] = mapped_column(Float)
    box_input_xyxy: Mapped[list[float]] = mapped_column(JSONX)
    clipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    mask: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONX, nullable=True)
    low_confidence: Mapped[bool] = mapped_column(Boolean)
    predicted_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))


class HumanFinding(Base):
    """ENT-06: a reviewer-drawn region. Creation and deletion are both finding_decisions events."""
    __tablename__ = "human_findings"
    __table_args__ = (
        UniqueConstraint("id", "inspection_id", name="uq_hf_inspection"),
        ForeignKeyConstraint(["media_id", "inspection_id"], ["inspection_media.id", "inspection_media.inspection_id"], name="fk_hf_media", ondelete="RESTRICT"),
        CheckConstraint(_in("category", REVIEW_CATEGORIES), name="ck_hf_category"),
        CheckConstraint("box_w > 0 AND box_h > 0 AND box_x >= 0 AND box_y >= 0 AND box_x + box_w <= 1.000001 AND box_y + box_h <= 1.000001", name="ck_hf_box"),
        CheckConstraint("(deleted_at IS NULL) = (deleted_by IS NULL)", name="ck_hf_deleted"),
        Index("ix_hf_inspection", "inspection_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(String(32))
    media_id: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(40))
    taxonomy_version: Mapped[str] = mapped_column(String(20))
    box_x: Mapped[float] = mapped_column(Float)
    box_y: Mapped[float] = mapped_column(Float)
    box_w: Mapped[float] = mapped_column(Float)
    box_h: Mapped[float] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    deleted_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)


class FindingDecision(Base):
    """ENT-07/08: one append-only review event. Each carries the inspection revision it created (unique per inspection)."""
    __tablename__ = "finding_decisions"
    __table_args__ = (
        UniqueConstraint("inspection_id", "inspection_revision", name="uq_fd_revision"),
        ForeignKeyConstraint(["prediction_id", "inspection_id"], ["defect_predictions.id", "defect_predictions.inspection_id"], name="fk_fd_prediction", ondelete="RESTRICT"),
        ForeignKeyConstraint(["human_finding_id", "inspection_id"], ["human_findings.id", "human_findings.inspection_id"], name="fk_fd_human", ondelete="RESTRICT"),
        CheckConstraint(_in("target_type", ("prediction", "human_finding", "inspection")), name="ck_fd_target_type"),
        CheckConstraint(_in("kind", DECISION_KINDS), name="ck_fd_kind"),
        CheckConstraint("decision IS NULL OR " + _in("decision", DECISIONS), name="ck_fd_decision_values"),
        CheckConstraint("corrected_class_id IS NULL OR " + _in("corrected_class_id", REVIEW_CATEGORIES), name="ck_fd_class"),
        CheckConstraint("inspection_revision >= 1", name="ck_fd_revision"),
        CheckConstraint("(target_type = 'prediction' AND prediction_id IS NOT NULL AND human_finding_id IS NULL) OR "
                        "(target_type = 'human_finding' AND human_finding_id IS NOT NULL AND prediction_id IS NULL) OR "
                        "(target_type = 'inspection' AND prediction_id IS NULL AND human_finding_id IS NULL)", name="ck_fd_target"),
        CheckConstraint("(kind IN ('signoff', 'reopen')) = (target_type = 'inspection')", name="ck_fd_kind_target"),
        CheckConstraint("(kind IN ('decision', 'reclassify')) = (decision IS NOT NULL)", name="ck_fd_decision"),
        CheckConstraint("(kind = 'reclassify') = (corrected_class_id IS NOT NULL) AND (kind <> 'reclassify' OR (target_type = 'prediction' AND decision = 'confirmed'))",
                        name="ck_fd_reclassify"),
        CheckConstraint("target_type <> 'human_finding' OR decision IS NULL OR decision <> 'pending'", name="ck_fd_human_never_pending"),
        CheckConstraint("kind <> 'note' OR comment IS NOT NULL", name="ck_fd_note"),
        Index("ix_fd_prediction", "prediction_id", sqlite_where=text("prediction_id IS NOT NULL"), postgresql_where=text("prediction_id IS NOT NULL")),
        Index("ix_fd_human", "human_finding_id", sqlite_where=text("human_finding_id IS NOT NULL"), postgresql_where=text("human_finding_id IS NOT NULL")),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"))
    target_type: Mapped[str] = mapped_column(String(15))
    prediction_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    human_finding_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(12))
    decision: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    corrected_class_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(600), nullable=True)
    previous: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONX, nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    inspection_revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class InspectionReport(Base):
    """ENT-09: a server-generated report pinned to one inspection revision; frozen once available (ADR-011)."""
    __tablename__ = "inspection_reports"
    __table_args__ = (
        CheckConstraint("inspection_revision >= 1", name="ck_report_revision"),
        CheckConstraint("format IN ('pdf')", name="ck_report_format"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="ck_report_status"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_report_sha"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ck_report_size"),
        CheckConstraint("pages IS NULL OR pages > 0", name="ck_report_pages"),
        CheckConstraint("contains_sample = false", name="ck_report_no_sample"),
        CheckConstraint("purged_at IS NULL OR status = 'available'", name="ck_report_purge"),
        CheckConstraint("(status = 'available') = (object_key IS NOT NULL AND sha256 IS NOT NULL AND size_bytes IS NOT NULL AND generated_at IS NOT NULL)",
                        name="ck_report_available"),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="ck_report_failed"),
        CheckConstraint("(analysis_run_id IS NULL) = (model_version_id IS NULL)", name="ck_report_model"),
        Index("ux_report_per_revision", "inspection_id", "inspection_revision", "template_version", unique=True,
              sqlite_where=text("status IN ('queued', 'generating', 'available')"), postgresql_where=text("status IN ('queued', 'generating', 'available')")),
        Index("ix_report_inspection", "inspection_id", "requested_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    inspection_id: Mapped[str] = mapped_column(ForeignKey("inspections.id", ondelete="RESTRICT"))
    inspection_revision: Mapped[int] = mapped_column(Integer)
    template_version: Mapped[str] = mapped_column(String(20))
    format: Mapped[str] = mapped_column(String(5), default="pdf", server_default=text("'pdf'"))
    status: Mapped[str] = mapped_column(String(12))
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    analysis_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("analysis_runs.id", ondelete="RESTRICT"), nullable=True)
    model_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=True)
    media_sha256: Mapped[list[dict[str, Any]]] = mapped_column(JSONX, default=list, server_default=text("'[]'"))
    object_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, unique=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contains_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    error_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(TS, default=now, server_default=text("CURRENT_TIMESTAMP"))
    generated_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    purged_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


# ---- append-only guards (every engine; the migration adds the same rules as database triggers) ----
from sqlalchemy import event as _event  # noqa: E402


class ImmutableRecordError(Exception):
    pass


@_event.listens_for(FindingDecision, "before_update")
@_event.listens_for(FindingDecision, "before_delete")
@_event.listens_for(DefectPrediction, "before_update")
@_event.listens_for(DefectPrediction, "before_delete")
def _append_only(mapper, connection, target):
    raise ImmutableRecordError(f"VT_IMMUTABLE: {mapper.local_table.name} rows are append-only")


@_event.listens_for(InspectionReport, "before_delete")
def _report_no_delete(mapper, connection, target):
    raise ImmutableRecordError("VT_IMMUTABLE: report rows are never deleted")
