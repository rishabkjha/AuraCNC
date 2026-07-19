/*
  ============================================================
  GALVO_STEP TMC2209 EDITION - ESP32
  ============================================================
  Rewrite of original AccelStepper-only firmware to drive
  TMC2209 stepper drivers over UART (single bus, two addresses),
  with 1/256 microstepping, auto-homing, manual jog via
  potentiometers, and G-code -> mirror-angle toolpath conversion.

  REQUIRED LIBRARIES (Arduino Library Manager):
    - AccelStepper      (by Mike McCauley)
    - TMCStepper         (by teemuatlut)

  WIRING ASSUMPTIONS (edit the #defines below if different):
    - TMC UART: shared TX/RX bus on pins 16/17
        Driver X -> UART address 0  (MS1=GND, MS2=GND)
        Driver Y -> UART address 3  (MS1=VIO, MS2=VIO)
    - Driver X: STEP=23, DIR=22
    - Driver Y: STEP=19, DIR=18
    - Limit switch X: GPIO 33   (active LOW w/ internal pullup)
    - Limit switch Y: GPIO 32   (active LOW w/ internal pullup)
    - Jog potentiometer X: GPIO 34 (ADC1, input only)
    - Jog potentiometer Y: GPIO 35 (ADC1, input only)
    - Laser: GPIO 13
    - Driver EN pin assumed tied to GND (always enabled).
      If you wired EN to a GPIO, add pinMode/digitalWrite calls.
  ============================================================
*/

#include <AccelStepper.h>
#include <MultiStepper.h>
#include <TMCStepper.h>

// ~~~~~~~~~~~~~~~~~~~~ Firmware Settings ~~~~~~~~~~~~~~~~~~~~

#define D 170.0   // orthogonal distance of "last" mirror and projection plane
#define E 19.0    // orthogonal distance of X and Y rotational axes

// ---- TMC2209 UART ----
#define TMC_TX_PIN     16
#define TMC_RX_PIN     17
#define TMC_SERIAL     Serial1
#define TMC_BAUD       115200
#define R_SENSE        0.11f     // verify against your driver module's datasheet
#define DRIVER_X_ADDR  0b00
#define DRIVER_Y_ADDR  0b11
#define RMS_CURRENT_MA 600       // motor RMS current in mA - tune to your motors
#define TOFF_VALUE     4

// ---- Step/Dir pins ----
#define STEP_X 23
#define DIR_X  22
#define STEP_Y 19
#define DIR_Y  18

// ---- Endstops ----
#define ENDSTOP_X 32
#define ENDSTOP_Y 33
#define ENDSTOP_TRIGGERED LOW   // switches wired to GND, using INPUT_PULLUP

// ---- Jog potentiometers ----
#define POT_X 34
#define POT_Y 35
#define POT_DEADZONE 150        // +/- counts around center to ignore (ADC is 12-bit, 0-4095)
#define POT_MAX_JOG_SPEED 2500  // steps/sec at full pot deflection

// ---- Laser ----
#define LASER 13

// ---- Microstepping ----
#define MICROSTEPS 256

#define INPUT_SIZE 20      // Maximum length of expected Gcode Commands
#define INTERPOLATION 20   // Radius moves approximated by n=INTERPOLATION linear submoves

// Speeds scaled 4x vs the old 64-microstep firmware (256/64 = 4)
// to keep the same physical angular speed. Re-tune as needed.
int jogSpeed  = 2000;   // G0 jog speed, steps/sec
int workSpeed = 1000;    // G1 work speed, steps/sec
int homeSpeed = 8000;   // homing seek speed, steps/sec

// Tune these for a true 45 degree mirror position after homing.
// Old values were for 64 microsteps; scaled x4 as a starting point.
long homePosX = 5880;
long homePosY = 5400;

#define AUTO_HOME_ON_BOOT true

// ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

int steps_per_rot = 200 * MICROSTEPS;
double degrees_per_step = 360.00 / steps_per_rot;

