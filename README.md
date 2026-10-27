# W55MH32 64x8 LED Matrix Streamer

This repository contains the current two-part LED streaming system:

- `streamer.py` runs on a Windows computer. It generates 64x8 animation frames and sends them over UDP.
- `UDP2.py` runs on a W55MH32 board using MicroPython. It receives the frames and drives two chained 32x8 WS2812 LED matrices.

Do not mix this pair with the older `UDP.py` and `streamer2.py` files. Those files use a different single-panel protocol.

## System overview

```text
Blynk dashboard (optional)
          │ MQTT
          ▼
Windows computer running streamer.py
          │ UDP 6454
          ▼
W55MH32 board running UDP2.py
          │ PC13 → matrix 1 DIN → matrix 2 DIN
          ▼
Two chained 32x8 WS2812 matrices = 64x8 display
```

The computer and board must be able to route UDP traffic between them. A wired Ethernet connection is recommended.

## Requirements

### Windows computer

- Windows with PowerShell
- Python 3.8 or newer recommended
- Network access to the W55MH32 board
- The Paho MQTT package if Blynk controls are required:

```powershell
py -3 -m pip install paho-mqtt
```

If `py` is not available, use the same Python executable for installation and execution:

```powershell
python -m pip install paho-mqtt
```

`streamer.py` can still transmit LED frames when Paho is not installed, but Blynk MQTT controls will be unavailable.

### W55MH32 board

The board must run a MicroPython firmware that provides:

- `machine.Pin`, `machine.I2C`, `machine.SPI`, and `machine.bitstream`
- `network.WIZNET5K`
- The `ssd1306` module used by `UDP2.py`

`UDP2.py` imports `ssd1306` at startup. Install/copy a compatible `ssd1306.py` driver to the board before running the script. The OLED hardware may be absent, but the module must still be importable unless the script is modified.

### LED and Ethernet hardware

- W55MH32 board with W5500 Ethernet
- Two 32x8 WS2812-compatible matrices connected in one data chain
- Suitable 5 V LED power supply
- Common ground between the board, both matrices, and the LED power supply
- Ethernet cable and a DHCP-enabled network, unless the board firmware is changed to use a static address

## Hardware wiring

`UDP2.py` uses the following pins and settings:

| Function | Pin or setting |
| --- | --- |
| WS2812 data output | `PC13` |
| Matrix chain | `PC13 → matrix 1 DIN`; matrix 1 `DOUT →` matrix 2 `DIN` |
| LED power MOSFET | `PD4`, active high |
| On/off button | `PA8`, active low with internal pull-up |
| W5500 SPI | SPI2 at 8 MHz |
| W5500 chip select | `PB12` |
| W5500 reset | `PD9` |
| OLED I2C | I2C1, address `0x3D`, 128x64 |
| Display size | Two 32x8 panels, logical size 64x8 |

Power the matrices according to their specifications. Do not power the LED load from an unsuitable board regulator. Make sure the W55MH32, W5500, matrices, and external LED supply share ground.

## Project files used for this workflow

| File | Runs on | Purpose |
| --- | --- | --- |
| `streamer.py` | Windows computer | Generates animations and sends UDP frames |
| `UDP2.py` | W55MH32 MicroPython board | Receives frames and drives both LED panels |
| `README.md` | Documentation | This setup guide |

The C files and the older `UDP.py`/`streamer2.py` workflow are not required to run this Python/MicroPython pair.

## Step 1: Prepare the computer

Open PowerShell in the repository directory:

```powershell
Set-Location "C:\Users\Jonat\OneDrive\Documentos\USER"
```

Confirm Python is available:

```powershell
py -3 --version
```

Install the optional MQTT dependency:

```powershell
py -3 -m pip install paho-mqtt
```

You can verify that the package imports successfully:

```powershell
py -3 -c "import paho.mqtt.client; print('paho-mqtt is installed')"
```

No additional Python package is required for UDP streaming. The remaining imports used by `streamer.py` are part of Python's standard library.

### Optional virtual environment

A virtual environment keeps the dependency isolated:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install paho-mqtt
```

If PowerShell blocks activation, either use the Python executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install paho-mqtt
.\.venv\Scripts\python.exe streamer.py
```

or adjust the execution policy only according to your organization's Windows policy.

## Step 2: Upload and start `UDP2.py` on the board

`UDP2.py` is a MicroPython script. It does not run under normal Windows Python because it imports board-specific modules such as `machine` and `network.WIZNET5K`.

Use the MicroPython upload/REPL tool appropriate for your board and firmware to copy these files to the board:

1. `UDP2.py`
2. A compatible `ssd1306.py` driver
3. Any other board-specific support modules required by the installed firmware

Configure `UDP2.py` as the board's startup script, or execute it from the MicroPython REPL. The repository does not prescribe a particular uploader or serial-port number, so the exact upload command depends on the tool and firmware environment you use.

The script starts executing immediately when loaded. It has no command-line arguments and no normal `main()` function.

### Expected board startup

After reset or upload, the serial console should show messages similar to:

```text
[BOOT] Starting two-matrix controller
[NET] DHCP success. Board IP: 10.0.1.214
[UDP] Listening on port 6454; expecting 774-byte packets, 2 per frame
```

The DHCP address will be different on many networks. Record the IP address printed by the board. The OLED, when connected and initialized, also shows the IP address, FPS, dropped frames, and current animation.

The board waits up to 10 seconds for DHCP. If DHCP fails, fix the Ethernet/W5500/network problem before continuing, or modify the board code to use a static configuration.

## Step 3: Configure the computer to use the board IP

`streamer.py` reads the board address from the `LED_BOARD_IP` environment variable. Its fallback value is `10.0.1.214`.

Set the address printed by the board in PowerShell:

```powershell
$env:LED_BOARD_IP = "10.0.1.214"
```

Replace `10.0.1.214` with the actual address. This setting applies only to the current PowerShell window.

You can also change the fallback directly in `streamer.py`:

```python
BOARD_IP = os.getenv("LED_BOARD_IP", "10.0.1.214")
```

Using the environment variable is preferred because it avoids editing the program when DHCP assigns a new address.

### Basic network checks

From the computer, test basic reachability:

```powershell
Test-Connection 10.0.1.214 -Count 2
```

A successful ping does not prove that UDP is allowed, but a failure usually indicates an incorrect address, disconnected Ethernet, or a routing problem. Make sure Windows Firewall or another security product does not block outbound UDP traffic to port `6454`.

## Step 4: Run `streamer.py`

From the repository root, with `LED_BOARD_IP` set, run:

```powershell
py -3 streamer.py
```

Or, if using the virtual environment:

```powershell
.\.venv\Scripts\python.exe streamer.py
```

The expected startup message is similar to:

```text
[Streamer] Streaming 60 FPS video to 10.0.1.214:6454...
```

The program then continuously sends frames at approximately 60 FPS. Stop it with `Ctrl+C`.

`streamer.py` has no command-line arguments. All configuration is done through environment variables or constants in the source file.

## Initial animation state

When the program starts, the default state is:

| Setting | Default |
| --- | --- |
| Animation | `clock` |
| Text | `HELLO` |
| Speed | `1.0` |
| Brightness | `0.4` |
| Display size | 64x8 pixels |
| Frame rate | Approximately 60 FPS |

Available animation names are:

```text
plasma, rainbow, waves, checker, fire, clock, text
```

The built-in text font supports uppercase letters, digits, spaces, `.`, `/`, and `:`. Text received through Blynk is uppercased, filtered to supported characters, and limited to 64 characters.

## Optional Blynk MQTT controls

Blynk support is enabled by default. It is independent of UDP frame delivery: if MQTT fails, the animation stream can still run.

The default MQTT settings in `streamer.py` are:

| Setting | Default |
| --- | --- |
| Broker | `sgp1.blynk.cloud` |
| TCP port | `1883` |
| Username | `device` |
| Client ID | Defined in `streamer.py` |
| Password | Defined in `streamer.py` |
| MQTT enabled | Yes |

The repository contains an MQTT credential in source code. Treat it as exposed: revoke or rotate it before using the project publicly, and do not commit replacement credentials to a public repository. For production use, move credentials to a protected configuration mechanism rather than storing them in source.

### Configure Blynk datastreams

Create or use a Blynk device with these virtual datastreams:

| Pin | Purpose | Accepted values |
| --- | --- | --- |
| `V0` | Animation | `plasma`, `rainbow`, `waves`, `checker`, `fire`, `clock`, `text` |
| `V1` | Text | Supported font characters, up to 64 characters |
| `V2` | Animation speed | Numeric value, clamped to `0.2`–`3.0` |
| `V3` | Brightness | Numeric value, clamped to `0.0`–`1.0` |

A practical Blynk dashboard uses a dropdown for `V0`, text input for `V1`, speed slider for `V2`, and brightness slider for `V3`.

The script subscribes to:

```text
downlink/ds/+
downlink/ds/#
```

It accepts the virtual-pin suffixes above and named suffixes such as `Animation`, `Text`, `LED Text`, `Animation Speed`, `Brightness`, `speed`, and `brightness`.

When connected, it publishes the initial animation, speed, and brightness to `ds/V0`, `ds/V2`, and `ds/V3`.

### MQTT environment variables

These settings can be changed without editing the file:

```powershell
$env:LED_MQTT_ENABLED = "1"
$env:LED_MQTT_HOST = "sgp1.blynk.cloud"
$env:LED_MQTT_PORT = "1883"
```

To run UDP streaming without attempting MQTT:

```powershell
$env:LED_MQTT_ENABLED = "0"
py -3 streamer.py
```

A value of `0`, `false`, or `no` disables MQTT.

## NTP clock synchronization

The `clock` animation uses the computer's clock. NTP synchronization is enabled by default and runs in a background thread.

Optional settings:

```powershell
$env:LED_NTP_ENABLED = "1"
$env:LED_NTP_SERVER = "pool.ntp.org"
$env:LED_NTP_TIMEOUT = "3"
$env:LED_NTP_INTERVAL = "21600"
```

To disable NTP and use the local system clock:

