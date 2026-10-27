import datetime
import json
import math
import os
import socket
import struct
import threading
import time

# Optional dependency: install with `python -m pip install paho-mqtt`.
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# Must match the IP address shown by UDP.py after DHCP/static setup.
BOARD_IP = os.getenv("LED_BOARD_IP", "10.0.1.214")
STREAM_PORT = 6454

WIDTH = 64
HEIGHT = 8

# NTP keeps the host clock used by the clock animation accurate.
NTP_ENABLED = os.getenv("LED_NTP_ENABLED", "1").lower() not in {"0", "false", "no"}
NTP_SERVER = os.getenv("LED_NTP_SERVER", "pool.ntp.org")
NTP_PORT = 123
NTP_TIMEOUT_SECONDS = float(os.getenv("LED_NTP_TIMEOUT", "3"))
NTP_SYNC_INTERVAL_SECONDS = float(os.getenv("LED_NTP_INTERVAL", "21600"))
ntp_offset_lock = threading.Lock()
ntp_offset_seconds = 0.0

# ==========================================
# BLYNK MQTT SETTINGS
# ==========================================
MQTT_ENABLED = os.getenv("LED_MQTT_ENABLED", "1").lower() not in {"0", "false", "no"}
MQTT_HOST = os.getenv("LED_MQTT_HOST", "sgp1.blynk.cloud")
MQTT_PORT = int(os.getenv("LED_MQTT_PORT", "1883"))

# Paste your working Blynk credentials here
MQTT_CLIENT_ID = "TMPL6VEugAJ7i"  # e.g., "TMPLxxxxxx"
MQTT_USERNAME = "device"
MQTT_PASSWORD = "JnYZaQCQqyGG558-Ya9X55pmphTc6GHS"  # <-- PASTE YOUR WORKING AUTH TOKEN HERE

# Datastream Pin mappings
PIN_ANIMATION = "V0"
PIN_TEXT = "V1"
PIN_SPEED = "V2"
PIN_BRIGHTNESS = "V3"

ANIMATIONS = ("plasma", "rainbow", "waves", "checker", "fire", "clock", "text")
ANIMATION_CODES = {name: index for index, name in enumerate(ANIMATIONS)}

# 5x7 font.
FONT = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}

for _letter, _rows in {
    "A":"01110 10001 10001 11111 10001 10001 10001", "B":"11110 10001 10001 11110 10001 10001 11110",
    "C":"01111 10000 10000 10000 10000 10000 01111", "D":"11110 10001 10001 10001 10001 10001 11110",
    "E":"11111 10000 10000 11110 10000 10000 11111", "F":"11111 10000 10000 11110 10000 10000 10000",
    "G":"01111 10000 10000 10111 10001 10001 01111", "H":"10001 10001 10001 11111 10001 10001 10001",
    "I":"11111 00100 00100 00100 00100 00100 11111", "J":"00111 00010 00010 00010 00010 10010 01100",
    "K":"10001 10010 10100 11000 10100 10010 10001", "L":"10000 10000 10000 10000 10000 10000 11111",
    "M":"10001 11011 10101 10101 10001 10001 10001", "N":"10001 11001 10101 10011 10001 10001 10001",
    "O":"01110 10001 10001 10001 10001 10001 01110", "P":"11110 10001 10001 11110 10000 10000 10000",
    "Q":"01110 10001 10001 10001 10101 10010 01101", "R":"11110 10001 10001 11110 10100 10010 10001",
    "S":"01111 10000 10000 01110 00001 00001 11110", "T":"11111 00100 00100 00100 00100 00100 00100",
    "U":"10001 10001 10001 10001 10001 10001 01110", "V":"10001 10001 10001 10001 10001 01010 00100",
    "W":"10001 10001 10001 10101 10101 11011 10001", "X":"10001 10001 01010 00100 01010 10001 10001",
    "Y":"10001 10001 01010 00100 00100 00100 00100", "Z":"11111 00001 00010 00100 01000 10000 11111",
}.items():
    FONT[_letter] = tuple(_rows.split())

state_lock = threading.Lock()
state = {
    "speed": 1.0,
    "brightness": 0.4,
    "animation": "clock",
    "text": "HELLO",
}


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def synchronize_clock_with_ntp():
    global ntp_offset_seconds
    packet = bytearray(48)
    packet[0] = 0x1B
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(NTP_TIMEOUT_SECONDS)
    try:
        request_time = time.time()
        sock.sendto(packet, (NTP_SERVER, NTP_PORT))
        response, _ = sock.recvfrom(512)
        receive_time = time.time()

        if len(response) < 48:
            raise ValueError("NTP response is too short")

        seconds, fraction = struct.unpack("!II", response[40:48])
        ntp_epoch = seconds - 2208988800 + fraction / 2**32
        measured_offset = ntp_epoch - ((request_time + receive_time) / 2)

        with ntp_offset_lock:
            ntp_offset_seconds = measured_offset
        print(f"[NTP] Synchronized with {NTP_SERVER}; offset {measured_offset:+.3f}s")
        return True
    except (OSError, ValueError, struct.error) as exc:
        print(f"[NTP] Synchronization failed: {exc}; using host RTC/system clock")
        return False
    finally:
        sock.close()