struct XY {
  double x;
  double y;
};

TMC2209Stepper driverX(&TMC_SERIAL, R_SENSE, DRIVER_X_ADDR);
TMC2209Stepper driverY(&TMC_SERIAL, R_SENSE, DRIVER_Y_ADDR);

AccelStepper Xaxis(AccelStepper::DRIVER, STEP_X, DIR_X);
AccelStepper Yaxis(AccelStepper::DRIVER, STEP_Y, DIR_Y);

const byte numChars = INPUT_SIZE;
char receivedChars[numChars];
boolean newData = false;
byte workMode = 0;

struct XY currPos;
struct XY saved;

boolean manualMode = false;

void setup() {

  pinMode(ENDSTOP_X, INPUT_PULLUP);
  pinMode(ENDSTOP_Y, INPUT_PULLUP);
  pinMode(LASER, OUTPUT);
  digitalWrite(LASER, LOW);

  Serial.begin(115200);
  Serial.println("<< GALVO_STEP TMC2209 V2 READY >>");

  setupTMCDrivers();

  // Reverse direction on both axes. This inverts the DIR pin logic so
  // homing, jogging, and normal moves are all flipped consistently.
  Xaxis.setPinsInverted(true, false, false);
  Yaxis.setPinsInverted(true, false, false);

  Xaxis.setMaxSpeed(jogSpeed);
  Yaxis.setMaxSpeed(jogSpeed);
  Xaxis.setAcceleration(4000);
  Yaxis.setAcceleration(4000);

  if (AUTO_HOME_ON_BOOT) {
    Serial.println("Auto-homing...");
    homing();
    Serial.println("Homing complete.");
  }
}

void loop() {

  if (manualMode) {
    jogUpdate();
  }

  recvWithEndMarker();
  if (newData == true) {
    readGCode();
    newData = false;
  }

  if (digitalRead(ENDSTOP_Y) == ENDSTOP_TRIGGERED) {
    Serial.println("Y limit triggered");
    delay(200);
  }
  if (digitalRead(ENDSTOP_X) == ENDSTOP_TRIGGERED) {
    Serial.println("X limit triggered");
    delay(200);
  }
}

// ~~~~~~~~~~~~~~~~~~~~ TMC2209 UART setup ~~~~~~~~~~~~~~~~~~~~

void setupTMCDrivers() {

  TMC_SERIAL.begin(TMC_BAUD, SERIAL_8N1, TMC_RX_PIN, TMC_TX_PIN);
  delay(100);

  driverX.begin();
  driverY.begin();

  driverX.toff(TOFF_VALUE);
  driverY.toff(TOFF_VALUE);

  driverX.rms_current(RMS_CURRENT_MA);
  driverY.rms_current(RMS_CURRENT_MA);

  driverX.microsteps(MICROSTEPS);
  driverY.microsteps(MICROSTEPS);

  // StealthChop for quiet operation. Set false + en_spreadCycle(true)
  // if you need more torque at speed and don't mind the noise.
  driverX.en_spreadCycle(false);
  driverY.en_spreadCycle(false);
  driverX.pwm_autoscale(true);
  driverY.pwm_autoscale(true);

  // Sanity check UART link
  Serial.print("Driver X UART test (0=fail): ");
  Serial.println(driverX.test_connection() == 0 ? "OK" : "FAIL");
  Serial.print("Driver Y UART test (0=fail): ");
  Serial.println(driverY.test_connection() == 0 ? "OK" : "FAIL");
}

// ~~~~~~~~~~~~~~~~~~~~ Motion / mirror math ~~~~~~~~~~~~~~~~~~~~