```powershell
$env:LED_NTP_ENABLED = "0"
```

NTP requires outbound UDP port `123`. If it is blocked, the streamer reports the failure and continues using the computer's system clock.

## UDP protocol and ports

The computer sends to the board at:

```text
<board IP>:6454/UDP
```

Each logical frame contains 512 RGB pixels, or 1536 RGB payload bytes. To keep packets small, `streamer.py` sends two packets for every frame:

- Packet size: 774 bytes
- Header: `AA 55`, 16-bit frame ID, part number, animation code
- Payload: 256 RGB pixels per packet
- Part numbers: `0` and `1`

`UDP2.py` reassembles both parts, maps the logical 64x8 image to the two column-serpentine panels, converts RGB data to the WS2812 GRB order, and sends one uninterrupted 512-pixel bitstream.

`UDP2.py` creates a transmit socket for port `6455`, but the current script does not send telemetry. Do not expect encoder or back-channel data from the current `UDP2.py` implementation.

## Verification checklist

A successful setup should satisfy all of the following:

1. `UDP2.py` prints `[BOOT] Starting two-matrix controller`.
2. The board prints a valid DHCP IP address.
3. The board prints that it is listening on UDP port `6454` and expects 774-byte packets.
4. `LED_BOARD_IP` is set to that same board IP in the computer's PowerShell session.
5. `streamer.py` prints its 60 FPS streaming message.
6. The board prints `[LINK] Connected to PC: ...` after receiving a complete frame.
7. The matrices display the selected animation.
8. The board periodically prints statistics similar to:

```text
[STATS] FPS: 60.0, drops: 0, animation: clock
```

If MQTT is enabled and configured correctly, the computer also prints messages indicating a Blynk connection and heartbeat.

## Troubleshooting

### `UDP2.py` does not start

- Confirm it is being run on the W55MH32 MicroPython board, not Windows Python.
- Check that the firmware provides `machine.bitstream` and `network.WIZNET5K`.
- Confirm a compatible `ssd1306.py` module is installed on the board. `UDP2.py` imports it before its optional OLED initialization handler can run.
- Check the serial console for a syntax error, missing module, or pin-name incompatibility.

### DHCP fails or no board IP is shown

- Check the Ethernet cable and network switch.
- Confirm the W5500 SPI, chip-select, and reset wiring match `SPI2`, `PB12`, and `PD9`.
- Confirm the network provides DHCP.
- Check that the board firmware supports `network.WIZNET5K`.
- Wait for the board's 10-second DHCP timeout message before diagnosing the next step.

### `streamer.py` starts but the LEDs do not update

- Confirm the computer is using the current board IP, not the old default or a previous DHCP lease.
- Confirm `UDP2.py` is running and says it is listening on port `6454`.
- Make sure the computer and board are on the same network or have a route between them.
- Check Windows Firewall and endpoint security software for UDP restrictions.
- Confirm the sender is `streamer.py`, not the legacy `streamer2.py`.
- Confirm both panels are powered, share ground, and are connected as `PC13 → DIN 1 → DOUT 1 → DIN 2`.
- Check that the LED power MOSFET on `PD4` is enabled.
- Check that the board's `drops` count is not increasing rapidly.

### Only one panel works or the display is scrambled

- Both panels must be 32x8 and connected in one continuous data chain.
- Do not insert a pause or separate signal between panel 1 and panel 2.
- Check panel orientation and the data direction markings.
- Confirm the panels share ground with the board.
- Verify that the firmware and `UDP2.py` agree on the 64x8, two-part protocol.

### MQTT or Blynk controls do not work

- Install Paho using the same Python interpreter that runs `streamer.py`:

  ```powershell
  py -3 -m pip install paho-mqtt
  ```

- Confirm `LED_MQTT_ENABLED` is not `0`, `false`, or `no`.
- Verify the broker, client ID, username, and token in `streamer.py`.
- Confirm the Blynk datastreams use `V0`, `V1`, `V2`, and `V3`.
- Check that TCP port `1883` is reachable from the computer.
- Read the `[RAW MQTT]` lines printed by the script to confirm the received topic and payload.
- MQTT is optional; disable it while diagnosing the UDP display path.

### Animation is running but the clock is incorrect

- Check the computer's system date, time zone, and time synchronization.
- Allow outbound UDP port `123` for NTP.
- Check the `[NTP]` message in the terminal.
- If NTP cannot be reached, the program continues with the local system clock.

### Stopping and restarting

- Stop the Windows streamer with `Ctrl+C`.
- Stop or reset the board using the MicroPython REPL, reset control, or board power cycle.
- There is no graceful shutdown command implemented in `UDP2.py`; reset or power-cycle it when necessary.

## Legacy files and separate applications

- `UDP.py` and `streamer2.py` implement an older single 32x8 protocol and are not compatible with the current `streamer.py` and `UDP2.py` pair.
- `streamer2.py` also contains a legacy UDP telemetry listener; `UDP2.py` does not implement that telemetry behavior.
- `main.c` and `MQTT/do_mqtt.c` are a separate embedded C MQTT application. They are not required for this LED streaming setup and use a different MQTT service/workflow.

