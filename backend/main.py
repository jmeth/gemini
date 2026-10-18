"""FastAPI application — entry point for the SO-101 web control tool."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import backend.config as config
from backend.robot.controller import RobotController
from backend.robot.calibration import CalibrationStateMachine, CalibrationStep
from backend.robot.teleop import TeleopController
from backend.robot.recordings import RecordingController
from backend.arcade.listener import ArcadeListener
from backend.commands import registry as commands_registry
from backend.commands.executor import match_command
from backend.voice.listener import VoiceListener
from backend.kinematics.chain import get_chain
from backend.system.discovery import list_cameras, list_serial_ports
from backend.vision.camera import CameraStream, manager as cameras
from backend.vision.detector import detector
from backend.vision.devices import is_capture_device

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._active.remove(ws)

    def broadcast(self, data: dict) -> None:
        """Fire-and-forget broadcast; dead connections are pruned."""
        dead = []
        text = json.dumps(data)
        for ws in self._active:
            try:
                # schedule coroutine without awaiting — we're in sync context
                ws.state  # cheap attribute access to detect closed socket
                asyncio.ensure_future(ws.send_text(text))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._active.remove(ws)

    async def broadcast_async(self, data: dict) -> None:
        """Async broadcast that waits for all sends to complete."""
        dead = []
        text = json.dumps(data)
        for ws in self._active:
            try:
                await ws.send_text(text)
            except Exception as exc:
                logger.warning("WebSocket send failed: %s", exc)
                dead.append(ws)
        for ws in dead:
            self._active.remove(ws)


manager = ConnectionManager()
# Captured at startup so the camera grab threads can schedule detection
# broadcasts back onto the event loop.
MAIN_LOOP: asyncio.AbstractEventLoop | None = None
robot   = RobotController(broadcast_cb=manager.broadcast)
calibrations: dict[str, CalibrationStateMachine] = {}  # arm_name -> calibration instance
teleop = TeleopController(follower_controller=robot)
recorder = RecordingController(follower_controller=robot, teleop_controller=teleop)
# Let the follower's broadcast carry the leader's positions, so the UI can draw
# both arms from a single frame instead of polling teleop separately.
robot.leader_provider = teleop.snapshot
# Recording / playback state rides along on the same frame.
robot.frame_extras_provider = recorder.snapshot


async def _run(fn):
    """Run a blocking callable on the default executor."""
    return await asyncio.get_event_loop().run_in_executor(None, fn)


async def _handle_voice_transcript(text: str) -> None:
    """
    A transcript from the backend Vosk listener. Same path the browser takes:
    match it against the command registry, and on a hit play the bound take.
    """
    cmds = await _run(commands_registry.list_commands)
    cmd, score, _trigger = match_command(text, cmds)
    if cmd is None:
        voice.status.last_action = f"“{text}” — no match ({score:.2f})"
        return
    if cmd.get("recording_id"):
        voice.status.last_action = f"“{text}” → {cmd['name']} — playing"
        try:
            await recorder.play(cmd["recording_id"], 1.0)
        except RuntimeError as exc:
            voice.status.last_action = f"{cmd['name']} — {exc}"
    else:
        voice.status.last_action = f"“{text}” → {cmd['name']} — no take bound"


async def _handle_arcade_press(button: int) -> None:
    """An arcade button went down: play the take bound to it, if there is one."""
    cmd = await _run(lambda: commands_registry.command_for_button(button))
    if cmd is None:
        arcade_note["last_action"] = f"Button {button + 1} — nothing bound"
        return
    if not cmd.get("recording_id"):
        arcade_note["last_action"] = f"Button {button + 1} → {cmd['name']} — no take bound"
        return
    try:
        await recorder.play(cmd["recording_id"], 1.0)
        arcade_note["last_action"] = f"Button {button + 1} → {cmd['name']} — playing"
    except RuntimeError as exc:
        # Most often "Already playing": a press mid-take is ignored, not queued.
        arcade_note["last_action"] = f"Button {button + 1} → {cmd['name']} — {exc}"


arcade_note: dict[str, str | None] = {"last_action": None}
arcade = ArcadeListener(on_press=_handle_arcade_press)


def _voice_grammar_phrases() -> list[str]:
    """
    Trigger phrases of every enabled command — the vocabulary the backend
    recogniser is pinned to. Empty (→ open vocabulary) when grammar is switched
    off in settings or nothing is bound yet.
    """
    if not config.get().voice_grammar:
        return []
    phrases: list[str] = []
    for cmd in commands_registry.list_commands():
        if cmd.get("enabled", True):
            phrases += cmd.get("triggers", [])
    return phrases


voice = VoiceListener(
    on_transcript=_handle_voice_transcript,
    grammar_provider=_voice_grammar_phrases,
)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    config.load()
    cfg = config.get()
    # Re-arm detection on the configured camera if it was left on. The device
    # only actually runs YOLO once a viewer opens the stream.
    if cfg.detect_enabled:
        _arm_detection()
    if cfg.follower_port:
        try:
            await robot.connect(cfg.follower_port)
        except Exception as exc:
            logger.warning("Follower auto-connect failed: %s", exc)
    # The leader is auto-connected too, otherwise every restart or reload drops
    # it silently and teleop looks broken for no visible reason. It comes up
    # passive (torque off), which is how teleop wants it anyway.
    if cfg.leader_port:
        try:
            await teleop.connect_leader(cfg.leader_port)
        except Exception as exc:
            logger.warning("Leader auto-connect failed: %s", exc)
    # Bring up the backend voice listener if that's the configured source.
    if cfg.voice_source == "backend":
        try:
            await voice.start(cfg.voice_mic)
        except Exception as exc:
            logger.warning("Backend voice auto-start failed: %s", exc)
    arcade.start()
    yield
    arcade.stop()
    cameras.close_all()
    await voice.stop()
    await recorder.cancel_all()
    await teleop.disconnect_leader()
    await robot.disconnect()


app = FastAPI(title="SO-101 Control", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep connection alive; client pings
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# System discovery
# ---------------------------------------------------------------------------

@app.get("/system/ports")
async def get_ports():
    return list_serial_ports()


@app.get("/system/cameras")
async def get_cameras():
    # Opening the picker rescans. Blocking probe, so off the event loop — it
    # shares that loop with the 50 Hz teleop.
    return await asyncio.get_event_loop().run_in_executor(None, lambda: list_cameras(refresh=True))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings")
async def get_settings():
    return config.get().model_dump()


@app.post("/settings")
async def post_settings(body: dict):
    updated = config.update(body)
    # voice_source / voice_mic drive a live subprocess. Apply the same side
    # effects the dedicated /voice endpoints do when they change from here.
    if "voice_source" in body or "voice_mic" in body:
        if updated.voice_source == "browser":
            await voice.stop()
        elif voice.status.running and voice.status.device != updated.voice_mic:
            try:
                await voice.start(updated.voice_mic)
            except RuntimeError as exc:
                logger.warning("Voice restart after settings change failed: %s", exc)
    if "voice_grammar" in body:
        await _run(voice.refresh_grammar)
    # Detection follows the camera and its own tuning, so re-point it if either
    # the target camera or any detect_* field moved.
    if any(k in body for k in (
        "detect_enabled", "detect_camera", "detect_fps", "detect_conf",
        "detect_imgsz", "detect_classes", "camera_overhead",
    )):
        _arm_detection()
    return updated.model_dump()


# ---------------------------------------------------------------------------
# Robot control
# ---------------------------------------------------------------------------

@app.post("/robot/connect")
async def robot_connect():
    cfg = config.get()
    if not cfg.follower_port:
        return JSONResponse({"error": "No follower port configured"}, status_code=400)
    await robot.connect(cfg.follower_port)
    return {"ok": True}


@app.post("/robot/disconnect")
async def robot_disconnect():
    # Broadcast disconnected state with proper joints structure so UI updates
    from backend.motors.tables import MOTOR_NAMES
    default_joint = {"pos": 0, "load": 0, "volt": 0, "temp": 0}
    await manager.broadcast_async({
        "connected": False,
        "t": 0,
        "joints": {name: default_joint for name in MOTOR_NAMES}
    })
    await robot.disconnect()
    return {"ok": True}


@app.post("/robot/estop")
async def robot_estop():
    """Release torque on every joint immediately. Also drops teleop mirroring."""
    if teleop.status.mirroring:
        await teleop.stop_mirroring()
    await recorder.cancel_all()
    await robot.estop()
    return {"ok": True}


@app.post("/robot/torque/all")
async def robot_set_torque_all(body: dict):
    """Re-arm (or release) every joint at once — how you recover from a stop."""
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "Missing enabled"}, status_code=400)
    if not robot.status.connected or robot._bus is None:
        return JSONResponse({"error": "Not connected"}, status_code=400)

    loop = asyncio.get_event_loop()
    if enabled and robot.last_positions:
        # The goal register still holds the pose from before the arm was released
        # (or dropped). Switching torque on cold would snap it back there, so hold
        # where it is now first.
        await robot.set_positions(dict(robot.last_positions))
    await loop.run_in_executor(None, robot._bus.set_torque_all, bool(enabled))
    return {"ok": True, "enabled": bool(enabled)}


@app.get("/robot/diagnostics")
async def robot_diagnostics(arm_name: str = "follower"):
    """
    What each servo will actually accept, next to what our calibration assumes.

    A servo enforces its own angle limits in EPROM. If those are tighter than the
    calibrated travel, the joint silently refuses to reach commanded positions and
    the only symptom is a tracking error that never closes.
    """
    bus = _arm_bus(arm_name)
    if bus is None:
        return JSONResponse(
            {"error": f"{arm_name.capitalize()} arm is not connected"}, status_code=400
        )

    from backend.motors.tables import MOTOR_NAMES, RESOLUTION

    loop = asyncio.get_event_loop()

    def read_limits() -> dict:
        return {
            "min": bus._locked_read("Min_Angle_Limit"),
            "max": bus._locked_read("Max_Angle_Limit"),
            "torque_limit": bus._locked_read("Torque_Limit"),
            "max_torque": bus._locked_read("Max_Torque_Limit"),
            # Whether the joint is actually holding at all. A joint left limp by
            # an abandoned calibration run can never follow anything.
            "torque_on": bus._locked_read("Torque_Enable"),
            "load": bus._locked_read("Present_Load"),
            "temp": bus._locked_read("Present_Temperature"),
        }

    raw = await loop.run_in_executor(None, read_limits)
    cal = CalibrationStateMachine.load_calibration(arm_name)

    def deg(counts: int) -> float:
        return round(counts * 360.0 / RESOLUTION, 1)

    out = []
    for name in MOTOR_NAMES:
        jc = cal.joints.get(name)
        servo_min = raw["min"].get(name)
        servo_max = raw["max"].get(name)
        row = {
            "joint": name,
            "servo_min_deg": deg(servo_min) if servo_min is not None else None,
            "servo_max_deg": deg(servo_max) if servo_max is not None else None,
            "torque_on": bool(raw["torque_on"].get(name, 0)),
            "load_raw": raw["load"].get(name),
            "temp": raw["temp"].get(name),
            "torque_limit": raw["torque_limit"].get(name),
            "max_torque": raw["max_torque"].get(name),
            "calibrated_min": round(jc.min_pos, 1) if jc else None,
            "calibrated_max": round(jc.max_pos, 1) if jc else None,
            "zero_offset": round(jc.zero_offset, 1) if jc else None,
        }
        # Our commands are in calibrated degrees; the servo thinks in raw.
        if jc and servo_min is not None and servo_max is not None:
            row["servo_min_calibrated"] = round(deg(servo_min) + jc.zero_offset, 1)
            row["servo_max_calibrated"] = round(deg(servo_max) + jc.zero_offset, 1)
            row["reachable"] = (
                row["servo_min_calibrated"] <= jc.min_pos + 0.5
                and row["servo_max_calibrated"] >= jc.max_pos - 0.5
            )
        out.append(row)

    return {"arm": arm_name, "joints": out}


@app.get("/rig")
async def rig_status():
    """
    Everything the header needs to answer "is the rig ready?" in one call:
    both arms, the cameras, and which calibrations exist.
    """
    cfg = config.get()
    saved = CalibrationStateMachine.list_calibrations()
    # Camera validity may enumerate devices; keep that off the event loop.
    loop = asyncio.get_event_loop()
    overhead_valid, ee_valid = await loop.run_in_executor(None, lambda: (
        cfg.camera_overhead is None or is_capture_device(cfg.camera_overhead),
        cfg.camera_ee is None or is_capture_device(cfg.camera_ee),
    ))
    return {
        "follower": {
            "connected": robot.status.connected,
            "port": cfg.follower_port,
            "error": robot.status.error,
            "calibrated": "follower" in saved,
        },
        "leader": {
            "connected": teleop.status.leader_connected,
            "port": cfg.leader_port,
            "mirroring": teleop.status.mirroring,
            "clutched": teleop.status.clutched,
            "error": teleop.status.error,
            "calibrated": "leader" in saved,
        },
        "cameras": {
            "overhead": cfg.camera_overhead,
            "ee": cfg.camera_ee,
            "overhead_valid": overhead_valid,
            "ee_valid": ee_valid,
        },
        "calibrations": saved,
    }


@app.post("/robot/joint/{name}")
async def robot_set_joint(name: str, body: dict):
    pos = body.get("pos")
    if pos is None:
        return JSONResponse({"error": "Missing pos"}, status_code=400)
    await robot.set_position(name, float(pos))
    return {"ok": True}


@app.post("/robot/torque/{name}")
async def robot_set_torque(name: str, body: dict):
    enabled = body.get("enabled")
    if enabled is None:
        return JSONResponse({"error": "Missing enabled"}, status_code=400)
    await robot.set_torque(name, bool(enabled))
    return {"ok": True}


@app.get("/robot/status")
async def robot_status():
    return {
        "connected": robot.status.connected,
        "error": robot.status.error,
        # Bus read cost. Teleop shares this port, so a long read is time the
        # 50 Hz loop (20 ms budget per frame) cannot write.
        "poll_ms": round(robot.poll_ms, 1),
        "poll_ms_max": round(robot.poll_ms_max, 1),
    }


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

ARMS = ("follower", "leader")


def _arm_bus(arm_name: str):
    """
    The bus for a named arm. Calibration used to read the follower's bus no
    matter which arm was named, so calibrating the leader silently recorded the
    follower's angles and saved them as the leader's calibration.
    """
    if arm_name == "follower":
        return robot._bus if robot.status.connected else None
    if arm_name == "leader":
        bus = teleop.leader_bus
        return bus if bus and bus.is_connected else None
    return None


@app.get("/robot/arms")
async def robot_arms():
    """The arms that can be calibrated, for the wizard's picker."""
    cfg = config.get()
    saved = CalibrationStateMachine.list_calibrations()
    return [
        {
            "name": "follower",
            "label": "Follower",
            "port": cfg.follower_port,
            "connected": robot.status.connected,
            "calibrated": "follower" in saved,
        },
        {
            "name": "leader",
            "label": "Leader",
            "port": cfg.leader_port,
            "connected": teleop.status.leader_connected,
            "calibrated": "leader" in saved,
        },
    ]