// Applies a 90 degree counter-clockwise rotation followed by a mirror
// to every physical move. currPos, G60/G61 save-recall, and the arc
// interpolation math all stay in the original logical G-code space;
// only the final physical target passed to move_To is transformed.
void transformXY(double xin, double yin, double &xout, double &yout) {
  // Step 1: rotate 90 deg CCW -> (x,y) becomes (-y, x)
  double rx = -yin;
  double ry = xin;

  // Step 2: mirror about the Y axis (flips left/right).
  // If the result comes out upside-down instead of left-right flipped,
  // swap which line is negated here to mirror about the X axis instead:
  //   double mx = rx; double my = -ry;
  double mx = -rx;
  double my = ry;

  xout = mx;
  yout = my;
}

void move_To(double x, double y) {

  double tx, ty;
  transformXY(x, y, tx, ty);

  struct XY angle;
  angle.y = (atan(ty / D) / 2) * 57.2957795131;
  angle.x = (atan(tx / (E + sqrt(pow(D, 2) + pow(ty, 2)))) / 2) * 57.2957795131;

  long step_pos[2];
  step_pos[0] = (long)(angle.x / degrees_per_step);
  step_pos[1] = (long)(angle.y / degrees_per_step);

  MultiStepper steppers;
  if (workMode == 0) {
    Xaxis.setMaxSpeed(jogSpeed);
    Yaxis.setMaxSpeed(jogSpeed);
  } else {
    Xaxis.setMaxSpeed(workSpeed);
    Yaxis.setMaxSpeed(workSpeed);
  }
  steppers.addStepper(Xaxis);
  steppers.addStepper(Yaxis);
  steppers.moveTo(step_pos);
  steppers.runSpeedToPosition();
}

// ~~~~~~~~~~~~~~~~~~~~ G-code parsing ~~~~~~~~~~~~~~~~~~~~

