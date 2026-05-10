# 🤖 LINA — Mini Robot

A WiFi-controlled robot with real-time sensor telemetry, autonomous obstacle avoidance, and an AI-powered dashboard.

---

## 📦 Project Structure

```
Lina/
├── Arduino_lina/       # Motor controller (Arduino Nano)
├── esp32_lina/         # WiFi bridge + sensor hub (ESP32)
└── Flask_app/          # Web dashboard + backend (Flask + SocketIO)
```

---

## 🔧 Hardware Overview

| Component | Role |
|---|---|
| **Arduino Nano** | Motor driver (L298N), reads 1x IR sensor |
| **ESP32** | WiFi bridge, reads head ultrasonic sensor, relays serial IR data |
| **HC-SR04** | Ultrasonic distance sensor — servo-mounted on head |
| **IR Sensor (x1)** | Center only — downward-facing for fall/edge detection (pin 12) |
| **Servo Motor** | Rotates head 0°–180° |
| **DC Motors (x2)** | Wheel drive — controlled via L298N |

---

## 📡 Sensor Setup — IR (Current)

> **Only 1 IR sensor is currently installed: CENTER (pin D12 on Arduino)**

- **Pin reads HIGH (1)** = no IR reflection = no floor = **FALL** (LED off)
- **Pin reads LOW  (0)** = IR reflects off floor = floor present = **safe** (LED on)

Left and right IR channels are hardcoded to `0` (safe) since sensors are not yet installed.

### ⚠️ IR vs Ultrasonic — Behavior Difference

| | Ultrasonic (obstacle) | IR (fall / edge) |
|---|---|---|
| Trigger | Object in front | No floor detected |
| Action | Pause motors | Hard stop (latch) |
| Auto-resume | ✅ Yes, when path clears | ❌ No |
| How to resume | Automatic | Send new move command from dashboard |

**Fall latch flow:**
```
IR_CENTER reads HIGH (1)  →  no floor / edge detected
  → Arduino: sets fallActive = true, stops motors (LATCHED)
  → Arduino: sends IR:0,1,0 over Serial to ESP32
  → ESP32: fallDetected() = true, sends "S" to Arduino
  → ESP32: floor returns → does NOT auto-resume (latch held)
  → Flask: broadcasts fall=true to dashboard
  → User sends new move command (F/L/R/B) from dashboard
  → Arduino: clears fallActive latch → executes movement
```

---

## 🔁 Communication Flow

```
Browser (Dashboard)
    ↕ WebSocket (Flask-SocketIO)
Flask Server (app.py)  ←port 4211
    ↕ UDP (JSON packets)
ESP32 (esp32_lina.ino) →port 4211 | listens on port 4210
    ↕ Serial (9600 baud, UART2 pins 16/17)
Arduino Nano (Arduino_lina.ino)
    ↕ GPIO
  Motors, IR Sensor, L298N
```

---

## 📤 ESP32 → Flask JSON Packet

```json
{
  "head_angle":  90,
  "head_dist":   45.3,
  "front_dist":  45.3,
  "ir_left":     0,
  "ir_center":   1,
  "ir_right":    0,
  "fall":        true,
  "battery":     7.4,
  "mode":        "MANUAL"
}
```

---

## 📥 Flask → ESP32 Commands

```json
{ "cmd": "MOVE",      "dir": "forward", "speed": 60 }
{ "cmd": "SET_SPEED", "speed": 70 }
{ "cmd": "SERVO",     "angle": 90 }
{ "cmd": "SET_MODE",  "mode": "MANUAL" }
```

---

## ▶️ Running

```bash
cd Flask_app
python app.py
# Dashboard: http://127.0.0.1:5000/cockpit
```

Flash `Arduino_lina.ino` to Arduino Nano, `esp32_lina.ino` to ESP32.

---

## 🛠️ Concepts Used

