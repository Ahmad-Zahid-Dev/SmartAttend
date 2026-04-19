"""
Antigravity: Student Face Recognition Attendance System
========================================================
Main entry point. Initializes the environment and launches the Flask app.

Usage:
    python main.py
"""

import os
import sys
import cv2
import shutil

# Ensure project root is in Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

def setup_environment():
    """Create required project directories and copy resources."""
    print("[SYSTEM] Initializing Antigravity Environment...")
    
    # 1. Directories
    dirs = ['dataset', 'exports']
    for d in dirs:
        os.makedirs(os.path.join(PROJECT_ROOT, d), exist_ok=True)
    
    # 2. Haar Cascade resource check
    cascade_file = "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_file):
        source = os.path.join(cv2.data.haarcascades, cascade_file)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(PROJECT_ROOT, cascade_file))
            print(f"[SYSTEM] Haar Cascade resource verified.")

def main():
    """Main application entry point."""
    print("-" * 50)
    print(" ANTIGRAVITY - AI Attendance Control System")
    print("-" * 50)

    # Step 1: Prepare filesystem
    setup_environment()

    # Step 2: Initialize Database (Importing triggers auto-init in db.py)
    try:
        from database.db import db
        if db._cache_loaded:
            print(f"[DATABASE] [OK] Cache loaded — {len(db._cache['students'])} students, {len(db._cache['classes'])} classes.")
        else:
            print("[DATABASE] [WARN] Cache not fully loaded — server will start in degraded mode.")
    except Exception as e:
        print(f"[DATABASE] [WARN] Startup issue ({e}) — server starting in limited mode. Quota may be exhausted; will recover automatically.")

    # Step 3: Launch Web Interface
    print("[NETWORK] Portal launching @ http://localhost:5000")
    from app import app
    
    # Use threaded=True for OpenCV video streaming stability
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
