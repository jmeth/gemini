# SO-101 Web Control Tool — Plan

## Hardware

- **Follower arm**: SO-101 with grabber end effector, 6x STS3215 Feetech servos (IDs 1–6)
  - Joints: shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper
- **Leader arm**: SO-101 with controller handle, same motor bus, passive (torque-disabled) for reading
- **Cameras**: up to 2 USB cameras — overhead (workspace view) and end effector (wrist-mounted)
- **Communication**: USB serial via scservo_sdk, 1,000,000 baud

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn | Async-first, lightweight, runs on RPi5 |
| Motor driver | Adapted from lerobot/motors/feetech/ (no lerobot dep) | Tested against STS3215, avoids PyTorch/HF stack |
| IK solver | ikpy (pure numpy, reads URDF) | placo is harder to install on ARM; ikpy is pip-only |
| URDF | vendor/SO-ARM100/Simulation/SO101/so101_new_calib.urdf | Has correct joints, limits, link geometry |
| Object detection | YOLO26n via NCNN (Ultralytics) | ~67 ms/frame on RPi5 CPU; NMS-free; built-in ByteTrack ids |
| Camera streaming | OpenCV + MJPEG endpoint | Simple, low latency, no WebRTC complexity |
| Voice | Web Speech API (browser-side) → POST transcript to backend | Zero server deps; works in any modern browser |
| Storage | SQLite (aiosqlite) + filesystem for recordings | Lightweight, no server setup |
| Frontend | Svelte + Vite | Compiles to vanilla JS, very lightweight on RPi5 |
| 3D Viz | Three.js (line segments + spheres) | Kinematic stick figure, no mesh loading needed |
| Time plot | Canvas 2D ring buffer | No chart library; push points, redraw at 10Hz |

## Project Structure

