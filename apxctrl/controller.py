"""
APx500 Controller Module.

Handles APx500 process lifecycle management and .NET API interactions.
Designed for robustness in factory environments (thousands of runs/day).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from apxctrl.model import (
    APxState,
    MeasurementInfo,
    ProjectInfo,
    SequenceInfo,
    ServerState,
    SignalPathInfo,
)

logger = logging.getLogger(__name__)

# APx500 process name (without .exe) - matches Task Manager display
APX_PROCESS_NAME = "Apx500"


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
        
        Uses PowerShell to find any process with "APx500" in the name
        and force-kills them.
        
        Returns:
            Number of processes killed
        """
        killed = 0
        try:
            # Use PowerShell to find all processes containing "APx500" in the name
            # This is more robust than taskkill /IM which requires exact name match
            find_cmd = [
                "powershell", "-Command",
                "Get-Process | Where-Object { $_.ProcessName -like '*APx500*' } | "
                "Select-Object -ExpandProperty Id"
            ]
            result = subprocess.run(find_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.warning(f"PowerShell process search failed: {result.stderr}")
            
            # Parse PIDs from output (one per line)
            pids = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
            
            if not pids:
                logger.info("No existing APx500 processes found to kill")
            else:
                logger.info(f"Found {len(pids)} APx500-related process(es) with PIDs: {pids}")
                
                # Kill each process by PID
                for pid in pids:
                    try:
                        kill_result = subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            text=True,
                        )
                        if kill_result.returncode == 0:
                            killed += 1
                            logger.info(f"Killed process with PID {pid}")
                        else:
                            logger.warning(
                                f"Failed to kill PID {pid}: {kill_result.stderr.strip()}"
                            )
                    except Exception as e:
                        logger.error(f"Error killing PID {pid}: {e}")
                
                logger.info(f"Successfully killed {killed} of {len(pids)} APx500 process(es)")
                
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
        apx_args: str = "",
    ) -> bool:
        """
        Launch APx500 and open a project file.
        
        If APx is already running, it will be shut down first.
        
        Args:
            project_path: Path to the .approjx project file
            project_name: Optional name for the project (defaults to filename)
            apx_mode: APx operating mode (default: SequenceMode)
            apx_args: Additional APx command line arguments
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # If APx is already running, shut it down first
            if self._apx_instance is not None:
                logger.info("APx already running, shutting down first...")
                self.shutdown(force=True)
            
            self._state.apx_state = APxState.STARTING
            
            # Verify project file exists and has content
            resolved_path = project_path.resolve()
            if not resolved_path.exists():
                raise FileNotFoundError(f"Project file not found: {resolved_path}")
            
            file_size = resolved_path.stat().st_size
            logger.info(f"Project file verified: {resolved_path} ({file_size} bytes)")
            
            if file_size == 0:
                raise ValueError(f"Project file is empty: {resolved_path}")
            
            # Initialize CLR if needed
            self._init_clr()
            
            # Import APx types after CLR is initialized
            from AudioPrecision.API import APx500, APx500_Application, APxOperatingMode
            
            # Determine the operating mode
            mode = getattr(APxOperatingMode, apx_mode, APxOperatingMode.SequenceMode)
            
            # Create APx application instance
            logger.info(f"Creating APx500_Application with mode={apx_mode}, args={apx_args}")
            self._apx_instance = APx500(mode, apx_args)
            self._apx_instance.Visible = True
            self._apx_instance.SignalMonitorsEnabled = False
            
            # Open the project - use absolute path as string
            project_path_str = str(resolved_path)
            logger.info(f"Opening project: {project_path_str}")
            
            self._apx_instance.OpenProject(project_path_str)
            logger.info("OpenProject call returned")
            
            # Verify project loaded by checking Sequence count
            try:
                sequence = self._apx_instance.Sequence
                seq_count = sequence.Count
                logger.info(f"Project loaded - Sequence has {seq_count} signal path(s)")
            except Exception as e:
                logger.warning(f"Could not verify project load: {e}")
            
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
                self._state.apx_state = APxState.NOT_RUNNING
                self._state.last_error = "APx instance lost"
                self._state.last_error_at = datetime.now()
                return False
        
        return True

    def _require_apx(self) -> Optional[str]:
        """
        Check that APx is available for operations.
        
        Returns:
            Error message if APx is not available, None if ready.
        """
        if self._apx_instance is None:
            return "APx not running. Call /setup first."
        return None

    def list_structure(self) -> tuple[list[SequenceInfo], Optional[str], Optional[str]]:
        """
        List full project structure: sequences -> signal paths -> measurements.
        
        Returns:
            Tuple of (list of SequenceInfo with nested structure, active sequence name, error message or None)
        """
        if err := self._require_apx():
            return [], None, err
        
        try:
            sequences = []
            active_sequence = None
            
            # Get sequences collection
            sequences_collection = self._apx_instance.Sequence.Sequences
            seq_count = sequences_collection.Count
            logger.info(f"Found {seq_count} sequence(s)")
            
            # Try to get active sequence name
            try:
                active_sequence = self._apx_instance.Sequence.ActiveSequence.Name
                logger.info(f"Active sequence: '{active_sequence}'")
            except Exception as e:
                logger.debug(f"Could not get active sequence: {e}")
            
            # Iterate through sequences
            for seq_idx in range(seq_count):
                seq = sequences_collection[seq_idx]
                seq_name = seq.Name
                logger.info(f"  Sequence {seq_idx}: '{seq_name}'")
                
                # Activate this sequence to get its structure
                sequences_collection.Activate(seq_name)
                
                # Get signal paths for this sequence
                signal_paths = []
                sequence = self._apx_instance.Sequence
                
                for sp_idx in range(sequence.Count):
                    signal_path = sequence.GetSignalPath(sp_idx)
                    measurements = []
                    
                    # Get measurements for this signal path
                    for m_idx in range(signal_path.Count):
                        measurement = signal_path.GetMeasurement(m_idx)
                        measurements.append(MeasurementInfo(
                            index=m_idx,
                            name=measurement.Name,
                            checked=measurement.Checked,
                        ))
                    
                    signal_paths.append(SignalPathInfo(
                        index=sp_idx,
                        name=signal_path.Name,
                        checked=signal_path.Checked,
                        measurements=measurements,
                    ))
                
                sequences.append(SequenceInfo(
                    index=seq_idx,
                    name=seq_name,
                    signal_paths=signal_paths,
                ))
            
            # Restore active sequence if we had one
            if active_sequence:
                try:
                    sequences_collection.Activate(active_sequence)
                except Exception as e:
                    logger.debug(f"Could not restore active sequence: {e}")
            
            total_sp = sum(len(s.signal_paths) for s in sequences)
            total_m = sum(
                sum(len(sp.measurements) for sp in s.signal_paths)
                for s in sequences
            )
            logger.info(
                f"Listed structure: {len(sequences)} sequences, "
                f"{total_sp} signal paths, {total_m} measurements"
            )
            
            return sequences, active_sequence, None
            
        except Exception as e:
            error_msg = f"Error listing structure: {e}"
            logger.error(error_msg)
            return [], None, error_msg

    def run_sequence(
        self,
        sequence_name: str,
        test_run_id: str = "",
    ) -> tuple[bool, float, Optional[str]]:
        """
        Activate and run a sequence.
        
        Args:
            sequence_name: Name of the sequence to run
            test_run_id: Test run ID (passed to APx as device ID)
            
        Returns:
            Tuple of (passed, duration_seconds, error message or None)
        """
        if err := self._require_apx():
            return False, 0.0, err
        
        if self._state.apx_state == APxState.RUNNING_STEP:
            return False, 0.0, "A sequence is already running"
        
        started_at = datetime.now()
        
        try:
            self._state.apx_state = APxState.RUNNING_STEP
            
            # Activate the sequence by name
            logger.info(f"Activating sequence: '{sequence_name}'")
            sequences_collection = self._apx_instance.Sequence.Sequences
            sequences_collection.Activate(sequence_name)
            logger.info(f"Sequence '{sequence_name}' activated")
            
            # Run the sequence with test_run_id (APx calls this "device ID")
            logger.info(f"Running sequence with test_run_id: '{test_run_id}'")
            self._apx_instance.Sequence.Run(test_run_id)
            logger.info("Sequence.Run() completed")
            
            # Check if passed
            passed = self._apx_instance.Sequence.Passed
            logger.info(f"Sequence passed: {passed}")
            
            duration = (datetime.now() - started_at).total_seconds()
            return passed, duration, None
            
        except Exception as e:
            error_msg = f"Error running sequence: {e}"
            logger.error(error_msg)
            self._state.last_error = error_msg
            self._state.last_error_at = datetime.now()
            
            duration = (datetime.now() - started_at).total_seconds()
            return False, duration, error_msg
        finally:
            # Always return to IDLE - APx is still running even after errors
            self._state.apx_state = APxState.IDLE

    def get_result(
        self,
        results_path: str,
    ) -> tuple[Optional[Path], Optional[str], Optional[str]]:
        """
        Find and compress test results directory.
        
        Searches for a directory matching <results_path>* (results_path is a path prefix).
        For example, if results_path is "c:\\apx-data\\ABCD-9483ur9sd", this will find
        directories like "c:\\apx-data\\ABCD-9483ur9sd-2026-01-22".
        
        Args:
            results_path: Path prefix to search for (e.g., "c:\\apx-data\\ABCD-9483ur9sd")
            
        Returns:
            Tuple of (zip_path or None, found_directory_name or None, error message or None)
        """
        
        try:
            base_path = Path(results_path)
            parent_dir = base_path.parent
            prefix = base_path.name
            
            if not parent_dir.exists():
                return None, None, f"Parent directory does not exist: {parent_dir}"
            
            if not parent_dir.is_dir():
                return None, None, f"Parent path is not a directory: {parent_dir}"
            
            # Find directories matching prefix* in parent directory
            matching_dirs = list(parent_dir.glob(f"{prefix}*"))
            matching_dirs = [d for d in matching_dirs if d.is_dir()]
            
            if not matching_dirs:
                return None, None, f"No directory found matching '{prefix}*' in {parent_dir}"
            
            if len(matching_dirs) > 1:
                # Sort by modification time, use most recent
                matching_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                logger.warning(
                    f"Multiple directories match '{prefix}*': {[d.name for d in matching_dirs]}. "
                    f"Using most recent: {matching_dirs[0].name}"
                )
            
            target_dir = matching_dirs[0]
            logger.info(f"Found results directory: {target_dir}")
            
            # Create a temporary zip file
            temp_dir = Path(tempfile.gettempdir()) / "apxctrl" / "results"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            zip_path = temp_dir / f"{target_dir.name}.zip"
            
            # Remove existing zip if present
            if zip_path.exists():
                zip_path.unlink()
            
            # Create zip archive
            logger.info(f"Creating zip archive: {zip_path}")
            shutil.make_archive(
                str(zip_path.with_suffix("")),  # Base name without .zip
                "zip",
                root_dir=str(target_dir.parent),
                base_dir=target_dir.name,
            )
            
            zip_size = zip_path.stat().st_size
            logger.info(f"Zip archive created: {zip_path} ({zip_size} bytes)")
            
            return zip_path, target_dir.name, None
            
        except Exception as e:
            error_msg = f"Error getting results: {e}"
            logger.error(error_msg)
            return None, None, error_msg

    def set_user_defined_variable(
        self,
        name: str,
        value: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Set a user-defined variable in APx.
        
        Args:
            name: Name of the user-defined variable
            value: Value to set for the variable
            
        Returns:
            Tuple of (success, error message or None)
        """
        if err := self._require_apx():
            return False, err
        
        try:
            logger.info(f"Setting user-defined variable '{name}' = '{value}'")
            self._apx_instance.Variables.SetUserDefinedVariable(name, value)
            logger.info(f"User-defined variable '{name}' set successfully")
            return True, None
            
        except Exception as e:
            error_msg = f"Error setting user-defined variable: {e}"
            logger.error(error_msg)
            return False, error_msg
