"""
=============================================================
  LINA - Robot Control Backend
  Flask + Flask-SocketIO Server
=============================================================

HARDWARE OVERVIEW:
  - Rotating head with ONE HC-SR04 ultrasonic sensor (servo-driven, 0-180°)
  - 3x IR obstacle sensors: LEFT, CENTER, RIGHT
  - 2x DC motors (wheels) controlled via L298N / L293D
  - MAX9814 microphone for voice commands
  - ESP32 as the main communication bridge (via WiFi UDP or Serial)

NOTE: Only ONE ultrasonic sensor exists (head-mounted, servo-controlled).
      front_dist is always aliased to head_dist — not a separate sensor.

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
    "head_dist":    45.3,     // Head sonar distance in cm (only sensor)
    "front_dist":   45.3,     // Same as head_dist — aliased, not a separate sensor
    "ir_left":      0,        // 0 = clear, 1 = obstacle detected
    "ir_center":    1,
    "ir_right":     0,
    "fall":         false,    // Fall detected (e.g., edge/tilt sensor)
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
import chatbot
import voice_handler

# ─────────────────────────────────────────────
#  UDP Config for ESP32 Communication
# ─────────────────────────────────────────────
import json
import socket as udp_socket

ESP32_IP   = "255.255.255.255"  # Broadcast by default. Auto-discovered to specific IP once telemetry is received.
ESP32_PORT = 4210            # UDP port ESP32 listens on for commands
LISTEN_PORT = 4211           # UDP port this server listens on for sensor data

udp_sock = udp_socket.socket(udp_socket.AF_INET, udp_socket.SOCK_DGRAM)
udp_sock.setsockopt(udp_socket.SOL_SOCKET, udp_socket.SO_REUSEADDR, 1)
udp_sock.setsockopt(udp_socket.SOL_SOCKET, udp_socket.SO_BROADCAST, 1)  # Allow broadcast
udp_sock.bind(("0.0.0.0", LISTEN_PORT))
udp_sock.settimeout(0.1)
# ─────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "dev_key")
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

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
    "head_dist":    100.0,  # Head ultrasonic reading (cm) — only sensor
    # front_dist is NOT a separate sensor; it is always aliased to head_dist
    "ir_left":      0,      # Left IR sensor: 0 = clear, 1 = obstacle
    "ir_center":    0,      # Center IR sensor
    "ir_right":     0,      # Right IR sensor
    "fall":         False,  # Fall detected flag (from ESP32 fall/tilt sensor)
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

_esp32_connected = False  # Track first real packet received

def generate_telemetry():
    """
    Background thread: Listens for incoming UDP packets from the ESP32.
    Decodes the JSON data, updates the global robot_state, and
    broadcasts the updated telemetry back out to the connected browser clients.
    """
    global _esp32_connected, ESP32_IP
    print(f'[UDP] Listening for ESP32 telemetry on port {LISTEN_PORT}...')
    
    while True:
        try:
            raw, addr = udp_sock.recvfrom(1024)
            data = json.loads(raw.decode())

            # [DEBUG] Log every real packet received
            print(f'[DEBUG] Real packet from {addr}: {data}')

            # Auto-discover ESP32 IP from incoming packet source
            if ESP32_IP != addr[0]:
                ESP32_IP = addr[0]
                print(f'[DEBUG] ESP32 IP auto-set to: {ESP32_IP}')

            # First real packet – notify the browser the ESP32 LED should go green
            if not _esp32_connected:
                _esp32_connected = True
                print('[DEBUG] ESP32 connected! Sending esp32_connected event to browser.')
                socketio.emit('esp32_connected')

            # Update our global state with the real data
            robot_state.update(data)

            # ── Alias: front_dist always mirrors head_dist (single sensor) ──
            robot_state["front_dist"] = robot_state["head_dist"]

            # Fall detection: DISABLED. Re-enable fall_alert logic here when needed.

            # Immediately push state to the browser dashboard
            broadcast_telemetry()
            
        except udp_socket.timeout:
            # No packet from ESP32 – broadcast current state so UI reflects manual changes (Simulation Mode)
            broadcast_telemetry()
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
    # front_dist is always aliased to head_dist (single sensor)
    head_dist = robot_state["head_dist"]
    head_angle = robot_state["head_angle"]

    # Calculate actual movement for the UI (if intended is forward but physically blocked, show wheels as stopped)
    last_cmd = robot_state.get("last_command", "stop")
    ui_direction = last_cmd
    if last_cmd == 'forward' and 70 <= head_angle <= 110 and 0 < head_dist < 9:
        ui_direction = 'stop'

    socketio.emit('telemetry_update', {
        # ── Sensor data ─────────────────────────────────────────────
        "head_angle": head_angle,                  # degrees: 0–180
        "head_dist":  head_dist,                   # cm (only sensor)
        "front_dist": head_dist,                   # aliased to head_dist
        "ir": {
            "left":   robot_state["ir_left"],      # 0 or 1
            "center": robot_state["ir_center"],    # 0 or 1
            "right":  robot_state["ir_right"],     # 0 or 1
        },
        # ── System data ──────────────────────────────────────────────
        "fall":      robot_state.get("fall", False), # bool
        "battery":   robot_state["battery"],         # volts
        "mode":      robot_state["mode"],             # string
        "direction": ui_direction,                   # movement direction for UI wheels
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
    _head_dist = robot_state["head_dist"]
    emit('telemetry_update', {
        "head_angle": robot_state["head_angle"],
        "head_dist":  _head_dist,
        "front_dist": _head_dist,   # aliased to head_dist
        "ir": {
            "left":   robot_state["ir_left"],
            "center": robot_state["ir_center"],
            "right":  robot_state["ir_right"],
        },
        "fall":    robot_state.get("fall", False),
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
    """
    valid_modes = ['MANUAL', 'AI_FOLLOW', 'VOICE']
    if mode not in valid_modes:
        emit('log_message', {'type': 'error', 'msg': f'Invalid mode: {mode}'})
        return

    robot_state['mode'] = mode
    print(f'[MODE] Switching to: {mode}')

    # Send to ESP32
    try:
        cmd = json.dumps({"cmd": "SET_MODE", "mode": mode}).encode()
        udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
    except Exception as e:
        print(f"[UDP] Error sending mode to ESP32: {e}")

    # Broadcast new mode to all connected browser tabs
    emit('mode_update', {'mode': mode}, broadcast=True)
    emit('log_message', {
        'type': 'info',
        'msg': f'Mode changed -> {mode}'
    }, broadcast=True)

    # Toggle voice handler
    if mode == 'VOICE':
        voice_handler.start_listening()
    else:
        voice_handler.stop_listening()