@app.post("/robot/calibrate/start")
async def calibrate_start(arm_name: str = "follower"):
    """Begin calibration. Torque is released on that arm so joints move by hand."""
    if arm_name not in ARMS:
        return JSONResponse({"error": f"Unknown arm '{arm_name}'"}, status_code=400)

    bus = _arm_bus(arm_name)
    if bus is None:
        return JSONResponse(
            {"error": f"{arm_name.capitalize()} arm is not connected"}, status_code=400
        )
    if arm_name == "leader" and teleop.status.mirroring:
        return JSONResponse(
            {"error": "Stop teleop mirroring before calibrating the leader"},
            status_code=400,
        )

    # Release torque on the arm being calibrated — not on whichever arm happens
    # to be the follower.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, bus.set_torque_all, False)

    calibrations[arm_name] = CalibrationStateMachine(arm_name=arm_name)
    return calibrations[arm_name].start()


@app.get("/robot/calibrate/position")
async def calibrate_position(arm_name: str = "follower"):
    """
    Live raw angles for the arm being calibrated. Raw, not calibrated, because
    these are the numbers the wizard is about to record.
    """
    if arm_name not in ARMS:
        return JSONResponse({"error": f"Unknown arm '{arm_name}'"}, status_code=400)

    bus = _arm_bus(arm_name)
    if bus is None:
        return JSONResponse(
            {"error": f"{arm_name.capitalize()} arm is not connected"}, status_code=400
        )

    if arm_name == "follower" and robot.last_raw:
        # Served from the poll cache: a second reader on the same port fights it.
        positions = dict(robot.last_raw)
    else:
        loop = asyncio.get_event_loop()
        states = await loop.run_in_executor(None, bus.read_all)
        positions = {n: st.pos_deg for n, st in states.items()}

    cal = calibrations.get(arm_name)
    return {
        "arm": arm_name,
        "motor": cal.current_motor if cal else None,
        "positions": {n: round(v, 2) for n, v in positions.items()},
    }


