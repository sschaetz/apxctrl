"""
APx Control Web Server.

A Flask-based web server for controlling Audio Precision APx500 test equipment
remotely. Designed for factory environments with robustness and reliability.

Usage:
    python main.py [--kill-existing] [--port PORT] [--host HOST]
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

from apx_controller import APxController
from models import (
    APxState,
    HealthResponse,
    ProjectInfo,
    ResetResponse,
    RunAllRequest,
    RunAllResponse,
    RunSignalPathRequest,
    RunSignalPathResponse,
    SequenceStructureResponse,
    ServerState,
    SetupResponse,
    ShutdownRequest,
    ShutdownResponse,
    StatusResponse,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Global state and controller (initialized in main)
_server_state: ServerState = None  # type: ignore
_controller: APxController = None  # type: ignore


def get_state() -> ServerState:
    """Get the global server state."""
    global _server_state
    if _server_state is None:
        _server_state = ServerState()
    return _server_state


def get_controller() -> APxController:
    """Get the global APx controller."""
    global _controller
    if _controller is None:
        _controller = APxController(get_state())
    return _controller


# ============================================================================
# Endpoints
# ============================================================================


@app.route("/", methods=["GET"])
def index():
    """Service information and available endpoints."""
    return jsonify({
        "service": "APx Control Server",
        "version": "0.4.0",
        "endpoints": {
            "GET /": "Service information",
            "GET /health": "Quick health check",
            "GET /status": "Detailed status",
            "POST /setup": "Upload project and launch APx",
            "GET /sequence/structure": "Get sequence structure (signal paths and measurements)",
            "POST /run-signal-path": "Run all measurements in a signal path",
            "POST /run-all": "Run all measurements and export reports",
            "POST /shutdown": "Shutdown APx gracefully",
            "POST /reset": "Kill APx and reset state",
        },
    })


@app.route("/health", methods=["GET"])
def health():
    """
    Quick health check endpoint.
    
    Returns basic health status - use /status for detailed information.
    """
    state = get_state()
    controller = get_controller()
    
    # Check if APx is healthy (updates state if crashed)
    controller.check_health()
    
    uptime = (datetime.now() - state.server_started_at).total_seconds()
    
    response = HealthResponse(
        status="healthy" if state.apx_state != APxState.ERROR else "degraded",
        apx_state=state.apx_state,
        uptime_seconds=uptime,
    )
    
    return jsonify(response.model_dump(mode="json"))


@app.route("/status", methods=["GET"])
def status():
    """
    Detailed status endpoint.
    
    Returns complete server state including project information.
    """
    state = get_state()
    controller = get_controller()
    
    # Check if APx is healthy
    controller.check_health()
    
    uptime = (datetime.now() - state.server_started_at).total_seconds()
    
    response = StatusResponse(
        apx_state=state.apx_state,
        project_name=state.project.name if state.project else None,
        project_sha256=state.project.sha256 if state.project else None,
        project_path=state.project.file_path if state.project else None,
        apx_pid=state.apx_pid,
        last_error=state.last_error,
        last_error_at=state.last_error_at,
        server_started_at=state.server_started_at,
        uptime_seconds=uptime,
    )
    
    return jsonify(response.model_dump(mode="json"))


@app.route("/setup", methods=["POST"])
def setup():
    """
    Setup endpoint: upload project file and launch APx.
    
    Expects multipart/form-data with:
    - file: The .approjx project file
    - project_name (optional): Name for the project
    - apx_mode (optional): APx operating mode (default: SequenceMode)
    - apx_args (optional): APx command line arguments (default: -Demo -APx517)
    """
    state = get_state()
    controller = get_controller()
    
    # Check if we're already running something
    if state.apx_state == APxState.RUNNING_STEP:
        return jsonify(SetupResponse(
            success=False,
            message="Cannot setup while a step is running",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 409
    
    # Get the uploaded file
    if "file" not in request.files:
        return jsonify(SetupResponse(
            success=False,
            message="No file provided. Send project file as 'file' in multipart/form-data",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify(SetupResponse(
            success=False,
            message="Empty filename",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Get optional parameters from form data
    project_name = request.form.get("project_name", None)
    apx_mode = request.form.get("apx_mode", "SequenceMode")
    apx_args = request.form.get("apx_args", "-Demo -APx517")
    
    # If APx is already running, shut it down first
    if state.apx_state in (APxState.IDLE, APxState.ERROR):
        logger.info("Shutting down existing APx instance before setup")
        controller.shutdown(force=True)
    
    # Save file to temp directory
    try:
        temp_dir = Path(tempfile.gettempdir()) / "apxctrl"
        temp_dir.mkdir(exist_ok=True)
        
        # Use original filename or project_name
        safe_filename = file.filename or "project.approjx"
        project_path = temp_dir / safe_filename
        
        logger.info(f"Saving project file to: {project_path}")
        file.save(str(project_path))
        
        # Compute SHA256 for logging
        project_info = ProjectInfo.from_file(project_path, project_name)
        logger.info(
            f"Project saved: name={project_info.name}, "
            f"path={project_info.file_path}, "
            f"sha256={project_info.sha256}"
        )
        
    except Exception as e:
        error_msg = f"Failed to save project file: {e}"
        logger.error(error_msg)
        return jsonify(SetupResponse(
            success=False,
            message=error_msg,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    # Launch APx and open project
    success = controller.launch_apx(
        project_path=project_path,
        project_name=project_name,
        apx_mode=apx_mode,
        apx_args=apx_args,
    )
    
    if success:
        return jsonify(SetupResponse(
            success=True,
            message="APx launched and project loaded successfully",
            project_name=state.project.name if state.project else None,
            project_sha256=state.project.sha256 if state.project else None,
            project_path=state.project.file_path if state.project else None,
            apx_state=state.apx_state,
        ).model_dump(mode="json"))
    else:
        return jsonify(SetupResponse(
            success=False,
            message=state.last_error or "Failed to launch APx",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500


@app.route("/sequence/structure", methods=["GET"])
def get_sequence_structure():
    """
    Get the structure of the loaded sequence.
    
    Returns the hierarchy of signal paths and measurements.
    """
    state = get_state()
    controller = get_controller()
    
    # Check state
    if state.apx_state == APxState.NOT_RUNNING:
        return jsonify(SequenceStructureResponse(
            success=False,
            message="APx not running. Call /setup first.",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 409
    
    # Get structure
    signal_paths, error = controller.get_sequence_structure()
    
    if error:
        return jsonify(SequenceStructureResponse(
            success=False,
            message=error,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    # Count totals
    total_measurements = sum(len(sp.measurements) for sp in signal_paths)
    
    return jsonify(SequenceStructureResponse(
        success=True,
        message="Sequence structure retrieved successfully",
        signal_paths=signal_paths,
        total_signal_paths=len(signal_paths),
        total_measurements=total_measurements,
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


@app.route("/run-signal-path", methods=["POST"])
def run_signal_path():
    """
    Run all checked measurements in a signal path.
    
    Expects JSON body:
    {
        "signal_path": "Analog Output",
        "timeout_seconds": 120.0  // optional, default 2 minutes per measurement
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify(RunSignalPathResponse(
                success=False,
                message="Request body must be JSON",
                signal_path="",
                apx_state=state.apx_state,
            ).model_dump(mode="json")), 400
        
        req = RunSignalPathRequest(**data)
    except Exception as e:
        return jsonify(RunSignalPathResponse(
            success=False,
            message=f"Invalid request: {e}",
            signal_path="",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Check state
    if state.apx_state != APxState.IDLE:
        return jsonify(RunSignalPathResponse(
            success=False,
            message=f"APx not ready. Current state: {state.apx_state.value}",
            signal_path=req.signal_path,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 409
    
    # Run signal path
    results, error = controller.run_signal_path(
        signal_path_name=req.signal_path,
        timeout_seconds=req.timeout_seconds,
    )
    
    # Calculate stats
    measurements_run = len(results)
    measurements_passed = sum(1 for r in results if r.passed)
    measurements_failed = sum(1 for r in results if r.success and not r.passed)
    total_duration = sum(r.duration_seconds for r in results)
    all_success = all(r.success for r in results)
    
    if error:
        return jsonify(RunSignalPathResponse(
            success=False,
            message=error,
            signal_path=req.signal_path,
            measurements_run=measurements_run,
            measurements_passed=measurements_passed,
            measurements_failed=measurements_failed,
            total_duration_seconds=total_duration,
            results=results,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    return jsonify(RunSignalPathResponse(
        success=all_success,
        message=f"Signal path '{req.signal_path}' completed. "
                f"{measurements_passed}/{measurements_run} passed.",
        signal_path=req.signal_path,
        measurements_run=measurements_run,
        measurements_passed=measurements_passed,
        measurements_failed=measurements_failed,
        total_duration_seconds=total_duration,
        results=results,
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


@app.route("/run-all", methods=["POST"])
def run_all():
    """
    Run all checked measurements in all checked signal paths and export reports.
    
    Expects JSON body (all optional):
    {
        "timeout_seconds": 120.0,   // default 2 minutes per measurement
        "export_csv": true,         // default true
        "export_pdf": false,        // default false
        "report_directory": null    // defaults to temp directory
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json() or {}
        req = RunAllRequest(**data)
    except Exception as e:
        return jsonify(RunAllResponse(
            success=False,
            message=f"Invalid request: {e}",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Check state
    if state.apx_state != APxState.IDLE:
        return jsonify(RunAllResponse(
            success=False,
            message=f"APx not ready. Current state: {state.apx_state.value}",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 409
    
    # Run all
    results_by_sp, error, csv_path, pdf_path = controller.run_all_and_export(
        timeout_seconds=req.timeout_seconds,
        export_csv=req.export_csv,
        export_pdf=req.export_pdf,
        report_directory=req.report_directory,
    )
    
    # Calculate stats
    signal_paths_run = len(results_by_sp)
    all_results = [r for results in results_by_sp.values() for r in results]
    measurements_run = len(all_results)
    measurements_passed = sum(1 for r in all_results if r.passed)
    measurements_failed = sum(1 for r in all_results if r.success and not r.passed)
    total_duration = sum(r.duration_seconds for r in all_results)
    all_success = all(r.success for r in all_results)
    all_passed = all(r.passed for r in all_results if r.success)
    
    if error:
        return jsonify(RunAllResponse(
            success=False,
            message=error,
            signal_paths_run=signal_paths_run,
            measurements_run=measurements_run,
            measurements_passed=measurements_passed,
            measurements_failed=measurements_failed,
            total_duration_seconds=total_duration,
            all_passed=False,
            csv_report_path=csv_path,
            pdf_report_path=pdf_path,
            results_by_signal_path={k: v for k, v in results_by_sp.items()},
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    return jsonify(RunAllResponse(
        success=all_success,
        message=f"All measurements completed. {measurements_passed}/{measurements_run} passed.",
        signal_paths_run=signal_paths_run,
        measurements_run=measurements_run,
        measurements_passed=measurements_passed,
        measurements_failed=measurements_failed,
        total_duration_seconds=total_duration,
        all_passed=all_passed,
        csv_report_path=csv_path,
        pdf_report_path=pdf_path,
        results_by_signal_path={k: v for k, v in results_by_sp.items()},
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


@app.route("/shutdown", methods=["POST"])
def shutdown():
    """
    Shutdown APx gracefully.
    
    Expects JSON body (optional):
    {
        "force": false  // Force kill if graceful shutdown fails
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    force = False
    try:
        data = request.get_json()
        if data:
            req = ShutdownRequest(**data)
            force = req.force
    except Exception:
        pass  # Use defaults
    
    # Shutdown
    success = controller.shutdown(force=force)
    
    if success:
        return jsonify(ShutdownResponse(
            success=True,
            message="APx shutdown successfully",
            apx_state=state.apx_state,
        ).model_dump(mode="json"))
    else:
        return jsonify(ShutdownResponse(
            success=False,
            message=state.last_error or "Shutdown failed",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500


@app.route("/reset", methods=["POST"])
def reset():
    """
    Reset the server by killing APx and clearing state.
    
    Use this for recovery after errors or crashes.
    """
    state = get_state()
    controller = get_controller()
    
    killed = controller.reset()
    
    return jsonify(ResetResponse(
        success=True,
        message=f"Reset complete. Killed {killed} process(es).",
        killed_processes=killed,
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


# ============================================================================
# Error handlers
# ============================================================================


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler."""
    logger.exception(f"Unhandled exception: {e}")
    return jsonify({
        "success": False,
        "message": f"Internal server error: {e}",
    }), 500


# ============================================================================
# Main
# ============================================================================


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="APx Control Web Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--kill-existing",
        action="store_true",
        help="Kill any existing APx500 processes on startup",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to bind to (default: 5000)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode",
    )
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    logger.info("=" * 60)
    logger.info("APx Control Server starting...")
    logger.info("=" * 60)
    
    # Initialize global state and controller
    global _server_state, _controller
    _server_state = ServerState()
    _controller = APxController(_server_state)
    
    # Kill existing APx processes if requested
    if args.kill_existing:
        logger.info("--kill-existing flag set, killing any running APx500 processes")
        killed = _controller.kill_existing_apx_processes()
        logger.info(f"Killed {killed} existing APx500 process(es)")
    
    # Log server info
    logger.info(f"Server will be available at: http://{args.host}:{args.port}")
    logger.info("Endpoints:")
    logger.info("  GET  /                    - Service information")
    logger.info("  GET  /health              - Health check")
    logger.info("  GET  /status              - Detailed status")
    logger.info("  POST /setup               - Upload project and launch APx")
    logger.info("  GET  /sequence/structure  - Get signal paths and measurements")
    logger.info("  POST /run-signal-path     - Run all measurements in a signal path")
    logger.info("  POST /run-all             - Run all and export reports")
    logger.info("  POST /shutdown            - Shutdown APx")
    logger.info("  POST /reset               - Kill APx and reset state")
    logger.info("=" * 60)
    
    # Run Flask app
    # Note: In production, use a proper WSGI server like gunicorn or waitress
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=False,  # Single-threaded since we have 1:1 client model
    )


if __name__ == "__main__":
    main()
