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


# ============================================================================
# List Structure Models (Sequences -> Signal Paths -> Measurements)
# ============================================================================

class MeasurementInfo(BaseModel):
    """Information about a single measurement."""
    index: int
    name: str
    checked: bool


class SignalPathInfo(BaseModel):
    """Information about a signal path and its measurements."""
    index: int
    name: str
    checked: bool
    measurements: list[MeasurementInfo]


class SequenceInfo(BaseModel):
    """Information about a sequence and its signal paths."""
    index: int
    name: str
    signal_paths: list[SignalPathInfo]


class ListResponse(BaseModel):
    """Response containing the full project structure."""
    success: bool
    message: str
    sequences: list[SequenceInfo] = []
    active_sequence: Optional[str] = None
    total_sequences: int = 0
    total_signal_paths: int = 0
    total_measurements: int = 0
    apx_state: APxState


# ============================================================================
# Run Sequence Models
# ============================================================================

class RunSequenceRequest(BaseModel):
    """Request for running a sequence."""
    sequence_name: str = Field(..., description="Name of the sequence to run")
    test_run_id: str = Field(
        default="",
        description="Test run ID to associate with the test run (passed to APx as device ID)"
    )


class RunSequenceResponse(BaseModel):
    """Response for running a sequence."""
    success: bool
    message: str
    sequence_name: str
    test_run_id: str
    passed: bool = False
    duration_seconds: float = 0.0
    apx_state: APxState


# ============================================================================
# Get Result Models
# ============================================================================

class GetResultRequest(BaseModel):
    """Request for getting test results."""
    results_path: str = Field(
        ...,
        description="Path prefix to search for (e.g. C:\\apx-data\\ABCD-9483ur9sd)"
    )


class GetResultResponse(BaseModel):
    """Response for get-result endpoint (metadata only, file sent separately)."""
    success: bool
    message: str
    results_path: str
    directory_found: Optional[str] = None
    zip_size_bytes: int = 0


# ============================================================================
# Set User Defined Variable Models
# ============================================================================

class SetUserDefinedVariableRequest(BaseModel):
    """Request for setting a user-defined variable."""
    name: str = Field(..., description="Name of the user-defined variable")
    value: str = Field(..., description="Value to set for the variable")


class SetUserDefinedVariableResponse(BaseModel):
    """Response for setting a user-defined variable."""
    success: bool
    message: str
    name: str
    value: str
    apx_state: APxState


# ============================================================================
# Upload Data File Models
# ============================================================================

class UploadDataFileResponse(BaseModel):
    """Response for uploading a data file."""
    success: bool
    message: str
    filename: str
    subdirectory: str
    file_path: str
    size_bytes: int = 0
