from flask import Blueprint, request, jsonify
import random

voice_bp = Blueprint('voice', __name__, url_prefix='/api')

@voice_bp.route('/voice', methods=['POST'])
def process_voice():
    # Audio field is expected in multipart/form-data
    _audio = request.files.get('audio')
    
    transcripts = ["turn left", "go forward", "stop", "scan area"]
    responses = ["Turning left now", "Moving forward", 
                 "Stopping all motors", "Scanning surroundings"]
                 
    idx = random.randint(0, len(transcripts) - 1)
    
    return jsonify({
        "status": "ok",
        "transcript": transcripts[idx],
        "response_text": responses[idx]
    })
