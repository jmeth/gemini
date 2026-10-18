"""
The Directives engine: inputs (button, voice, timer, object, startup) fire rules,
and a rule runs its actions in order (movement, pose, sound, wait).

Everything an input does lands in a ring of events, so the Test bench and the
Activity page can show what the robot heard and why it did or did not act.

Arms. A rule's actions name arms (left / right / both). Only one follower is
wired up today and `side_provider` says which side it stands for; an action that
targets only the other side is skipped, with a note, instead of failing. That
keeps rules written for both arms valid for when the second arm arrives.

Busy policy, per rule, decides what happens when the arm is already in use:
skip (default), queue behind the current rule, or interrupt it. Rules with only
sounds and waits never touch the arm and are never held up by it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime
from typing import Callable

from backend.commands.executor import match_command
from backend.directives import store
from backend.directives.audio import AudioPlayer

logger = logging.getLogger(__name__)

# An object must be seen this many detection frames in a row to "appear", and be
# gone this many to "disappear". Detection runs at a few fps, so a flicker of one
# missed frame does not make a rule fire twice.
APPEAR_FRAMES = 2
DISAPPEAR_FRAMES = 10

STARTUP_DELAY_S = 3.0


class DirectivesEngine:
    def __init__(self, recorder, audio: AudioPlayer, side_provider: Callable[[], str]) -> None:
        self._rec = recorder
        self._audio = audio
        self._side = side_provider

        self.events: deque[dict] = deque(maxlen=400)
        self._seq = 0

        self._rules: list[dict] = []
        self._cooldown: dict[int, float] = {}
        self._timer_marks: dict[tuple[int, int], object] = {}
        self._obj_state: dict[tuple[int, int], dict] = {}

        self._arm_lock = asyncio.Lock()
        self._arm_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._timer_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        self.refresh()
        self._timer_task = self._loop.create_task(self._timer_loop())
        self._loop.create_task(self._fire_startup())

    async def stop(self) -> None:
        if self._timer_task:
            self._timer_task.cancel()
        for t in list(self._tasks):
            t.cancel()
        await self._audio.stop()

    def refresh(self) -> None:
        """Re-read the rules. Call after any change to them."""
        self._rules = store.list_rules()
        live = {r["id"] for r in self._rules}
        self._obj_state = {k: v for k, v in self._obj_state.items() if k[0] in live}

    def voice_phrases(self) -> list[str]:
        """Phrases of every enabled voice trigger — what the recogniser should expect."""
        return [p for r in self._rules if r["enabled"]
                for t in r["triggers"] if t["type"] == "voice" for p in t["phrases"]]

    # ------------------------------------------------------------------ event log

    def log(self, kind: str, text: str, **data) -> None:
        self._seq += 1
        self.events.append({"seq": self._seq, "t": time.time(), "kind": kind, "text": text, **data})

    def events_after(self, seq: int = 0) -> list[dict]:
        return [e for e in self.events if e["seq"] > seq]

    @staticmethod
    def _describe(ev: dict) -> str:
        kind = ev.get("type")
        if kind == "button":
            return f"Button {int(ev['button']) + 1} pressed"
        if kind == "voice":
            return f"Heard “{ev.get('text', '')}”"
        if kind == "object":
            conf = f" ({round(ev['conf'] * 100)}%)" if ev.get("conf") else ""
            return f"{ev.get('label')} {ev.get('event', 'appears')}{conf}"
        if kind == "timer":
            return "Timer fired"
        if kind == "startup":
            return "Startup"
        return str(kind)

    # ------------------------------------------------------------------ firing

    async def fire(self, event: dict, manual: bool = False) -> list[int]:
        """An input happened. Run every rule it triggers; return their ids."""
        try:
            return await self._fire(event, manual)
        except Exception as exc:   # an input must never take its caller down
            logger.exception("Directives fire failed")
            self.log("error", f"Could not process {event.get('type')}: {exc}")
            return []

    async def _fire(self, event: dict, manual: bool) -> list[int]:
        self.log("trigger", self._describe(event) + (" (test)" if manual else ""),
                 event=event, manual=manual)
        enabled = [r for r in self._rules if r["enabled"]]
        matched: list[dict] = []

        if event["type"] == "voice":
            # The best-scoring phrase wins; two similar phrases must not both fire.
            pseudo = [{"id": r["id"], "enabled": True,
                       "triggers": [p for t in r["triggers"] if t["type"] == "voice"
                                    for p in t["phrases"]]} for r in enabled]
            cmd, score, phrase = match_command(event.get("text", ""), [c for c in pseudo if c["triggers"]])
            if cmd is None:
                self.log("skip", f"No rule matched (best {score:.2f})")
            else:
                matched = [r for r in enabled if r["id"] == cmd["id"]]
                self.log("match", f"“{phrase}” → {matched[0]['name']} ({score:.2f})", rule_id=cmd["id"])
        else:
            matched = [r for r in enabled
                       if any(self._trigger_matches(t, i, r, event) for i, t in enumerate(r["triggers"]))]

        for rule in matched:
            self._launch(rule, cause=self._describe(event))
        return [r["id"] for r in matched]

    @staticmethod
    def _trigger_matches(t: dict, index: int, rule: dict, ev: dict) -> bool:
        if t["type"] != ev["type"]:
            return False
        if t["type"] == "button":
            return t["button"] == ev.get("button")
        if t["type"] == "timer":
            return ev.get("rule_id") == rule["id"] and ev.get("index") == index
        if t["type"] == "object":
            if "rule_id" in ev and (ev["rule_id"], ev.get("index")) != (rule["id"], index):
                return False     # a tracked transition belongs to exactly one trigger
            return (t["label"] == str(ev.get("label", "")).lower()
                    and t["event"] == ev.get("event", "appears")
                    and (ev.get("conf") or 1.0) >= t["min_conf"])
        return True              # startup

    def run_now(self, rule_id: int) -> bool:
        """Run a rule on request, ignoring its triggers and cooldown."""
        rule = next((r for r in self._rules if r["id"] == rule_id), None)
        if rule is None:
            return False
        return self._launch(rule, cause="run manually", force=True)

    # ------------------------------------------------------------------ running

    def _launch(self, rule: dict, cause: str, force: bool = False) -> bool:
        name, now = rule["name"], time.monotonic()
        if not force and rule["cooldown_s"] and now - self._cooldown.get(rule["id"], -1e9) < rule["cooldown_s"]:
            self.log("skip", f"“{name}” skipped — cooling down", rule_id=rule["id"])
            return False

        uses_arm = any(a["type"] in ("movement", "pose") for a in rule["actions"])
        if uses_arm:
            busy = (self._arm_lock.locked() or self._rec.status.playing
                    or self._rec.status.recording)
            if busy and rule["busy_policy"] == "skip":
                self.log("skip", f"“{name}” skipped — the arm is busy", rule_id=rule["id"])
                return False
            if busy and rule["busy_policy"] == "interrupt" and self._arm_task:
                self._arm_task.cancel()

        self._cooldown[rule["id"]] = now
        task = asyncio.get_event_loop().create_task(self._run(rule, cause, uses_arm))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run(self, rule: dict, cause: str, uses_arm: bool) -> None:
        name, rid = rule["name"], rule["id"]
        self.log("rule", f"▶ {name}", rule_id=rid, cause=cause)
        try:
            if uses_arm:
                async with self._arm_lock:
                    self._arm_task = asyncio.current_task()
                    if rule["busy_policy"] == "queue":
                        await self._rec.wait_idle()
                    elif rule["busy_policy"] == "interrupt" and self._rec.status.playing:
                        # Something not started by a rule (the UI, say) is moving the
                        # arm. We hold the arm now, so stop it before taking over.
                        await self._rec.stop_play()
                    await self._actions(rule)
            else:
                await self._actions(rule)
            await asyncio.get_event_loop().run_in_executor(None, store.touch_rule, rid)
            self.log("rule", f"✓ {name} finished", rule_id=rid)
        except asyncio.CancelledError:
            self.log("rule", f"■ {name} interrupted", rule_id=rid)
            raise
        except Exception as exc:
            logger.warning("Rule '%s' failed: %s", name, exc)
            self.log("error", f"{name}: {exc}", rule_id=rid)
        finally:
            if self._arm_task is asyncio.current_task():
                self._arm_task = None

    async def _actions(self, rule: dict) -> None:
        for a in rule["actions"]:
            kind = a["type"]
            if kind == "movement":
                await self._movement(rule, a)
            elif kind == "pose":
                await self._pose(rule, a)
            elif kind == "sound":
                await self._sound(rule, a)
            elif kind == "wait":
                self.log("action", f"Wait {a['seconds']:g}s", rule_id=rule["id"])
                await asyncio.sleep(a["seconds"])

    def _targets(self, rule: dict, a: dict, what: str) -> bool:
        """Is the connected arm one this action targets? Logs why not if it isn't."""
        side = self._side()
        if side in a["arms"]:
            others = [s for s in a["arms"] if s != side]
            if others:
                self.log("skip", f"{what}: {', '.join(others)} arm not connected — playing {side} only",
                         rule_id=rule["id"])
            return True
        self.log("skip", f"{what} skipped — it targets {', '.join(a['arms'])}, "
                         f"but the connected arm is {side}", rule_id=rule["id"])
        return False

    async def _movement(self, rule: dict, a: dict) -> None:
        label = f"Movement “{self._take_name(a['id'])}”"
        if not self._targets(rule, a, label):
            return
        self.log("action", f"{label} (×{a['speed']:g})", rule_id=rule["id"])
        await self._rec.play(a["id"], a["speed"])
        await self._rec.wait_idle()

    async def _pose(self, rule: dict, a: dict) -> None:
        loop = asyncio.get_event_loop()
        pose = await loop.run_in_executor(None, store.get_pose, a["id"])
        if pose is None:
            raise RuntimeError("that pose has been deleted")
        label = f"Pose “{pose['name']}”"
        if not self._targets(rule, a, label):
            return
        joints = pose["arms"].get(self._side())
        if not joints:
            self.log("skip", f"{label} skipped — it has no {self._side()} arm values",
                     rule_id=rule["id"])
            return
        self.log("action", f"{label} over {a['duration_s']:g}s", rule_id=rule["id"])
        await self._rec.go_to(joints, a["duration_s"], label=pose["name"])
        await self._rec.wait_idle()

    async def _sound(self, rule: dict, a: dict) -> None:
        loop = asyncio.get_event_loop()
        sound = await loop.run_in_executor(None, store.get_sound, a["id"])
        if sound is None:
            raise RuntimeError("that sound has been deleted")
        self.log("action", f"Sound “{sound['name']}”", rule_id=rule["id"])
        await self._audio.play(store.sound_path(sound), a["volume"], wait=a["wait"])

    def _take_name(self, rec_id: str) -> str:
        try:
            return json.loads(self._rec._path(rec_id).read_text()).get("name", rec_id)
        except Exception:
            return rec_id

    # ------------------------------------------------------------------ timers / startup

    async def _fire_startup(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_S)
        await self.fire({"type": "startup"})

    async def _timer_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now, wall = time.monotonic(), datetime.now()
            for rule in list(self._rules):
                if not rule["enabled"]:
                    continue
                for i, t in enumerate(rule["triggers"]):
                    if t["type"] != "timer":
                        continue
                    key = (rule["id"], i)
                    if t["mode"] == "interval":
                        last = self._timer_marks.setdefault(key, now)
                        if now - last >= t["seconds"]:
                            self._timer_marks[key] = now
                            await self.fire({"type": "timer", "rule_id": rule["id"], "index": i})
                    else:
                        stamp = wall.strftime("%Y-%m-%d %H:%M")
                        if wall.strftime("%H:%M") == t["at"] and self._timer_marks.get(key) != stamp:
                            self._timer_marks[key] = stamp
                            await self.fire({"type": "timer", "rule_id": rule["id"], "index": i})

    # ------------------------------------------------------------------ objects

    def note_detections(self, index: int, objects: list[dict]) -> None:
        """Called from a camera thread with each detection frame."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._on_detections, objects)

    def _on_detections(self, objects: list[dict]) -> None:
        best: dict[str, float] = {}
        for o in objects:
            label = str(o.get("label") or "").lower()
            best[label] = max(best.get(label, 0.0), float(o.get("conf") or 0.0))

        for rule in self._rules:
            if not rule["enabled"]:
                continue
            for i, t in enumerate(rule["triggers"]):
                if t["type"] != "object":
                    continue
                st = self._obj_state.setdefault((rule["id"], i), {"present": False, "on": 0, "off": 0})
                if best.get(t["label"], 0.0) >= t["min_conf"]:
                    st["on"], st["off"] = st["on"] + 1, 0
                else:
                    st["on"], st["off"] = 0, st["off"] + 1

                event = None
                if not st["present"] and st["on"] >= APPEAR_FRAMES:
                    st["present"], event = True, "appears"
                elif st["present"] and st["off"] >= DISAPPEAR_FRAMES:
                    st["present"], event = False, "disappears"
                if event == t["event"]:
                    asyncio.ensure_future(self.fire({
                        "type": "object", "label": t["label"], "event": event,
                        "conf": best.get(t["label"]), "rule_id": rule["id"], "index": i}))
