from flask import Blueprint, jsonify
from robot_link import robot_link

telemetry_bp = Blueprint('telemetry', __name__, url_prefix='/api')

@telemetry_bp.route('/status', methods=['GET'])
def get_status():
    st = robot_link.state
    # Uptime could be added later if ESP32 sends it, or just return server info
    return jsonify({
        "connected": st["connected"],
        "battery": st["battery"],
        "mode": st["mode"]
    })

@telemetry_bp.route('/telemetry', methods=['GET'])
def get_telemetry():
    st = robot_link.state
    return jsonify({
        "distance_cm": st["distance_cm"],
        "ir_left": st["ir_left"],
        "ir_right": st["ir_right"],
        "servo_angle": st["servo_angle"],
        "direction": st["direction"],
        "speed": st["speed"]
    })
