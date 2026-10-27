"""Two chained 32x8 WS2812 matrices (64x8 logical display).

Wiring: PC13 -> matrix 1 DIN, matrix 1 DOUT -> matrix 2 DIN.
Both panels must share ground with the board and their power supplies.
"""
import socket
import struct
import time
import machine
import network
import ssd1306
from machine import Pin, I2C, SPI, bitstream

WIDTH = 64
PANEL_WIDTH = 32
HEIGHT = 8
PANEL_LEDS = PANEL_WIDTH * HEIGHT
TOTAL_LEDS = WIDTH * HEIGHT
LED_PIN_NAME = "PC13"
LED_BITSTREAM_ENCODING = 0
LED_BITSTREAM_TIMING = (400, 850, 800, 450)
MOSFET_PIN_NAME = "PD4"
MOSFET_ACTIVE_LEVEL = 1
MATRIX_POWER_SETTLE_MS = 100
BUTTON_PIN_NAME = "PA8"
BUTTON_ACTIVE_LEVEL = 0
BUTTON_DEBOUNCE_MS = 40
ETHERNET_SPI_ID = 2
ETHERNET_SPI_BAUDRATE = 8_000_000
ETHERNET_CS_PIN_NAME = "PB12"
ETHERNET_RESET_PIN_NAME = "PD9"
UDP_RX_PORT = 6454
UDP_TX_PORT = 6455
OLED_WIDTH = 128
OLED_HEIGHT = 64
OLED_I2C_ID = 1
OLED_I2C_ADDRESS = 0x3D
OLED_UPDATE_INTERVAL_MS = 250
FRAME_HEADER_MAGIC = b"\xAA\x55"
FRAME_HEADER_SIZE = 4
PART_HEADER_SIZE = 2
BYTES_PER_PIXEL = 3
PART_PIXEL_COUNT = PANEL_LEDS
PART_PAYLOAD_SIZE = PART_PIXEL_COUNT * BYTES_PER_PIXEL
EXPECTED_PACKET_SIZE = FRAME_HEADER_SIZE + PART_HEADER_SIZE + PART_PAYLOAD_SIZE
ANIMATION_NAMES = ("plasma", "rainbow", "waves", "checker", "solid", "clock", "text")
DHCP_TIMEOUT_MS = 10000
MAX_PACKETS_PER_LOOP = 2

lights_enabled = True
matrix_power_ready_at = 0
button_pin = None
button_last_raw_state = 1
button_stable_state = 1
button_state_changed_at = 0
pc_ip = None
current_animation = "clock"
last_frame_id = None
pending_frame_id = None
received_parts = 0
frame_parts = (bytearray(PART_PAYLOAD_SIZE), bytearray(PART_PAYLOAD_SIZE))
dropped_frames = 0
frame_count = 0
last_stats_time = time.ticks_ms()


def initialize_mosfet():
    return Pin(MOSFET_PIN_NAME, Pin.OUT, value=0)


def set_lights_enabled(pin, enabled):
    global lights_enabled, matrix_power_ready_at
    lights_enabled = bool(enabled)
    pin.value(MOSFET_ACTIVE_LEVEL if lights_enabled else 1 - MOSFET_ACTIVE_LEVEL)
    matrix_power_ready_at = (time.ticks_add(time.ticks_ms(), MATRIX_POWER_SETTLE_MS)
                             if lights_enabled else 0)


def clear_matrix(matrix):
    for index in range(TOTAL_LEDS):
        matrix[index] = (0, 0, 0)
    matrix.write()


class BitstreamMatrix:
    """One uninterrupted 512-pixel GRB stream for both chained panels."""
    def __init__(self, pin):
        self.pin = pin
        self.pixel_count = TOTAL_LEDS
        self.buffer = bytearray(TOTAL_LEDS * BYTES_PER_PIXEL)

    def __setitem__(self, index, color):
        red, green, blue = color
        offset = index * BYTES_PER_PIXEL
        self.buffer[offset] = green
        self.buffer[offset + 1] = red
        self.buffer[offset + 2] = blue

    def write(self):
        # Never pause between panel 1 and panel 2 data.
        bitstream(self.pin, LED_BITSTREAM_ENCODING, LED_BITSTREAM_TIMING, self.buffer)


