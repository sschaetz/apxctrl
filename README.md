# APx Control Web Server

A Flask-based web server for controlling Audio Precision APx test equipment remotely.

## Installation

On Windows, install Python and dependencies:

In PowerShell:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "C:\Users\sschaetz\.local\bin;$env:Path"
uv python install 3.11.4
```

Install dependencies:
```bash
uv pip install -e .
```

## Running the Server

Start the web server:
```bash
python main.py
```

The server will be available at `http://0.0.0.0:5000` and accepts requests from any IP address.



