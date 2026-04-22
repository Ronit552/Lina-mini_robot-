from flask import Blueprint, request, jsonify
from robot_link import robot_link

control_bp = Blueprint('control', __name__, url_prefix='/api')

@control_bp.route('/mode', methods=['POST'])
def set_mode():
    data = request.get_json() or {}
    mode = data.get('mode')
    
    if mode not in ["manual", "auto", "idle"]:
        return jsonify({"error": "Invalid mode"}), 400
        
    robot_link.state["mode"] = mode
    robot_link.send_command(f"<MODE,{mode}>")
    return jsonify({"status": "ok", "mode": mode})

@control_bp.route('/move', methods=['POST'])
def move():
    data = request.get_json() or {}
    direction = data.get('direction')
    speed = data.get('speed')
    
    if direction not in ["forward", "back", "left", "right", "stop"]:
        return jsonify({"error": "Invalid direction"}), 400
        
    if not isinstance(speed, int) or speed < 0 or speed > 255:
        return jsonify({"error": "Invalid speed, must be 0-255"}), 400
        
    robot_link.state["direction"] = direction
    robot_link.state["speed"] = speed
    robot_link.send_command(f"<MOVE,{direction},{speed}>")
    
    return jsonify({"status": "ok", "direction": direction, "speed": speed})

@control_bp.route('/servo', methods=['POST'])
def set_servo():
    data = request.get_json() or {}
    angle = data.get('angle')
    
    if not isinstance(angle, int) or angle < 0 or angle > 180:
        return jsonify({"error": "Invalid angle, must be 0-180"}), 400
        
    robot_link.state["servo_angle"] = angle
    robot_link.send_command(f"<SERVO,{angle}>")
    
    return jsonify({"status": "ok", "angle": angle})
