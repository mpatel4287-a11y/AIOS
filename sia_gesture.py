#!/usr/bin/env python3
"""Gesture controller — runs as separate process to avoid segfault."""
import sys, time, subprocess, os
from collections import deque

def dist(a, b):
    return ((a.x-b.x)**2+(a.y-b.y)**2)**0.5

def detect(lm, hist):
    T=4; I=8; IM=5; M=12; MM=9; R=16; RM=13; P=20; PM=17; W=0; TM=2

    def up(tip,mcp): return lm[tip].y < lm[mcp].y

    thu = lm[T].y < lm[TM].y
    iu  = up(I,IM); mu = up(M,MM)
    ru  = up(R,RM); pu = up(P,PM)
    fc  = sum([iu,mu,ru,pu])

    pd  = dist(lm[T], lm[I])
    wx  = lm[W].x
    wy  = lm[W].y

    for k,ml in [("p",10),("wx",15),("wy",15)]:
        if k not in hist: hist[k] = deque(maxlen=ml)
    hist["p"].append(pd)
    hist["wx"].append(wx)
    hist["wy"].append(wy)

    if len(hist["p"]) >= 8:
        if pd<0.04 and hist["p"][0]>0.08: return "pinch_close"
        if pd>0.12 and hist["p"][0]<0.06: return "pinch_open"
    if len(hist["wx"]) >= 12:
        if hist["wx"][0]-wx>0.20: return "swipe_left"
        if wx-hist["wx"][0]>0.20: return "swipe_right"
    if len(hist["wy"]) >= 12:
        if hist["wy"][0]-wy>0.15: return "swipe_up"
        if wy-hist["wy"][0]>0.15: return "swipe_down"

    if fc==4 and not thu:             return "open_palm"
    if fc==0 and not thu:
        if lm[T].y > lm[W].y:        return "thumbs_down"
        return "fist"
    if thu and fc==0:                 return "thumbs_up"
    if iu and mu and not ru and not pu: return "peace"
    if iu and not mu and not ru and not pu: return "point_up"
    if pd<0.05 and mu and ru and pu:  return "ok_sign"
    if thu and pu and not iu and not mu and not ru: return "call_me"
    if iu and mu and ru and not pu:   return "three_fingers"
    return None

def main():
    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import cv2
    except ImportError as e:
        print(f"GESTURE_ERROR:missing library - {e}")
        sys.exit(1)

    model_path = os.path.join(os.path.dirname(__file__), 'hand_landmarker.task')
    if not os.path.exists(model_path):
        print("GESTURE_ERROR:missing hand_landmarker.task file (download with wget)")
        sys.exit(1)

    base_options = python.BaseOptions(model_asset_path=model_path)
    options = vision.HandLandmarkerOptions(base_options=base_options, num_hands=1)
    detector = vision.HandLandmarker.create_from_options(options)

    cap  = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("GESTURE_ERROR:cannot open webcam")
        sys.exit(1)

    print("GESTURE_READY", flush=True)

    hist      = {}
    last_g    = None
    last_t    = 0
    COOLDOWN  = 1.5

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame  = cv2.flip(frame, 1)
        rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        
        result = detector.detect(mp_image)

        if result.hand_landmarks:
            for hlm in result.hand_landmarks:
                g = detect(hlm, hist)
                if g:
                    now = time.time()
                    if g != last_g or now-last_t > COOLDOWN:
                        last_g = g
                        last_t = now
                        hist.clear()
                        print(f"GESTURE:{g}", flush=True)
                
                # Draw landmarks manually for debugging
                for lm in hlm:
                    x, y = int(lm.x * frame.shape[1]), int(lm.y * frame.shape[0])
                    cv2.circle(frame, (x, y), 2, (0, 255, 0), -1)

        cv2.imshow("Sia Gestures  (Q=hide)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            cv2.destroyAllWindows()
        time.sleep(0.03)

    cap.release()

if __name__ == "__main__":
    main()