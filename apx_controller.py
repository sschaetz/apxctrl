"""
APx500 Controller Module.

Handles APx500 process lifecycle management and .NET API interactions.
Designed for robustness in factory environments (thousands of runs/day).
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import APxState, ProjectInfo, ServerState

logger = logging.getLogger(__name__)

# APx500 process name (without .exe)
APX_PROCESS_NAME = "APx500"


@dataclass
class RunStepResult:
    """Result of running a sequence step."""
    success: bool
    sequence: str
    signal: str
    started_at: datetime
    completed_at: datetime
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()


class APxController:
    """
    Controller for APx500 application.
    
    Manages the APx500 process lifecycle and provides a clean interface
    to the .NET API. Thread-safe for the simple 1:1 client model.
    """
    
    def __init__(self, state: ServerState) -> None:
        """
        Initialize the controller.
        
        Args:
            state: Shared server state object
        """
        self._state = state
        self._apx_instance = None  # Will hold the .NET APx500_Application object
        self._clr_initialized = False
    
    def _init_clr(self) -> None:
        """Initialize the CLR and load APx assemblies (lazy loading)."""
        if self._clr_initialized:
            return
        
        try:
            import clr
            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
            clr.AddReference(
                r"C:\Program Files\Audio Precision\APx500 9.1\API\AudioPrecision.API2.dll"
            )
            clr.AddReference(
                r"C:\Program Files\Audio Precision\APx500 9.1\API\AudioPrecision.API.dll"
            )
            self._clr_initialized = True
            logger.info("CLR and APx assemblies loaded successfully")
        except Exception as e:
            logger.error(f"Failed to initialize CLR: {e}")
            raise RuntimeError(f"Failed to initialize CLR/APx assemblies: {e}") from e
    
    def kill_existing_apx_processes(self) -> int:
        """
        Kill all running APx500 processes.
        
        Returns:
            Number of processes killed
        """
        killed = 0
        try:
            # Use taskkill on Windows to force-kill APx500 processes
            result = subprocess.run(
                ["taskkill", "/F", "/IM", f"{APX_PROCESS_NAME}.exe"],
                capture_output=True,
                text=True,
            )
            # taskkill returns 0 on success, 128 if no processes found
            if result.returncode == 0:
                # Count killed processes from output
                # Output format: "SUCCESS: The process ... has been terminated."
                killed = result.stdout.count("SUCCESS:")
                logger.info(f"Killed {killed} existing APx500 process(es)")
            elif result.returncode == 128:
                logger.info("No existing APx500 processes to kill")
            else:
                logger.warning(f"taskkill returned {result.returncode}: {result.stderr}")
        except Exception as e:
            logger.error(f"Error killing APx500 processes: {e}")
        
        # Also clear our internal reference
        self._apx_instance = None
        self._state.apx_state = APxState.NOT_RUNNING
        self._state.apx_pid = None
        self._state.project = None
        
        return killed
    
    def is_apx_process_running(self) -> bool:
        """Check if the APx500 process is still running."""
        if self._state.apx_pid is None:
            return False
        
        try:
            # Use tasklist to check if process with our PID exists
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {self._state.apx_pid}"],
                capture_output=True,
                text=True,
            )
            return APX_PROCESS_NAME in result.stdout
        except Exception as e:
            logger.error(f"Error checking APx process status: {e}")
            return False
    
    def launch_apx(
        self,
        project_path: Path,
        project_name: Optional[str] = None,
        apx_mode: str = "SequenceMode",
        apx_args: str = "-Demo -APx517",
    ) -> bool:
        """
        Launch APx500 and open a project file.
        
        Args:
            project_path: Path to the .approjx project file
            project_name: Optional name for the project (defaults to filename)
            apx_mode: APx operating mode (default: SequenceMode)
            apx_args: Additional APx command line arguments
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self._state.apx_state = APxState.STARTING
            logger.info(f"Launching APx500 with project: {project_path}")
            
            # Initialize CLR if needed
            self._init_clr()
            
            # Import APx types after CLR is initialized
            from AudioPrecision.API import APx500_Application, APxOperatingMode
            
            # Determine the operating mode
            mode = getattr(APxOperatingMode, apx_mode, APxOperatingMode.SequenceMode)
            
            # Create APx application instance
            logger.info(f"Creating APx500_Application with mode={apx_mode}, args={apx_args}")
            self._apx_instance = APx500_Application(mode, apx_args)
            self._apx_instance.Visible = True
            
            # Try to get the process ID
            try:
                # The APx API might expose the process ID
                # If not available, we'll skip this
                self._state.apx_pid = None  # TODO: Find way to get PID from APx API
            except Exception:
                self._state.apx_pid = None
            
            # Open the project
            project_path_str = str(project_path.resolve())
            logger.info(f"Opening project: {project_path_str}")
            self._apx_instance.OpenProject(project_path_str)
            
            # Create project info with SHA256
            self._state.project = ProjectInfo.from_file(project_path, project_name)
            self._state.apx_state = APxState.IDLE
            self._state.last_error = None
            
            logger.info(
                f"APx500 launched successfully. "
                f"Project: {self._state.project.name}, "
                f"SHA256: {self._state.project.sha256}"
            )
            return True
            
        except Exception as e:
            error_msg = f"Failed to launch APx500: {e}"
            logger.error(error_msg)
            self._state.apx_state = APxState.ERROR
            self._state.last_error = error_msg
            self._state.last_error_at = datetime.now()
            self._apx_instance = None
            return False
    
    def run_step(
        self,
        sequence: str,
        signal: str,
        timeout_seconds: float = 120.0,
    ) -> RunStepResult:
        """
        Run a sequence/signal step.
        
        Args:
            sequence: Name of the sequence to run
            signal: Name of the signal to use
            timeout_seconds: Maximum time to wait for completion
            
        Returns:
            RunStepResult with success status and timing information
        """
        started_at = datetime.now()
        
        # Validate state
        if self._state.apx_state != APxState.IDLE:
            return RunStepResult(
                success=False,
                sequence=sequence,
                signal=signal,
                started_at=started_at,
                completed_at=datetime.now(),
                error=f"APx not in IDLE state (current: {self._state.apx_state.value})",
            )
        
        if self._apx_instance is None:
            return RunStepResult(
                success=False,
                sequence=sequence,
                signal=signal,
                started_at=started_at,
                completed_at=datetime.now(),
                error="APx instance not initialized",
            )
        
        try:
            self._state.apx_state = APxState.RUNNING_STEP
            logger.info(f"Running step: sequence={sequence}, signal={signal}")
            
            # =================================================================
            # STUB: APx .NET API call to run the sequence/signal
            # =================================================================
            # TODO: Implement actual APx API calls here. Example:
            #
            # # Select the sequence
            # self._apx_instance.Sequence.SetSequence(sequence)
            # 
            # # Select the signal
            # self._apx_instance.Sequence.SetSignal(signal)
            # 
            # # Run the sequence
            # self._apx_instance.Sequence.Run()
            #
            # # Wait for completion (with timeout)
            # start_time = time.time()
            # while self._apx_instance.Sequence.IsRunning:
            #     if time.time() - start_time > timeout_seconds:
            #         raise TimeoutError("Sequence timed out")
            #     time.sleep(0.1)
            # =================================================================
            
            logger.info(f"STUB: Would run sequence={sequence}, signal={signal}")
            time.sleep(0.5)  # Simulate some work
            
            # =================================================================
            # End of STUB
            # =================================================================
            
            self._state.apx_state = APxState.IDLE
            completed_at = datetime.now()
            
            logger.info(
                f"Step completed: sequence={sequence}, signal={signal}, "
                f"duration={(completed_at - started_at).total_seconds():.2f}s"
            )
            
            return RunStepResult(
                success=True,
                sequence=sequence,
                signal=signal,
                started_at=started_at,
                completed_at=completed_at,
            )
            
        except Exception as e:
            error_msg = f"Error running step: {e}"
            logger.error(error_msg)
            self._state.apx_state = APxState.ERROR
            self._state.last_error = error_msg
            self._state.last_error_at = datetime.now()
            
            return RunStepResult(
                success=False,
                sequence=sequence,
                signal=signal,
                started_at=started_at,
                completed_at=datetime.now(),
                error=error_msg,
            )
    
    def shutdown(self, force: bool = False) -> bool:
        """
        Shutdown APx500 gracefully.
        
        Args:
            force: If True, force-kill the process if graceful shutdown fails
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self._apx_instance is not None:
                logger.info("Closing APx500 gracefully...")
                try:
                    self._apx_instance.Close()
                    time.sleep(1)  # Give it a moment to close
                except Exception as e:
                    logger.warning(f"Graceful close failed: {e}")
                    if force:
                        logger.info("Force-killing APx500...")
                        self.kill_existing_apx_processes()
            
            self._apx_instance = None
            self._state.apx_state = APxState.NOT_RUNNING
            self._state.apx_pid = None
            self._state.project = None
            
            logger.info("APx500 shutdown complete")
            return True
            
        except Exception as e:
            error_msg = f"Error during shutdown: {e}"
            logger.error(error_msg)
            self._state.last_error = error_msg
            self._state.last_error_at = datetime.now()
            
            if force:
                self.kill_existing_apx_processes()
                return True
            
            return False
    
    def reset(self) -> int:
        """
        Reset the controller state by killing APx and clearing state.
        
        Returns:
            Number of processes killed
        """
        logger.info("Resetting APx controller...")
        killed = self.kill_existing_apx_processes()
        self._apx_instance = None
        self._state.apx_state = APxState.NOT_RUNNING
        self._state.apx_pid = None
        self._state.project = None
        self._state.last_error = None
        self._state.last_error_at = None
        return killed
    
    def check_health(self) -> bool:
        """
        Check if APx is healthy (if it's supposed to be running).
        
        Updates state to ERROR if APx process has crashed.
        
        Returns:
            True if healthy or not running, False if crashed
        """
        if self._state.apx_state in (APxState.IDLE, APxState.RUNNING_STEP):
            if self._apx_instance is None:
                # We think APx is running but we have no instance
                self._state.apx_state = APxState.ERROR
                self._state.last_error = "APx instance lost"
                self._state.last_error_at = datetime.now()
                return False
            
            # Could add additional health checks here, like:
            # - Check if APx process is still running
            # - Try a simple API call to verify responsiveness
        
        return True