```
so101-web/
├── backend/
│   ├── main.py                  # FastAPI app, lifespan, WebSocket hub
│   ├── config.py                # Persisted settings (ports, camera indices) -> settings.json
│   ├── motors/                  # Adapted feetech driver (no lerobot dep)
│   │   ├── __init__.py
│   │   ├── bus.py               # FeetechBus: connect, read, write, sync_read
│   │   └── tables.py            # STS3215 control table, model constants
│   ├── robot/
│   │   ├── __init__.py
│   │   ├── controller.py        # Async 10Hz poll loop, state broadcast, e-stop
│   │   ├── calibration.py       # Calibration state machine (zero -> sweep per joint)
│   │   ├── recordings.py        # Record/replay trajectories (timestamped JSON)
│   │   └── teleop.py            # Leader read loop -> follower write loop (50Hz)
│   ├── kinematics/
│   │   ├── __init__.py
│   │   ├── chain.py             # Parses the URDF into a joint tree, served to the viewer
│   │   └── workspace.py         # Camera pixel -> world XYZ via homography
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── devices.py           # V4L2 QUERYCAP enumeration — capture nodes only
│   │   ├── camera.py            # Shared capture per device, MJPEG fan-out
│   │   └── detector.py          # YOLO26n NCNN tracking on camera frames
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── registry.py          # SQLite CRUD for command->action mappings
│   │   └── executor.py          # Voice match + action dispatch
│   ├── system/
│   │   ├── __init__.py
│   │   └── discovery.py         # list_serial_ports(), list_cameras()
│   └── db.py                    # aiosqlite setup, schema migrations
├── frontend/
│   ├── src/
│   │   ├── App.svelte           # Root layout: header + stage + rail (flex siblings)
│   │   ├── stores/
│   │   │   ├── robot.ts         # WebSocket state: joints, leader block, history
│   │   │   ├── settings.ts      # Port/camera config, persisted to backend
│   │   │   ├── ranges.ts        # Calibrated travel per joint, shared by every widget
│   │   │   ├── rig.ts           # "Is the rig ready?" — both arms, cameras, calibrations
│   │   │   ├── teleop.ts        # Teleop status + actions, one poll shared by two views
│   │   │   ├── ui.ts            # Mode, overlay, rail open/closed
│   │   │   ├── commands.ts      # Command registry (fetch from backend)
│   │   │   └── detection.ts     # Object detection results + workspace state
│   │   └── components/
│   │       ├── Header.svelte        # Rig status, mode tabs, STOP, settings gear
│   │       ├── RigStatus.svelte     # Status dots; each opens the thing that fixes it
│   │       ├── EStop.svelte         # Releases all torque, latches into re-arm
│   │       ├── JointConsole.svelte  # Six fused rows: readout + slider + torque + health
│   │       ├── Dashboard/
│   │       │   ├── Dashboard.svelte      # The stage — tiles chosen by mode
│   │       │   ├── CameraFeed.svelte     # MJPEG img tag, bbox overlay (SVG), placeholder
│   │       │   ├── Arm3D.svelte          # Three.js stick figure + leader ghost overlay
│   │       │   └── TimePlot.svelte       # Canvas 2D — joint history or tracking error
│   │       ├── Modes/
│   │       │   ├── ManualPanel.svelte    # Home controls, relax, calibration prompt
│   │       │   ├── TeleopBar.svelte      # Mirroring strip at the top of the rail
│   │       │   ├── TeleopPanel.svelte    # Clutch, speed, cut-out, "not following"
│   │       │   ├── CalibratePanel.svelte # Step wizard UI (shown in a modal)
│   │       │   ├── PlaceholderPanel.svelte # Honest "not built yet" for Pick
│   │       │   └── PickPanel.svelte      # Detected objects, click-to-pick, workspace cal
│   │       ├── Record/
│   │       │   └── RecordWorkspace.svelte # Full-stage record mode: Record · Takes · Commands+Voice
│   │       └── Settings/
│   │           ├── SettingsModal.svelte  # Gear icon opens this overlay
│   │           └── CalibrateModal.svelte # Wraps the wizard; opened from the cal dot
│   ├── package.json
│   └── vite.config.ts
├── recordings/                  # Stored trajectory JSON files
├── calibration/                 # Per-arm calibration JSON files
├── settings.json                # Persisted runtime config (port, cameras)
├── so101.db                     # SQLite: commands, actions registry
└── pyproject.toml               # Python deps
```

