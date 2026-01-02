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
cd chromatic/experimental/seb/apxctrl
uv pip install -e .
```

## Running the Server

Basic usage:

```bash
python main.py
```

With options:

```bash
# Kill any existing APx500 processes on startup
python main.py --kill-existing

# Custom port
python main.py --port 8080

# Debug mode (not for production)
python main.py --debug
```

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

### POST /run-step

Run a sequence/signal step. (Currently a stub - .NET API integration pending)

**Request**: JSON

```json
{
    "sequence": "Frequency Response",
    "signal": "1kHz Sine",
    "timeout_seconds": 120
}
```

**Response**:

```json
{
    "success": true,
    "message": "Step completed successfully",
    "sequence": "Frequency Response",
    "signal": "1kHz Sine",
    "duration_seconds": 5.23,
    "apx_state": "idle"
}
```

### POST /get-results

Get result files from a directory as a ZIP archive. (Currently a stub)

**Request**: JSON

```json
{
    "directory": "C:\\APx\\Results\\Run001"
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

# Run a step
response = requests.post(
    f"{SERVER}/run-step",
    json={
        "sequence": "Frequency Response",
        "signal": "1kHz Sine",
        "timeout_seconds": 120
    }
)
print(response.json())

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

1. **`apx_controller.py:run_step()`**: Actual APx .NET API calls for running sequences
2. **`main.py:/get-results`**: Zip directory and return as download

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