def initialize_led_matrix():
    return BitstreamMatrix(Pin(LED_PIN_NAME, Pin.OUT, value=0))


def initialize_oled():
    """Initialize the SSD1306 OLED if its driver and hardware are available."""
    try:
        i2c = I2C(OLED_I2C_ID)
        return ssd1306.SSD1306_I2C(
            OLED_WIDTH,
            OLED_HEIGHT,
            i2c,
            addr=OLED_I2C_ADDRESS,
        )
    except ImportError:
        print("[OLED] ssd1306 module not found")
    except OSError as exc:
        print("[OLED] Initialization failed:", exc)
    except Exception as exc:
        print("[OLED] Unexpected initialization error:", exc)
    return None


def build_index_map():
    """Map logical 64x8 rows to two 32x8 column-serpentine panels."""
    index_map = [0] * TOTAL_LEDS
    for y in range(HEIGHT):
        for x in range(WIDTH):
            logical = y * WIDTH + x
            panel_x = x % PANEL_WIDTH
            panel = x // PANEL_WIDTH
            if panel_x % 2 == 0:
                panel_index = panel_x * HEIGHT + y
            else:
                panel_index = panel_x * HEIGHT + (HEIGHT - 1 - y)
            index_map[logical] = panel * PANEL_LEDS + panel_index
    return index_map


def initialize_button():
    global button_pin, button_last_raw_state, button_stable_state, button_state_changed_at
    button_pin = Pin(BUTTON_PIN_NAME, Pin.IN, Pin.PULL_UP)
    button_last_raw_state = button_pin.value()
    button_stable_state = button_last_raw_state
    button_state_changed_at = time.ticks_ms()


def update_button(mosfet_pin, matrix):
    global button_last_raw_state, button_stable_state, button_state_changed_at
    raw = button_pin.value()
    now = time.ticks_ms()
    if raw != button_last_raw_state:
        button_last_raw_state = raw
        button_state_changed_at = now
        return
    if raw == button_stable_state or time.ticks_diff(now, button_state_changed_at) < BUTTON_DEBOUNCE_MS:
        return
    button_stable_state = raw
    if raw == BUTTON_ACTIVE_LEVEL:
        if lights_enabled:
            clear_matrix(matrix)
        set_lights_enabled(mosfet_pin, not lights_enabled)


def initialize_ethernet():
    spi = SPI(ETHERNET_SPI_ID, baudrate=ETHERNET_SPI_BAUDRATE, polarity=0, phase=0)
    cs = Pin(ETHERNET_CS_PIN_NAME, Pin.OUT, value=1)
    reset = Pin(ETHERNET_RESET_PIN_NAME, Pin.OUT)
    reset.value(0)
    time.sleep_ms(10)
    reset.value(1)
    time.sleep_ms(200)
    try:
        nic = network.WIZNET5K(spi, cs, reset)
        nic.active(True)
        nic.ifconfig("dhcp")
        return nic
    except Exception as exc:
        print("[NET] W5500 initialization failed:", exc)
        return None


def wait_for_dhcp(nic):
    start = time.ticks_ms()
    while not nic.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) >= DHCP_TIMEOUT_MS:
            return False
        time.sleep_ms(250)
    return True


def initialize_udp_sockets():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("0.0.0.0", UDP_RX_PORT))
    rx.setblocking(False)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setblocking(False)
    return rx, tx


def frame_difference(current, previous):
    return (current - previous) & 0xFFFF