## UI Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ SO-101 │ ●follower ●leader ●calibrated │ ●cameras │ [Manual][Teleop]     │
│                                          [Record][Pick] │ [STOP] ⚙       │
├─────────────────────────────────────────┬────────────────────────────────┤
│ STAGE — mode decides what earns the room│ RAIL — flex sibling, not an     │
│                                         │ overlay; the stage reflows      │
│ ┌─────────────────────┬───────────────┐ │ ┌────────────────────────────┐ │
│ │  Arm — kinematic    │ Overhead cam  │ │ │ (teleop only) Mirroring [●]│ │
│ │  (leader ghost      │ / EE cam      │ │ ├────────────────────────────┤ │
│ │   overlaid in       │ / takes       │ │ │ JOINTS          7.4V 43°C  │ │
│ │   teleop)           │               │ │ │ T shoulder pan      12.3°  │ │
│ └─────────────────────┴───────────────┘ │ │   ├───◇──●────┤   34% 41°  │ │
│                                         │ │ T shoulder lift    -45.1°  │ │
│ ┌─────────────────────────────────────┐ │ │   ├──◇────●───┤   58% 43°  │ │
│ │ Joint history  /  Tracking error    │ │ │ ... six rows, always shown  │ │
│ │              [10s][30s][60s][5m]    │ │ ├────────────────────────────┤ │
│ └─────────────────────────────────────┘ │ │ MODE PANE                  │ │
│ □ Overhead camera not configured  [fix] │ │ home / clutch / limits …   │ │
└─────────────────────────────────────────┴────────────────────────────────┘
```

Three rules the layout follows:

1. **The rail is a flex sibling of the stage**, never an overlay. Opening the
   controls reflows the view instead of covering it.
2. **The joint console is always present**, in every mode. One row per motor
   fuses readout and control: torque toggle, name, live degrees, a slider whose
   handle *is* the live position, and the calibrated end stops. In Manual the
   row carries that joint's load and temperature; in Teleop it carries the
   leader as a second marker, and the gap between the two is the tracking error.
3. **Unbuilt features give their space back.** A camera with no index selected
   collapses to a one-line prompt rather than holding a third of the window.

Safety controls live outside the modes: **STOP** is in the header in every mode
and latches into a re-arm state, and the rig-status cluster answers "is the rig
ready?" in one place, each dot opening the thing that fixes it.

## Modes

| Mode | Rail contents (below the always-present joint console) |
|---|---|
| Manual | Go home / Set home / Relax, plus a prompt to calibrate when ranges are nominal |
| Teleop | Mirroring strip above the console; clutch, re-zero, speed limit, error cut-out, "not following" list |
| Record | Recorder (name + capture, 10 Hz) and the takes list; below it the voice-command registry with a **Voice input** selector — browser mic (Web Speech API) or a local ALSA microphone on the Pi (Vosk, offline). Recording and playback state ride the WS frame |
| Pick | Placeholder — Phase 4-5 |
| Calibrate | Not a mode. A wizard modal opened from the calibration status dot |
| Settings (gear) | Motor port, leader port, camera indices |

## API Surface

### WebSocket
`ws://host/ws` — server pushes JSON at 10Hz:
```json
{
  "t": 1234567890.123,
  "joints": {
    "shoulder_pan":  { "pos": 12.3, "load": 34, "temp": 42, "voltage": 7.4 },
    "shoulder_lift": { "pos": -45.1, "load": 12, "temp": 41, "voltage": 7.4 },
    "elbow_flex":    { "pos": 67.2, "load": 28, "temp": 43, "voltage": 7.4 },
    "wrist_flex":    { "pos": 0.0,  "load": 5,  "temp": 40, "voltage": 7.4 },
    "wrist_roll":    { "pos": 0.0,  "load": 5,  "temp": 40, "voltage": 7.4 },
    "gripper":       { "pos": 80.0, "load": 10, "temp": 41, "voltage": 7.4 }
  },
  "mode": "monitor",
  "teleop_active": false,
  "recording": false
}
```

While the leader arm is connected, the same frame carries a `leader` block, so
the UI can draw both arms without a second poll. It is absent otherwise:
```json
{
  "leader": {
    "positions": { "shoulder_pan": 13.1, "...": 0 },   // calibrated leader angles
    "targets":   { "shoulder_pan": 12.4, "...": 0 },   // what the follower was asked for
    "clamped":   ["wrist_flex"],                        // leader beyond follower's range
    "mirroring": true, "clutched": false,
    "hz": 49.6, "dropped": 0
  }
}
```

