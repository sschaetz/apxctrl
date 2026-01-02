# APx Control Server

A Flask-based web server for controlling Audio Precision APx500 test equipment remotely.

**Designed for factory environments** with robustness and reliability (thousands of runs/day).

## Architecture

This server acts as a shim between a Linux client and the Windows-based APx500 application.
It exposes a REST API that wraps the APx500 .NET API.

```
┌─────────────┐         HTTP          ┌─────────────────┐        .NET API       ┌──────────────┐
│   Linux     │  ─────────────────►   │   APx Control   │  ──────────────────►  │   APx500     │
│   Client    │  ◄─────────────────   │   Server (Win)  │  ◄──────────────────  │   Application│
└─────────────┘                       └─────────────────┘                       └──────────────┘
```

### Design Principles

- **1:1 client model**: One client, one server, sequential requests
- **State tracking**: Server tracks APx state, loaded project (name + SHA256)
- **Robustness**: Clear error states, recovery mechanisms
- **Factory-ready**: Designed for high-volume, unattended operation

## Installation

On Windows, install Python and uv:

```powershell
# Install uv (Python package manager)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "C:\Users\$env:USERNAME\.local\bin;$env:Path"

# Install Python 3.11
uv python install 3.11.4
```

Install dependencies:

```bash
uv pip install -e .
```

## Running the Server

Allow access through firewall

```bash
netsh firewall add portopening TCP 5000 "apxctrl"
netsh advfirewall firewall add rule name="apxctrl TCP Port 5000" dir=in action=allow protocol=TCP localport=5000
netsh advfirewall firewall add rule name="apxctrl TCP Port 5000" dir=out action=allow protocol=TCP localport=5000
```

Basic usage:

```bash
uv run .\main.py --kill-existing
```

Note that you can't run this from a no-GUI terminal (like an SSH session), it will not work.

With options:

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--kill-existing` | Kill running APx500 processes on startup | False |
| `--host HOST` | Host to bind to | 0.0.0.0 |
| `--port PORT` | Port to bind to | 5000 |
| `--debug` | Enable Flask debug mode | False |

## API Endpoints

### GET /

Returns service information and available endpoints.

### GET /health

Quick health check. Returns:

```json
{
    "status": "healthy",
    "timestamp": "2024-01-15T10:30:00",
    "apx_state": "idle",
    "uptime_seconds": 3600.5
}
```

### GET /status

Detailed status including project information:

```json
{
    "apx_state": "idle",
    "project_name": "my_test_project",
    "project_sha256": "abc123...",
    "project_path": "C:\\Users\\...\\Temp\\apxctrl\\my_test.approjx",
    "apx_pid": 12345,
    "last_error": null,
    "server_started_at": "2024-01-15T09:00:00",
    "uptime_seconds": 5400.0
}
```

### POST /setup

Upload a project file and launch APx500.

**Request**: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | Yes | The .approjx project file |
| project_name | string | No | Name for the project (defaults to filename) |
| apx_mode | string | No | APx operating mode (default: SequenceMode) |
| apx_args | string | No | APx CLI arguments (default: -Demo -APx517) |

**Example using curl**:

```bash
curl -X POST http://windows-host:5000/setup \
  -F "file=@/path/to/project.approjx" \
  -F "project_name=my_project" \
  -F "apx_args=-APx517"
