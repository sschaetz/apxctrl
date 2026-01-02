"""
Data models for the APx Control Server.

Uses Pydantic for robust validation and serialization.
Simplified for 1:1 client-server model with sequential requests.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class APxState(str, Enum):
    """Current state of the APx application."""
    NOT_RUNNING = "not_running"
    STARTING = "starting"
    IDLE = "idle"  # APx is running, project loaded, ready for commands
    RUNNING_STEP = "running_step"
    ERROR = "error"


class ProjectInfo(BaseModel):
    """Information about the currently loaded project."""
    name: str
    file_path: str
    sha256: str
    loaded_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_file(cls, file_path: Path, name: Optional[str] = None) -> "ProjectInfo":
        """Create ProjectInfo from a file path, computing SHA256."""
        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        return cls(
            name=name or file_path.stem,
            file_path=str(file_path.resolve()),
            sha256=sha256,
        )


class ServerState(BaseModel):
    """Complete state of the APx Control Server."""
    apx_state: APxState = APxState.NOT_RUNNING
    project: Optional[ProjectInfo] = None
    apx_pid: Optional[int] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    server_started_at: datetime = Field(default_factory=datetime.now)

    def to_summary(self) -> dict:
        """Return a summary dict for API responses."""
        return {
            "apx_state": self.apx_state.value,
            "project_name": self.project.name if self.project else None,
            "project_sha256": self.project.sha256 if self.project else None,
            "project_path": self.project.file_path if self.project else None,
            "apx_pid": self.apx_pid,
            "last_error": self.last_error,
        }


# ============================================================================
# Request Models
# ============================================================================

class SetupRequest(BaseModel):
    """Metadata for setup request (file comes as multipart upload)."""
    project_name: Optional[str] = Field(
        default=None,
        description="Name to identify this project (defaults to filename)"
    )
    apx_mode: str = Field(
        default="SequenceMode",
        description="APx operating mode"
    )
    apx_args: str = Field(
        default="-Demo -APx517",
        description="Additional APx command line arguments"
    )


class RunStepRequest(BaseModel):
    """Request for running a sequence step."""
    sequence: str = Field(..., description="Name of the sequence to run")
    signal: str = Field(..., description="Name of the signal to use")
    timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=3600.0,
        description="Maximum time to wait for step completion (default: 2 min)"
    )


class GetResultsRequest(BaseModel):
    """Request for getting result files."""
    directory: str = Field(..., description="Directory path to zip and return")


class ShutdownRequest(BaseModel):
    """Request for shutdown endpoint."""
    force: bool = Field(
        default=False,
        description="Force kill APx process if graceful shutdown fails"
    )


# ============================================================================
# Response Models
# ============================================================================

class HealthResponse(BaseModel):
    """Response for health check endpoint."""
    status: str = "healthy"
    timestamp: datetime = Field(default_factory=datetime.now)
    apx_state: APxState
    uptime_seconds: float


class SetupResponse(BaseModel):
    """Response for setup endpoint."""
    success: bool
    message: str
    project_name: Optional[str] = None
    project_sha256: Optional[str] = None
    project_path: Optional[str] = None
    apx_state: APxState
    killed_processes: int = 0


class RunStepResponse(BaseModel):
    """Response for run step endpoint."""
    success: bool
    message: str
    sequence: Optional[str] = None
    signal: Optional[str] = None
    duration_seconds: Optional[float] = None
    apx_state: APxState


class GetResultsResponse(BaseModel):
    """Response metadata for results (stub)."""
    success: bool
    message: str


class ShutdownResponse(BaseModel):
    """Response for shutdown endpoint."""
    success: bool
    message: str
    apx_state: APxState


class ResetResponse(BaseModel):
    """Response for reset endpoint."""
    success: bool
    message: str
    killed_processes: int = 0
    apx_state: APxState


class StatusResponse(BaseModel):
    """Detailed status response."""
    apx_state: APxState
    project_name: Optional[str] = None
    project_sha256: Optional[str] = None
    project_path: Optional[str] = None
    apx_pid: Optional[int] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    server_started_at: datetime
    uptime_seconds: float