void readGCode() {

  struct XY pos;
  char CommandTypes[] = { 'G', 'X', 'Y', 'M', 'I', 'J', 'F' };

  double Commands[sizeof(CommandTypes)] = { 0 };
  int CommandIndeces[sizeof(CommandTypes)] = { 0 };

  for (unsigned int j = 0; j < sizeof(CommandTypes); j++) {

    CommandIndeces[j] = indexof(receivedChars, CommandTypes[j]);
    if (CommandIndeces[j] != -1) {
      int endIndex = CommandIndeces[j];
      while (receivedChars[endIndex] != ' ' && receivedChars[endIndex] != 0) {
        endIndex++;
      }
      endIndex--;
      int dLen = endIndex - CommandIndeces[j];
      char cmd[dLen + 1];
      cmd[dLen] = '\0';
      int h = 0;
      for (int k = CommandIndeces[j] + 1; k <= endIndex; k++) {
        cmd[h] = receivedChars[k];
        h++;
      }
      Commands[j] = atof(cmd);
    }
  }

  if (CommandIndeces[1] != -1) {
    pos.x = Commands[1];
  } else { pos.x = currPos.x; }

  if (CommandIndeces[2] != -1) {
    pos.y = Commands[2];
  } else { pos.y = currPos.y; }

  if (CommandIndeces[0] != -1) {
    switch ((int)Commands[0]) {
      case 0:
        if (CommandIndeces[6] != -1) {
          jogSpeed = (int)Commands[6];
        }
        workMode = 0;
        move_To(pos.x, pos.y);
        Serial.println("Moved laser");
        Serial.println("OK");
        break;
      case 1:
        if (CommandIndeces[6] != -1) {
          workSpeed = (int)Commands[6];
        }
        workMode = 1;
        delay(50);
        move_To(pos.x, pos.y);
        delay(10);
        Serial.println("Moved laser");
        Serial.println("OK");
        break;
      case 2:
        if (CommandIndeces[4] == -1) { Commands[4] = 0; }
        if (CommandIndeces[5] == -1) { Commands[5] = 0; }
        moveArc(true, Commands[1], Commands[2], Commands[4], Commands[5]);
        Serial.println("Moved laser");
        Serial.println("OK");
        break;
      case 3:
        if (CommandIndeces[4] == -1) { Commands[4] = 0; }
        if (CommandIndeces[5] == -1) { Commands[5] = 0; }
        moveArc(false, Commands[1], Commands[2], Commands[4], Commands[5]);
        Serial.println("Moved laser");
        Serial.println("OK");
        break;
      case 28:
        homing();
        Serial.println("Mirrors homed successfully.");
        Serial.println("OK");
        break;
      case 60:
        saved.x = currPos.x;
        saved.y = currPos.y;
        Serial.println("Saved the current position");
        Serial.println("OK");
        break;
      case 61:
        pos.x = saved.x;
        pos.y = saved.y;
        move_To(pos.x, pos.y);
        Serial.print("Remembered X:"); Serial.print(pos.x); Serial.print(", Y: "); Serial.println(pos.y);
        Serial.println("OK");
        break;
      case 99:
        Serial.println("RESETTING");
        Serial.end();
        ESP.restart();
        break;
      default:
        Serial.println("Command unknown. I'll skip this one.");
        Serial.println("OK");
        break;
    }
  } else {
    switch ((int)Commands[3]) {
      case 3:
        digitalWrite(LASER, HIGH);
        Serial.println("LASER ON");
        Serial.println("OK");
        break;
      case 4:
        digitalWrite(LASER, HIGH);
        Serial.println("LASER ON");
        Serial.println("OK");
        break;
      case 5:
        digitalWrite(LASER, LOW);
        Serial.println("LASER OFF");
        Serial.println("OK");
        break;
      case 6:
        Serial.println("Firing the Laser for 100ms");
        digitalWrite(LASER, HIGH);
        delay(100);
        digitalWrite(LASER, LOW);
        Serial.println("OK");
        break;
      case 50:
        manualMode = true;
        Serial.println("Manual jog mode ON (potentiometers). Send M51 to exit.");
        Serial.println("OK");
        break;
      case 51:
        manualMode = false;
        Xaxis.stop();
        Yaxis.stop();
        Serial.println("Manual jog mode OFF");
        Serial.println("OK");
        break;
      case 201:
        Serial.println("Acceleration is deprecated.");
        Serial.println("OK");
        break;
      case 203:
        Serial.println("Max Speed is deprecated.");
        Serial.println("OK");
        break;
      case 906:
        // Usage: M906 X[mA] Y[mA] -- live current adjust
        if (CommandIndeces[1] != -1) driverX.rms_current((uint16_t)Commands[1]);
        if (CommandIndeces[2] != -1) driverY.rms_current((uint16_t)Commands[2]);
        Serial.println("Current updated");
        Serial.println("OK");
        break;
      default:
        Serial.println("Command unknown. I'll skip this one.");
        Serial.println("OK");
        break;
    }
  }

  currPos.x = pos.x;
  currPos.y = pos.y;
}

void recvWithEndMarker() {
  static byte ndx = 0;
  char endMarker = '\n';
  char rc;

  while (Serial.available() > 0 && newData == false) {
    rc = Serial.read();
    if (rc != endMarker) {
      receivedChars[ndx] = rc;
      ndx++;
      if (ndx >= numChars) {
        ndx = numChars - 1;
      }
    } else {
      receivedChars[ndx] = '\0';
      ndx = 0;
      newData = true;
    }
  }
}

int indexof(char str[], char c) {
  byte j = 0;
  boolean found = false;
  while (j < numChars) {
    if (str[j] != c) { j++; }
    else { found = true; break; }
  }
  if (found) { return j; }
  else { return -1; }
}

// ~~~~~~~~~~~~~~~~~~~~ Homing ~~~~~~~~~~~~~~~~~~~~

