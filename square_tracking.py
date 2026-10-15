"""Track squares that begin blue and become visually identical white squares.

Windows: py -m pip install numpy opencv-python mss scipy pynput
         py blue_square_tracker.py

Select the game board, then release the left mouse button to start.
Press F to freeze and save results, or Esc to stop and save.

IMPORTANT: Visual tracking cannot reliably resolve every full occlusion or
indistinguishable crossing. The script marks possibly ambiguous identities.
"""

import csv
import math
import time
import threading
from pathlib import Path

import cv2
import mss
import numpy as np
from pynput import keyboard, mouse
from scipy.optimize import linear_sum_assignment

# --- Tune these for your game's colors and speed ---
MONITOR = 1                   # See sct.monitors for monitor numbering
TARGET_FPS = 30.0
BLUE_HSV_LOWER = (90, 90, 65)
BLUE_HSV_UPPER = (135, 255, 255)
WHITE_HSV_LOWER = (0, 0, 185)
WHITE_HSV_UPPER = (179, 55, 255)
MIN_SIDE_PX = 9
MAX_SIDE_FRACTION = 0.40      # Ignore large white background regions
MAX_MERGED_SIZE_FACTOR = 1.35
INITIAL_BLUE_TIMEOUT = 6.0   # Time to wait for blue squares after click

OUTPUT_DIR = Path(__file__).resolve().parent

start_event = threading.Event()
stop_event = threading.Event()


