# APx Control Server

A Flask-based web server for controlling Audio Precision APx500 test equipment remotely.

## Architecture

This server acts as a shim between a Linux client and the Windows-based APx500 application.
It exposes a REST API that wraps the APx500 .NET API.

```
┌─────────────┐      HTTP         ┌─────────────┐      .NET API      ┌──────────────┐
│   Client    │  ─────────────►   │   apxctrl   │  ───────────────►  │  APx500      │
│             │  ◄─────────────   │             │  ◄───────────────  │  Application │
└─────────────┘                   └─────────────┘                    └──────────────┘
```

### Design Principles

- **1:1 client model**: One client, one server, sequential requests
- **State tracking**: Server tracks APx state, loaded project (name + SHA256)

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

## Running tests

```bash
uv run py.test -v
```

## Running the Server

Allow access through firewall

```bash
netsh firewall add portopening TCP 5000 "apxctrl"
netsh advfirewall firewall add rule name="apxctrl TCP Port 5000" dir=in action=allow protocol=TCP localport=5000
netsh advfirewall firewall add rule name="apxctrl TCP Port 5000" dir=out action=allow protocol=TCP localport=5000
```

Basic usage of server:

```bash
uv run apxctrl --kill-existing
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

Basic usage of client (demonstrates API usage):

```bash
uv run apxctrl-client --help
```
