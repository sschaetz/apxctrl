#!/usr/bin/env python3
"""
Example client for APx Control Server.

Usage:
    python client_example.py --server http://windows-host:5000 --project /path/to/project.approjx
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
    
    # NOTE: /reset is a POST endpoint!
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


def get_sequence_structure(server: str) -> dict:
    """Get the sequence structure (signal paths and measurements)."""
    print(f"\n{'='*60}")
    print("Getting sequence structure...")
    print(f"{'='*60}")
    
    response = requests.get(f"{server}/sequence/structure")
    data = response.json()
    
    if not data["success"]:
        print(f"  ✗ Failed: {data['message']}")
        return data
    
    print(f"  Total Signal Paths:  {data['total_signal_paths']}")
    print(f"  Total Measurements:  {data['total_measurements']}")
    print()
    
    for sp in data["signal_paths"]:
        checked = "✓" if sp["checked"] else "○"
        print(f"  [{checked}] Signal Path {sp['index']}: {sp['name']}")
        
        for m in sp["measurements"]:
            m_checked = "✓" if m["checked"] else "○"
            print(f"      [{m_checked}] Measurement {m['index']}: {m['name']}")
    
    return data


def run_signal_path(server: str, signal_path: str, timeout: float = 120.0) -> dict:
    """Run all checked measurements in a signal path."""
    print(f"\n{'='*60}")
    print(f"Running signal path: {signal_path}")
    print(f"{'='*60}")
    
    response = requests.post(
        f"{server}/run-signal-path",
        json={"signal_path": signal_path, "timeout_seconds": timeout},
    )
    data = response.json()
    
    if not data["success"]:
        print(f"  ✗ Failed: {data['message']}")
        return data
    
    print(f"  ✓ Signal path completed!")
    print(f"  Measurements run:    {data['measurements_run']}")
    print(f"  Measurements passed: {data['measurements_passed']}")
    print(f"  Measurements failed: {data['measurements_failed']}")
    print(f"  Total duration:      {data['total_duration_seconds']:.2f}s")
    print()
    
    # Print individual results
    for result in data["results"]:
        status = "✓ PASS" if result["passed"] else "✗ FAIL"
        if not result["success"]:
            status = "⚠ ERROR"
        
        print(f"  [{status}] {result['name']} ({result['duration_seconds']:.2f}s)")
        
        if result.get("meter_values"):
            lower_limits = result.get("lower_limits") or {}
            upper_limits = result.get("upper_limits") or {}
            
            for ch, val in result["meter_values"].items():
                lower = lower_limits.get(ch)
                upper = upper_limits.get(ch)
                
                # Format with limits if available
                if lower is not None and upper is not None:
                    print(f"           {ch}: {lower:.2f} <= {val:.2f} <= {upper:.2f}")
                elif lower is not None:
                    print(f"           {ch}: {lower:.2f} <= {val:.2f}")
                elif upper is not None:
                    print(f"           {ch}: {val:.2f} <= {upper:.2f}")
                else:
                    print(f"           {ch}: {val:.2f}")
        
        if result.get("error"):
            print(f"           Error: {result['error']}")
    
    return data


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
        help="Only check status, don't setup or get structure",
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
        "--run-signal-path",
        metavar="NAME",
        help="Run all checked measurements in the named signal path",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout per measurement in seconds (default: 120)",
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
    
    # Always check status first
    status = check_status(args.server)
    
    if args.status_only:
        return
    
    # If project provided, set it up
    if args.project:
        setup_result = setup_project(
            args.server,
            args.project,
            args.project_name,
        )
        
        if not setup_result["success"]:
            sys.exit(1)
    
    # Get sequence structure if APx is running
    if status["apx_state"] == "idle" or (args.project and setup_result.get("success")):
        structure = get_sequence_structure(args.server)
        
        # Run signal path if requested
        if args.run_signal_path:
            run_signal_path(args.server, args.run_signal_path, args.timeout)
    else:
        print("\n  Skipping sequence structure (APx not in idle state)")
        print("  Use --project to upload a project first")


if __name__ == "__main__":
    main()