def detect_squares(frame_bgr, size_hint=None):
    """Return plausible square centers, dimensions and initial color."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, w = frame_bgr.shape[:2]
    results = []

    for color, lower, upper in (
        ("blue", BLUE_HSV_LOWER, BLUE_HSV_UPPER),
        ("white", WHITE_HSV_LOWER, WHITE_HSV_UPPER),
    ):
        mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            if min(bw, bh) < MIN_SIDE_PX:
                continue
            if max(bw, bh) > MAX_SIDE_FRACTION * min(w, h):
                continue
            if not 0.76 <= bw / bh <= 1.32:
                continue
            if cv2.contourArea(contour) / (bw * bh) < 0.69:
                continue
            if size_hint is not None:
                # Reject large blobs caused by overlapping / merged squares.
                if max(bw, bh) > size_hint * MAX_MERGED_SIZE_FACTOR:
                    continue
                if min(bw, bh) < size_hint * 0.65:
                    continue

            results.append({
                "x": x + bw / 2.0,
                "y": y + bh / 2.0,
                "side": (bw + bh) / 2.0,
                "color": color,
            })

    # Prefer blue when color masks accidentally detect the same square twice.
    results.sort(key=lambda d: d["color"] != "blue")
    unique = []
    for d in results:
        if not any(
            math.dist((d["x"], d["y"]), (u["x"], u["y"]))
            < 0.4 * max(d["side"], u["side"])
            for u in unique
        ):
            unique.append(d)
    return unique


class SquareTrack:
    """Constant-velocity Kalman filter with a permanent original-blue flag."""

    def __init__(self, number, detection):
        self.number = number
        self.was_blue = detection["color"] == "blue"
        self.state = np.array(
            [detection["x"], detection["y"], 0.0, 0.0], dtype=float
        )
        self.covariance = np.diag([16.0, 16.0, 80000.0, 80000.0])
        self.misses = 0
        self.uncertain = False
        self.last_seen = time.perf_counter()
        self.observed = True

    def predict(self, dt):
        f = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=float)
        g = np.array([
            [0.5 * dt * dt, 0],
            [0, 0.5 * dt * dt],
            [dt, 0],
            [0, dt],
        ])
        acceleration_sd = 650.0  # px/s^2; increase for abrupt movement
        q = (acceleration_sd ** 2) * (g @ g.T)
        self.state = f @ self.state
        self.covariance = f @ self.covariance @ f.T + q
        self.observed = False

    def correct(self, detection, now):
        h = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        r = np.eye(2) * 9.0
        z = np.array([detection["x"], detection["y"]])
        residual = z - h @ self.state
        innovation = h @ self.covariance @ h.T + r
        k = self.covariance @ h.T @ np.linalg.inv(innovation)
        self.state += k @ residual
        self.covariance = (np.eye(4) - k @ h) @ self.covariance
        self.misses = 0
        self.last_seen = now
        self.observed = True


def associate_and_update(tracks, detections, dt, square_side, now):
    """Hungarian assignment; coast through occlusions instead of inventing IDs."""
    for t in tracks:
        t.predict(dt)

    if not detections:
        for t in tracks:
            t.misses += 1
            if now - t.last_seen > 0.3:
                t.uncertain = True
        return

    # If a merged white blob lies near several tracks, don't force an ID.
    skipped = set()
    if len(detections) < len(tracks):
        for j, d in enumerate(detections):
            near = [
                t for t in tracks
                if math.dist(t.state[:2], (d["x"], d["y"]))
                < 0.8 * square_side
            ]
            if len(near) >= 2:
                skipped.add(j)
                for t in near:
                    t.uncertain = True

    cost = np.full((len(tracks), len(detections)), 1e6)
    for i, t in enumerate(tracks):
        speed = np.linalg.norm(t.state[2:])
        gate = max(1.3 * square_side, speed * dt * 1.5 + 0.6 * square_side)
        gate += min(t.misses, 10) * 0.16 * square_side
        for j, d in enumerate(detections):
            if j in skipped:
                continue
            distance = math.dist(t.state[:2], (d["x"], d["y"]))
            if distance <= gate:
                cost[i, j] = distance

    rows, cols = linear_sum_assignment(cost)
    matched = set()
    for i, j in zip(rows, cols):
        if cost[i, j] < 1e6:
            tracks[i].correct(detections[j], now)
            matched.add(i)

    for i, t in enumerate(tracks):
        if i not in matched:
            t.misses += 1
            if now - t.last_seen > 0.3:
                t.uncertain = True

    # Warn about near-identical crossing trajectories. Once uncertain, an
    # identity remains flagged, even if later frames look clear.
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            if math.dist(a.state[:2], b.state[:2]) < square_side * 0.55:
                if not (a.observed and b.observed):
                    a.uncertain = True
                    b.uncertain = True


def draw_results(frame, tracks, square_side):
    annotated = frame.copy()
    for t in tracks:
        x, y = map(lambda q: int(round(q)), t.state[:2])
        half = int(square_side / 2)
        color = (255, 100, 0) if t.was_blue else (90, 210, 90)
        cv2.rectangle(annotated, (x - half, y - half),
                      (x + half, y + half), color, 2)
        label = f"BLUE {t.number}" if t.was_blue else f"{t.number}"
        if t.uncertain:
            label += " ?"
        cv2.putText(annotated, label, (x - half, max(12, y - half - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2)
    return annotated


def on_click(x, y, button, pressed):
    if button == mouse.Button.left and not pressed:
        start_event.set()
        return False  # Start only once; automatic movements can't retrigger.


def on_key(key):
    if key in (keyboard.Key.esc,):
        stop_event.set()
        return False
    try:
        if key.char and key.char.lower() == "f":
            stop_event.set()
            return False
    except AttributeError:
        pass


def main():
    print("Select ONLY the game board, press ENTER to confirm.")
    with mss.mss() as sct:
        full_monitor = sct.monitors[MONITOR]
        shot = np.array(sct.grab(full_monitor))
        preview = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        roi = cv2.selectROI("Select board, then ENTER", preview, False, False)
        cv2.destroyAllWindows()
        rx, ry, rw, rh = [int(v) for v in roi]
        if rw == 0 or rh == 0:
            print("No region selected. Exiting.")
            return
        region = {
            "left": full_monitor["left"] + rx,
            "top": full_monitor["top"] + ry,
            "width": rw,
            "height": rh,
        }
        print("Release LEFT mouse button when starting the game.")
        print("Press F to finish & save, or ESC to stop & save.")
        with keyboard.Listener(on_press=on_key), mouse.Listener(on_click=on_click):
            while not start_event.is_set() and not stop_event.is_set():
                stop_event.wait(0.015)
            if stop_event.is_set():
                return

            print("Started. Waiting for visible blue and white squares...")
            begin = time.perf_counter()
            tracks = []
            size_hint = None
            last_frame = None
            frames = []
            last_timestamp = None
            frame_index = 0
            next_report = begin + 1.0
            frame_period = 1.0 / TARGET_FPS

            while not stop_event.is_set():
                tick = time.perf_counter()
                shot = np.asarray(sct.grab(region))
                frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
                now = time.perf_counter()
                last_frame = frame

                if not tracks:
                    dets = detect_squares(frame)
                    nblue = sum(d["color"] == "blue" for d in dets)
                    if nblue and len(dets) >= 2:
                        dets.sort(key=lambda d: (d["y"], d["x"]))
                        tracks = [SquareTrack(i + 1, d) for i, d in enumerate(dets)]
                        size_hint = float(np.median([d["side"] for d in dets]))
                        last_timestamp = now
                        cv2.imwrite(str(OUTPUT_DIR / "squares_initial.png"),
                                    draw_results(frame, tracks, size_hint))
                        print(f"Initialized {len(tracks)} squares; {nblue} were blue.")
                    elif now - begin > INITIAL_BLUE_TIMEOUT:
                        print("No suitable blue/white squares found. Check ROI and HSV thresholds.")
                        break
                else:
                    dt = max(1e-3, min(now - last_timestamp, 0.25))
                    last_timestamp = now
                    dets = detect_squares(frame, size_hint=size_hint)
                    associate_and_update(tracks, dets, dt, size_hint, now)
                    for t in tracks:
                        frames.append([
                            frame_index, round(now - begin, 4), t.number,
                            int(t.was_blue), round(t.state[0], 2),
                            round(t.state[1], 2), int(t.observed),
                            int(t.uncertain), round(now - t.last_seen, 3),
                        ])
                    if now >= next_report:
                        seen = sum(t.observed for t in tracks)
                        flagged = sum(t.uncertain for t in tracks)
                        print(f"Tracking: {seen}/{len(tracks)} detected, "
                              f"{flagged} IDs flagged uncertain")
                        next_report = now + 1.0
                frame_index += 1
                stop_event.wait(max(0.0, frame_period - (time.perf_counter() - tick)))

            if tracks and last_frame is not None:
                result = OUTPUT_DIR / "squares_final.png"
                log = OUTPUT_DIR / "squares_tracking.csv"
                cv2.imwrite(str(result), draw_results(last_frame, tracks, size_hint))
                with open(log, "w", newline="", encoding="utf-8") as file:
                    writer = csv.writer(file)
                    writer.writerow([
                        "frame", "seconds", "id", "originally_blue",
                        "x_in_board", "y_in_board", "observed",
                        "possibly_ambiguous", "seconds_since_seen",
                    ])
                    writer.writerows(frames)
                print("\nFinal positions of originally blue squares:")
                for t in tracks:
                    if t.was_blue:
                        status = "UNCERTAIN" if t.uncertain or not t.observed else "tracked"
                        print(f"  BLUE #{t.number}: x={t.state[0]:.0f}, "
                              f"y={t.state[1]:.0f} (within board) [{status}]")
                print(f"Saved image: {result}")
                print(f"Saved tracking data: {log}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.")
