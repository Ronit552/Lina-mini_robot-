#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

const char* ssid     = "Airtel_6837";
const char* password = "S@fe_p@ssword_123";

WiFiUDP udp;
const char* FLASK_IP   = "192.168.1.4";
const int   FLASK_PORT = 4211;
const int   LOCAL_PORT = 4210;

#define RXD2               16
#define TXD2               17
#define SERVO_PIN          13
#define TRIG_HEAD          25
#define ECHO_HEAD          26

// ─── Distance / angle thresholds ──────────────
#define OBSTACLE_DIST_CM   20   // forward: stop if closer than this (cm)
#define SCAN_CLEAR_CM      35   // side scan: "clear" if farther than this (cm)
#define OBSTACLE_ANGLE_MIN 70
#define OBSTACLE_ANGLE_MAX 110

// ─── Auto-mode servo look angles ──────────────
// Head sweeps to extreme 0° / 180° to scan full side corridors
#define ANGLE_CENTER  90   // forward-facing (default)
#define ANGLE_LEFT     0   // servo 0°   = left corridor scan
#define ANGLE_RIGHT  180   // servo 180° = right corridor scan

// ─── Auto-mode timing ─────────────────────────
#define LOOK_SETTLE_MS   900   // wait after servo reaches 0°/180° before reading dist
                               // (longer because servo travels full 90° from center)
#define CENTER_SETTLE_MS 500   // wait after head returns to center before body turns
#define TURN_DURATION_MS 2900  // how long to apply L/R command (body turns more to align)
#define FORWARD_MIN_MS  1500   // min forward time before obstacle check resumes
                               // prevents immediate re-detection right after a turn

// ─── Auto-mode state machine ──────────────────
enum AutoState {
  AUTO_IDLE,
  AUTO_FORWARD,
  AUTO_LOOK_LEFT,        // servo at ANGLE_LEFT, waiting to settle
  AUTO_LOOK_RIGHT,       // servo at ANGLE_RIGHT, waiting to settle
  AUTO_CENTER_RETURN,    // head returning to 90° — wait before body turn
  AUTO_TURN_LEFT,        // sending L, timing the turn
  AUTO_TURN_RIGHT,       // sending R, timing the turn
  AUTO_BOTH_BLOCKED      // no path found, waiting before retry
};

bool          autoMode       = false;
AutoState     autoState      = AUTO_IDLE;
unsigned long stateTimer     = 0;
unsigned long forwardStartTime = 0;   // when we last entered AUTO_FORWARD
char          pendingTurn    = ' ';   // 'L' or 'R' — set during scan, applied after center return

// ─── Shared state ─────────────────────────────
HardwareSerial NanoSerial(2);
Servo headServo;
int    headAngle         = ANGLE_CENTER;
bool   obstacleBlocked   = false;
String intendedDirection = "stop";

int  ir_left = 0, ir_center = 0, ir_right = 0;
bool lastFallState = false;

// ─── Helpers ──────────────────────────────────
// Arduino normalizes IR: ir_center=1 → fall, 0 → safe
bool fallDetected() { return (ir_center == 1); }

// 0 = no echo (very far) or beyond clear threshold → treat as clear
bool isClear(float d)  { return (d == 0.0f || d > SCAN_CLEAR_CM); }

void moveServo(int angle) {
  headAngle = constrain(angle, 0, 180);
  headServo.write(headAngle);
}

void nano(const char* cmd) {
  NanoSerial.println(cmd);
  Serial.print("[NANO] "); Serial.println(cmd);
}

// ─── IR parse from Arduino ────────────────────
void readNanoSerial() {
  if (!NanoSerial.available()) return;
  String line = NanoSerial.readStringUntil('\n');
  line.trim();
  if (!line.startsWith("IR:")) return;
  line = line.substring(3);
  int c1 = line.indexOf(','), c2 = line.lastIndexOf(',');
  if (c1 < 0 || c2 < 0) return;
  ir_left   = line.substring(0, c1).toInt();
  ir_center = line.substring(c1 + 1, c2).toInt();
  ir_right  = line.substring(c2 + 1).toInt();
}

