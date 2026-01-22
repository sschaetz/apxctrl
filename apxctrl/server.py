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
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from apxctrl.controller import APxController
from apxctrl.model import (
    APxState,
    GetResultRequest,
    GetResultResponse,
    HealthResponse,
    ListResponse,
    ProjectInfo,
    ResetResponse,
    RunSequenceRequest,
    RunSequenceResponse,
    ServerState,
    SetupResponse,
    SetUserDefinedVariableRequest,
    SetUserDefinedVariableResponse,
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
        "version": "1.0.0",
        "endpoints": {
            "GET /": "Service information",
            "GET /health": "Quick health check",
            "GET /status": "Detailed status",
            "POST /setup": "Upload project and launch APx",
            "GET /list": "List sequences, signal paths, and measurements",
            "POST /run-sequence": "Activate and run a sequence",
            "POST /get-result": "Download test results as zip",
            "POST /set-user-defined-variable": "Set a user-defined variable",
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
    
    # Save file to ~/apxctrl-data/
    # (APx needs write access to the project directory)
    try:
        # Use original filename or project_name
        safe_filename = file.filename or "project.approjx"
        
        data_dir = Path.home() / "apxctrl-data"
        data_dir.mkdir(parents=True, exist_ok=True)

        project_path = data_dir / safe_filename
        
        logger.info(f"Saving project file to: {project_path}")
        file.save(str(project_path))
        
        # Verify file was saved correctly
        saved_size = project_path.stat().st_size
        logger.info(f"File saved: {saved_size} bytes")
        
        if saved_size == 0:
            raise ValueError("Saved file is empty!")
        
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


@app.route("/list", methods=["GET"])
def list_structure():
    """
    List full project structure: sequences -> signal paths -> measurements.
    
    Returns the hierarchy of all sequences with their signal paths and measurements.
    """
    state = get_state()
    controller = get_controller()
    
    # Get structure (controller handles state checking)
    sequences, active_sequence, error = controller.list_structure()
    
    if error:
        return jsonify(ListResponse(
            success=False,
            message=error,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    # Count totals
    total_signal_paths = sum(len(s.signal_paths) for s in sequences)
    total_measurements = sum(
        sum(len(sp.measurements) for sp in s.signal_paths)
        for s in sequences
    )
    
    return jsonify(ListResponse(
        success=True,
        message=f"Found {len(sequences)} sequence(s)",
        sequences=sequences,
        active_sequence=active_sequence,
        total_sequences=len(sequences),
        total_signal_paths=total_signal_paths,
        total_measurements=total_measurements,
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


@app.route("/run-sequence", methods=["POST"])
def run_sequence():
    """
    Activate and run a sequence.
    
    Expects JSON body:
    {
        "sequence_name": "My Sequence",
        "test_run_id": "TR-12345"  // optional, default ""
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify(RunSequenceResponse(
                success=False,
                message="Request body must be JSON",
                sequence_name="",
                test_run_id="",
                apx_state=state.apx_state,
            ).model_dump(mode="json")), 400
        
        req = RunSequenceRequest(**data)
    except Exception as e:
        return jsonify(RunSequenceResponse(
            success=False,
            message=f"Invalid request: {e}",
            sequence_name="",
            test_run_id="",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Run sequence (controller handles state checking)
    passed, duration, error = controller.run_sequence(
        sequence_name=req.sequence_name,
        test_run_id=req.test_run_id,
    )
    
    if error:
        return jsonify(RunSequenceResponse(
            success=False,
            message=error,
            sequence_name=req.sequence_name,
            test_run_id=req.test_run_id,
            passed=False,
            duration_seconds=duration,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    return jsonify(RunSequenceResponse(
        success=True,
        message=f"Sequence '{req.sequence_name}' completed. {'PASSED' if passed else 'FAILED'}",
        sequence_name=req.sequence_name,
        test_run_id=req.test_run_id,
        passed=passed,
        duration_seconds=duration,
        apx_state=state.apx_state,
    ).model_dump(mode="json"))


@app.route("/get-result", methods=["POST"])
def get_result():
    """
    Download test results as a zip file.
    
    Expects JSON body:
    {
        "test_run_id": "TR-12345",
        "results_path": "C:\\Users\\user\\Documents\\output"
    }
    
    Searches for a directory matching <test_run_id>* in results_path,
    compresses it, and returns the zip file.
    """
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify(GetResultResponse(
                success=False,
                message="Request body must be JSON",
                test_run_id="",
            ).model_dump(mode="json")), 400
        
        req = GetResultRequest(**data)
    except Exception as e:
        return jsonify(GetResultResponse(
            success=False,
            message=f"Invalid request: {e}",
            test_run_id="",
        ).model_dump(mode="json")), 400
    
    # Get result
    zip_path, found_dir, error = controller.get_result(
        test_run_id=req.test_run_id,
        results_path=req.results_path,
    )
    
    if error:
        return jsonify(GetResultResponse(
            success=False,
            message=error,
            test_run_id=req.test_run_id,
        ).model_dump(mode="json")), 404
    
    # Send the zip file
    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_path.name,
    )


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


@app.route("/set-user-defined-variable", methods=["POST"])
def set_user_defined_variable():
    """
    Set a user-defined variable in APx.
    
    Expects JSON body:
    {
        "name": "MyVariable",
        "value": "MyValue"
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify(SetUserDefinedVariableResponse(
                success=False,
                message="Request body must be JSON",
                name="",
                value="",
                apx_state=state.apx_state,
            ).model_dump(mode="json")), 400
        
        req = SetUserDefinedVariableRequest(**data)
    except Exception as e:
        return jsonify(SetUserDefinedVariableResponse(
            success=False,
            message=f"Invalid request: {e}",
            name="",
            value="",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Set the variable (controller handles state checking)
    success, error = controller.set_user_defined_variable(
        name=req.name,
        value=req.value,
    )
    
    if error:
        return jsonify(SetUserDefinedVariableResponse(
            success=False,
            message=error,
            name=req.name,
            value=req.value,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500
    
    return jsonify(SetUserDefinedVariableResponse(
        success=True,
        message=f"Variable '{req.name}' set to '{req.value}'",
        name=req.name,
        value=req.value,
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
    logger.info("  GET  /              - Service information")
    logger.info("  GET  /health        - Health check")
    logger.info("  GET  /status        - Detailed status")
    logger.info("  POST /setup         - Upload project and launch APx")
    logger.info("  GET  /list          - List sequences, signal paths, measurements")
    logger.info("  POST /run-sequence  - Activate and run a sequence")
    logger.info("  POST /get-result    - Download test results as zip")
    logger.info("  POST /set-user-defined-variable - Set a user-defined variable")
    logger.info("  POST /shutdown      - Shutdown APx")
    logger.info("  POST /reset         - Kill APx and reset state")
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
