import socket
import threading
import time

class RobotLink:
    def __init__(self, listen_port=5005, target_port=5006):
        self.listen_port = listen_port
        self.target_port = target_port
        
        # We will auto-discover the ESP32 IP when it sends the first telemetry packet
        self.target_ip = None
        
        # Thread-safe state dictionary
        self.state = {
            "connected": False,
            "battery": 100,
            "mode": "manual",
            "distance_cm": 0,
            "ir_left": False,
            "ir_right": False,
            "servo_angle": 90,
            "direction": "stop",
            "speed": 0,
            "last_seen": 0
        }
        
        # Create UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow reusing the port (useful during active development restarts)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', self.listen_port))
        
        self.running = False

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            print(f"[RobotLink] Listening for UDP telemetry on port {self.listen_port}...")

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass

    def send_command(self, cmd_string):
        """Send a string like <MOVE,forward,180> to the ESP32"""
        if self.target_ip:
            try:
                self.sock.sendto(cmd_string.encode('utf-8'), (self.target_ip, self.target_port))
                print(f"[RobotLink] Sent to {self.target_ip}: {cmd_string}")
            except Exception as e:
                print(f"[RobotLink] Error sending command: {e}")
        else:
            print(f"[RobotLink] Warning: Cannot send {cmd_string}. ESP32 IP not discovered yet!")

    def _listen_loop(self):
        self.sock.settimeout(1.0) # Check self.running gracefully
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                message = data.decode('utf-8').strip()
                
                # Auto-discover the ESP32 IP
                if self.target_ip != addr[0]:
                    print(f"[RobotLink] Connected to ESP32 at IP: {addr[0]}")
                    self.target_ip = addr[0]
                
                self.state["connected"] = True
                self.state["last_seen"] = time.time()
                
                # Parse: <TELEMETRY,battery,distance,ir_left,ir_right>
                if message.startswith("<TELEMETRY") and message.endswith(">"):
                    parts = message[1:-1].split(",")
                    if len(parts) >= 5:
                        self.state["battery"] = int(parts[1])
                        self.state["distance_cm"] = int(parts[2])
                        self.state["ir_left"] = bool(int(parts[3]))
                        self.state["ir_right"] = bool(int(parts[4]))
                        
            except socket.timeout:
                # Check for disconnection
                if self.state["connected"] and (time.time() - self.state["last_seen"] > 3.0):
                    self.state["connected"] = False
                    print("[RobotLink] ESP32 connection lost (timeout)")
            except Exception as e:
                print(f"[RobotLink] Error reading socket: {e}")

# Global instance to be imported by routes
robot_link = RobotLink()
