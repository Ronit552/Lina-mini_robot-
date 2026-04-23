"""
=============================================================
  LINA - Robot Control Backend
  Flask + Flask-SocketIO Server
=============================================================

HARDWARE OVERVIEW:
  - Rotating head with HC-SR04 ultrasonic sensor (servo-driven, 0-180°)
  - Fixed front-facing HC-SR04 ultrasonic sensor
  - 3x IR obstacle sensors: LEFT, CENTER, RIGHT
  - 2x DC motors (wheels) controlled via L298N / L293D
  - MAX9814 microphone for voice commands
  - ESP32 as the main communication bridge (via WiFi UDP or Serial)

COMMUNICATION FLOW:
  Browser (Dashboard) <--WebSocket--> Flask Server <--UDP/Serial--> ESP32 <---> Arduino/Motors/Sensors

HOW TO CONNECT ESP32:
  1. ESP32 sends sensor data as JSON via UDP to this server (see RECEIVE section).
  2. This server decodes the JSON, rebroadcasts it to the browser via SocketIO.
  3. Browser sends control commands via SocketIO events.
  4. Server receives those commands and forwards them to ESP32 via UDP (see SEND section).

  Expected JSON from ESP32 (sensor packet):
  {
    "head_angle":   90,       // Current servo angle (0 to 180 degrees)
    "head_dist":    45.3,     // Head sonar distance in cm
    "front_dist":   22.1,     // Front sonar distance in cm
    "ir_left":      0,        // 0 = clear, 1 = obstacle detected
    "ir_center":    1,
    "ir_right":     0,
    "battery":      7.4,      // Battery voltage in volts
    "mode":         "MANUAL"  // Current robot mode
  }
=============================================================
"""

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import threading
import time
import random
import math
import os

# ─────────────────────────────────────────────
#  UDP Config for ESP32 Communication
# ─────────────────────────────────────────────
import json
import socket as udp_socket

ESP32_IP   = "192.168.1.50"  # <-- Set your ESP32's IP address here
ESP32_PORT = 4210             # <-- UDP port ESP32 listens on
LISTEN_PORT = 4211            # <-- UDP port this server listens on for sensor data

udp_sock = udp_socket.socket(udp_socket.AF_INET, udp_socket.SOCK_DGRAM)
udp_sock.setsockopt(udp_socket.SOL_SOCKET, udp_socket.SO_REUSEADDR, 1)
udp_sock.bind(("0.0.0.0", LISTEN_PORT))
udp_sock.settimeout(1.0)
# ─────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "dev_key")
socketio = SocketIO(app, cors_allowed_origins="*")

#________________________________
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────
#  GLOBAL ROBOT STATE
#  This dictionary holds the latest known state
#  of the robot. Updated by incoming sensor data.
# ─────────────────────────────────────────────
robot_state = {
    "head_angle":   90,     # Head servo position (degrees): 0 = full left, 90 = center, 180 = full right
    "head_dist":    100.0,  # Head ultrasonic reading (cm)
    "front_dist":   100.0,  # Front ultrasonic reading (cm)
    "ir_left":      0,      # Left IR sensor: 0 = clear, 1 = obstacle
    "ir_center":    0,      # Center IR sensor
    "ir_right":     0,      # Right IR sensor
    "battery":      7.4,    # Battery voltage (V); alert if < 6.5V
    "mode":         "MANUAL",  # Current operating mode: MANUAL | AI_FOLLOW | VOICE
    "speed_limit":  50,     # Motor speed 0-100 (mapped to PWM duty cycle)
    "last_command": "stop"  # Last movement command sent to motors
}


# =============================================================
#  DEMO TELEMETRY GENERATOR
#  Simulates realistic sensor data without actual hardware.
#  ─────────────────────────────────────────────────────────────
#  TO REPLACE WITH REAL DATA:
#    1. Enable the UDP socket block above.
#    2. Replace the body of `generate_telemetry()` with the
#       UDP receive loop (see comments inside the function).
#    3. Parse the JSON from ESP32 into `robot_state`.
#    4. Call `broadcast_telemetry()` after every update.
# =============================================================

def generate_telemetry():
    """
    Background thread: Listens for incoming UDP packets from the ESP32.
    Decodes the JSON data, updates the global robot_state, and
    broadcasts the updated telemetry back out to the connected browser clients.
    """
    print(f'[UDP] Listening for ESP32 telemetry on port {LISTEN_PORT}...')
    
    while True:
        try:
            raw, addr = udp_sock.recvfrom(1024)
            data = json.loads(raw.decode())
            
            # Update our global state with the real data
            robot_state.update(data)
            
            # Immediately push state to the browser dashboard
            broadcast_telemetry()
            
        except udp_socket.timeout:
            # We didn't receive a packet in the time window; loop and try again
            pass
        except json.JSONDecodeError:
            print("[UDP] Error: Received malformed JSON payload.")
        except Exception as e:
            print(f"[UDP] Error: {e}")


def broadcast_telemetry():
    """
    Broadcasts the current robot_state to all connected browser clients.
    Called by the telemetry thread or by the UDP receive loop when real
    ESP32 data is received.

    The browser listens for the 'telemetry_update' SocketIO event and
    uses it to update the radar canvas, IR indicators, and proximity bar.
    """
    socketio.emit('telemetry_update', {
        # ── Sensor data ─────────────────────────────────────────────
        "head_angle": robot_state["head_angle"],   # degrees: 0–180
        "head_dist":  robot_state["head_dist"],    # cm
        "front_dist": robot_state["front_dist"],   # cm
        "ir": {
            "left":   robot_state["ir_left"],      # 0 or 1
            "center": robot_state["ir_center"],    # 0 or 1
            "right":  robot_state["ir_right"],     # 0 or 1
        },
        # ── System data ──────────────────────────────────────────────
        "battery":  robot_state["battery"],        # volts
        "mode":     robot_state["mode"],            # string
    })


