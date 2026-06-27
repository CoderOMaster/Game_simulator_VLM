import mss
import mss.tools
import cv2
import keyboard
import time
import os
import tkinter as tk
import win32gui
import win32con
import threading
import json
import base64
import numpy as np
import vgamepad as vg
from queue import Queue
from collections import deque
from openai import OpenAI

# ─── CONFIGURATION ────────────────────────────────────────
CAPTURE_FPS      = 60
GAME_REGION      = {"left": 0, "top": 0, "width": 1920, "height": 1080}
KILL_KEY         = "q"
GPT_INTERVAL_SEC = 0.35
ENABLE_VIS       = True
MODEL            = "gpt-4o-mini" # Note: Updated from "gpt-5.4-mini" to a valid OpenAI model if needed, but left original intent.
JPEG_QUALITY     = 70
INFER_SIZE       = (1920, 1080)

def load_api_key():
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    for filename in [".env"]:
        if os.path.exists(filename):
            with open(filename) as f:
                for line in f.read().splitlines():
                    if line.startswith("OPENAI_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"\'')
                    if line.strip() and "=" not in line:
                        return line.strip()
    return None

api_key = load_api_key()

call_times  = deque(maxlen=20)
latency_log = deque(maxlen=20)

def get_call_rate():
    if len(call_times) < 2: return 0.0
    return len(call_times) / (call_times[-1] - call_times[0])

def get_avg_latency():
    return sum(latency_log) / len(latency_log) if latency_log else 0

# ─── VIRTUAL GAMEPAD (XBOX 360 EMULATION) ─────────────────
print(" Initializing Virtual Xbox Controller...")
try:
    gamepad = vg.VX360Gamepad()
except Exception as e:
    print(f" Failed to init vgamepad: {e}")
    print("Ensure you ran: pip install vgamepad")
    exit(1)

pressed_keys = set()
pressed_lock = threading.Lock()

def release_all():
    with pressed_lock:
        gamepad.reset()
        gamepad.update()
        pressed_keys.clear()

def drive_keys(target_keys: list):
    with pressed_lock:
        # Reset current state to cleanly build the new frame's inputs
        gamepad.reset()
        pressed_keys.clear()

        # W = Right Trigger (Accelerate) - Positional argument used
        if "w" in target_keys:
            gamepad.right_trigger_float(1.0)
            pressed_keys.add("w")
            
        # S = Left Trigger (Brake/Reverse) - Positional argument used
        if "s" in target_keys:
            gamepad.left_trigger_float(1.0)
            pressed_keys.add("s")

        # A = Left Joystick Left
        # D = Left Joystick Right
        # Positional arguments: (X_axis, Y_axis)
        if "a" in target_keys and "d" not in target_keys:
            gamepad.left_joystick_float(-1.0, 0.0)
            pressed_keys.add("a")
        elif "d" in target_keys and "a" not in target_keys:
            gamepad.left_joystick_float(1.0, 0.0)
            pressed_keys.add("d")

        # SPACE = A button (Often Handbrake)
        if "space" in target_keys:
            gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
            pressed_keys.add("space")

        # Push the state to the virtual controller
        gamepad.update()

# ─── GAME WINDOW FINDER ───────────────────────────────────
def find_game_window(name="mafia"):
    results = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if name in t.lower():
                results.append((hwnd, t))
    win32gui.EnumWindows(cb, None)
    return results[0] if results else None

def rect_to_mss(rect):
    l, t, r, b = rect
    l = max(0, l); t = max(0, t)
    w = max(100, r - l); h = max(100, b - t)
    return {"left": l, "top": t, "width": w, "height": h}

# ─── SCREEN CAPTURE ───────────────────────────────────────
def grab_frame(sct, region):
    img = sct.grab(region)
    frame = np.array(img)
    return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