@socketio.on('control_input')
def handle_control(data):
    """
    Handles movement (dpad) and configuration (slider) events from the UI.
    """
    print(f"[DEBUG] control_input received: {data}")
    input_type = data.get('type')

    # ── D-Pad Direction Command ──────────────────────────────────────
    if input_type == 'dpad':
        direction = data.get('dir', 'stop')

        # We no longer override 'direction' to 'stop' here when blocked.
        # This allows 'last_command' to remain 'forward' so that the ESP32 can auto-resume
        # movement when the obstacle is removed. The ESP32 handles the physical blocking locally.

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
            try:
                cmd = json.dumps({"cmd": "SET_SPEED", "speed": int(value)}).encode()
                udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
            except Exception as e:
                pass
            emit('log_message', {
                'type': 'info',
                'msg': f'Speed limit -> {value}%'
            })

        elif name == 'pan':
            robot_state['head_angle'] = int(value)
            cmd = json.dumps({"cmd": "SERVO", "angle": int(value)}).encode()
            if ESP32_IP:
                print(f"[UDP] SERVO -> angle={value} -> {ESP32_IP}:{ESP32_PORT}")
                try:
                    udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
                    print("[UDP] Sent OK")
                except Exception as e:
                    print(f"[UDP] Send failed: {e}")
            else:
                print(f"[WARN] ESP32 IP not yet discovered. Waiting for first telemetry packet from ESP32.")
                print(f"[WARN] Make sure ESP32 is powered, connected to WiFi, and sending UDP to this PC's IP on port {LISTEN_PORT}")
            emit('log_message', {
                'type': 'info',
                'msg': f'Head panning -> {value}°'
            }, broadcast=True)
            # Push updated head angle immediately so radar scanline follows the servo
            broadcast_telemetry()

    else:
        print(f'[WARN] Unknown control_input type: {input_type}')


@socketio.on('chat_message')
def handle_chat(data):
    """
    Handles incoming chat messages from the dashboard.
    Uses chatbot.py to route, search, and generate a response.
    """
    user_text = data.get('msg', '')
    if not user_text:
        return

    print(f"[CHAT] User: {user_text}")

    try:
        # 1. Route the query (decide if search is needed)
        decision = chatbot.route(user_text)
        needs_search = decision.get("needs_search", False)
        stype = decision.get("search_type", "none")
        query = decision.get("query", user_text)

        # 2. Perform search if required
        search_result = None
        if needs_search and stype != "none":
            print(f"[CHAT] LINA is searching via {stype}...")
            search_result = chatbot.smart_search(stype, query)

        # 3. Generate final response
        reply = chatbot.chat(user_text, search_result)
        print(f"[CHAT] LINA: {reply}")

        # 4. Send back to browser
        emit('chat_response', {'msg': reply})
        
        # Also log to the system log
        emit('log_message', {'type': 'info', 'msg': f'LINA: {reply}'}, broadcast=True)

    except Exception as e:
        print(f"[CHAT] Error: {e}")
        emit('chat_response', {'msg': "I'm sorry, my brain is a bit scrambled right now. (Error: check server logs)"})