### REST
```
GET  /system/ports             -> list of serial port strings
GET  /system/cameras           -> list of {index, label} for available USB cameras
GET  /kinematics/chain         -> URDF joint tree: origin xyz + rpy, axis, limits

GET  /settings                 -> current persisted settings
POST /settings                 -> update settings (port, camera indices, etc.)

POST /robot/connect            -> connect follower arm on configured port
POST /robot/disconnect
POST /robot/estop              -> disable all torque immediately (also stops teleop)
POST /robot/torque/all         -> enable/disable every joint at once {enabled: bool}
GET  /rig                      -> one-call readiness: both arms, cameras, calibrations
POST /robot/joint/{name}       -> set position for one joint {pos: float}
POST /robot/torque/{name}      -> enable/disable torque {enabled: bool}

GET  /robot/arms               -> arms that can be calibrated: port, connected, calibrated
POST /robot/calibrate/start    -> begin calibration on ?arm_name= (releases that arm's torque)
GET  /robot/calibrate/position -> live RAW angles from the arm being calibrated
POST /robot/calibrate/confirm  -> record the selected arm's current position, advance

POST /teleop/connect           -> connect leader arm on configured leader port
POST /teleop/start             -> begin leader->follower mimic loop
POST /teleop/stop
POST /teleop/clutch            -> {engaged: bool} hold the follower / re-engage with offsets
POST /teleop/offsets/clear     -> drop clutch offsets, map leader angles straight through
POST /teleop/limits            -> {max_error: float, stop_on_error: bool} tracking cut-out

GET  /recordings               -> list saved recordings
POST /recordings/start         -> begin recording (name in body)
POST /recordings/stop          -> stop and save recording
DELETE /recordings/{id}
POST /recordings/{id}/play     -> replay a recording
POST /recordings/stop-play

GET  /commands                 -> list commands from registry
POST /commands                 -> create command {name, triggers[], recording_id}
PUT  /commands/{id}
DELETE /commands/{id}
POST /commands/match           -> {transcript: str} -> matched command or 404

GET  /voice/status             -> voice source + backend-listener state
GET  /voice/mics               -> ALSA capture devices on the host
POST /voice/source             -> {source: "browser"|"backend"}
POST /voice/mic                -> {device: str|null} ALSA device for the Pi mic
POST /voice/start              -> start the backend Vosk listener
POST /voice/stop               -> stop it

GET  /camera/{index}/stream    -> MJPEG stream (multipart/x-mixed-replace)
GET  /camera/{index}/snapshot  -> single JPEG frame
GET  /camera/status            -> open streams + available capture devices

GET  /vision/status            -> detector settings, live object count, model load state
POST /vision/detect            -> {enabled?, camera?, fps?, conf?, imgsz?, classes?} toggle/tune detection
                                  (also broadcasts {type:"detections", camera, objects[], t} on /ws)

GET  /pick/calibration         -> touch points, fit quality, live gripper tip
POST /pick/calibration/point   -> {pixel, label?} or {track_id?} -> record one touch (tip xyz via FK)
DELETE /pick/calibration/point/{i}, POST /pick/calibration/clear
GET  /pick/objects             -> detections with xyz in the arm base frame (?height= lifts off the table)
POST /pick                     -> {label: str} or {px, py} -> execute IK pick (not built)
```

## Recording File Format

```json
{
  "id": "grab_red_block_20260822_103000",
  "name": "grab red block",
  "created_at": "2026-08-22T10:30:00Z",
  "duration_s": 4.2,
  "hz": 10,
  "frames": [
    { "t": 0.0, "shoulder_pan": 12.3, "shoulder_lift": -45.1, "elbow_flex": 67.2,
      "wrist_flex": 0.0, "wrist_roll": 0.0, "gripper": 80.0 },
    ...
  ]
}
```

## Build Phases

### Phase 1 — Foundation & Manual Control ✅ COMPLETE
- ✅ Python backend: FastAPI, WebSocket hub, async 10Hz motor poll loop
- ✅ Motor driver: adapted from lerobot feetech code, no lerobot dependency
- ✅ System discovery: serial ports, USB cameras
- ✅ Settings: port/camera config, persist to settings.json
- ✅ Svelte frontend: responsive layout with collapsible drawer
- ✅ Header: connection status, mode selector, connect/disconnect buttons, settings
- ✅ Dashboard: camera placeholders, 3D arm view, motor positions, time plot
- ✅ Manual mode panel: position sliders with real-time control, torque toggles
- ✅ Settings modal: port + camera selection
- ✅ WebSocket fixes: proper async broadcast, disconnect state updates
- ✅ Torque control: enable on connect, disable on disconnect
- ✅ Home position: customizable default position

### Phase 2 — Calibration UI ✅ COMPLETE
- ✅ Wizard is arm-aware end to end: the arm is picked from a list of configured
     arms, and every read, torque release and re-arm addresses that arm's bus.
     Previously `confirm` always read the follower's bus, so calibrating the
     leader recorded the follower's angles and saved them as the leader's file.
