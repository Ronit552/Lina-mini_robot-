// ─── Motor Pins ───────────────────────────────
#define PWMA 5
#define AIN1 4
#define AIN2 3
#define PWMB 6
#define BIN1 7
#define BIN2 8

// ─── IR Sensor Pin (center only) ──────────────
#define IR_CENTER 12

// ─── Soft-Start Ramp Config ───────────────────
// RAMP_STEP: how many PWM units to add per loop tick (every 50 ms).
// 0 → 200 in steps of 20 = 10 ticks × 50 ms = ~500 ms to reach full speed.
// Increase RAMP_STEP for a faster ramp, decrease for a gentler one.
#define RAMP_STEP 20

int  speedLimit  = 200;   // max PWM target (set by V command, 0–255)
int  actualSpeed = 0;     // currently applied PWM — ramps toward speedLimit
char lastCmd     = 'S';   // last direction: F / L / R / S
bool fallActive  = false;

// ─── Direction Apply ──────────────────────────
// Writes direction pins + applies current actualSpeed to both motors.
// Called every ramp tick so speed rises smoothly.
void applyMotors() {
  switch (lastCmd) {
    case 'F':
      digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH); analogWrite(PWMA, actualSpeed);
      digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);  analogWrite(PWMB, actualSpeed);
      break;
    case 'B':
      digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);  analogWrite(PWMA, actualSpeed);
      digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH); analogWrite(PWMB, actualSpeed);
      break;
    case 'L':
      digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH); analogWrite(PWMA, actualSpeed); // right → forward
      digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH); analogWrite(PWMB, actualSpeed); // left → backward
      break;
    case 'R':
      digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);  analogWrite(PWMA, actualSpeed); // right → backward
      digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);  analogWrite(PWMB, actualSpeed); // left → forward
      break;
    default:
      analogWrite(PWMA, 0);
      analogWrite(PWMB, 0);
      break;
  }
}

// ─── Instant Stop ─────────────────────────────
// Always immediate — safety must never be ramped.
void stopMotors() {
  actualSpeed = 0;
  lastCmd     = 'S';
  applyMotors();
}

// ─── Soft-Start Ramp ──────────────────────────
// Called every loop tick. Nudges actualSpeed toward speedLimit,
// re-applying direction pins each step so speed rises gradually.
// If speed limit was lowered while moving, snaps down immediately.
void rampMotors() {
  if (lastCmd == 'S') return;            // not moving, nothing to ramp

  if (actualSpeed < speedLimit) {
    // Ramp up one step
    actualSpeed = min(actualSpeed + RAMP_STEP, speedLimit);
    applyMotors();
  } else if (actualSpeed > speedLimit) {
    // Speed limit was reduced while moving — snap down, no need to ramp
    actualSpeed = speedLimit;
    applyMotors();
  }
  // If actualSpeed == speedLimit: already at target, nothing to do
}

// ─── IR Read + Send ───────────────────────────
void handleIR() {
  int c = digitalRead(IR_CENTER);

  // Sensor polarity (confirmed by hardware test):
  //   Surface present → IR reflects → LED ON  → pin LOW  (0) → safe
  //   No surface/edge → no reflect  → LED OFF → pin HIGH (1) → FALL
  bool fall = (c == 1);   // HIGH = no reflection = no floor = fall

  // Normalize for ESP32: always send 1 for fall, 0 for safe.
  // This keeps ESP32 + Flask logic unchanged (ir_center == 1 means fall).
  int irVal = fall ? 1 : 0;

  Serial.print("IR:");
  Serial.print(0);     Serial.print(",");  // left  → no sensor, always safe
  Serial.print(irVal); Serial.print(",");  // center → real sensor (normalized)
  Serial.println(0);                       // right  → no sensor, always safe

  // FALL LATCH: once triggered, motors stop and stay stopped.
  // Unlike the ultrasonic sensor, returning to a safe surface does NOT
  // auto-resume. Only an explicit new move command from the user unlocks this.
  if (fall && !fallActive) {
    fallActive = true;
    stopMotors();   // instant stop — resets actualSpeed to 0
  }
  // ← NO auto-clear. fallActive is cleared only by a deliberate move command.
}

// ─── Command Handler ──────────────────────────
void handleCommands() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0)     return;
  if (cmd.startsWith("IR:")) return;  // ignore our own IR echo

  // ── Fall-latch gate ────────────────────────────────────────────────
  // While fall-locked, block everything. Any explicit move command (F/L/R/B)
  // clears the latch — the user is deliberately choosing to move again.
  // Speed changes and stop commands do nothing while locked.
  if (fallActive) {
    if (cmd == "F" || cmd == "L" || cmd == "R" || cmd == "B") {
      fallActive = false;   // user override → unlock and fall through to execute
    } else {
      stopMotors();
      return;               // S or V while locked → keep stopped
    }
  }

  // ── Speed command (V<0-100>) ───────────────────────────────────────
  if (cmd.startsWith("V")) {
    int val    = cmd.substring(1).toInt();
    speedLimit = map(val, 0, 100, 0, 255);
    if (val > 0 && speedLimit < 100) speedLimit = 100;   // enforce minimum drive PWM
    // Do NOT reset actualSpeed — rampMotors() will adjust smoothly next tick
    return;
  }

  // ── Direction commands ─────────────────────────────────────────────
  // Reset actualSpeed to 0 so the ramp always starts from zero,
  // giving a smooth launch regardless of previous speed.
  if      (cmd == "F") { actualSpeed = 0; lastCmd = 'F'; }
  else if (cmd == "L") { actualSpeed = 0; lastCmd = 'L'; }
  else if (cmd == "R") { actualSpeed = 0; lastCmd = 'R'; }
  else if (cmd == "B") { stopMotors(); }   // backward = stop (no reverse wired)
  else if (cmd == "S") { stopMotors(); }   // explicit stop — always instant
}

// ─── Setup ────────────────────────────────────
void setup() {
  Serial.begin(9600);
  Serial.setTimeout(10);

  pinMode(PWMA, OUTPUT); pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT);
  pinMode(PWMB, OUTPUT); pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT);

  pinMode(IR_CENTER, INPUT);

  stopMotors();
}

// ─── Loop ─────────────────────────────────────
void loop() {
  handleIR();        // 1. read sensor, enforce fall latch
  handleCommands();  // 2. process incoming commands from ESP32
  rampMotors();      // 3. nudge actualSpeed one step toward speedLimit
  delay(50);         // 4. 50 ms tick → full ramp in ~500 ms
}