@app.post("/robot/calibrate/confirm")
async def calibrate_confirm(arm_name: str = "follower"):
    """Record the current position of the joint being calibrated, then advance."""
    if arm_name not in ARMS:
        return JSONResponse({"error": f"Unknown arm '{arm_name}'"}, status_code=400)

    calibration = calibrations.get(arm_name)
    if calibration is None:
        return JSONResponse(
            {"error": f"No active calibration for '{arm_name}'"}, status_code=400
        )

    motor = calibration.current_motor
    if not motor:
        return JSONResponse({"error": "No active calibration"}, status_code=400)

    bus = _arm_bus(arm_name)
    if bus is None:
        return JSONResponse(
            {"error": f"{arm_name.capitalize()} arm is not connected"}, status_code=400
        )

    # Read from the arm being calibrated.
    loop = asyncio.get_event_loop()
    if arm_name == "follower" and motor in robot.last_raw:
        raw_pos = robot.last_raw[motor]
    else:
        states = await loop.run_in_executor(None, bus.read_all)
        raw_pos = states[motor].pos_deg if motor in states else 0.0

    status = calibration.confirm(raw_pos)

    if status.get("step") == "complete":
        # The follower holds position again; the leader must stay passive so it
        # can still be moved by hand for teleop.
        if arm_name == "follower":
            await loop.run_in_executor(None, bus.set_torque_all, True)
            robot._calibration = CalibrationStateMachine.load_calibration("follower")
            logger.info("Follower calibration reloaded into the controller")

    return status