- ✅ Leader stays passive after calibrating; only the follower re-arms
- ✅ Saving a follower calibration reloads it into the live controller
- ✅ Backend calibration state machine (min → max → calculate zero as midpoint)
- ✅ Calibration wizard panel: step instructions, progress indicator, confirm button
- ✅ Saves calibration to `calibration/follower_arm.json`
- ✅ Calibration applied to motor reads/writes (zero offset + range limits)
- ✅ Custom home position: set and save current position as home
- ✅ Slider ranges updated from calibrated min/max values

### Phase 2.5 — Basic Teleop (leader -> follower mimic) ✅ COMPLETE
- ✅ Leader arm motor bus (second serial port, torque-disabled, read-only)
- ✅ 50Hz teleop loop: read leader positions → write to follower
- ✅ Teleop panel: leader connect, mimic toggle, speed limit
- ✅ Speed limiting for safe motion (configurable 0-100%)
- ✅ Real-time mirroring with position tracking

### Phase 3 — Commands + Recording + Voice ✅ COMPLETE
- ✅ Trajectory recording: 10 Hz capture sampled from the follower poll cache, so
     a demo adds no bus traffic. Torque is left to the operator — a "Release
     torque to demo by hand" action is one click in the panel. Saved to
     `recordings/<slug>_<timestamp>.json` in the planned frame format.
- ✅ Playback: 50 Hz loop, linear interpolation between frames, sync-write of the
     whole arm each step, clamped to the follower's calibrated travel so a
     recording made under a different calibration can't drive a joint into its
     stop. Speed 0.1–4× (0.25–2× in the UI). Requires the follower already armed.
- ✅ Only one record loop and one playback loop at a time; each refuses to start
     while the other runs. E-STOP and shutdown abort playback and finalize any
     in-progress recording.
- ✅ Recording / playing state is added to the follower's 10 Hz WS frame via a
     `frame_extras_provider` hook; `/recordings/status` carries the finer detail
     (elapsed, frame count, playback progress) on a 500 ms poll.
- ✅ Command registry in SQLite (`so101.db`, stdlib `sqlite3` behind a lock):
     name, trigger phrases (JSON), optional bound recording. REST CRUD +
     `POST /commands/match`.
- ✅ Voice match: `difflib` only, no ML. Score = max(trigger-word coverage,
     blend of coverage and whole-phrase ratio); fires at ≥ 0.6. Tolerates filler
     words and small mishearings, 404 on no match.
- ✅ Web Speech API mic runs entirely in the browser (`stores/speech.ts`),
     continuous with auto-restart; a final transcript POSTs to `/commands/match`
     and, on a hit with a bound take, POSTs the play. Chrome/Edge only — the
     panel says so and stays usable without it.
- ✅ Record mode UI: `Record/RecordWorkspace.svelte` takes over the full stage
     (no 3D arm / time plot) with three columns — **Record** (name, big
     Record/Stop, release-torque, live elapsed/frames, playback speed +
     transport, arm-ready line), **Takes** (roomy list, per-row play +
     delete-confirm, inline progress on the playing take), **Commands & voice**
     (mic toggle, heard/last-action readout, command list with inline edit,
     add/edit form binding a take). Per-joint arm control still lives in the
     rail's JointConsole. (Superseded the earlier rail-only RecordPanel +
     CommandsPanel, 2026-08-29.)

