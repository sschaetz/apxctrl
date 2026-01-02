import sys, clr 
import time
import threading
import os
from pathlib import Path
from flask import Flask, jsonify, request
from datetime import datetime

clr.AddReference("System.Drawing")              
clr.AddReference("System.Windows.Forms")

# Add a reference to the APx API        
clr.AddReference(r"C:\\Program Files\\Audio Precision\\APx500 9.1\\API\\AudioPrecision.API2.dll")    
clr.AddReference(r"C:\\Program Files\\Audio Precision\\APx500 9.1\\API\\AudioPrecision.API.dll") 

from AudioPrecision.API import APx500_Application, APxOperatingMode

app = Flask(__name__)

# Global variables to track state
apx_instance = None
sequence_status = {
    "running": False,
    "last_started": None,
    "last_completed": None,
    "error": None
}

def initialize_apx():
    """Initialize the APx application instance"""
    global apx_instance
    try:
        if apx_instance is None:
            print("Initializing APx application...")
            apx_instance = APx500_Application(APxOperatingMode.SequenceMode, "-Demo -APx517")
            apx_instance.Visible = True
            
            # Get the directory where this script is located
            script_dir = Path(__file__).parent.absolute()
            
            # Navigate to the project file relative to script location
            project_path = script_dir / ".." / ".." / ".." / "ops" / "projects" / "P0001" / "apx517_2cctest_template.approjx"
            project_path = project_path.resolve()  # Convert to absolute path
            
            print(f"Opening project: {project_path}")
            apx_instance.OpenProject(str(project_path))
            print("APx application initialized successfully")
        return True
    except Exception as e:
        print(f"Error initializing APx: {e}")
        sequence_status["error"] = str(e)
        return False

def run_sequence_async():
    """Run the APx sequence in a separate thread"""
    global sequence_status
    try:
        sequence_status["running"] = True
        sequence_status["last_started"] = datetime.now().isoformat()
        sequence_status["error"] = None
        
        print("Starting APx sequence...")
        time.sleep(10)  # Wait time before running sequence
        apx_instance.Sequence.Run()
        
        sequence_status["running"] = False
        sequence_status["last_completed"] = datetime.now().isoformat()
        print("APx sequence completed successfully")
        
    except Exception as e:
        sequence_status["running"] = False
        sequence_status["error"] = str(e)
        print(f"Error running sequence: {e}")

@app.route('/')
def home():
    """Home page with basic information"""
    return jsonify({
        "service": "APx Control Server",
        "version": "0.1.0",
        "endpoints": {
            "/start": "POST - Start a new sequence",
            "/status": "GET - Get current status",
            "/health": "GET - Health check"
        }
    })

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "apx_initialized": apx_instance is not None
    })

@app.route('/status')
def status():
    """Get current sequence status"""
    return jsonify(sequence_status)

@app.route('/start', methods=['POST'])
def start_sequence():
    """Start a new APx sequence"""
    global sequence_status
    
    # Check if sequence is already running
    if sequence_status["running"]:
        return jsonify({
            "error": "Sequence is already running",
            "status": sequence_status
        }), 409
    
    # Initialize APx if not already done
    if not initialize_apx():
        return jsonify({
            "error": "Failed to initialize APx application",
            "status": sequence_status
        }), 500
    
    # Start sequence in background thread
    sequence_thread = threading.Thread(target=run_sequence_async, daemon=True)
    sequence_thread.start()
    
    return jsonify({
        "message": "Sequence started successfully",
        "status": sequence_status
    })

@app.route('/stop', methods=['POST'])
def stop_sequence():
    """Stop the current sequence (if possible)"""
    global apx_instance, sequence_status
    
    try:
        if apx_instance and sequence_status["running"]:
            # Note: Stopping mid-sequence might not be supported by all APx operations
            # This is a basic implementation
            sequence_status["running"] = False
            sequence_status["error"] = "Manually stopped"
            return jsonify({"message": "Sequence stop requested"})
        else:
            return jsonify({"message": "No sequence currently running"})
    except Exception as e:
        return jsonify({"error": f"Failed to stop sequence: {e}"}), 500

def main():
    """Main function to start the web server"""
    print("Starting APx Control Web Server...")
    print("Server will be available at: http://0.0.0.0:5000")
    print("Endpoints:")
    print("  GET  / - Service information")
    print("  GET  /health - Health check")
    print("  GET  /status - Sequence status")
    print("  POST /start - Start sequence")
    print("  POST /stop - Stop sequence")
    
    # Run Flask app on all interfaces (0.0.0.0) to accept requests from any IP
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()