@app.post("/robot/calibrate/cancel")
async def calibrate_cancel(arm_name: str = "follower"):
    """
    Abandon an in-progress calibration and keep the saved one. Nothing is written
    until the last joint is confirmed, so this only drops the run and restores
    the arm's torque.
    """
    if arm_name not in ARMS:
        return JSONResponse({"error": f"Unknown arm '{arm_name}'"}, status_code=400)

    calibration = calibrations.get(arm_name)
    if calibration is None or calibration.step in (
        CalibrationStep.IDLE, CalibrationStep.COMPLETE
    ):
        return JSONResponse(
            {"error": f"No calibration in progress for '{arm_name}'"}, status_code=400
        )

    del calibrations[arm_name]

    # Start released torque on this arm. The follower must hold position again;
    # the leader stays passive so it can still be moved by hand for teleop.
    bus = _arm_bus(arm_name)
    if arm_name == "follower" and bus is not None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, bus.set_torque_all, True)

    logger.info("Calibration for '%s' cancelled; keeping the saved calibration", arm_name)
    return await calibrate_status(arm_name)


@app.get("/robot/calibrate/status")
async def calibrate_status(arm_name: str = "follower"):
    """Get current calibration state."""
    if arm_name in calibrations:
        return calibrations[arm_name].get_status()
    # Return idle state if no active calibration
    return {
        "active": False,
        "step": "idle",
        "motor": None,
        "progress": [0, 6],
        "instruction": "Click 'Start Calibration' to begin"
    }


@app.get("/robot/calibrate/list")
async def calibrate_list():
    """List all saved calibrations."""
    return {"calibrations": CalibrationStateMachine.list_calibrations()}


@app.get("/robot/calibrate/ranges")
async def calibrate_ranges(arm_name: str = "follower"):
    """Get calibrated min/max ranges for all joints."""
    cal_data = CalibrationStateMachine.load_calibration(arm_name=arm_name)
    return {
        name: {
            "min": cal.min_pos,
            "max": cal.max_pos,
        }
        for name, cal in cal_data.joints.items()
    }


@app.post("/robot/home")
async def robot_home():
    """Move all joints to saved home position."""
    if not robot.status.connected:
        return JSONResponse({"error": "Not connected"}, status_code=400)

    # Load home positions from calibration
    cal_data = CalibrationStateMachine.load_calibration()
    home_positions = cal_data.home_positions

    # Send all positions
    for name, pos in home_positions.items():
        await robot.set_position(name, pos)

    return {"ok": True, "positions": home_positions}


@app.post("/robot/home/set")
async def robot_set_home(arm_name: str = "follower"):
    """Save an arm's current position as its home position."""
    if arm_name not in ARMS:
        return JSONResponse({"error": f"Unknown arm '{arm_name}'"}, status_code=400)

    bus = _arm_bus(arm_name)
    if bus is None:
        return JSONResponse(
            {"error": f"{arm_name.capitalize()} arm is not connected"}, status_code=400
        )

    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, bus.read_positions)

    # A failed bus read comes back empty or short. Saving that would store a
    # bogus home, so insist on a reading for every joint.
    from backend.motors.tables import MOTOR_NAMES
    missing = [n for n in MOTOR_NAMES if n not in raw]
    if missing:
        return JSONResponse(
            {"error": f"No reading from {', '.join(missing)} — home not saved"},
            status_code=502,
        )

    # Calibrated positions: raw angle plus the arm's zero offset.
    cal_data = (
        robot._calibration if arm_name == "follower"
        else CalibrationStateMachine.load_calibration(arm_name)
    )
    current_positions = {}
    for name in MOTOR_NAMES:
        cal = cal_data.joints.get(name)
        current_positions[name] = round(raw[name] + cal.zero_offset if cal else raw[name], 1)

    CalibrationStateMachine.save_home_positions(current_positions, arm_name=arm_name)
    if arm_name == "follower":
        robot._calibration.home_positions = current_positions

    return {"ok": True, "arm": arm_name, "positions": current_positions}


# ---------------------------------------------------------------------------
# Teleoperation
# ---------------------------------------------------------------------------

@app.post("/teleop/connect")
async def teleop_connect():
    """Connect to leader arm on configured port."""
    cfg = config.get()
    if not cfg.leader_port:
        return JSONResponse({"error": "No leader port configured"}, status_code=400)

    await teleop.connect_leader(cfg.leader_port)
    return {"ok": True}


@app.post("/teleop/disconnect")
async def teleop_disconnect():
    """Disconnect from leader arm."""
    await teleop.disconnect_leader()
    return {"ok": True}


@app.post("/teleop/start")
async def teleop_start():
    """Start position mirroring (leader → follower)."""
    try:
        await teleop.start_mirroring()
        return {"ok": True}
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/teleop/stop")
async def teleop_stop():
    """Stop position mirroring."""
    await teleop.stop_mirroring()
    return {"ok": True}


@app.post("/teleop/speed")
async def teleop_set_speed(body: dict):
    """Set speed limit (0.0 to 1.0)."""
    limit = body.get("limit")
    if limit is None:
        return JSONResponse({"error": "Missing limit"}, status_code=400)

    teleop.set_speed_limit(float(limit))
    return {"ok": True, "limit": teleop.status.speed_limit}


@app.post("/teleop/clutch")
async def teleop_clutch(body: dict):
    """
    Hold the follower while the leader is repositioned. Releasing the clutch
    re-zeroes the offsets so the follower resumes from where it is.
    """
    engaged = body.get("engaged")
    if engaged is None:
        return JSONResponse({"error": "Missing engaged"}, status_code=400)

    teleop.set_clutch(bool(engaged))
    return {"ok": True, "clutched": teleop.status.clutched}


