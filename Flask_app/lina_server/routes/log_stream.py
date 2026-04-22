from flask import Blueprint, Response
import time
import json
from datetime import datetime

log_stream_bp = Blueprint('log_stream', __name__, url_prefix='/api')

MESSAGES = [
    {"source": "ESP32", "message": "WiFi signal strength: -62dBm"},
    {"source": "ESP32", "message": "Audio buffer ready"},
    {"source": "NANO",  "message": "Motor PWM set to 180"},
    {"source": "NANO",  "message": "Ultrasonic ping: 45cm"},
    {"source": "SERVER","message": "Command processed: forward"},
    {"source": "SERVER","message": "TTS response sent"}
]

def generate_logs():
    idx = 0
    while True:
        msg = MESSAGES[idx % len(MESSAGES)]
        idx += 1
        
        event_data = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "source": msg["source"],
            "message": msg["message"]
        }
        
        yield f"data: {json.dumps(event_data)}\n\n"
        time.sleep(1.5)

@log_stream_bp.route('/log/stream')
def log_stream():
    return Response(
        generate_logs(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )
