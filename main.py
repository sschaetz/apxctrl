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

from flask import Flask, jsonify, request, send_file

from apx_controller import APxController
from models import (
    APxState,
    GetResultsRequest,
    HealthResponse,
    ProjectInfo,
    ResetResponse,
    RunStepRequest,
    RunStepResponse,
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
        "version": "0.2.0",
        "endpoints": {
            "GET /": "Service information",
            "GET /health": "Quick health check",
            "GET /status": "Detailed status",
            "POST /setup": "Upload project and launch APx",
            "POST /run-step": "Run a sequence/signal step",
            "POST /get-results": "Get result files (stub)",
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


@app.route("/run-step", methods=["POST"])
def run_step():
    """
    Run a sequence/signal step.
    
    Expects JSON body:
    {
        "sequence": "sequence_name",
        "signal": "signal_name",
        "timeout_seconds": 120.0  // optional, default 2 minutes
    }
    """
    state = get_state()
    controller = get_controller()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify(RunStepResponse(
                success=False,
                message="Request body must be JSON",
                apx_state=state.apx_state,
            ).model_dump(mode="json")), 400
        
        req = RunStepRequest(**data)
    except Exception as e:
        return jsonify(RunStepResponse(
            success=False,
            message=f"Invalid request: {e}",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 400
    
    # Check state
    if state.apx_state != APxState.IDLE:
        return jsonify(RunStepResponse(
            success=False,
            message=f"APx not ready. Current state: {state.apx_state.value}",
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 409
    
    # Run the step
    result = controller.run_step(
        sequence=req.sequence,
        signal=req.signal,
        timeout_seconds=req.timeout_seconds,
    )
    
    if result.success:
        return jsonify(RunStepResponse(
            success=True,
            message="Step completed successfully",
            sequence=result.sequence,
            signal=result.signal,
            duration_seconds=result.duration_seconds,
            apx_state=state.apx_state,
        ).model_dump(mode="json"))
    else:
        return jsonify(RunStepResponse(
            success=False,
            message=result.error or "Step failed",
            sequence=result.sequence,
            signal=result.signal,
            apx_state=state.apx_state,
        ).model_dump(mode="json")), 500


@app.route("/get-results", methods=["POST"])
def get_results():
    """
    Get result files from a directory (stub).
    
    Expects JSON body:
    {
        "directory": "C:\\path\\to\\results"
    }
    
    Returns: ZIP file as attachment (not implemented yet)
    """
    state = get_state()
    
    # Parse request
    try:
        data = request.get_json()
        if data is None:
            return jsonify({
                "success": False,
                "message": "Request body must be JSON",
            }), 400
        
        req = GetResultsRequest(**data)
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Invalid request: {e}",
        }), 400
    
    # ==========================================================================
    # STUB: Zip directory and return
    # ==========================================================================
    # TODO: Implement actual zip creation and file return. Example:
    #
    # directory = Path(req.directory)
    # if not directory.exists():
    #     return jsonify({"success": False, "message": "Directory not found"}), 404
    #
    # # Create zip in memory
    # import io
    # import zipfile
    # 
    # buffer = io.BytesIO()
    # with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
    #     for file in directory.rglob("*"):
    #         if file.is_file():
    #             zf.write(file, file.relative_to(directory))
    # 
    # buffer.seek(0)
    # return send_file(
    #     buffer,
    #     mimetype="application/zip",
    #     as_attachment=True,
    #     download_name="results.zip",
    # )
    # ==========================================================================
    
    logger.info(f"STUB: Would zip and return directory: {req.directory}")
    
    return jsonify({
        "success": True,
        "message": f"STUB: Would return zipped contents of {req.directory}",
    })


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
    logger.info("  GET  /           - Service information")
    logger.info("  GET  /health     - Health check")
    logger.info("  GET  /status     - Detailed status")
    logger.info("  POST /setup      - Upload project and launch APx")
    logger.info("  POST /run-step   - Run sequence/signal step")
    logger.info("  POST /get-results - Get result files (stub)")
    logger.info("  POST /shutdown   - Shutdown APx")
    logger.info("  POST /reset      - Kill APx and reset state")
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
