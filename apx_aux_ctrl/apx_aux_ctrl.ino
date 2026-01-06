/*
  Arduino Uno R3: 5 inputs + 5 outputs + USB serial command interface

  Commands:
    get-out <0..4>
    set-out <0..4> <0|1>
    get-in  <0..4>
    help

  Default input mode: INPUT_PULLUP
  - read value is inverted (LOW means "active") only if you wire switches to GND.
  - If you use external 5V logic inputs, switch INPUT_MODE below to INPUT and add pulldown resistors.
*/

#include <Arduino.h>

// -------- Pin configuration --------
static const uint8_t IN_PINS[5]  = {2, 3, 4, 5, 6};
static const uint8_t OUT_PINS[5] = {8, 9, 10, 11, 12};

// Choose input mode:
//   INPUT_PULLUP: easiest wiring (switch to GND). Reads HIGH when open, LOW when grounded.
//   INPUT: for external driven 0/5V signals (add pulldown if signal can float).
static const uint8_t INPUT_MODE = INPUT_PULLUP;

// If using INPUT_PULLUP with switches-to-GND, you might want to invert so "pressed" -> 1.
// Set to true if you want logical 1 when pin reads LOW.
static const bool INVERT_INPUT_LOGIC = false;

// -------- Simple line buffer --------
static const size_t LINE_BUF_SIZE = 96;
static char lineBuf[LINE_BUF_SIZE];
static size_t lineLen = 0;

static int parseIndex(const char* s) {
  if (!s || !*s) return -1;
  char* endp = nullptr;
  long v = strtol(s, &endp, 10);
  if (*endp != '\0') return -1;
  if (v < 0 || v > 4) return -1;
  return (int)v;
}

static int parseBit(const char* s) {
  if (!s || !*s) return -1;
  if (strcmp(s, "0") == 0) return 0;
  if (strcmp(s, "1") == 0) return 1;
  return -1;
}

static int readLogicalInput(uint8_t idx) {
  int raw = digitalRead(IN_PINS[idx]); // HIGH or LOW
  int logical = (raw == HIGH) ? 1 : 0;
  if (INVERT_INPUT_LOGIC) logical = 1 - logical;
  return logical;
}

static int readOutput(uint8_t idx) {
  // digitalRead on OUTPUT pin returns the last written state on AVR.
  int v = digitalRead(OUT_PINS[idx]);
  return (v == HIGH) ? 1 : 0;
}

static void writeOutput(uint8_t idx, int bit) {
  digitalWrite(OUT_PINS[idx], bit ? HIGH : LOW);
}

static void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  get-out <0..4>        -> prints 0 or 1"));
  Serial.println(F("  set-out <0..4> <0|1>  -> prints OK"));
  Serial.println(F("  get-in  <0..4>        -> prints 0 or 1"));
  Serial.println(F("  help"));
  Serial.println(F(""));
  Serial.print(F("Input mode: "));
  Serial.println(INPUT_MODE == INPUT_PULLUP ? F("INPUT_PULLUP") : F("INPUT"));
  Serial.print(F("Input logic inverted: "));
  Serial.println(INVERT_INPUT_LOGIC ? F("yes") : F("no"));
}

// Tokenize in-place by spaces. Returns number of tokens.
static int tokenize(char* s, char* toks[], int maxToks) {
  int n = 0;
  while (*s && n < maxToks) {
    while (*s == ' ' || *s == '\t' || *s == '\r') s++;
    if (!*s) break;
    toks[n++] = s;
    while (*s && *s != ' ' && *s != '\t' && *s != '\r') s++;
    if (*s) { *s = '\0'; s++; }
  }
  return n;
}

static void handleLine(char* line) {
  // Trim leading spaces
  while (*line == ' ' || *line == '\t' || *line == '\r') line++;

  if (*line == '\0') return;

  char* toks[4] = {0};
  int nt = tokenize(line, toks, 4);

  if (nt <= 0) return;

  if (strcmp(toks[0], "help") == 0) {
    printHelp();
    return;
  }

  if (strcmp(toks[0], "get-out") == 0) {
    if (nt != 2) { Serial.println(F("ERR usage: get-out <0..4>")); return; }
    int idx = parseIndex(toks[1]);
    if (idx < 0) { Serial.println(F("ERR index must be 0..4")); return; }
    Serial.println(readOutput((uint8_t)idx));
    return;
  }

  if (strcmp(toks[0], "set-out") == 0) {
    if (nt != 3) { Serial.println(F("ERR usage: set-out <0..4> <0|1>")); return; }
    int idx = parseIndex(toks[1]);
    if (idx < 0) { Serial.println(F("ERR index must be 0..4")); return; }
    int bit = parseBit(toks[2]);
    if (bit < 0) { Serial.println(F("ERR value must be 0 or 1")); return; }
    writeOutput((uint8_t)idx, bit);
    Serial.println(F("OK"));
    return;
  }

  if (strcmp(toks[0], "get-in") == 0) {
    if (nt != 2) { Serial.println(F("ERR usage: get-in <0..4>")); return; }
    int idx = parseIndex(toks[1]);
    if (idx < 0) { Serial.println(F("ERR index must be 0..4")); return; }
    Serial.println(readLogicalInput((uint8_t)idx));
    return;
  }

  Serial.println(F("ERR unknown command (try: help)"));
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { /* Uno doesn't need this, but harmless */ }

  for (uint8_t i = 0; i < 5; i++) {
    pinMode(IN_PINS[i], INPUT_MODE);
  }
  for (uint8_t i = 0; i < 5; i++) {
    pinMode(OUT_PINS[i], OUTPUT);
    digitalWrite(OUT_PINS[i], LOW);
  }

  Serial.println(F("READY"));
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    // Treat Enter (\r) as newline so terminals work normally
    if (c == '\r') c = '\n';

    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      handleLine(lineBuf);
      lineLen = 0;
      continue;
    }

    // Add to buffer if space remains
    if (lineLen < LINE_BUF_SIZE - 1) {
      lineBuf[lineLen++] = c;
    } else {
      // Overflow: reset and error
      lineLen = 0;
      Serial.println(F("ERR line too long"));
    }
  }
}
