#!/usr/bin/env python3
"""
Example client for APx Control Server.

Usage:
    # Upload project and show structure
    python client_example.py --server http://windows-host:5000 --project /path/to/project.approjx
    
    # List project structure (sequences -> signal paths -> measurements)
    python client_example.py --server http://windows-host:5000 --list
    
    # Run a sequence with test run ID
    python client_example.py --server http://windows-host:5000 --run-sequence "Production Test" --test-run-id "TR-001"
    
    # Download test results
    python client_example.py --server http://windows-host:5000 --get-result "TR-001" --results-path "C:\\Users\\user\\output" --output ./results.zip
    
    # Reset server
    python client_example.py --server http://windows-host:5000 --reset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests


def check_status(server: str) -> dict:
    """Check server status."""
    print(f"\n{'='*60}")
    print("Checking server status...")
    print(f"{'='*60}")
    
    response = requests.get(f"{server}/status")
    data = response.json()
    
    print(f"  APx State:     {data['apx_state']}")
    print(f"  Project:       {data.get('project_name') or '(none)'}")
    print(f"  Project SHA:   {(data.get('project_sha256') or '')[:16]}...")
    print(f"  Last Error:    {data.get('last_error') or '(none)'}")
    print(f"  Uptime:        {data['uptime_seconds']:.1f}s")
    
    return data


def reset_server(server: str) -> dict:
    """Reset the server (kill APx and clear state)."""
    print(f"\n{'='*60}")
    print("Resetting server...")
    print(f"{'='*60}")
    
    response = requests.post(f"{server}/reset")
    data = response.json()
    
    if data["success"]:
        print(f"  ✓ {data['message']}")
        print(f"  Killed processes: {data['killed_processes']}")
        print(f"  APx State: {data['apx_state']}")
    else:
        print(f"  ✗ Failed: {data['message']}")
    
    return data


def setup_project(server: str, project_path: Path, project_name: str | None = None) -> dict:
    """Upload project and launch APx."""
    print(f"\n{'='*60}")
    print(f"Setting up project: {project_path}")
    print(f"{'='*60}")
    
    if not project_path.exists():
        print(f"  ERROR: File not found: {project_path}")
        sys.exit(1)
    
    file_size_mb = project_path.stat().st_size / (1024 * 1024)
    print(f"  File size: {file_size_mb:.2f} MB")
    print("  Uploading...")
    
    with open(project_path, "rb") as f:
        files = {"file": (project_path.name, f)}
        data = {}
        if project_name:
            data["project_name"] = project_name
        
        response = requests.post(f"{server}/setup", files=files, data=data)
    
    result = response.json()
    
    if result["success"]:
        print(f"  ✓ Success!")
        print(f"  Project Name: {result['project_name']}")
        print(f"  Project Path: {result['project_path']}")
        print(f"  Project SHA:  {result['project_sha256'][:16]}...")
        print(f"  APx State:    {result['apx_state']}")
    else:
        print(f"  ✗ Failed: {result['message']}")
    
    return result


def list_structure(server: str) -> dict:
    """List project structure: sequences -> signal paths -> measurements."""
    print(f"\n{'='*60}")
    print("Project structure...")
    print(f"{'='*60}")
    
    response = requests.get(f"{server}/list")
    data = response.json()
    
    if not data["success"]:
        print(f"  ✗ Failed: {data['message']}")
        return data
    
    print(f"  Total Sequences:     {data['total_sequences']}")
    print(f"  Total Signal Paths:  {data['total_signal_paths']}")
    print(f"  Total Measurements:  {data['total_measurements']}")
    if data.get("active_sequence"):
        print(f"  Active Sequence:     {data['active_sequence']}")
    print()
    
    for seq in data["sequences"]:
        active = " (active)" if seq["name"] == data.get("active_sequence") else ""
        print(f"  Sequence [{seq['index']}]: {seq['name']}{active}")
        
        for sp in seq["signal_paths"]:
            checked = "✓" if sp["checked"] else "○"
            print(f"    [{checked}] Signal Path {sp['index']}: {sp['name']}")
            
            for m in sp["measurements"]:
                m_checked = "✓" if m["checked"] else "○"
                print(f"        [{m_checked}] {m['name']}")
    
    return data


def run_sequence(server: str, sequence_name: str, test_run_id: str = "") -> dict:
    """Run a sequence."""
    print(f"\n{'='*60}")
    print(f"Running sequence: {sequence_name}")
    if test_run_id:
        print(f"Test Run ID: {test_run_id}")
    print(f"{'='*60}")
    
    response = requests.post(
        f"{server}/run-sequence",
        json={"sequence_name": sequence_name, "test_run_id": test_run_id},
    )
    data = response.json()
    
    if not data["success"]:
        print(f"  ✗ Failed: {data['message']}")
        return data
    
    status = "✓ PASSED" if data["passed"] else "✗ FAILED"
    print(f"  {status}")
    print(f"  Sequence:    {data['sequence_name']}")
    print(f"  Test Run ID: {data['test_run_id'] or '(none)'}")
    print(f"  Duration:    {data['duration_seconds']:.2f}s")
    
    return data


def get_result(server: str, test_run_id: str, results_path: str, output_path: Path) -> bool:
    """Download test results as a zip file."""
    print(f"\n{'='*60}")
    print(f"Getting results for: {test_run_id}")
    print(f"Searching in: {results_path}")
    print(f"{'='*60}")
    
    response = requests.post(
        f"{server}/get-result",
        json={"test_run_id": test_run_id, "results_path": results_path},
        stream=True,
    )
    
    # Check if we got a JSON error response
    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        data = response.json()
        print(f"  ✗ Failed: {data['message']}")
        return False
    
    # Check if we got a zip file
    if response.status_code != 200:
        print(f"  ✗ Failed: HTTP {response.status_code}")
        return False
    
    # Save the zip file
    total_size = 0
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            total_size += len(chunk)
    
    size_mb = total_size / (1024 * 1024)
    print(f"  ✓ Downloaded: {output_path}")
    print(f"  Size: {size_mb:.2f} MB")
    
    return True


def shutdown_server(server: str, force: bool = False) -> dict:
    """Shutdown APx gracefully."""
    print(f"\n{'='*60}")
    print("Shutting down APx...")
    print(f"{'='*60}")
    
    response = requests.post(f"{server}/shutdown", json={"force": force})
    data = response.json()
    
    if data["success"]:
        print(f"  ✓ {data['message']}")
    else:
        print(f"  ✗ Failed: {data['message']}")
    
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Example client for APx Control Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server",
        default="http://localhost:5000",
        help="Server URL (default: http://localhost:5000)",
    )
    parser.add_argument(
        "--project",
        type=Path,
        help="Path to .approjx project file to upload",
    )
    parser.add_argument(
        "--project-name",
        help="Optional name for the project",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only check status, don't setup or list",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset the server (kill APx and clear state)",
    )
    parser.add_argument(
        "--shutdown",
        action="store_true",
        help="Shutdown APx gracefully",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force shutdown (with --shutdown)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List project structure (sequences -> signal paths -> measurements)",
    )
    parser.add_argument(
        "--run-sequence",
        metavar="NAME",
        help="Run the named sequence",
    )
    parser.add_argument(
        "--test-run-id",
        default="",
        help="Test run ID to associate with sequence run (default: empty)",
    )
    parser.add_argument(
        "--get-result",
        metavar="TEST_RUN_ID",
        help="Download results for the given test run ID",
    )
    parser.add_argument(
        "--results-path",
        default="",
        help="Path on the server to search for results (e.g. C:\\Users\\user\\output)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results.zip"),
        help="Local path to save the downloaded results zip (default: results.zip)",
    )
    args = parser.parse_args()
    
    # Check server health first
    try:
        response = requests.get(f"{args.server}/health", timeout=5)
        if response.status_code != 200:
            print(f"ERROR: Server returned {response.status_code}")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to server at {args.server}")
        sys.exit(1)
    
    print(f"Connected to APx Control Server at {args.server}")
    
    # Handle reset
    if args.reset:
        reset_server(args.server)
        return
    
    # Handle shutdown
    if args.shutdown:
        shutdown_server(args.server, force=args.force)
        return
    
    # Handle get-result (doesn't require APx to be running)
    if args.get_result:
        if not args.results_path:
            print("ERROR: --results-path is required with --get-result")
            sys.exit(1)
        success = get_result(
            args.server,
            args.get_result,
            args.results_path,
            args.output,
        )
        sys.exit(0 if success else 1)
    
    # Always check status first
    status = check_status(args.server)
    
    if args.status_only:
        return
    
    # If project provided, set it up
    setup_result = None
    if args.project:
        setup_result = setup_project(
            args.server,
            args.project,
            args.project_name,
        )
        
        if not setup_result["success"]:
            sys.exit(1)
    
    # Check if APx is running and ready
    apx_ready = status["apx_state"] == "idle" or (setup_result and setup_result.get("success"))
    
    if not apx_ready:
        print("\n  APx not in idle state. Use --project to upload a project first.")
        return
    
    # List structure if requested or after setup
    if args.list or args.project:
        list_structure(args.server)
    
    # Run sequence if requested
    if args.run_sequence:
        run_sequence(args.server, args.run_sequence, args.test_run_id)


if __name__ == "__main__":
    main()
