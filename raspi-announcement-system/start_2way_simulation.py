"""
start_2way_simulation.py
========================
Launches TWO Aura peer nodes locally for testing 2-way communication.

  Node A → http://localhost:5000  (peer: 5001)
  Node B → http://localhost:5001  (peer: 5000)

Open both browser tabs side by side and test two-way broadcasting!
"""

import subprocess
import sys
import time
import os
import signal

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
NODE_SCRIPT = os.path.join(BASE_DIR, "aura_node.py")

def launch_node(name, port, peer_port):
    cmd = [
        sys.executable, NODE_SCRIPT,
        "--name",   name,
        "--port",   str(port),
        "--peer",   f"http://localhost:{peer_port}",
        "--engine", "pyttsx3",
    ]
    print(f"[Simulation] Starting {name} on port {port} (peer -> {peer_port})")
    return subprocess.Popen(cmd, cwd=BASE_DIR)

if __name__ == "__main__":
    print("""
+--------------------------------------------------------------+
|   AURA 2-Way Intercom -- Local Simulation                    |
+--------------------------------------------------------------+
|  Node A -> http://localhost:5000  (peer: 5001)               |
|  Node B -> http://localhost:5001  (peer: 5000)               |
|                                                              |
|  Open BOTH tabs in your browser and test bi-directional      |
|  broadcasting!  Press Ctrl+C to stop both nodes.             |
+--------------------------------------------------------------+
""")

    proc_a = launch_node("Node A", 5000, 5001)
    time.sleep(1.5)  # Stagger start so ports bind cleanly
    proc_b = launch_node("Node B", 5001, 5000)

    print("\n[Simulation] Both nodes running. Press Ctrl+C to stop.\n")

    try:
        while True:
            # Check if either process died unexpectedly
            if proc_a.poll() is not None:
                print("[Simulation] WARNING: Node A exited unexpectedly.")
                break
            if proc_b.poll() is not None:
                print("[Simulation] WARNING: Node B exited unexpectedly.")
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[Simulation] Shutting down both nodes...")
    finally:
        for proc, name in [(proc_a, "Node A"), (proc_b, "Node B")]:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"[Simulation] {name} stopped.")
            except Exception:
                proc.kill()