def clock_datetime():
    with ntp_offset_lock:
        offset = ntp_offset_seconds
    return datetime.datetime.fromtimestamp(time.time() + offset)


def ntp_worker():
    if not NTP_ENABLED:
        return
    while True:
        synchronize_clock_with_ntp()
        time.sleep(max(60.0, NTP_SYNC_INTERVAL_SECONDS))


def apply_animation_command(payload):
    animation = payload.decode("utf-8").strip().lower()
    if animation in ANIMATIONS:
        with state_lock:
            state["animation"] = animation
        print(f"\n[MQTT] Animation selected: {animation}")
    else:
        print(f"\n[MQTT] Unknown animation '{animation}'. Choose: {', '.join(ANIMATIONS)}")


def apply_text_command(payload):
    text = payload.decode("utf-8", errors="ignore").upper().strip()
    text = "".join(character for character in text if character in FONT)
    if text:
        with state_lock:
            state["text"] = text[:64]
        print(f"\n[MQTT] Text selected: {text[:64]}")


def text_pixel(text, x, y, offset):
    message_width = len(text) * 6
    column = (x + offset) % (message_width + WIDTH) - WIDTH
    if column < 0 or column >= message_width or y >= 7:
        return False
    character_index, character_column = divmod(column, 6)
    if character_column == 5:
        return False
    rows = FONT.get(text[character_index], FONT[" "])
    return rows[y][character_column] == "1"


def compact_text_pixel(text, x, y):
    """Render one compact 3x3 glyph per character for the 64-pixel display."""
    if y < 0 or y >= 3 or x < 0:
        return False
    character_index, character_column = divmod(x, 3)
    if character_index >= len(text):
        return False
    source_rows = (0, 3, 6)
    source_columns = (0, 2, 4)
    return FONT.get(text[character_index], FONT[" "])[source_rows[y]][source_columns[character_column]] == "1"


def centered_compact_pixel(text, x, y, row):
    start_x = (WIDTH - len(text) * 3) // 2
    return compact_text_pixel(text, x - start_x, y - row)