### Phase 4 — Camera + Object Detection 🔧 STREAMING + DETECTION DONE
- ✅ MJPEG stream endpoints for up to 2 cameras
- ✅ One capture per device shared by every viewer; frame encoded once
- ✅ Lazy open, released a few seconds after the last viewer leaves
- ✅ V4L2-aware enumeration — capture nodes only, labelled by device name
- ✅ Live previews in the camera picker; stale indices flagged in the rig status
- ✅ YOLO26n NCNN detector on backend — operator-toggled, slow refresh, lazy model load
- ✅ Boxes + track ids drawn into the feed server-side; object data broadcast on /ws for later click-to-pick
- ✅ Table calibration (replaces the planned "click 4 known points" homography). The arm is
     bolted to the side of a box at ~45 degrees, so the table is not the arm's XY plane.
     Instead the table plane is measured in the arm's own frame: touch an object's centre with
     the gripper tip, record its pixel next to the tip's FK position, repeat 8+ times. A plane
     fitted to the tips is the table; a homography maps pixel -> position on that plane. Saved
     to calibration/table_map.json. (backend/kinematics/fk.py, backend/vision/table_map.py,
     Pick panel.) In simulation with a table tilted 45 degrees: ~1-3 mm with 8-12 points,
     versus ~46 mm mean error if the table is assumed flat. Not yet validated on the real rig.
- ✅ The object's pixel is locked BEFORE the arm moves in — the arm hides it from YOLO by the
     time the tip arrives.
- ✅ Camera feed reattaches after a backend restart (it used to freeze until a hard refresh).
- ⏸ PAUSED — colored-dot calibration targets. Stick 8-12 matte, saturated dots (~15-20 mm,
     a colour absent from the table and arm) across the pick area instead of using scissors:
       * a dot has an unambiguous centre, so sub-pixel detection and no "where is the centre of
         a pair of scissors" ambiguity between the box and the touch point (likely the largest
         calibration error source with object boxes);
       * scan every dot once with the arm out of the way, then touch them one by one — no
         per-touch lock step, and the arm covering a dot no longer matters;
       * the dots stay put, so recalibrating after a camera knock is a minute's work;
       * does not depend on the COCO model.
     Sketch: "Find dots" in the Pick panel (HSV threshold, keep round blobs of the right area,
     centroids) -> list of dots -> pick one, guide the tip onto it, Record touch, mark done.
     The same colour detector would also find coloured blocks for picking, which YOLO's 80 COCO
     classes cannot. Open questions: dot colour/size, replace the object flow or sit beside it.

### Phase 5 — IK Pick-and-Place ⏳ PLANNED
- ✅ FK from the URDF (backend/kinematics/fk.py), same convention as the 3D viewer
- ikpy chain from SO-101 URDF (so101_new_calib.urdf) for IK
- Grasp orientation: a bounding box gives no angle, so scissors need a segmentation mask or
  similar; the table normal from the calibration gives the approach direction
- IK pick: world XYZ -> joint angles -> pre-approach / approach / grasp / lift
- Click-or-voice pick dispatch from Pick panel

### Phase 6 — Web Teleoperation (later)
- Virtual joystick / sliders in browser -> position commands
- Leader arm streaming over WebSocket from remote machine
- Safety: joint limit enforcement, speed clamping

### Phase 7 — 3D Viz Enhancement (later)
- ✅ FK now built from the vendored URDF rather than hand-copied constants. The
     old model kept each joint's translation but discarded its `origin rpy` —
     four of those are +/-90 degree frame rotations — and guessed the axes as
     Z/Y/Y/Y/X/Y in one shared frame when the URDF rotates every joint about its
     own local Z. It also computed in URDF Z-up while the grid was three.js
     Y-up. Combined error at the tool tip was ~287 mm.
- ✅ Camera orbits under the pointer instead of on a timer, so the render can be
     compared against the real arm
- Refine Three.js kinematic chain: joint axis indicators, workspace sphere
- Overlay detected object positions in 3D
- End-effector trajectory trail

