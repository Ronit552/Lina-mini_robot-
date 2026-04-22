#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// ─── WiFi ───────────────────────────────────
const char* ssid     = "Airtel_6837";
const char* password = "S@fe_p@ssword_123";

// ─── Static IP ──────────────────────────────
IPAddress local_IP(192, 168, 1, 50);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 255, 0);

// ─── UDP ────────────────────────────────────
WiFiUDP udp;
const char* FLASK_IP   = "192.168.1.5";  // YOUR PC IPv4
const int   FLASK_PORT = 4211;
const int   LOCAL_PORT = 4210;

// ─── Pins ───────────────────────────────────
#define RXD2        16
#define TXD2        17
#define SERVO_PIN   13
#define TRIG_HEAD   25
#define ECHO_HEAD   26
#define TRIG_FRONT  27
#define ECHO_FRONT  14

// ─── Objects ────────────────────────────────
HardwareSerial NanoSerial(2);
Servo headServo;
int headAngle = 90;

// ─── Ultrasonic ─────────────────────────────
float readDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  long dur = pulseIn(echo, HIGH, 30000);
  return dur * 0.034 / 2.0;
}

// ─── Send telemetry to Flask ─────────────────
void sendTelemetry(float hDist, float fDist) {
  StaticJsonDocument<256> doc;
  doc["head_angle"] = headAngle;
  doc["head_dist"]  = hDist;
  doc["front_dist"] = fDist;
  doc["ir_left"]    = 0;
  doc["ir_center"]  = 0;
  doc["ir_right"]   = 0;
  doc["battery"]    = 7.4;
  doc["mode"]       = "MANUAL";

  char buf[256];
  serializeJson(doc, buf);
  udp.beginPacket(FLASK_IP, FLASK_PORT);
  udp.write((uint8_t*)buf, strlen(buf));
  udp.endPacket();
}

// ─── Receive commands from Flask ─────────────
void receiveCommands() {
  int size = udp.parsePacket();
  if (size) {
    char buf[256];
    udp.read(buf, sizeof(buf));
    buf[size] = '\0';

    Serial.print("RAW UDP: ");
    Serial.println(buf);

    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, buf) == DeserializationError::Ok) {
      String cmd = doc["cmd"] | "";
      Serial.print("CMD: ");
      Serial.println(cmd);

      // ─── Servo ──────────────────────────────
      if (cmd == "SERVO") {
        headAngle = constrain((int)doc["angle"], 0, 180);
        headServo.write(headAngle);
        Serial.print("Servo → ");
        Serial.println(headAngle);
      }

      // ─── Motor ──────────────────────────────
      else if (cmd == "MOVE") {
        String dir = doc["dir"] | "stop";
        Serial.print("MOVE → ");
        Serial.println(dir);

        if      (dir == "forward")  NanoSerial.println("F");
        else if (dir == "backward") NanoSerial.println("B");
        else if (dir == "left")     NanoSerial.println("L");
        else if (dir == "right")    NanoSerial.println("R");
        else                        NanoSerial.println("S");
      }

      // ─── Mode ───────────────────────────────
      else if (cmd == "SET_MODE") {
        String mode = doc["mode"] | "MANUAL";
        Serial.print("MODE → ");
        Serial.println(mode);
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  NanoSerial.begin(9600, SERIAL_8N1, RXD2, TXD2);

  pinMode(TRIG_HEAD,  OUTPUT);
  pinMode(ECHO_HEAD,  INPUT);
  pinMode(TRIG_FRONT, OUTPUT);
  pinMode(ECHO_FRONT, INPUT);

  headServo.attach(SERVO_PIN);
  headServo.write(headAngle);

  // Static IP
  WiFi.config(local_IP, gateway, subnet);
  WiFi.begin(ssid, password);

  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.print("\nESP32 IP: ");
  Serial.println(WiFi.localIP());

  udp.begin(LOCAL_PORT);
  Serial.println("UDP ready.");
}

void loop() {
  receiveCommands();

  float hDist = readDistance(TRIG_HEAD,  ECHO_HEAD);
  float fDist = readDistance(TRIG_FRONT, ECHO_FRONT);

  sendTelemetry(hDist, fDist);
  delay(100);
}