```

**Response**:

```json
{
    "success": true,
    "message": "APx launched and project loaded successfully",
    "project_name": "my_project",
    "project_sha256": "abc123def456...",
    "project_path": "C:\\Users\\...\\Temp\\apxctrl\\project.approjx",
    "apx_state": "idle",
    "killed_processes": 0
}
```

### GET /sequence/structure

Get the structure of the loaded sequence (signal paths and their measurements).

**Response**:

```json
{
    "success": true,
    "message": "Sequence structure retrieved successfully",
    "signal_paths": [
        {
            "index": 0,
            "name": "Analog Output",
            "checked": true,
            "measurements": [
                {"index": 0, "name": "Level and Gain", "checked": true},
                {"index": 1, "name": "THD+N", "checked": true},
                {"index": 2, "name": "Frequency Response", "checked": true}
            ]
        },
        {
            "index": 1,
            "name": "Digital Input",
            "checked": true,
            "measurements": [
                {"index": 0, "name": "Level and Gain", "checked": true},
                {"index": 1, "name": "Crosstalk", "checked": false}
            ]
        }
    ],
    "total_signal_paths": 2,
    "total_measurements": 5,
    "apx_state": "idle"
}
```

### POST /run-signal-path

Run all checked measurements in a signal path.

**Request**: JSON

```json
{
    "signal_path": "Analog Output",
    "timeout_seconds": 120
}
```

**Response**:

```json
{
    "success": true,
    "message": "Signal path 'Analog Output' completed. 3/3 passed.",
    "signal_path": "Analog Output",
    "measurements_run": 3,
    "measurements_passed": 3,
    "measurements_failed": 0,
    "total_duration_seconds": 15.5,
    "results": [
        {
            "name": "Level and Gain",
            "success": true,
            "passed": true,
            "duration_seconds": 5.2,
            "meter_values": {"ch1": -0.5, "ch2": -0.4}
        },
        ...
    ],
    "apx_state": "idle"
}
```

### POST /run-all

Run all checked measurements in all checked signal paths and export reports.

**Request**: JSON (all optional)

```json
{
    "timeout_seconds": 120,
    "export_csv": true,
    "export_pdf": false,
    "report_directory": null
}
```

**Response**:

```json
{
    "success": true,
    "message": "All measurements completed. 5/5 passed.",
    "signal_paths_run": 2,
    "measurements_run": 5,
    "measurements_passed": 5,
    "measurements_failed": 0,
    "total_duration_seconds": 25.3,
    "all_passed": true,
    "csv_report_path": "C:\\Users\\...\\Temp\\apxctrl\\reports\\report_20240115_103000.csv",
    "pdf_report_path": null,
    "results_by_signal_path": {
        "Analog Output": [...],
        "Digital Input": [...]
    },
    "apx_state": "idle"
}
```

### POST /shutdown

Shutdown APx500 gracefully.

**Request**: JSON (optional)

```json
{
    "force": false
}
```

### POST /reset

Kill APx500 and reset server state. Use for recovery after errors.

**Response**:

```json
{
    "success": true,
    "message": "Reset complete. Killed 1 process(es).",
    "killed_processes": 1,
    "apx_state": "not_running"
}
```

## State Machine

The server tracks APx state:

```
                    ┌─────────────┐
        ┌──────────►│ NOT_RUNNING │◄──────────┐
        │           └──────┬──────┘           │
        │                  │                  │
        │             /setup                  │
        │                  │                  │
        │                  ▼                  │
        │           ┌─────────────┐           │
        │           │  STARTING   │           │
        │           └──────┬──────┘           │
        │                  │                  │
        │             success              /shutdown
        │                  │               /reset
        │                  ▼                  │
        │           ┌─────────────┐           │
   /reset           │    IDLE     │───────────┤
        │           └──────┬──────┘           │
        │                  │                  │
        │            /run-step                │
        │                  │                  │
        │                  ▼                  │
        │           ┌─────────────┐           │
        │           │ RUNNING_STEP│           │
        │           └──────┬──────┘           │
        │                  │                  │
        │            complete                 │
        │                  │                  │
        │                  ▼                  │
        │           ┌─────────────┐           │
        └───────────│    ERROR    │───────────┘
                    └─────────────┘
```

## Example Client Usage (Python)

```python
import requests

SERVER = "http://windows-host:5000"

# Upload project and launch APx
with open("project.approjx", "rb") as f:
    response = requests.post(
        f"{SERVER}/setup",
        files={"file": f},
        data={"project_name": "my_test"}
    )
    print(response.json())

# Get sequence structure
response = requests.get(f"{SERVER}/sequence/structure")
structure = response.json()
print(f"Signal paths: {structure['total_signal_paths']}")
print(f"Measurements: {structure['total_measurements']}")
for sp in structure['signal_paths']:
    print(f"  {sp['name']}:")
    for m in sp['measurements']:
        print(f"    - {m['name']} (checked={m['checked']})")

# Run a specific signal path
response = requests.post(
    f"{SERVER}/run-signal-path",
    json={"signal_path": "Analog Output", "timeout_seconds": 120}
)
result = response.json()
print(f"Passed: {result['measurements_passed']}/{result['measurements_run']}")

# Or run everything and export reports
response = requests.post(
    f"{SERVER}/run-all",
    json={"export_csv": True, "export_pdf": True}
)
result = response.json()
print(f"All passed: {result['all_passed']}")
print(f"CSV report: {result['csv_report_path']}")
print(f"PDF report: {result['pdf_report_path']}")

# Get status
response = requests.get(f"{SERVER}/status")
print(response.json())

# Shutdown
response = requests.post(f"{SERVER}/shutdown")
print(response.json())
```

## File Structure

```
apxctrl/
├── main.py              # Flask app with all endpoints
├── models.py            # Pydantic models for requests/responses/state
├── apx_controller.py    # APx process management + .NET API wrapper
├── pyproject.toml       # Dependencies
├── README.md            # This file
└── .python-version      # Python version
```

## Development Notes

### Stubs to Implement

1. **`apx_controller.py:get_sequence_structure()`**: Get actual signal paths/measurements from APx
2. **`apx_controller.py:run_measurement()`**: Run measurement via APx .NET API, get results
3. **`apx_controller.py:run_all_and_export()`**: Export reports via `APx.Sequence.Report.ExportText/Pdf()`

### Production Considerations

- For production, consider using a WSGI server like `waitress`:
  ```bash
  pip install waitress
  waitress-serve --host=0.0.0.0 --port=5000 main:app
  ```

- The server is single-threaded by design (1:1 client model)

## Security Warning

⚠️ **This server accepts connections from any IP address (`0.0.0.0`)**.
For production, consider:
- Firewall rules to restrict access
- VPN/network segmentation
- Adding authentication if needed
