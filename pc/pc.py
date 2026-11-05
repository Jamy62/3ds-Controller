import socket, select, struct, time, math, signal, sys, os, datetime, platform

debug_mode, last_button_state, running = False, 0, True
gamepad, connected_devices = None, {}

# How much of the step is smoothed per 16.6ms (60Hz) frame:
# 0.45 = ultra smooth, 0.55 = balanced smooth filter, 0.70 = raw/snappy
SMOOTHING_PER_FRAME = 0.50

cur_lx, cur_ly, cur_rx, cur_ry = 0.0, 0.0, 0.0, 0.0
tgt_lx, tgt_ly, tgt_rx, tgt_ry = 0, 0, 0, 0
last_step_time = time.perf_counter()

KEY_A, KEY_B, KEY_SELECT, KEY_START = 1, 2, 4, 8
KEY_DRIGHT, KEY_DLEFT, KEY_DUP, KEY_DDOWN = 16, 32, 64, 128
KEY_R, KEY_L, KEY_X, KEY_Y, KEY_ZL, KEY_ZR = 256, 512, 1024, 2048, 16384, 32768

os_name = platform.system()
print(f"Detected OS: {os_name}")

if os_name == "Windows":
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass
    try:
        import vgamepad as vg
        gamepad_type = "vgamepad"
    except ImportError:
        os.system('pip install vgamepad')
        try:
            import vgamepad as vg
            gamepad_type = "vgamepad"
        except ImportError:
            print("Failed to install vgamepad. Install it manually: pip install vgamepad")
            sys.exit(1)
elif os_name == "Linux":
    try:
        import uinput
        gamepad_type = "uinput"
    except ImportError:
        os.system('pip install python-uinput')
        try:
            import uinput
            gamepad_type = "uinput"
        except ImportError:
            print("Failed to install python-uinput. Install it manually: pip install python-uinput")
            sys.exit(1)
else:
    print(f"Unsupported OS: {os_name}")
    sys.exit(1)

def setup_controller():
    global gamepad_type
    try:
        if gamepad_type == "vgamepad":
            gamepad = vg.VX360Gamepad()
            print("Xbox 360 controller initialized")
            gamepad.reset()
            return gamepad
        elif gamepad_type == "uinput":
            events = (
                uinput.BTN_A, uinput.BTN_B, uinput.BTN_X, uinput.BTN_Y, uinput.BTN_TL, uinput.BTN_TR,
                uinput.BTN_START, uinput.BTN_SELECT, uinput.BTN_THUMBL, uinput.BTN_THUMBR,
                uinput.ABS_X + (0, 255, 0, 0), uinput.ABS_Y + (0, 255, 0, 0),
                uinput.ABS_RX + (0, 255, 0, 0), uinput.ABS_RY + (0, 255, 0, 0),
                uinput.ABS_Z + (0, 255, 0, 0), uinput.ABS_RZ + (0, 255, 0, 0),
                uinput.ABS_HAT0X + (-1, 1, 0, 0), uinput.ABS_HAT0Y + (-1, 1, 0, 0)
            )
            device = uinput.Device(events, name="3DS Controller")
            print("Linux uinput controller initialized")
            for axis in [uinput.ABS_X, uinput.ABS_Y, uinput.ABS_RX, uinput.ABS_RY]: device.emit(axis, 128)
            for axis in [uinput.ABS_Z, uinput.ABS_RZ, uinput.ABS_HAT0X, uinput.ABS_HAT0Y]: device.emit(axis, 0)
            return device
    except Exception as e:
        print(f"Failed to initialize controller: {e}")
        return None

def map_stick_radial(x, y, deadzone=15, max_in=142):
    """True circular deadzone with smooth 0..32767 rescaling"""
    mag = math.hypot(x, y)
    if mag <= deadzone:
        return 0, 0
    norm = min(1.0, (mag - deadzone) / (max_in - deadzone))
    scale = (norm * 32767.0) / mag
    out_x = int(max(-32768, min(32767, round(x * scale))))
    out_y = int(max(-32768, min(32767, round(y * scale))))
    return out_x, out_y