### Directives — rules engine 🔧 FIRST SLICE DONE
The Directives tab (the Wall-E build's "directives") is where behaviour is authored. The
three things the UI is for: visualise the rig (Dashboard), set it up (calibrate, ports,
models, jog joints), and automate it (this tab). Built beside the existing tabs so nothing
was removed; Record stays until Movements matches it.

Sub-pages: **Movements** (record/replay/rename takes, tagged with the arm they were recorded
on), **Poses** (named joint positions per arm), **Sounds** (upload, preview, play on the
robot), **Rules**, **Test bench** (live inputs + fire any input by hand), **Activity**
(what fired, what was skipped and why).

Rules: `WHEN any trigger  THEN actions in order`.
- Triggers: voice phrase, arcade button, timer (every N s / daily at HH:MM), object seen
  (appears/disappears, label + min confidence, debounced over several frames), startup.
- Actions: play movement, go to pose, play sound (optionally wait), wait.
- Each movement/pose action names arms (left/right/both). Per rule: busy policy
  (skip / queue / interrupt) and a cooldown. Sound-only rules never wait on the arm.
- Voice: the single best-scoring rule fires (two similar phrases cannot both run), and rule
  phrases join the recogniser's grammar. Browser speech also posts to `/directives/voice`.
- Existing voice/button commands keep working; "Import existing commands" copies them into
  rules and switches the old ones off so nothing fires twice.

Decisions: the tab is named **Directives**; sounds play on the **robot's host** (afplay on
macOS, ffplay/aplay/mpg123/paplay elsewhere) so triggers work with no browser open; and the
first slice **models both arms but drives one** — `arm_side` in settings says which of
left/right the connected follower stands for, and an action that targets only the other
side is skipped with a note in Activity. Rules written for both arms stay valid when the
second arm arrives.

Backend: `backend/directives/` (`store.py` SQLite + validation, `engine.py`, `audio.py`),
endpoints `/poses`, `/sounds`, `/rules`, `/directives/*`, `PUT /recordings/{id}` (rename).
Sounds live in `sounds/` (gitignored). Tested with fake arms: busy policies, cooldown, voice
matching, object debounce, timers, arm skipping, plus an HTTP pass against temp storage.

Not built yet / next:
- **Second arm in the backend.** `robot` and `teleop` are single global controllers with one
  port setting each. Real left+right needs a controller, port and calibration per arm, and
  takes/poses/3D view keyed by side. Until then the other side is modelled, not driven.
- Reusable **sequences** (a named action list rules can call), **state/modes** ("asleep")
  that gate rules, import/export of rule sets, speak-text and LED actions.
- Activity is an in-memory ring (400 events), cleared on restart.
- Fold Manual / Pick calibration / Settings into a **Setup** area and make Dashboard the
  visualisation home, once Directives has settled.

## Current Status Summary

**Completed:**
- ✅ Full web UI with responsive layout (desktop + tablet)
- ✅ Real-time motor control via the joint console
- ✅ WebSocket-based state updates (10Hz), leader block included in teleop
- ✅ Motor calibration system with midpoint zero calculation
- ✅ Calibration files saved under a legacy name are still found and used
     (a rename had orphaned them, so the arm silently ran on raw motor angles)
- ✅ Custom home position
- ✅ Connect/disconnect with torque control
- ✅ Settings persistence
- ✅ E-STOP wired to the UI, present in every mode, with an explicit re-arm
- ✅ Leader/follower tracking: paired markers per joint, clamp detection,
     error plot, clutch, and a tracking-error cut-out

**Layout rebuild (2026-08-25):**
- Rail became a flex sibling of the stage — controls no longer cover the view
- Mode picker modal replaced by header tabs
- MotorPositions + MonitorPanel fused into JointConsole, sharing one set of
  calibrated ranges (they previously disagreed: ±180° hardcoded vs. calibrated)
- Calibrate demoted from an operating mode to a setup wizard modal
- Rig-status cluster added; leader connect moved out of the Teleop panel

**Phase 3 (2026-08-29):**
- Trajectory recording + interpolated playback (`backend/robot/recordings.py`)
- SQLite command registry + `difflib` voice matcher (`backend/commands/`)
- Browser Web Speech API mic; Record-mode panels for takes and commands

**In Progress:**
- Object detection and workspace calibration (rest of Phase 4)

