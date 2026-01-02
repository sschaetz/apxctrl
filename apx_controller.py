"""
APx500 Controller Module.

Handles APx500 process lifecycle management and .NET API interactions.
Designed for robustness in factory environments (thousands of runs/day).
"""
from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import (
    APxState,
    MeasurementInfo,
    MeasurementResult,
    ProjectInfo,
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

            # Log initial visibility state
            try:
                initial_visible = self._apx_instance.Visible
                logger.info(f"Initial Visible state: {initial_visible}")
            except Exception as e:
                logger.warning(f"Could not read initial Visible state: {e}")

            # Set Visible AFTER opening project (some APIs require this order)
            logger.info("Setting Visible = True")
            self._apx_instance.Visible = True
            
            # Verify visibility was set
            try:
                final_visible = self._apx_instance.Visible
                logger.info(f"Final Visible state: {final_visible}")
            except Exception as e:
                logger.warning(f"Could not read final Visible state: {e}")

            # Try to bring window to front if method exists
            try:
                if hasattr(self._apx_instance, 'ShowWindow'):
                    self._apx_instance.ShowWindow()
                    logger.info("Called ShowWindow()")
                elif hasattr(self._apx_instance, 'Activate'):
                    self._apx_instance.Activate()
                    logger.info("Called Activate()")
                elif hasattr(self._apx_instance, 'BringToFront'):
                    self._apx_instance.BringToFront()
                    logger.info("Called BringToFront()")
            except Exception as e:
                logger.warning(f"Could not bring window to front: {e}")

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

    def get_sequence_structure(self) -> tuple[list[SignalPathInfo], Optional[str]]:
        """
        Get the structure of the loaded sequence.
        
        Traverses the APx Sequence to enumerate all signal paths and their
        measurements, including their checked state.
        
        Returns:
            Tuple of (list of SignalPathInfo, error message or None)
        """
        if self._apx_instance is None:
            return [], "APx instance not initialized"
        
        if self._state.apx_state == APxState.NOT_RUNNING:
            return [], "APx not running"
        
        try:
            signal_paths = []
            sequence = self._apx_instance.Sequence
            
            # Traverse each signal path in the sequence using index-based access
            # Sequence has Count property and Item(index) indexer
            for sp_idx in range(sequence.Count):
                logger.info(f"{sp_idx} / {sequence.Count}")
                signal_path = sequence.GetSignalPath(sp_idx)
                measurements = []
                
                # Traverse each measurement in the signal path
                # ISignalPath also has Count and Item(index)
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
            
            logger.info(
                f"Retrieved sequence structure: {len(signal_paths)} signal paths, "
                f"{sum(len(sp.measurements) for sp in signal_paths)} total measurements"
            )
            return signal_paths, None
            
        except Exception as e:
            error_msg = f"Error getting sequence structure: {e}"
            logger.error(error_msg)
            return [], error_msg

    def run_measurement(
        self,
        signal_path_name: str,
        measurement_name: str,
        timeout_seconds: float = 120.0,
    ) -> MeasurementResult:
        """
        Run a single measurement by signal path and measurement name.
        
        Args:
            signal_path_name: Name of the signal path
            measurement_name: Name of the measurement
            timeout_seconds: Maximum time to wait for completion
            
        Returns:
            MeasurementResult with success status and data
        """
        started_at = datetime.now()
        
        if self._apx_instance is None:
            return MeasurementResult(
                name=measurement_name,
                success=False,
                passed=False,
                duration_seconds=0.0,
                error="APx instance not initialized",
            )
        
        try:
            self._state.apx_state = APxState.RUNNING_STEP
            logger.info(f"Running measurement: {signal_path_name}/{measurement_name}")
            
            # =================================================================
            # STUB: APx .NET API call to run the measurement
            # =================================================================
            # TODO: Implement actual APx API calls. Example:
            #
            # signal_path = self._apx_instance.Sequence[signal_path_name]
            # measurement = signal_path[measurement_name]
            # 
            # # Run the measurement
            # measurement.Run()
            # 
            # # Get results
            # passed = True
            # meter_values = {}
            # if measurement.HasSequenceResults:
            #     for result in measurement.SequenceResults:
            #         passed = passed and result.PassedUpperLimitCheck and result.PassedLowerLimitCheck
            #         if result.HasMeterValues:
            #             values = result.GetMeterValues()
            #             for i, v in enumerate(values):
            #                 meter_values[f"ch{i+1}"] = v
            # =================================================================
            
            logger.info(f"STUB: Would run {signal_path_name}/{measurement_name}")
            time.sleep(0.2)  # Simulate some work
            
            # STUB: Mock result
            passed = True
            meter_values = {"ch1": -0.5, "ch2": -0.4}
            
            # =================================================================
            
            self._state.apx_state = APxState.IDLE
            completed_at = datetime.now()
            duration = (completed_at - started_at).total_seconds()
            
            logger.info(
                f"Measurement completed: {signal_path_name}/{measurement_name}, "
                f"passed={passed}, duration={duration:.2f}s"
            )
            
            return MeasurementResult(
                name=measurement_name,
                success=True,
                passed=passed,
                duration_seconds=duration,
                meter_values=meter_values,
            )
            
        except Exception as e:
            error_msg = f"Error running measurement: {e}"
            logger.error(error_msg)
            self._state.apx_state = APxState.ERROR
            self._state.last_error = error_msg
            self._state.last_error_at = datetime.now()
            
            return MeasurementResult(
                name=measurement_name,
                success=False,
                passed=False,
                duration_seconds=(datetime.now() - started_at).total_seconds(),
                error=error_msg,
            )

    def run_signal_path(
        self,
        signal_path_name: str,
        timeout_seconds: float = 120.0,
    ) -> tuple[list[MeasurementResult], Optional[str]]:
        """
        Run all checked measurements in a signal path.
        
        Args:
            signal_path_name: Name of the signal path
            timeout_seconds: Timeout per measurement
            
        Returns:
            Tuple of (list of MeasurementResult, error message or None)
        """
        if self._apx_instance is None:
            return [], "APx instance not initialized"
        
        if self._state.apx_state != APxState.IDLE:
            return [], f"APx not in IDLE state (current: {self._state.apx_state.value})"
        
        results = []
        
        try:
            # Get the sequence structure first
            structure, error = self.get_sequence_structure()
            if error:
                return [], error
            
            # Find the signal path
            signal_path = None
            for sp in structure:
                if sp.name == signal_path_name:
                    signal_path = sp
                    break
            
            if signal_path is None:
                return [], f"Signal path '{signal_path_name}' not found"
            
            # Run each checked measurement
            for measurement in signal_path.measurements:
                if measurement.checked:
                    result = self.run_measurement(
                        signal_path_name=signal_path_name,
                        measurement_name=measurement.name,
                        timeout_seconds=timeout_seconds,
                    )
                    results.append(result)
                    
                    # If measurement failed to run (not just failed limits), stop
                    if not result.success:
                        logger.warning(
                            f"Stopping signal path run due to measurement error: "
                            f"{measurement.name}"
                        )
                        break
            
            return results, None
            
        except Exception as e:
            error_msg = f"Error running signal path: {e}"
            logger.error(error_msg)
            return results, error_msg

    def run_all_and_export(
        self,
        timeout_seconds: float = 120.0,
        export_csv: bool = True,
        export_pdf: bool = False,
        report_directory: Optional[str] = None,
    ) -> tuple[dict[str, list[MeasurementResult]], Optional[str], Optional[str], Optional[str]]:
        """
        Run all checked measurements and export reports.
        
        Args:
            timeout_seconds: Timeout per measurement
            export_csv: Whether to export CSV report
            export_pdf: Whether to export PDF report
            report_directory: Directory for reports (defaults to temp)
            
        Returns:
            Tuple of (results dict by signal path, error, csv_path, pdf_path)
        """
        if self._apx_instance is None:
            return {}, "APx instance not initialized", None, None
        
        if self._state.apx_state != APxState.IDLE:
            return {}, f"APx not in IDLE state (current: {self._state.apx_state.value})", None, None
        
        results_by_signal_path: dict[str, list[MeasurementResult]] = {}
        csv_path = None
        pdf_path = None
        
        try:
            # Get the sequence structure
            structure, error = self.get_sequence_structure()
            if error:
                return {}, error, None, None
            
            # Run each checked signal path
            for signal_path in structure:
                if signal_path.checked:
                    logger.info(f"Running signal path: {signal_path.name}")
                    
                    sp_results, sp_error = self.run_signal_path(
                        signal_path_name=signal_path.name,
                        timeout_seconds=timeout_seconds,
                    )
                    
                    results_by_signal_path[signal_path.name] = sp_results
                    
                    if sp_error:
                        logger.warning(f"Signal path error: {sp_error}")
                        # Continue with other signal paths
            
            # Export reports
            if report_directory:
                report_dir = Path(report_directory)
            else:
                import tempfile
                report_dir = Path(tempfile.gettempdir()) / "apxctrl" / "reports"
            
            report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # =================================================================
            # STUB: Export reports via APx API
            # =================================================================
            # TODO: Implement actual APx API calls. Example:
            #
            # if export_csv:
            #     csv_path = str(report_dir / f"report_{timestamp}.csv")
            #     self._apx_instance.Sequence.Report.ExportText(csv_path)
            #     logger.info(f"Exported CSV report: {csv_path}")
            #
            # if export_pdf:
            #     pdf_path = str(report_dir / f"report_{timestamp}.pdf")
            #     self._apx_instance.Sequence.Report.ExportPdf(pdf_path)
            #     logger.info(f"Exported PDF report: {pdf_path}")
            # =================================================================
            
            if export_csv:
                csv_path = str(report_dir / f"report_{timestamp}.csv")
                logger.info(f"STUB: Would export CSV report to: {csv_path}")
            
            if export_pdf:
                pdf_path = str(report_dir / f"report_{timestamp}.pdf")
                logger.info(f"STUB: Would export PDF report to: {pdf_path}")
            
            return results_by_signal_path, None, csv_path, pdf_path
            
        except Exception as e:
            error_msg = f"Error running all measurements: {e}"
            logger.error(error_msg)
            return results_by_signal_path, error_msg, csv_path, pdf_path