// ─── Ultrasonic ───────────────────────────────
float readDistance() {
  digitalWrite(TRIG_HEAD, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_HEAD, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_HEAD, LOW);
  long dur = pulseIn(ECHO_HEAD, HIGH, 30000);
  return dur * 0.034f / 2.0f;
}

// ─── Manual movement (used outside auto mode) ─
void applyMovement() {
  if (intendedDirection == "forward" && (obstacleBlocked || fallDetected())) {
    nano("S"); return;
  }
  if      (intendedDirection == "forward")  nano("F");
  else if (intendedDirection == "backward") nano("B");
  else if (intendedDirection == "left")     nano("L");
  else if (intendedDirection == "right")    nano("R");
  else                                      nano("S");
}

// ─── Telemetry → Flask ────────────────────────
void sendTelemetry(float dist) {
  StaticJsonDocument<256> doc;
  doc["head_angle"] = headAngle;
  doc["head_dist"]  = dist;
  doc["front_dist"] = dist;
  doc["ir_left"]    = ir_left;
  doc["ir_center"]  = ir_center;
  doc["ir_right"]   = ir_right;
  doc["fall"]       = fallDetected();
  doc["battery"]    = 7.4;
  doc["mode"]       = autoMode ? "AI_FOLLOW" : "MANUAL";
  char buf[256];
  serializeJson(doc, buf);
  udp.beginPacket(FLASK_IP, FLASK_PORT);
  udp.write((uint8_t*)buf, strlen(buf));
  udp.endPacket();
}

// ─── Exit auto mode cleanly ───────────────────
void exitAutoMode(const char* reason) {
  autoMode  = false;
  autoState = AUTO_IDLE;
  intendedDirection = "stop";
  moveServo(ANGLE_CENTER);
  nano("S");
  Serial.print("[AUTO] EXIT — "); Serial.println(reason);
}

// ─── Enter auto mode ──────────────────────────
void enterAutoMode() {
  autoMode        = true;
  autoState       = AUTO_FORWARD;
  intendedDirection = "forward";
  moveServo(ANGLE_CENTER);
  nano("F");
  stateTimer      = millis();
  forwardStartTime = millis();   // start the cooldown timer from now
  Serial.println("[AUTO] STARTED → moving forward");
}

// ─── UDP commands from Flask ──────────────────
void receiveCommands() {
  int sz = udp.parsePacket();
  if (!sz) return;
  char buf[256];
  int len = udp.read(buf, 255);
  if (len >= 0) buf[len] = '\0';
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, buf) != DeserializationError::Ok) return;
  String cmd = doc["cmd"] | "";

  if (cmd == "SERVO") {
    if (!autoMode) {   // don't allow manual servo during auto scan
      moveServo((int)doc["angle"]);
      Serial.print("SERVO → "); Serial.println(headAngle);
    }
  }
  else if (cmd == "MOVE") {
    if (autoMode) exitAutoMode("manual override");
    intendedDirection = doc["dir"] | "stop";
    applyMovement();
  }
  else if (cmd == "SET_SPEED") {
    int spd = doc["speed"] | 100;
    NanoSerial.print("V"); NanoSerial.println(spd);
  }
  else if (cmd == "SET_MODE") {
    String mode = doc["mode"] | "MANUAL";
    if (mode == "AI_FOLLOW") enterAutoMode();
    else                     exitAutoMode("SET_MODE MANUAL");
  }
}

