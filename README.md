# SO-101 Web Control

Web-based monitoring and control tool for the SO-101 robotic arm.

## Setup

### Backend (Python)

```bash
cd so101-web
pip install fastapi "uvicorn[standard]" pyserial opencv-python-headless feetech-servo-sdk pydantic vosk
make voice-model   # ~40 MB Vosk English model, for the Pi-microphone voice path
```

`make install` does both of the above.

### Frontend (Node)

```bash
cd so101-web/frontend
npm install
```

## Running (development)

Two terminals:

```bash
# Terminal 1 — backend
cd so101-web
python3 -m backend.main

# Terminal 2 — frontend (hot-reload)
cd so101-web/frontend
npm run dev
```

Open http://localhost:5173

## Running (production / RPi5)

```bash
cd so101-web/frontend && npm run build
cd ..
python3 -m backend.main
```

Open http://<rpi-ip>:8000

## Usage

The header carries everything you need before a session: status dots for the
follower, the leader, calibration and cameras. Click any dot to fix what it
reports — the follower dot connects the arm, the calibration dot opens the
wizard, the camera dot opens the picker.

- **STOP** sits in the header in every mode. It releases torque on all joints
  immediately and stops teleop. It then latches into a "re-arm" state so torque
  never comes back without you asking.
- **The joint console** on the right is always visible. Each row is one motor:
  torque toggle, live position, a slider whose handle is that position, the
  calibrated end stops, and that joint's load and temperature.
- **Manual** jogs joints directly. **Teleop** pairs each row with the leader arm
  — the hollow ring is the leader, the filled knob is the follower, and the gap
  between them is the tracking error. Hold **Clutch** to reposition the leader
  without moving the follower.
- **Record** takes over the whole stage with three columns — Record, Takes, and
  Commands & voice. Capture a take at 10 Hz: release torque, walk the arm
  through the motion by hand, and Stop to save it under `recordings/`. Play a
  take back on the follower (it must be armed) at 0.25–2×; playback interpolates
  between frames and is clamped to the follower's calibrated travel. The command
  column binds spoken **trigger phrases** to a take. Toggle the mic (Chrome/Edge
  — the Web Speech API runs in the browser) and a matched phrase plays its take;
  matching is fuzzy word-overlap, no ML, and tolerates filler words. Per-joint
  torque and position stay in the rail's joint console.

Calibrate both arms separately before using teleop; they do not share a zero.

## Local state (gitignored)

These are rig-specific and written back by the app itself, so they're not
tracked in git and won't exist right after a clone:

- `settings.json` — ports, camera indices, detection tuning. Regenerated with
  defaults on first run; edit via the UI or `POST /settings`.
- `calibration/*.json` — per-arm end stops from the calibration wizard.
- `so101.db` — voice command → take bindings.
- `recordings/*.json` — captured takes.
- `models/` — Vosk and YOLO26n weights, fetched by `make voice-model` /
  `make vision-model`.

**Cameras.** The picker lists only real video capture devices and shows a live
preview of each, so you can tell which is which before saving. One capture is
shared by everyone watching, and the device is released a few seconds after the
last viewer leaves. Capture defaults to 640x480 at 10 fps, tunable via
`camera_width` / `camera_height` / `camera_fps` / `camera_quality` in
`settings.json`.

## Phase status

- [x] Phase 1 — Monitor dashboard, manual control, settings
- [x] Phase 2 — Calibration UI wizard
- [x] Phase 2.5 — Teleop (leader → follower mimic)
- [x] Phase 3 — Commands, recording, voice
- [~] Phase 4 — Camera streams ✅, object detection (YOLO26n) ✅, workspace calibration ⏳
- [ ] Phase 5 — IK pick-and-place
- [ ] Phase 6 — Web teleoperation
- [ ] Phase 7 — Enhanced 3D viz