# =============================================================
#  FLASK ROUTES
# =============================================================

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/cockpit')
def cockpit():
    return render_template('cockpit.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

@app.route('/debug')
def debug():
    return render_template('debug.html')


# =============================================================
#  SOCKETIO EVENT HANDLERS
#  These receive messages from the browser and (in the real
#  version) forward them to the ESP32 via UDP/Serial.
# =============================================================

@socketio.on('connect')
def handle_connect():
    """
    Fires when a browser tab connects. Sends a welcome log and
    pushes the current state immediately so the UI isn't blank.
    """
    print('[WS] Browser client connected.')
    emit('log_message', {'type': 'info', 'msg': 'LINA Core System — ONLINE'})
    # Push current state immediately on connect
    emit('telemetry_update', {
        "head_angle": robot_state["head_angle"],
        "head_dist":  robot_state["head_dist"],
        "front_dist": robot_state["front_dist"],
        "ir": {
            "left":   robot_state["ir_left"],
            "center": robot_state["ir_center"],
            "right":  robot_state["ir_right"],
        },
        "battery": robot_state["battery"],
        "mode":    robot_state["mode"],
    })


@socketio.on('disconnect')
def handle_disconnect():
    """Fires when a browser tab disconnects."""
    print('[WS] Browser client disconnected.')


@socketio.on('set_mode')
def handle_mode(mode):
    """
    Called when the user clicks MANUAL / AI_FOLLOW / VOICE in the browser.

    ── REAL ESP32 REPLACEMENT ──────────────────────────────────────
    Forward mode change to ESP32 so it can switch its own logic:

        cmd = json.dumps({"cmd": "SET_MODE", "mode": mode}).encode()
        udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
    ────────────────────────────────────────────────────────────────
    """
    valid_modes = ['MANUAL', 'AI_FOLLOW', 'VOICE']
    if mode not in valid_modes:
        emit('log_message', {'type': 'error', 'msg': f'Invalid mode: {mode}'})
        return

    robot_state['mode'] = mode
    print(f'[MODE] Switching to: {mode}')

    # Broadcast new mode to all connected browser tabs
    emit('mode_update', {'mode': mode}, broadcast=True)
    emit('log_message', {
        'type': 'info',
        'msg': f'Mode changed → {mode}'
    }, broadcast=True)


@socketio.on('control_input')
def handle_control(data):
    """
    Called when the user interacts with the D-pad in the browser.

    Expected payload (data dict):
      From D-Pad:   { "type": "dpad",   "dir": "forward"|"backward"|"left"|"right"|"stop" }
      From Slider:  { "type": "slider", "name": "speed"|"pan", "value": <int> }

    ── REAL ESP32 REPLACEMENT ──────────────────────────────────────
    After updating robot_state, forward the command to ESP32:

        cmd = json.dumps({"cmd": "MOVE", "dir": data['dir'],
                          "speed": robot_state['speed_limit']}).encode()
        udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
    ────────────────────────────────────────────────────────────────
    """
    input_type = data.get('type')

    # ── D-Pad Direction Command ──────────────────────────────────────
    if input_type == 'dpad':
        direction = data.get('dir', 'stop')

        # Safety check
        if direction == 'forward' and robot_state['front_dist'] < 15:
            emit('log_message', {
                'type': 'warn',
                'msg': f'BLOCKED: Front obstacle at {robot_state["front_dist"]:.1f} cm'
            })
            direction = 'stop'

        robot_state['last_command'] = direction
        print(f'[DRIVE] D-Pad command: {direction.upper()}')

        # ── Send to ESP32 via UDP ──────────────────
        cmd = json.dumps({
            "cmd": "MOVE",
            "dir": direction,
            "speed": robot_state['speed_limit']
        }).encode()
        udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))

        emit('log_message', {
            'type': 'info',
            'msg': f'Drive → {direction.upper()} (speed: {robot_state["speed_limit"]}%)'
        }, broadcast=True)

    # ── Slider / Preset Value Command ──────────────────────────────
    elif input_type == 'slider':
        name  = data.get('name')
        value = data.get('value', 0)

        if name == 'speed':
            # Speed limit 0–100 maps to PWM duty cycle on ESP32
            robot_state['speed_limit'] = int(value)
            print(f'[SPEED] Speed limit set to: {value}%')
            emit('log_message', {
                'type': 'info',
                'msg': f'Speed limit → {value}%'
            })

        elif name == 'pan':
            robot_state['head_angle'] = int(value)
            cmd = json.dumps({"cmd": "SERVO", "angle": int(value)}).encode()
            print(f"[UDP] Sending servo cmd: {cmd} to {ESP32_IP}:{ESP32_PORT}")  # ADD
            try:
                udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
                print("[UDP] Sent OK")  # ADD
            except Exception as e:
                print(f"[UDP] Send failed: {e}")  # ADD
            emit('log_message', {
                'type': 'info',
                'msg': f'Head panning → {value}°'
            })

    else:
        print(f'[WARN] Unknown control_input type: {input_type}')


# =============================================================
#  APPLICATION STARTUP
# =============================================================

# Start the background telemetry thread (demo mode)
# When connecting real ESP32, this thread should be replaced
# (or modified) to read UDP packets instead of generating fake data.
telemetry_thread = threading.Thread(target=generate_telemetry, daemon=True)
telemetry_thread.start()
print('[LINA] Telemetry thread started (DEMO MODE).')

if __name__ == '__main__':
    print('[LINA] Starting server on http://0.0.0.0:5000')
    print('[LINA] Open the dashboard at http://127.0.0.1:5000/cockpit')
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