def animation_pixel(animation, x, y, t, brightness, text):
    if animation == "clock":
        now = clock_datetime()
        clock_text = "{} {} {}.{} /{}".format(
            now.strftime("%H:%M"),
            now.strftime("%A").upper(),
            now.strftime("%b").upper(),
            now.day,
            now.year,
        ).replace(" /", "/")
        # Use the original 5x7 font and scroll the complete time/date string
        # across the 64-pixel display on one horizontal line.
        lit = text_pixel(clock_text, x, y, int(t * 3))
        return (int(255 * brightness), int(180 * brightness), int(40 * brightness)) if lit else (0, 0, 0)
    if animation == "text":
        lit = text_pixel(text, x, y, int(t * 8))
        return (int(80 * brightness), int(220 * brightness), int(255 * brightness)) if lit else (0, 0, 0)
    if animation == "rainbow":
        phase = (x / WIDTH + t * 0.08) % 1.0
        red = int((math.sin(phase * math.tau) + 1.0) * 127.5)
        green = int((math.sin(phase * math.tau + 2.094) + 1.0) * 127.5)
        blue = int((math.sin(phase * math.tau + 4.188) + 1.0) * 127.5)
    elif animation == "waves":
        wave = (math.sin(x * 0.35 + y * 0.7 - t * 2.0) + 1.0) * 0.5
        red = int(40 + 80 * wave)
        green = int(80 + 150 * wave)
        blue = int(140 + 115 * wave)
    elif animation == "checker":
        phase = int(t * 2.0) % 2
        lit = ((x // 4 + y // 2 + phase) % 2) == 0
        red, green, blue = ((255, 35, 15) if lit else (10, 10, 45))
    elif animation == "fire":
        # Fire effect: hot yellow/orange base with flickering flames upward.
        height = (HEIGHT - 1 - y) / max(1, HEIGHT - 1)
        flicker = (
            math.sin(x * 0.47 + t * 2.4)
            + math.sin(x * 0.19 - t * 3.1)
            + math.sin((x + y) * 0.31 + t * 1.7)
        ) / 3.0
        heat = clamp(height * 0.72 + (flicker + 1.0) * 0.28, 0.0, 1.0)
        red = int(255 * heat)
        green = int(180 * heat * heat)
        blue = int(35 * heat * heat * heat)
    else:  # plasma
        red = int((math.sin(x * 0.25 + t) + 1.0) * 127.0)
        green = int((math.cos(y * 0.6 + t) + 1.0) * 127.0)
        blue = int((math.sin((x + y) * 0.2 + t) + 1.0) * 127.0)

    return (
        int(clamp(red * brightness, 0, 255)),
        int(clamp(green * brightness, 0, 255)),
        int(clamp(blue * brightness, 0, 255)),
    )


def start_blynk_mqtt():
    """Initializes and runs the Blynk MQTT listener in Paho's background network loop."""
    if not MQTT_ENABLED or mqtt is None:
        return None

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("downlink/ds/+", qos=0)
            client.subscribe("downlink/ds/#", qos=0)
            print(f"[MQTT] Connected to Blynk ({MQTT_HOST}:{MQTT_PORT}); subscribed to downlinks.")

            client.publish("ds/V0", state["animation"], qos=0)
            client.publish("ds/V2", str(state["speed"]), qos=0)
            client.publish("ds/V3", str(state["brightness"]), qos=0)
            print("[MQTT] Heartbeat sent to ds/V0, ds/V2, ds/V3")
        else:
            print(f"[MQTT] Connection to Blynk failed, code: {rc}")

    def on_message(client, userdata, message):
        raw_val = message.payload.decode("utf-8").strip()
        topic = message.topic
        
        print(f"\n[RAW MQTT] Topic: '{topic}' | Payload: '{raw_val}'")

        # Match Animation dropdown
        if topic.endswith(f"/{PIN_ANIMATION}") or topic.endswith("/Animation"):
            apply_animation_command(message.payload)

        # Match Text input (added /Text matching your Blynk datastream name)
        elif (
            topic.endswith(f"/{PIN_TEXT}") 
            or topic.endswith("/Text") 
            or topic.endswith("/LED Text") 
            or topic.endswith("/text")
        ):
            apply_text_command(message.payload)

        # Match Speed slider
        elif topic.endswith(f"/{PIN_SPEED}") or topic.endswith("/Animation Speed") or topic.endswith("/speed"):
            try:
                val = clamp(float(raw_val), 0.2, 3.0)
                with state_lock:
                    state["speed"] = val
                print(f"[MQTT] Blynk speed set: {val:.2f}")
            except ValueError:
                pass

        # Match Brightness slider
        elif topic.endswith(f"/{PIN_BRIGHTNESS}") or topic.endswith("/Brightness") or topic.endswith("/brightness"):
            try:
                val = clamp(float(raw_val), 0.0, 1.0)
                with state_lock:
                    state["brightness"] = val
                print(f"[MQTT] Blynk brightness set: {val:.2f}")
            except ValueError:
                pass

    client = mqtt.Client(client_id=MQTT_CLIENT_ID.strip())
    client.username_pw_set(MQTT_USERNAME.strip(), MQTT_PASSWORD.strip())
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
        return client
    except Exception as exc:
        print(f"[MQTT] Failed to start Blynk MQTT: {exc}")
        return None


def run_streamer():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    frame_id = 0
    t = 0.0
    target_frame_time = 1.0 / 60.0

    print(f"[Streamer] Streaming 60 FPS video to {BOARD_IP}:{STREAM_PORT}...")

    try:
        while True:
            start_time = time.perf_counter()
            with state_lock:
                speed = state["speed"]
                brightness = state["brightness"]
                animation = state["animation"]
                text = state["text"]

            t += 0.05 * speed
            payload = bytearray()
            for y in range(HEIGHT):
                for x in range(WIDTH):
                    r, g, b = animation_pixel(animation, x, y, t, brightness, text)
                    payload.extend([r, g, b])

            # Split the 512-pixel frame into two MTU-safe UDP packets.
            half_size = len(payload) // 2
            for part in (0, 1):
                part_header = bytearray([
                    0xAA, 0x55,
                    (frame_id >> 8) & 0xFF,
                    frame_id & 0xFF,
                    part,
                    ANIMATION_CODES.get(animation, ANIMATION_CODES["plasma"]),
                ])
                packet = part_header + payload[part * half_size:(part + 1) * half_size]
                sock.sendto(packet, (BOARD_IP, STREAM_PORT))
            frame_id = (frame_id + 1) & 0xFFFF

            elapsed = time.perf_counter() - start_time
            sleep_time = target_frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    t_ntp = threading.Thread(target=ntp_worker, daemon=True)
    t_ntp.start()

    mqtt_client = start_blynk_mqtt()

    run_streamer()