- **Serial Communication** (UART): Arduino → ESP32 (IR data relay)
- **UDP Sockets**: ESP32 ↔ Flask (JSON telemetry + commands)
- **WebSocket / SocketIO**: Flask ↔ Browser (real-time dashboard)
- **PWM Motor Control**: Arduino motor speed via analogWrite
- **Soft-Start / Acceleration Ramp**: `actualSpeed` ramps 0 → `speedLimit` in RAMP_STEP increments per 50ms tick
- **HC-SR04 Ultrasonic**: Distance measurement via pulseIn
- **Servo Control**: Head pan using ESP32Servo library
- **Fall Detection**: Downward IR sensor — active-LOW, hard latch, exits auto mode
- **IR Signal Normalization**: Sensor polarity abstracted before Serial transmission
- **Obstacle Avoidance**: Ultrasonic threshold (9cm, forward ±20°)
- **Auto-Navigation State Machine**: ESP32 finite-state machine for autonomous driving
- **Threading**: Flask background thread for UDP listening
- **ArduinoJSON**: JSON parsing on ESP32
- **UI/UX Design**: Modern, responsive 3-Column CSS Grid layout with dedicated panels for Voice Chat, Sensor Telemetry, Power Systems, and Glassmorphism aesthetics.
- **Simulation Mode**: Backend fallback mechanism broadcasting software state telemetry when the hardware ESP32 UDP stream is absent, keeping the UI fully interactive.

---

## 🤖 Auto-Mode (AI_FOLLOW) — Improved Obstacle Avoidance

Triggered from the dashboard by switching to `AI_FOLLOW` mode.

### How It Works
When moving forward, if the ultrasonic sensor (head at 90°) detects an obstacle:
1. **Body stops immediately**
2. **Head sweeps to 180°** (full right) — scans right corridor
3. If right is **clear** → head returns to 90° → body turns right → resumes forward
4. If right is **blocked** → head sweeps to **0°** (full left) — scans left corridor
5. If left is **clear** → head returns to 90° → body turns left → resumes forward
6. If **both blocked** → head centers, waits 2 s, retries from step 2

### State Flow
```
AUTO_FORWARD  (head 90°, moving forward)
  obstacle (dist < 20cm, head facing front)
    → body STOP + head swings to 180° (full right sweep)
    → AUTO_LOOK_RIGHT  (wait 900ms for servo to settle)
        right CLEAR (dist > 35cm)
          → head returns to 90° → AUTO_CENTER_RETURN (500ms)
          → body turns RIGHT for 1100ms → AUTO_FORWARD
        right BLOCKED
          → head swings to 0° (full left sweep)
          → AUTO_LOOK_LEFT  (wait 900ms)
              left CLEAR
                → head returns to 90° → AUTO_CENTER_RETURN (500ms)
                → body turns LEFT for 1100ms → AUTO_FORWARD
              BOTH BLOCKED
                → head centers, stop → AUTO_BOTH_BLOCKED
                → wait 2 s → retry from 180° sweep

IR fall detected at ANY state → exitAutoMode() → motors stop, mode = MANUAL
Manual MOVE command received  → exitAutoMode() → user takes over
```

### Tuning Constants (in `esp32_lina.ino`)
| Constant | Value | Purpose |
|---|---|---|
| `OBSTACLE_DIST_CM` | `20` cm | Forward stop distance |
| `SCAN_CLEAR_CM` | `35` cm | Side scan "clear" threshold |
| `LOOK_SETTLE_MS` | `900` ms | Servo settle time at 0°/180° (full 90° sweep) |
| `CENTER_SETTLE_MS` | `500` ms | Head centering time before body turns |
| `TURN_DURATION_MS` | `1100` ms | Duration of L/R turn (body rotates ~90°) |
| `FORWARD_MIN_MS` | `1500` ms | Min forward time before obstacle check resumes |
| `ANGLE_LEFT` | `0°` | Full left sweep angle |
| `ANGLE_RIGHT` | `180°` | Full right sweep angle |

---

## ⚙️ Tuning the Soft-Start Ramp

In `Arduino_lina.ino`, adjust `RAMP_STEP`:

| `RAMP_STEP` | Time to full speed (200 PWM, 50ms tick) |
|---|---|
| `10` | ~1000 ms (very gentle) |
| `20` | ~500 ms ← default |
| `40` | ~250 ms (snappier) |
| `200` | instant (no ramp) |