from flask import Flask, render_template
from flask_cors import CORS

from routes.control import control_bp
from routes.telemetry import telemetry_bp
from routes.voice import voice_bp
from routes.log_stream import log_stream_bp
from robot_link import robot_link

app = Flask(__name__)
CORS(app)

app.register_blueprint(control_bp)
app.register_blueprint(telemetry_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(log_stream_bp)

@app.route("/")
def index():
    try:
        return render_template('dashboard.html', initial_state=robot_link.state)
    except Exception:
        return "Backend OK"

if __name__ == "__main__":
    robot_link.start()
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)