void homing() {

  int prevXmaxSpeed = Xaxis.maxSpeed();
  int prevYmaxSpeed = Yaxis.maxSpeed();

  Xaxis.setMaxSpeed(homeSpeed);
  Yaxis.setMaxSpeed(homeSpeed);

  // --- X axis ---
  Xaxis.setSpeed(-homeSpeed);
  while (digitalRead(ENDSTOP_X) != ENDSTOP_TRIGGERED) {
    Xaxis.runSpeed();
  }
  Xaxis.setCurrentPosition(0);

  Xaxis.moveTo(homePosX);
  Xaxis.setSpeed(homeSpeed);
  while (Xaxis.distanceToGo() != 0) {
    Xaxis.runSpeed();
  }
  Xaxis.setCurrentPosition(0);

  // --- Y axis ---
  Yaxis.setSpeed(-homeSpeed);
  while (digitalRead(ENDSTOP_Y) != ENDSTOP_TRIGGERED) {
    Yaxis.runSpeed();
  }
  Yaxis.setCurrentPosition(0);

  Yaxis.moveTo(homePosY);
  Yaxis.setSpeed(homeSpeed);
  while (Yaxis.distanceToGo() != 0) {
    Yaxis.runSpeed();
  }
  Yaxis.setCurrentPosition(0);

  Xaxis.setMaxSpeed(prevXmaxSpeed);
  Yaxis.setMaxSpeed(prevYmaxSpeed);

  currPos.x = 0.0;
  currPos.y = 0.0;
}

// ~~~~~~~~~~~~~~~~~~~~ Manual jog (potentiometers) ~~~~~~~~~~~~~~~~~~~~

void jogUpdate() {

  int rawX = analogRead(POT_X);   // 0-4095, 12-bit ADC
  int rawY = analogRead(POT_Y);

  int centeredX = rawX - 2048;
  int centeredY = rawY - 2048;

  float speedX = 0;
  float speedY = 0;

  if (abs(centeredX) > POT_DEADZONE) {
    speedX = map(centeredX, -2048, 2048, -POT_MAX_JOG_SPEED, POT_MAX_JOG_SPEED);
  }
  if (abs(centeredY) > POT_DEADZONE) {
    speedY = map(centeredY, -2048, 2048, -POT_MAX_JOG_SPEED, POT_MAX_JOG_SPEED);
  }

  Xaxis.setSpeed(speedX);
  Yaxis.setSpeed(speedY);

  if (speedX != 0) Xaxis.runSpeed();
  if (speedY != 0) Yaxis.runSpeed();
}

// ~~~~~~~~~~~~~~~~~~~~ Arcs ~~~~~~~~~~~~~~~~~~~~

void moveArc(boolean CW, double X, double Y, double I, double J) {
  struct XY center;
  double startAngle;
  double endAngle;
  struct XY startPos = currPos;
  struct XY endPos; endPos.x = X; endPos.y = Y;
  struct XY points[INTERPOLATION];
  double radius = sqrt(pow(I, 2) + pow(J, 2));
  points[0] = startPos;
  points[INTERPOLATION - 1] = endPos;

  center.x = startPos.x + I;
  center.y = startPos.y + J;

  startAngle = getAngle(startPos.x - center.x, startPos.y - center.y);
  endAngle = getAngle(endPos.x - center.x, endPos.y - center.y);
  if (CW) {
    if (startAngle <= endAngle) {
      startAngle += 6.28318530718;
    }
  } else {
    if (startAngle >= endAngle) {
      endAngle += 6.28318530718;
    }
  }

  double increment = ((endAngle - startAngle) / (INTERPOLATION - 1));
  double currAngle = startAngle;
  struct XY currPoint;
  for (int i = 1; i < INTERPOLATION - 1; i++) {
    currAngle += increment;
    currPoint.x = radius * cos(currAngle) + center.x;
    currPoint.y = radius * sin(currAngle) + center.y;
    points[i] = currPoint;
  }
  for (int i = 1; i < INTERPOLATION; i++) {
    move_To(points[i].x, points[i].y);
  }
}

double getAngle(double x, double y) {
  double phi = atan(y / x);
  if (x >= 0 && y >= 0) {
    return phi;
  } else if (x > 0 && y < 0) {
    return phi + 6.28318530718;
  } else {
    return phi + 3.14159265359;
  }
}
