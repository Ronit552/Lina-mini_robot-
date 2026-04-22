#define PWMA 5
#define AIN1 4
#define AIN2 3
#define PWMB 6
#define BIN1 7
#define BIN2 8

void forward() {
  digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH); analogWrite(PWMA, 180);
  digitalWrite(BIN1, LOW); digitalWrite(BIN2, HIGH);  analogWrite(PWMB, 180);
}

void backward() {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW); analogWrite(PWMA, 180);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW); analogWrite(PWMB, 180);
}


void turnLeft() {
  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);  analogWrite(PWMA, 150);
  digitalWrite(BIN1, LOW);  digitalWrite(BIN2, HIGH); analogWrite(PWMB, 150);
}

void turnRight() {
  digitalWrite(AIN1, LOW);  digitalWrite(AIN2, HIGH); analogWrite(PWMA, 150);
  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);  analogWrite(PWMB, 150);
}

void stopMotors() {
  analogWrite(PWMA, 0);
  analogWrite(PWMB, 0);
}

void setup() {
  Serial.begin(9600);
  pinMode(PWMA, OUTPUT); pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT);
  pinMode(PWMB, OUTPUT); pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT);
  stopMotors();
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if      (cmd == "F") forward();
    else if (cmd == "B") backward();
    else if (cmd == "L") turnLeft();
    else if (cmd == "R") turnRight();
    else if (cmd == "S") stopMotors();
  }
}