// ─── Auto-mode state machine ──────────────────
//
// Improved AI-Follow obstacle avoidance:
//
//  AUTO_FORWARD  (head at 90°, moving forward)
//    → obstacle detected → STOP body
//      → head sweeps to 180° (full right)  → AUTO_LOOK_RIGHT
//
//  AUTO_LOOK_RIGHT  (head at 180°, wait LOOK_SETTLE_MS)
//    → right clear  → head returns to 90° → AUTO_CENTER_RETURN (pendingTurn='R')
//    → right blocked → head sweeps to 0° (full left) → AUTO_LOOK_LEFT
//
//  AUTO_LOOK_LEFT   (head at 0°, wait LOOK_SETTLE_MS)
//    → left clear   → head returns to 90° → AUTO_CENTER_RETURN (pendingTurn='L')
//    → left blocked → head to 90°, stop   → AUTO_BOTH_BLOCKED
//
//  AUTO_CENTER_RETURN  (head returning to 90°, wait CENTER_SETTLE_MS)
//    → send body turn command (L or R) → AUTO_TURN_LEFT / AUTO_TURN_RIGHT
//
//  AUTO_TURN_LEFT / AUTO_TURN_RIGHT  (wait TURN_DURATION_MS)
//    → head to 90°, send F → AUTO_FORWARD
//
//  AUTO_BOTH_BLOCKED (wait 2 s then retry full sweep)
//    → head to 180° → AUTO_LOOK_RIGHT
//
//  IR fall at any point → exitAutoMode()
//
void runAutoMode(float dist) {
  // ── IR fall: hard exit auto mode ────────────
  if (fallDetected()) {
    exitAutoMode("IR FALL DETECTED");
    return;
  }

  unsigned long now = millis();

  switch (autoState) {

    case AUTO_FORWARD: {
      // Cooldown: don't check obstacles until FORWARD_MIN_MS has elapsed.
      // Prevents immediate re-detection right after completing a turn.
      if (now - forwardStartTime < FORWARD_MIN_MS) break;

      bool front = (headAngle >= OBSTACLE_ANGLE_MIN && headAngle <= OBSTACLE_ANGLE_MAX);
      if (front && dist > 0 && dist < OBSTACLE_DIST_CM) {
        nano("S");  // stop body immediately
        Serial.print("[AUTO] Obstacle "); Serial.print(dist);
        Serial.println(" cm → head swings to 180° (full right scan)");
        moveServo(ANGLE_RIGHT);   // 180° = full right sweep
        stateTimer = now;
        autoState  = AUTO_LOOK_RIGHT;
      }
      break;
    }

    case AUTO_LOOK_LEFT: {
      // Head is at 0° (full left sweep)
      if (now - stateTimer < LOOK_SETTLE_MS) break;
      Serial.print("[AUTO] Left corridor (0°) dist: "); Serial.println(dist);
      if (isClear(dist)) {
        Serial.println("[AUTO] Left CLEAR → head returning to 90°...");
        moveServo(ANGLE_CENTER);   // head back to 90°
        pendingTurn = 'L';         // turn body left after head centers
        stateTimer  = now;
        autoState   = AUTO_CENTER_RETURN;
      } else {
        // Both sides blocked — stop and wait before retrying
        Serial.println("[AUTO] BOTH sides blocked → waiting...");
        moveServo(ANGLE_CENTER);
        nano("S");
        stateTimer = now;
        autoState  = AUTO_BOTH_BLOCKED;
      }
      break;
    }

    case AUTO_LOOK_RIGHT: {
      // Head is at 180° (full right sweep)
      if (now - stateTimer < LOOK_SETTLE_MS) break;
      Serial.print("[AUTO] Right corridor (180°) dist: "); Serial.println(dist);
      if (isClear(dist)) {
        Serial.println("[AUTO] Right CLEAR → head returning to 90°...");
        moveServo(ANGLE_CENTER);   // head back to 90°
        pendingTurn = 'R';         // turn body right after head centers
        stateTimer  = now;
        autoState   = AUTO_CENTER_RETURN;
      } else {
        // Right blocked → sweep head to 0° and check left
        Serial.println("[AUTO] Right blocked → head swings to 0° (full left scan)");
        moveServo(ANGLE_LEFT);     // 0° = full left sweep
        stateTimer = now;
        autoState  = AUTO_LOOK_LEFT;
      }
      break;
    }

    // ── Head has returned to center; now send the body turn ──
    case AUTO_CENTER_RETURN: {
      if (now - stateTimer < CENTER_SETTLE_MS) break;  // wait for head to reach 90°
      // NOTE: body turn is physically inverted — send opposite command to go correct way
      if (pendingTurn == 'R') {
        Serial.println("[AUTO] Head centered → turning RIGHT");
        nano("L");   // inverted wiring: 'L' command = robot turns physically RIGHT
        stateTimer = now;
        autoState  = AUTO_TURN_RIGHT;
      } else {
        Serial.println("[AUTO] Head centered → turning LEFT");
        nano("R");   // inverted wiring: 'R' command = robot turns physically LEFT
        stateTimer = now;
        autoState  = AUTO_TURN_LEFT;
      }
      pendingTurn = ' ';
      break;
    }

    case AUTO_TURN_LEFT:
    case AUTO_TURN_RIGHT: {
      if (now - stateTimer >= TURN_DURATION_MS) {
        Serial.println("[AUTO] Turn done → forward");
        moveServo(ANGLE_CENTER);
        nano("F");
        intendedDirection  = "forward";
        forwardStartTime   = millis();   // reset cooldown — don't check obstacles immediately
        autoState          = AUTO_FORWARD;
      }
      break;
    }

    case AUTO_BOTH_BLOCKED: {
      // Both sides blocked — retry full sweep after 2 s
      if (now - stateTimer >= 2000) {
        Serial.println("[AUTO] Retrying full sweep → head to 180°");
        moveServo(ANGLE_RIGHT);    // restart sweep from right (180°)
        stateTimer = now;
        autoState  = AUTO_LOOK_RIGHT;
      }
      break;
    }

    default: break;
  }
}

