import os
import sys
import time
import subprocess
import threading

def start_server(project_root):
    """
    Launches the TTS Flask Server in a separate subprocess.
    """
    server_path = os.path.join(project_root, 'raspi_tts', 'tts_server.py')
    print(f"[Simulation] Starting TTS Server: {server_path}")
    
    # Run the server using the current Python interpreter
    return subprocess.Popen(
        [sys.executable, server_path],
        cwd=os.path.join(project_root, 'raspi_tts')
    )

def start_client(project_root):
    """
    Launches the VTT Client in the main process thread.
    """
    client_path = os.path.join(project_root, 'raspi_vtt', 'vtt_client.py')
    print(f"[Simulation] Starting VTT Client: {client_path}")
    
    # We execute it using subprocess.run to avoid space quoting issues on Windows
    try:
        subprocess.run([sys.executable, client_path], cwd=os.path.dirname(client_path))
    except KeyboardInterrupt:
        pass

def main():
    print("="*60)
    print("      AURA ANNOUNCEMENT SYSTEM - SIMULATION ENVIRONMENT      ")
    print("="*60)
    print("This script spins up both the Server and a Client locally.")
    print("This lets you test the Web Dashboard and Text-to-Speech")
    print("announcements directly on your PC!\n")
    
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    server_proc = None
    try:
        # 1. Start Server
        server_proc = start_server(project_root)
        
        # Give Flask server a few seconds to spin up and bind to port 5000
        print("[Simulation] Waiting for Server to initialize on port 5000...")
        time.sleep(2.5)
        
        print("\n[Simulation] Server is ready! Dashboard available at: http://localhost:5000")
        print("[Simulation] Launching simulated VTT Client...\n")
        
        # 2. Run client in main process (it will prompt for keyboard inputs)
        start_client(project_root)
        
    except KeyboardInterrupt:
        print("\n[Simulation] Simulation interrupted by user.")
    except Exception as e:
        print(f"\n[Simulation] Error encountered: {e}")
    finally:
        # 3. Clean shutdown of background server process
        if server_proc:
            print("\n[Simulation] Shutting down TTS Server...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()
            print("[Simulation] Cleanup complete. Goodbye!")

if __name__ == '__main__':
    main()