@app.post("/teleop/offsets/clear")
async def teleop_clear_offsets():
    """Drop clutch offsets — leader angles map straight through again."""
    teleop.clear_offsets()
    return {"ok": True}


@app.post("/teleop/limits")
async def teleop_set_limits(body: dict):
    """Configure the tracking-error cut-out."""
    teleop.set_limits(
        max_error=body.get("max_error"),
        stop_on_error=body.get("stop_on_error"),
        match_ranges=body.get("match_ranges"),
    )
    return {
        "ok": True,
        "max_error": teleop.status.max_error,
        "stop_on_error": teleop.status.stop_on_error,
        "match_ranges": teleop.status.match_ranges,
    }


@app.get("/teleop/status")
async def teleop_status():
    """Get teleoperation status."""
    return {
        "leader_connected": teleop.status.leader_connected,
        "mirroring": teleop.status.mirroring,
        "speed_limit": teleop.status.speed_limit,
        "clutched": teleop.status.clutched,
        "max_error": teleop.status.max_error,
        "stop_on_error": teleop.status.stop_on_error,
        "match_ranges": teleop.status.match_ranges,
        "hz": round(teleop.telemetry.hz, 1),
        "dropped": teleop.telemetry.dropped,
        "jitter_ms": round(teleop.telemetry.jitter_ms, 1),
        "clamped": teleop.telemetry.clamped,
        "error": teleop.status.error,
    }


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------

@app.get("/kinematics/chain")
async def kinematics_chain():
    """
    The arm's kinematic tree, straight from the URDF: per-joint origin xyz, the
    origin rpy that orients each joint frame, the rotation axis and the limits.
    The 3D view builds its transforms from this so it cannot drift from the
    hardware description.
    """
    return get_chain()


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

BOUNDARY = "frame"


def _resolve_detect_camera() -> int | None:
    """Which camera detection runs on: the explicit choice, else the overhead."""
    cfg = config.get()
    return cfg.detect_camera if cfg.detect_camera is not None else cfg.camera_overhead


def _emit_detections(index: int, objects: list[dict]) -> None:
    """
    Fan detected objects out to every WebSocket client. Called from a camera grab
    thread, so it hops back onto the event loop to send.
    """
    loop = MAIN_LOOP
    if loop is None or loop.is_closed():
        return
    payload = {"type": "detections", "camera": index, "objects": objects, "t": time.time()}
    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(manager.broadcast_async(payload)))


def _arm_detection() -> None:
    """Point the shared detector at the configured camera with current settings."""
    cfg = config.get()
    index = _resolve_detect_camera()
    if not cfg.detect_enabled or index is None:
        cameras.set_detection(None)
        return
    cameras.set_detection(
        index,
        on_detections=_emit_detections,
        enabled=True,
        fps=cfg.detect_fps,
        conf=cfg.detect_conf,
        imgsz=cfg.detect_imgsz,
        classes=cfg.detect_classes,
    )


def _configure(stream: CameraStream) -> CameraStream:
    """Apply the persisted capture tuning to a stream that is not open yet."""
    if not stream.is_open:
        cfg = config.get()
        stream.width = cfg.camera_width
        stream.height = cfg.camera_height
        stream.fps = cfg.camera_fps
        stream.quality = cfg.camera_quality
    # Detection rides on whichever stream is the configured target, whether it is
    # open yet or not; the grab loop only acts on it while a viewer is connected.
    cfg = config.get()
    stream.on_detections = _emit_detections
    if cfg.detect_enabled and stream.index == _resolve_detect_camera():
        stream.apply_detect(
            enabled=True, fps=cfg.detect_fps, conf=cfg.detect_conf,
            imgsz=cfg.detect_imgsz, classes=cfg.detect_classes,
        )
    return stream