// ─── Setup ────────────────────────────────────
void setup() {
  Serial.begin(115200);
  NanoSerial.begin(9600, SERIAL_8N1, RXD2, TXD2);

  pinMode(TRIG_HEAD, OUTPUT);
  pinMode(ECHO_HEAD, INPUT);

  headServo.attach(SERVO_PIN);
  headServo.write(ANGLE_CENTER);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.print("\nIP: "); Serial.println(WiFi.localIP());
  udp.begin(LOCAL_PORT);
  Serial.println("UDP ready.");
}

// ─── Loop ─────────────────────────────────────
void loop() {
  readNanoSerial();
  receiveCommands();

  float dist = readDistance();

  if (autoMode) {
    // ── AUTO: state machine drives everything ──
    runAutoMode(dist);

  } else {
    // ── MANUAL: obstacle guard + fall latch ───
    bool facingFront = (headAngle >= OBSTACLE_ANGLE_MIN && headAngle <= OBSTACLE_ANGLE_MAX);
    bool obstacleNow = (facingFront && dist > 0 && dist < OBSTACLE_DIST_CM);
    if (obstacleNow != obstacleBlocked) {
      obstacleBlocked = obstacleNow;
      applyMovement();
      if (obstacleBlocked) { Serial.print("OBSTACLE: "); Serial.println(dist); }
    }

    bool fallNow = fallDetected();
    if (fallNow && !lastFallState) {
      nano("S");
      Serial.println("FALL LATCH → STOP");
    } else if (!fallNow && lastFallState) {
      Serial.println("FLOOR OK → latched, awaiting command");
    }
    lastFallState = fallNow;
  }

  Serial.print("HEAD: "); Serial.print(dist);
  Serial.print("cm | IR:"); Serial.print(ir_left);
  Serial.print(","); Serial.print(ir_center);
  Serial.print(","); Serial.print(ir_right);
  Serial.print(" | MODE:"); Serial.println(autoMode ? "AUTO" : "MANUAL");

  sendTelemetry(dist);
  delay(100);
}