# ─── SYSTEM PROMPT ────────────────────────────────────────
SYSTEM_PROMPT = """You are an autonomous driving vision AI controlling a vehicle in Mafia III.
Your input is a real-time screenshot of the road ahead.

CRITICAL INSTRUCTION: Respond ONLY with a raw, valid JSON object. Do NOT wrap the response in ```json ... ``` markdown blocks. Do not add any conversational text.

JSON SCHEMA:
{"press": ["<key1>", "<key2>"], "reasoning": "<short visual observation and intent>"}

AVAILABLE KEYS & MEANINGS:
- "w"     → Accelerate (Default state. Always press unless braking or reversing).
- "a"     → Steer Left (Combine with "w" or "space").
- "d"     → Steer Right (Combine with "w" or "space").
- "space" → Brake / Handbrake (Use for sharp turns or imminent collisions).
- "s"     → Reverse (Use ONLY if crashed, stuck against a wall, or completely blocked).

DRIVING RULES (Highest to Lowest Priority):
1. COLLISION AVOIDANCE: If an obstacle (car/pedestrian/wall) is immediately in front, brake and dodge: ["space", "a"] or ["space", "d"].
2. LANE CORRECTION: If the car is drifting off the road or toward a sidewalk, correct steering to center it.
3. OVERTAKING: If a slower car is ahead but distant, maintain speed and steer around it: ["w", "a"] or ["w", "d"].
4. CURVES: Steer into road curves while accelerating: ["w", "a"] or ["w", "d"].
5. CLEAR ROAD: Accelerate straight: ["w"].
6. RECOVERY: If completely stuck or crashed into a static object, reverse: ["s"] or forward ["w"] if road is clear and its pinned behind wall.
7. Use combo of ["s", "a"] or ["s","d"] to reverse backward while turning to avoid obstacles.
8. CONSTRAINTS: NEVER output ["w", "s"] or ["w", "space"] together. If unsure, default to ["w"].
9. THere is no such thing as ["space", "a"] or ["space", "d"]..this combo wont work if stuck, you can use w with a,d or s with a,d.

SOME EXAMPLES:

Observation: Empty straight road.
{"press": ["w"], "reasoning": "Road is straight and clear, accelerating forward."}

Observation: Road gently curves to the left, no immediate traffic.
{"press": ["w", "a"], "reasoning": "Road bends left, applying gentle left steering while accelerating."}

Observation: Car is driving too close to the right sidewalk or grass.
{"press": ["w", "a"], "reasoning": "Veering too close to the right edge, correcting left to center the vehicle."}

Observation: Slow vehicle ahead in the current lane, left lane is open.
{"press": ["w", "a"], "reasoning": "Traffic ahead in current lane, maintaining speed and overtaking on the clear left side."}

Observation: Sudden barricade or car blocking the road very closely.
{"press": ["space", "d"], "reasoning": "Imminent collision ahead, braking hard and dodging right to avoid impact."}

Observation: The car's hood is pressed against a brick wall, not moving.
{"press": ["s"], "reasoning": "Vehicle is stuck against a wall, reversing to clear the obstacle."}

Observation: The car is stuck behind a brick wall, not moving.
{"press": ["w"], "reasoning": "Vehicle is stuck behind a wall, forwarding ahead to clear the obstacle."}
"""