@app.get("/camera/{index}/stream")
async def camera_stream(index: int):
    """
    MJPEG stream. Every viewer shares one capture; the frame is encoded once
    and the same bytes go to all of them.
    """
    if not await asyncio.get_event_loop().run_in_executor(None, is_capture_device, index):
        return JSONResponse(
            {"error": f"/dev/video{index} is not a video capture device"},
            status_code=404,
        )

    stream = _configure(cameras.get(index))
    loop = asyncio.get_running_loop()
    await stream.acquire(loop)

    if not stream.is_open:
        stream.release()
        return JSONResponse(
            {"error": stream.error or f"Could not open camera {index}"},
            status_code=503,
        )

    async def frames():
        try:
            async for jpeg in stream.frames():
                yield (
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            # Runs when the browser closes the connection, too.
            stream.release()

    return StreamingResponse(
        frames(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
    )


@app.get("/camera/{index}/snapshot")
async def camera_snapshot(index: int):
    """One JPEG — used for picker previews, and cheap while the device lingers."""
    if not await asyncio.get_event_loop().run_in_executor(None, is_capture_device, index):
        return JSONResponse(
            {"error": f"/dev/video{index} is not a video capture device"},
            status_code=404,
        )

    stream = _configure(cameras.get(index))
    loop = asyncio.get_running_loop()
    await stream.acquire(loop)
    try:
        jpeg = await stream.snapshot_async(loop)
    finally:
        stream.release()

    if not jpeg:
        return JSONResponse(
            {"error": stream.error or f"Camera {index} produced no frame"},
            status_code=503,
        )
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/camera/status")
async def camera_status():
    """What each opened camera is doing, plus the devices actually available."""
    available = await asyncio.get_event_loop().run_in_executor(None, list_cameras)
    return {"streams": cameras.status(), "available": available}


# ---------------------------------------------------------------------------
# Object detection — YOLO26n tracking + bounding boxes on a camera feed
# ---------------------------------------------------------------------------

def _vision_state() -> dict:
    cfg = config.get()
    active = cameras.get_detecting()
    return {
        "enabled": cfg.detect_enabled,
        "camera": _resolve_detect_camera(),
        "fps": cfg.detect_fps,
        "conf": cfg.detect_conf,
        "imgsz": cfg.detect_imgsz,
        "classes": cfg.detect_classes,
        "objects": len(active.last_detections) if active else 0,
        "model": detector.status(),
    }


@app.get("/vision/status")
async def vision_status():
    """Detection settings, the live object count, and the model's load state."""
    return _vision_state()


@app.post("/vision/detect")
async def vision_detect(body: dict):
    """
    Turn detection on or off and tune it. The operator drives this to spend the
    Pi's CPU on YOLO only when a pick-and-place actually needs it.

    Body: {enabled?, camera?, fps?, conf?, imgsz?, classes?}. Persisted, so it
    survives a restart. Enabling kicks off the (one-time) model load in the
    background so the operator gets immediate feedback instead of a stalled frame.
    """
    patch: dict = {}
    for key in ("detect_fps", "detect_conf", "detect_imgsz"):
        short = key.removeprefix("detect_")
        if short in body:
            patch[key] = body[short]
    if "camera" in body:
        patch["detect_camera"] = body["camera"]
    if "classes" in body:
        patch["detect_classes"] = body["classes"]
    if "enabled" in body:
        patch["detect_enabled"] = bool(body["enabled"])
    cfg = config.update(patch)

    if cfg.detect_enabled and _resolve_detect_camera() is None:
        config.update({"detect_enabled": False})
        return JSONResponse(
            {"error": "No detection camera — set one, or configure the overhead camera"},
            status_code=400,
        )

    _arm_detection()

    # Warm the model off the event loop so a slow first-time NCNN export does not
    # block the request or the camera thread's first frame.
    if config.get().detect_enabled and not detector.ready and not detector.loading:
        asyncio.get_event_loop().run_in_executor(None, detector.ensure_loaded)

    return _vision_state()


# ---------------------------------------------------------------------------
# Recordings — trajectory capture and playback
# ---------------------------------------------------------------------------

@app.get("/recordings")
async def recordings_list():
    return RecordingController.list_recordings()


@app.get("/recordings/status")
async def recordings_status():
    s = recorder.status
    return {
        "recording": s.recording,
        "playing": s.playing,
        "name": s.name,
        "frames": s.frames,
        "elapsed_s": s.elapsed_s,
        "play_id": s.play_id,
        "play_progress": s.play_progress,
        "error": s.error,
    }


@app.post("/recordings/start")
async def recordings_start(body: dict):
    # Default is a teleop demonstration: mirroring is started if it isn't already
    # running, so the leader drives the motion being recorded. Pass
    # {"teleop": false} to record a follower walked through by hand instead.
    teleop_demo = bool(body.get("teleop", True))
    try:
        await recorder.start_recording(body.get("name", ""), teleop=teleop_demo)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True}


@app.post("/recordings/stop")
async def recordings_stop():
    saved = await recorder.stop_recording()
    if saved is None:
        return JSONResponse({"error": "Nothing was recorded"}, status_code=400)
    return {"ok": True, "recording": saved}


@app.post("/recordings/stop-play")
async def recordings_stop_play():
    await recorder.stop_play()
    return {"ok": True}


@app.post("/recordings/{rec_id}/play")
async def recordings_play(rec_id: str, body: dict):
    try:
        # Both default on: skip the take's idle lead-in and finish at home, so
        # one take flows into the next. {"trim_start": false} / {"return_home":
        # false} give the raw recording.
        await recorder.play(
            rec_id, body.get("speed", 1.0),
            trim_start=bool(body.get("trim_start", True)),
            return_home=bool(body.get("return_home", True)),
        )
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True}