def map_axis_to_uinput(value):
    return int((value + 32768) * 255 / 65535)

def signal_handler(sig, frame):
    global running
    print("\nExiting...")
    running = False

def update_buttons_and_targets(data, gamepad):
    global last_button_state, tgt_lx, tgt_ly, tgt_rx, tgt_ry
    buttons, cpad_x, cpad_y, cstick_x, cstick_y = struct.unpack("=Ihhhh", data[0:12])
    button_changes = buttons ^ last_button_state

    tgt_lx, tgt_ly = map_stick_radial(cpad_x, cpad_y, deadzone=15, max_in=142)
    tgt_rx, tgt_ry = map_stick_radial(cstick_x, cstick_y, deadzone=15, max_in=142)

    if gamepad_type == "vgamepad":
        # Positional mapping: 3DS B -> Xbox A, 3DS A -> Xbox B, 3DS Y -> Xbox X, 3DS X -> Xbox Y
        for key, vg_btn in [(KEY_B, vg.XUSB_BUTTON.XUSB_GAMEPAD_A), (KEY_A, vg.XUSB_BUTTON.XUSB_GAMEPAD_B), 
                            (KEY_Y, vg.XUSB_BUTTON.XUSB_GAMEPAD_X), (KEY_X, vg.XUSB_BUTTON.XUSB_GAMEPAD_Y),
                            (KEY_L, vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER), (KEY_R, vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER),
                            (KEY_START, vg.XUSB_BUTTON.XUSB_GAMEPAD_START), (KEY_SELECT, vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK)]:
            if button_changes & key:
                gamepad.press_button(vg_btn) if buttons & key else gamepad.release_button(vg_btn)
        
        gamepad.left_trigger(255 if buttons & KEY_ZL else 0)
        gamepad.right_trigger(255 if buttons & KEY_ZR else 0)
        
        for key, dpad_btn in [(KEY_DUP, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP),
                              (KEY_DDOWN, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN),
                              (KEY_DLEFT, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),
                              (KEY_DRIGHT, vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)]:
            if button_changes & key:
                gamepad.press_button(dpad_btn) if buttons & key else gamepad.release_button(dpad_btn)

    elif gamepad_type == "uinput":
        for key, ui_btn in [(KEY_B, uinput.BTN_A), (KEY_A, uinput.BTN_B), (KEY_Y, uinput.BTN_X), (KEY_X, uinput.BTN_Y), 
                            (KEY_L, uinput.BTN_TL), (KEY_R, uinput.BTN_TR), (KEY_START, uinput.BTN_START), (KEY_SELECT, uinput.BTN_SELECT)]:
            if button_changes & key:
                gamepad.emit(ui_btn, 1 if buttons & key else 0)
        gamepad.emit(uinput.ABS_Z, 255 if buttons & KEY_ZL else 0)
        gamepad.emit(uinput.ABS_RZ, 255 if buttons & KEY_ZR else 0)
        gamepad.emit(uinput.ABS_HAT0Y, -1 if buttons & KEY_DUP else 1 if buttons & KEY_DDOWN else 0)
        gamepad.emit(uinput.ABS_HAT0X, -1 if buttons & KEY_DLEFT else 1 if buttons & KEY_DRIGHT else 0)

    last_button_state = buttons