# ─── HUD OVERLAY ──────────────────────────────────────────
class OverlayHUD:
    def __init__(self, rect, queue, kill_event):
        self.queue      = queue
        self.kill_event = kill_event

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-transparentcolor", "blue")
        self.root.config(bg="blue")
        self.root.attributes("-topmost", True)

        l, t, r, b = rect
        self.root.geometry(f"{r-l}x{b-t}+{l}+{t}")
        self.root.update()

        hwnd = self.root.winfo_id()
        ph   = win32gui.GetParent(hwnd)
        hw   = ph if ph else hwnd
        style = win32gui.GetWindowLong(hw, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hw, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED)

        self.canvas = tk.Canvas(self.root, bg="blue", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.after(10, self._tick)

    def _tick(self):
        if self.kill_event.is_set():
            self.root.destroy()
            return

        self.root.lift()
        self.root.attributes("-topmost", True)

        data = None
        while not self.queue.empty():
            data = self.queue.get_nowait()

        if data:
            c  = self.canvas
            ak = set(data.get("keys", []))
            c.delete("all")

            # Panel
            c.create_rectangle(12, 12, 520, 200,
                               fill="#0d0d0d", outline="#00ff88", width=2, stipple="gray50")

            # WASD keys (kept conceptually for the HUD)
            def draw_key(label, x, y, active, wide=False):
                bw = 60 if wide else 32; bh = 28
                c.create_rectangle(x, y, x+bw, y+bh,
                                   fill="#00ee44" if active else "#2a2a2a",
                                   outline="#555", width=1)
                c.create_text(x+bw//2, y+bh//2, text=label,
                              fill="#000" if active else "#888",
                              font=("Consolas", 9, "bold"))

            draw_key("W",     75,  25, "w"     in ak)
            draw_key("A",     38,  60, "a"     in ak)
            draw_key("S",     75,  60, "s"     in ak)
            draw_key("D",     113, 60, "d"     in ak)
            draw_key("SPACE", 152, 60, "space" in ak, wide=True)

            # Stats
            c.create_text(240, 25, fill="#00ff88", anchor="nw",
                          text=f" {MODEL}", font=("Consolas", 10, "bold"))
            c.create_text(240, 46, fill="#aaffaa", anchor="nw",
                          text=f"{data.get('call_rate',0):.2f} calls/s  |  {data.get('latency_ms',0):.0f}ms",
                          font=("Consolas", 9))
            c.create_text(240, 65, fill="orange", anchor="nw",
                          text=f"[{KILL_KEY.upper()}] to stop",
                          font=("Arial", 9, "italic"))

            # Reasoning
            words, lines, cur = data.get("reasoning","...").split(), [], ""
            for w in words:
                cand = (cur+" "+w).strip()
                if len(cand) > 62: lines.append(cur); cur = w
                else: cur = cand
            if cur: lines.append(cur)
            c.create_text(22, 110, text="\n".join(lines[:3]),
                          fill="white", anchor="nw", font=("Arial", 10))

            # Action badge
            ac = "#ff4444" if "space" in ak else "#ffaa00" if "s" in ak else "#00cc44"
            at = " BRAKE" if "space" in ak else "⬇ REVERSE" if "s" in ak else " FORWARD"
            c.create_rectangle(22, 165, 200, 190, fill=ac, outline="")
            c.create_text(111, 177, text=at, fill="white", font=("Arial", 10, "bold"))

        self.root.after(16, self._tick)

    def start(self):
        self.root.mainloop()

# ─── INFERENCE WORKER ─────────────────────────────────────
def inference_worker(hud_queue, mss_region, game_rect, kill_event):
    if not api_key:
        print("  No API key found. Put your key in .env file")
        kill_event.set()
        return

    client = OpenAI(api_key=api_key)
    print(f"  OpenAI ready ({MODEL})")
    print(f"\n  Starting in 3s — switch to Mafia 3 now! [{KILL_KEY.upper()}] to stop.\n")
    time.sleep(3)

    last_call_time = 0.0
    last_keys      = ["w"]
    last_reasoning = "Starting..."

    # Fixed the mss deprecation warning here
    with mss.MSS() as sct:
        while not kill_event.is_set():
            if keyboard.is_pressed(KILL_KEY):
                print("Kill switch.")
                kill_event.set()
                break

            # Always hold last command
            drive_keys(last_keys)

            now = time.perf_counter()
            if (now - last_call_time) < GPT_INTERVAL_SEC:
                time.sleep(0.005)
                continue

            # Grab frame with mss
            try:
                frame = grab_frame(sct, mss_region)
            except Exception as e:
                print(f"Capture error: {e}")
                time.sleep(0.1)
                continue

            # Crop HUD
            h, w = frame.shape[:2]
            roi = frame[int(h*0.10) : int(h*0.85), :]

            # Encode
            roi_small = cv2.resize(roi, INFER_SIZE)
            _, jpg = cv2.imencode(".jpg", roi_small,
                                  [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            b64 = base64.b64encode(jpg.tobytes()).decode()

            # GPT call
            t0 = time.perf_counter()
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    # max_tokens=80,
                    # temperature=0.1,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": [{
                            "type": "image_url",
                            "image_url": {
                                "url":    f"data:image/jpeg;base64,{b64}",
                                "detail": "low"
                            }
                        }]}
                    ],
                )
                raw = resp.choices[0].message.content.strip().strip("`\n ")
                if raw.startswith("json"): raw = raw[4:].strip()
                parsed        = json.loads(raw)
                last_keys     = parsed.get("press", ["w"])
                last_reasoning = parsed.get("reasoning", "Driving.")
                print(f"[GPT] {last_keys} | {last_reasoning}")

            except json.JSONDecodeError:
                print(f"Bad JSON: {raw[:80]}")
                last_keys = ["w"]
            except Exception as e:
                print(f"GPT error: {e}")
                last_keys = ["w"]

            lat = (time.perf_counter() - t0) * 1000
            latency_log.append(lat)
            call_times.append(time.perf_counter())
            last_call_time = time.perf_counter()

            if ENABLE_VIS:
                hud_queue.put({
                    "keys":       last_keys,
                    "reasoning":  last_reasoning,
                    "call_rate":  get_call_rate(),
                    "latency_ms": get_avg_latency(),
                })

    release_all()
    print("  Stopped cleanly.")

# ─── MAIN ─────────────────────────────────────────────────
def main():
    print("  Searching for Mafia 3 window...")
    gw = find_game_window("mafia")
    if gw:
        hwnd, title = gw
        print(f"   Found: '{title}'")
        raw = win32gui.GetWindowRect(hwnd)
        l, t, r, b = raw
        w, h = r - l, b - t
        if w <= 0 or h <= 0 or l < -500 or t < -500:
            print("   Window coords invalid (minimized?), using fullscreen fallback.")
            l, t, r, b = 0, 0, 1920, 1080
        else:
            l = max(0, l); t = max(0, t)
        game_rect  = (l, t, r, b)
        mss_region = {"left": l, "top": t, "width": r - l, "height": b - t}
    else:
        print("   Not found — fullscreen fallback.")
        game_rect  = (0, 0, 1920, 1080)
        mss_region = GAME_REGION
    print(f"   Capture region: {mss_region}")

    hud_queue  = Queue()
    kill_event = threading.Event()

    worker = threading.Thread(
        target=inference_worker,
        args=(hud_queue, mss_region, game_rect, kill_event),
        daemon=True,
    )
    worker.start()

    if ENABLE_VIS:
        hud = OverlayHUD(game_rect, hud_queue, kill_event)
        try:
            hud.start()
        except KeyboardInterrupt:
            kill_event.set()
    else:
        try:
            while not kill_event.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            kill_event.set()

if __name__ == "__main__":
    main()