**Camera streaming (2026-08-25):**
- One `cv2.VideoCapture` per device regardless of viewer count — V4L2 will not
  open a device twice, so a second browser tab must not mean a second capture
- The JPEG is encoded once per frame and the same bytes go to every viewer
- Devices open lazily on the first viewer and are released `LINGER_S` after the
  last one leaves, so enumeration keeps working without reopening on every
  mode switch
- Enumeration asks V4L2 what each node is. A UVC webcam publishes a metadata
  node beside its capture node (a C920 gives video0 for video, video1 for
  metadata) and a Pi publishes a dozen codec/ISP nodes; all open cleanly and
  none ever yield a frame, which is how a metadata node got saved as a camera
- Camera opens and snapshot waits run on their own small thread pool, never the
  default executor the motor bus uses

**File Structure:**
```
backend/
  main.py                # FastAPI app, WebSocket, all endpoints, /rig
  config.py              # Settings persistence
  motors/
    bus.py               # Feetech motor driver
    tables.py            # Motor constants
  robot/
    controller.py        # Motor poll loop, position control, leader passthrough
    calibration.py       # Calibration state machine
    teleop.py            # 50Hz mimic loop, clamping, clutch, error cut-out
    recordings.py        # 10Hz trajectory capture, 50Hz interpolated playback
  commands/
    registry.py          # SQLite CRUD for command -> take mappings (so101.db)
    executor.py          # difflib voice match, no ML
  system/
    discovery.py         # Port/camera discovery

frontend/src/
  App.svelte             # Header + stage + rail
  stores/                # robot, settings, ranges, rig, teleop, ui,
                         #   recordings, commands, speech
  components/
    Header.svelte        # Rig status, mode tabs, STOP, gear
    RigStatus.svelte     # Readiness dots with per-dot fixes
    EStop.svelte         # Torque release + re-arm latch
    JointConsole.svelte  # The always-present six-row console
    Dashboard/           # Stage tiles: Arm3D, CameraFeed, TimePlot
    Modes/               # ManualPanel, TeleopBar, TeleopPanel, CalibratePanel,
                         #   PlaceholderPanel
    Record/              # RecordWorkspace — full-stage record/replay/commands
    Settings/            # SettingsModal, CalibrateModal

calibration/
  calibration_follower.json   # Saved calibration + home position
  calibration_leader.json     # Required for teleop
settings.json            # Port/camera settings
```

## Key Design Decisions

1. **No lerobot dependency** — copy/adapt only the feetech motor bus (~300 lines). Avoids
   PyTorch, HuggingFace Hub, and ML deps. Critical for RPi5 startup time and memory.

2. **ikpy over placo** — placo needs native compilation, tricky on ARM. ikpy is pure
   Python/numpy, reads the existing URDF directly.

3. **NCNN for YOLO26n** — NCNN is the fastest CPU backend on the Pi 5 (~67 ms/frame),
   and YOLO26 is NMS-free with a built-in ByteTrack tracker, so `model.track()` gives
   persistent ids for free. Ultralytics/torch is heavy but opt-in (`make install-vision`)
   and torch is only used to export the NCNN weights once; inference runs through ncnn.
   Detection is operator-toggled and refreshes slowly (a few fps) — plenty for pick mode
   and it leaves the arm's poll loop its CPU.

4. **Web Speech API client-side** — voice runs in user's browser, zero server cost.
   If always-on headless listening is needed later, add Whisper as optional backend service.

5. **MJPEG over WebRTC** — simpler, no STUN/TURN, works over local network, acceptable
   latency for monitoring and pick mode.

6. **Canvas 2D for time plot** — ring buffer + redraw at 10Hz, no chart library dependency.
   Svelte compiled output stays small and fast on RPi5.

7. **Target OS: macOS + RPi5** — serial ports: /dev/tty.usbmodem* (mac), /dev/ttyACM* (linux).
   Camera index probing works cross-platform via OpenCV.