def step_stick_smoothing(gamepad, force_update=False):
    global cur_lx, cur_ly, cur_rx, cur_ry, last_step_time
    now = time.perf_counter()
    dt = min(0.05, max(0.0005, now - last_step_time))
    last_step_time = now

    # Time-corrected filter so it always smooths at the exact SMOOTHING_PER_FRAME rate
    alpha = 1.0 - math.pow(1.0 - SMOOTHING_PER_FRAME, dt / 0.01667)

    prev_ix, prev_iy, prev_rx, prev_ry = int(round(cur_lx)), int(round(cur_ly)), int(round(cur_rx)), int(round(cur_ry))

    if tgt_lx == 0 and tgt_ly == 0:
        cur_lx, cur_ly = 0.0, 0.0
    else:
        cur_lx += (tgt_lx - cur_lx) * alpha
        cur_ly += (tgt_ly - cur_ly) * alpha

    if tgt_rx == 0 and tgt_ry == 0:
        cur_rx, cur_ry = 0.0, 0.0
    else:
        cur_rx += (tgt_rx - cur_rx) * alpha
        cur_ry += (tgt_ry - cur_ry) * alpha

    ix, iy, irx, iry = int(round(cur_lx)), int(round(cur_ly)), int(round(cur_rx)), int(round(cur_ry))
    changed = (ix != prev_ix or iy != prev_iy or irx != prev_rx or iry != prev_ry)

    if changed or force_update:
        if gamepad_type == "vgamepad":
            gamepad.left_joystick(ix, iy)
            gamepad.right_joystick(irx, iry)
            gamepad.update()
        elif gamepad_type == "uinput":
            gamepad.emit(uinput.ABS_X, map_axis_to_uinput(ix))
            gamepad.emit(uinput.ABS_Y, map_axis_to_uinput(-iy))
            gamepad.emit(uinput.ABS_RX, map_axis_to_uinput(irx))
            gamepad.emit(uinput.ABS_RY, map_axis_to_uinput(-iry))
            gamepad.syn()

def register_device(addr):
    ip_address = addr[0]
    if ip_address not in connected_devices:
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{current_time}] New 3DS connected from {ip_address}")
        connected_devices[ip_address] = {"last_seen": time.time(), "name": f"3DS at {ip_address}"}
    else:
        connected_devices[ip_address]["last_seen"] = time.time()

def check_inactive_devices():
    current_time = time.time()
    for addr in [a for a, info in connected_devices.items() if current_time - info["last_seen"] > 10]:
        current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{current_time_str}] 3DS at {addr} disconnected (timeout)")
        connected_devices.pop(addr)

def main():
    global debug_mode, running, gamepad, gamepad_type, server_socket
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--debug":
        debug_mode = True
        print("Debug mode enabled")
    
    gamepad = setup_controller()
    if not gamepad:
        print("Cannot continue without virtual controller. Exiting.")
        sys.exit(1)
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    server_socket.bind(('0.0.0.0', 8888))
    server_socket.setblocking(False)
    
    print("3DS Controller PC Receiver (Time-Locked Smooth Filter + 60Hz Ready)")
    print(f"Running in {gamepad_type} mode for {os_name}")
    print("Listening on port 8888...")
    print("Press Ctrl+C to exit")
    
    last_check_time = time.time()
    
    try:
        while running:
            ready, _, _ = select.select([server_socket], [], [], 0.004)
            got_state = False
            if ready:
                while True:
                    try:
                        data, addr = server_socket.recvfrom(1024)
                        register_device(addr)
                        if len(data) == 5 and data == b'ping\x00':
                            server_socket.sendto(b'pong', addr)
                            if debug_mode: print(f"Ping from {addr[0]}, sent pong")
                        elif len(data) >= 12:
                            update_buttons_and_targets(data, gamepad)
                            got_state = True
                    except (BlockingIOError, ConnectionResetError):
                        break

            step_stick_smoothing(gamepad, force_update=got_state)

            current_time = time.time()
            if current_time - last_check_time > 2:
                check_inactive_devices()
                last_check_time = current_time
    finally:
        print("Shutting down...")
        if os_name == "Windows":
            try: ctypes.windll.winmm.timeEndPeriod(1)
            except Exception: pass
        if gamepad and gamepad_type == "vgamepad":
            gamepad.reset()
        server_socket.close()
        print("Socket closed. Exiting.")

if __name__ == "__main__":
    main()