def _on_voice_input(text):
    """Callback when Vosk detects a spoken phrase from the microphone."""
    print(f"[VOICE] Recognized: {text}")
    # Tell the browser to display the recognized text in the chat UI
    socketio.emit('chat_message_received', {'msg': text, 'sender': 'user'}, broadcast=True)
    
    # Process it exactly like a chat message
    try:
        decision = chatbot.route(text)
        
        # If it's a direct robot command (like "move forward", "stop"), we should also execute it
        if decision.get("intent") == "robot_command":
            action = decision.get("robot_action")
            if action:
                print(f"[VOICE] Executing robot command: {action.upper()}")
                robot_state['last_command'] = action
                cmd = json.dumps({"cmd": "MOVE", "dir": action, "speed": robot_state['speed_limit']}).encode()
                try:
                    udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
                except Exception as e:
                    print(f"[UDP] Error sending move command: {e}")
                
                socketio.emit('log_message', {
                    'type': 'info',
                    'msg': f'Voice Drive → {action.upper()}'
                }, broadcast=True)
        
        # Generate the chatbot reply
        needs_search = decision.get("needs_search", False)
        stype = decision.get("search_type", "none")
        query = decision.get("query", text)

        search_result = None
        if needs_search and stype != "none":
            search_result = chatbot.smart_search(stype, query)

        reply = chatbot.chat(text, search_result)
        print(f"[CHAT] LINA: {reply}")

        socketio.emit('chat_response', {'msg': reply}, broadcast=True)
        socketio.emit('log_message', {'type': 'info', 'msg': f'LINA: {reply}'}, broadcast=True)
        
    except Exception as e:
        print(f"[VOICE/CHAT] Error: {e}")
        socketio.emit('chat_response', {'msg': "I heard you, but my brain had an error processing it."}, broadcast=True)

voice_handler.set_callback(_on_voice_input)



@socketio.on('toggle_wake_word')
def handle_wake_word(data):
    """Toggles the wake word listening state on the ESP32/Robot."""
    active = data.get('active', False)
    print(f"[SYSTEM] Wake Word listener: {'ON' if active else 'OFF'}")
    
    # Forward to ESP32
    try:
        cmd = json.dumps({"cmd": "WAKE_WORD", "active": active}).encode()
        udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
    except Exception as e:
        print(f"[UDP] Error sending wake word state: {e}")

    emit('log_message', {
        'type': 'info',
        'msg': f"Wake Word -> {'ENABLED' if active else 'DISABLED'}"
    }, broadcast=True)


@socketio.on('trigger_auto_move')
def handle_auto_move():
    """Triggers a simple demo autonomous movement routine."""
    print("[DRIVE] Triggering Auto-Move DEMO...")
    emit('log_message', {'type': 'info', 'msg': 'Auto-Move DEMO started (Pattern: Forward-Right)'}, broadcast=True)
    
    def demo_routine():
        try:
            # 1. Forward
            robot_state['last_command'] = 'forward'
            cmd = json.dumps({"cmd": "MOVE", "dir": "forward", "speed": robot_state['speed_limit']}).encode()
            udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
            broadcast_telemetry()
            time.sleep(2.5)
            
            # 2. Turn Right
            robot_state['last_command'] = 'right'
            cmd = json.dumps({"cmd": "MOVE", "dir": "right", "speed": robot_state['speed_limit']}).encode()
            udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
            broadcast_telemetry()
            time.sleep(1.5)
            
            # 3. Stop
            robot_state['last_command'] = 'stop'
            cmd = json.dumps({"cmd": "MOVE", "dir": "stop", "speed": 0}).encode()
            udp_sock.sendto(cmd, (ESP32_IP, ESP32_PORT))
            broadcast_telemetry()
            emit('log_message', {'type': 'info', 'msg': 'Auto-Move DEMO finished.'}, broadcast=True)
        except Exception as e:
            print(f"[DEMO] Error during auto-move: {e}")

    threading.Thread(target=demo_routine, daemon=True).start()


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
    print(f'[LINA] ESP32 target IP  : {ESP32_IP}:{ESP32_PORT}  (pan/drive commands sent here)')
    print(f'[LINA] Listening for ESP32 telemetry on UDP port: {LISTEN_PORT}')
    print('[LINA] ESP32 IP will auto-update once first telemetry packet is received.')
    
    # Initialize the voice listening thread (but it starts PAUSED based on mode)
    if robot_state['mode'] == 'VOICE':
        voice_handler.start_listening(_on_voice_input)
    else:
        voice_handler.start_listening(_on_voice_input)
        voice_handler.stop_listening()
        
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