@app.delete("/recordings/{rec_id}")
async def recordings_delete(rec_id: str):
    if recorder.status.play_id == rec_id:
        await recorder.stop_play()
    if not recorder.delete_recording(rec_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Commands — voice-triggered recording playback
# ---------------------------------------------------------------------------

@app.get("/commands")
async def commands_list():
    return await _run(commands_registry.list_commands)


def _button_or_none(value) -> int | None:
    """An arcade button index from a request body; null clears the binding."""
    if value is None or value == "":
        return None
    return int(value)


@app.get("/arcade/status")
async def arcade_status():
    s = arcade.status
    return {
        "connected": s.connected,
        "device": s.device,
        "error": s.error,
        "last_button": s.last_button,
        "last_press_at": s.last_press_at,
        "press_seq": s.press_seq,
        "last_action": arcade_note["last_action"],
    }


@app.post("/commands")
async def commands_create(body: dict):
    cmd = await _run(lambda: commands_registry.create_command(
        body.get("name", ""), body.get("triggers", []), body.get("recording_id"),
        enabled=bool(body.get("enabled", True)),
        button=_button_or_none(body.get("button")),
    ))
    await _run(voice.refresh_grammar)
    return cmd


@app.put("/commands/{cmd_id}")
async def commands_update(cmd_id: int, body: dict):
    kwargs: dict = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "triggers" in body:
        kwargs["triggers"] = body["triggers"]
    if "recording_id" in body:
        kwargs["recording_id"] = body["recording_id"]
    if "enabled" in body:
        kwargs["enabled"] = bool(body["enabled"])
    if "button" in body:
        kwargs["button"] = _button_or_none(body["button"])
    cmd = await _run(lambda: commands_registry.update_command(cmd_id, **kwargs))
    if cmd is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    await _run(voice.refresh_grammar)
    return cmd


@app.delete("/commands/{cmd_id}")
async def commands_delete(cmd_id: int):
    if not await _run(lambda: commands_registry.delete_command(cmd_id)):
        return JSONResponse({"error": "Not found"}, status_code=404)
    await _run(voice.refresh_grammar)
    return {"ok": True}


@app.post("/commands/match")
async def commands_match(body: dict):
    cmds = await _run(commands_registry.list_commands)
    cmd, score, trigger = match_command(body.get("transcript", ""), cmds)
    if cmd is None:
        return JSONResponse(
            {"error": "No matching command", "score": score}, status_code=404
        )
    return {"command": cmd, "score": score, "matched_trigger": trigger}


# ---------------------------------------------------------------------------
# Voice input — browser (Web Speech API) vs backend (Vosk + local microphone)
# ---------------------------------------------------------------------------

@app.get("/voice/status")
async def voice_status():
    cfg = config.get()
    s = voice.status
    return {
        "source": cfg.voice_source,
        "mic": cfg.voice_mic,
        "grammar_enabled": cfg.voice_grammar,
        "running": s.running,
        "model_loaded": s.model_loaded,
        "device": s.device,
        "grammar": s.grammar,          # phrases the live recogniser is pinned to; null = open vocab
        "last_text": s.last_text,
        "last_action": s.last_action,
        "error": s.error,
    }


@app.get("/voice/mics")
async def voice_mics():
    """ALSA capture devices on this host, for the Pi-microphone picker."""
    return await _run(VoiceListener.list_mics)


@app.get("/voice/mic/test")
async def voice_mic_test(device: str | None = None):
    """
    Capture ~1s from `device` (or the configured mic) and report its level, so
    the Settings panel can show a live/idle dot. If the listener already owns
    that device, its own health stands in — a second capture would just clash.
    """
    dev = device if device is not None else config.get().voice_mic
    s = voice.status
    if s.running and dev == s.device:
        return {"ok": s.error is None, "error": s.error, "note": "listener running"}
    return await _run(lambda: VoiceListener.probe(dev))


@app.post("/voice/source")
async def voice_set_source(body: dict):
    source = body.get("source")
    if source not in ("browser", "backend"):
        return JSONResponse(
            {"error": "source must be 'browser' or 'backend'"}, status_code=400
        )
    patch: dict = {"voice_source": source}
    # A USB mic (e.g. a webcam) is rarely ALSA card 0, so the default device
    # would capture silence. Pick the first real capture device the first time
    # the backend source is chosen; the user can still override it.
    if source == "backend" and not config.get().voice_mic:
        mics = await _run(VoiceListener.list_mics)
        if mics:
            patch["voice_mic"] = mics[0]["device"]
    config.update(patch)
    if source == "browser":
        await voice.stop()
    return {"ok": True, "source": source, "mic": config.get().voice_mic}


@app.post("/voice/mic")
async def voice_set_mic(body: dict):
    device = body.get("device") or None
    config.update({"voice_mic": device})
    # If the listener is live, move it to the new device now.
    if voice.status.running:
        try:
            await voice.start(device)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True, "mic": device}


@app.post("/voice/start")
async def voice_start():
    cfg = config.get()
    try:
        await voice.start(cfg.voice_mic)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"ok": True}


@app.post("/voice/stop")
async def voice_stop():
    await voice.stop()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Serve built frontend (production)
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    # timeout_graceful_shutdown: an MJPEG response never completes on its own,
    # so without a cap uvicorn waits forever for a camera viewer to disconnect
    # and a reload never finishes.
    uvicorn.run(
        "backend.main:app", host="0.0.0.0", port=8000, reload=True,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    run()