def process_frame(data, addr, matrix, index_map):
    global pc_ip, current_animation, last_frame_id, pending_frame_id, received_parts
    global dropped_frames, frame_count

    if not lights_enabled or time.ticks_diff(time.ticks_ms(), matrix_power_ready_at) < 0:
        return
    if len(data) != EXPECTED_PACKET_SIZE or data[0:2] != FRAME_HEADER_MAGIC:
        return

    frame_id = (data[2] << 8) | data[3]
    part = data[4]
    animation_code = data[5]
    if part > 1 or animation_code >= len(ANIMATION_NAMES):
        return

    if last_frame_id is not None:
        difference = frame_difference(frame_id, last_frame_id)
        if difference == 0 or difference >= 0x8000:
            return

    if pending_frame_id is None:
        pending_frame_id = frame_id
        received_parts = 0
    elif frame_id != pending_frame_id:
        difference = frame_difference(frame_id, pending_frame_id)
        if difference >= 0x8000:
            return
        if difference > 1:
            dropped_frames += difference - 1
        pending_frame_id = frame_id
        received_parts = 0

    payload_start = FRAME_HEADER_SIZE + PART_HEADER_SIZE
    frame_parts[part][:] = data[payload_start:]
    current_animation = ANIMATION_NAMES[animation_code]
    received_parts |= 1 << part

    if received_parts != 3:
        return

    if pc_ip is None:
        pc_ip = addr[0]
        print("[LINK] Connected to PC:", pc_ip)

    for logical in range(TOTAL_LEDS):
        part_index = logical // PANEL_LEDS
        pixel_in_part = logical % PANEL_LEDS
        offset = pixel_in_part * BYTES_PER_PIXEL
        physical = index_map[logical]
        matrix[physical] = (
            frame_parts[part_index][offset],
            frame_parts[part_index][offset + 1],
            frame_parts[part_index][offset + 2],
        )

    last_frame_id = frame_id
    pending_frame_id = None
    received_parts = 0
    matrix.write()
    frame_count += 1


def receive_frames(rx, matrix, index_map):
    for _ in range(MAX_PACKETS_PER_LOOP):
        try:
            data, addr = rx.recvfrom(EXPECTED_PACKET_SIZE)
        except (OSError, MemoryError):
            return
        process_frame(data, addr, matrix, index_map)


def update_oled(oled, nic, fps):
    if oled is None:
        return
    try:
        ip_address = nic.ifconfig()[0] if nic is not None else "NO NET"
        oled.fill(0)
        oled.text("W55MH32L 2-MAT", 0, 0)
        oled.text("IP: " + ip_address, 0, 12)
        oled.text("FPS: {:.1f}".format(fps), 0, 24)
        oled.text("DROPS: {}".format(dropped_frames), 0, 36)
        oled.text("ANIM: " + current_animation, 0, 48)
        oled.show()
    except Exception as exc:
        print("[OLED] Update failed:", exc)


def update_telemetry(oled, nic):
    global frame_count, last_stats_time
    now = time.ticks_ms()
    elapsed_ms = time.ticks_diff(now, last_stats_time)
    if elapsed_ms < OLED_UPDATE_INTERVAL_MS:
        return
    elapsed_seconds = elapsed_ms / 1000.0
    fps = frame_count / elapsed_seconds if elapsed_seconds > 0 else 0.0
    frame_count = 0
    last_stats_time = now
    print("[STATS] FPS: {:.1f}, drops: {}, animation: {}".format(
        fps, dropped_frames, current_animation
    ))
    update_oled(oled, nic, fps)


print("[BOOT] Starting two-matrix controller")
matrix = initialize_led_matrix()
oled = initialize_oled()
mosfet_pin = initialize_mosfet()
initialize_button()
set_lights_enabled(mosfet_pin, lights_enabled)
index_map = build_index_map()
nic = initialize_ethernet()
if nic is not None and wait_for_dhcp(nic):
    print("[NET] DHCP success. Board IP:", nic.ifconfig()[0])
else:
    print("[NET] DHCP unavailable or failed")
try:
    rx_socket, tx_socket = initialize_udp_sockets()
    print("[UDP] Listening on port {}; expecting {}-byte packets, 2 per frame".format(UDP_RX_PORT, EXPECTED_PACKET_SIZE))
except OSError as exc:
    print("[UDP] Socket initialization failed:", exc)
    rx_socket = tx_socket = None

while True:
    update_button(mosfet_pin, matrix)
    if rx_socket is not None:
        receive_frames(rx_socket, matrix, index_map)
    update_button(mosfet_pin, matrix)
    update_telemetry(oled, nic)
    time.sleep_ms(1)
