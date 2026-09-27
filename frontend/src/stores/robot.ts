import { writable, derived } from 'svelte/store'
import { pushDetections, type DetectionFrame } from './detection'

export const MOTOR_NAMES = [
  'shoulder_pan', 'shoulder_lift', 'elbow_flex',
  'wrist_flex', 'wrist_roll', 'gripper',
] as const
export type MotorName = typeof MOTOR_NAMES[number]

export interface JointState {
  pos:  number   // degrees
  load: number   // percent
  volt: number   // volts
  temp: number   // celsius
}

/** Leader-arm view, present on the frame only while the leader is connected. */
export interface LeaderState {
  positions: Partial<Record<MotorName, number>>  // calibrated leader angles
  targets:   Partial<Record<MotorName, number>>  // where the leader points, unlimited
  commands:  Partial<Record<MotorName, number>>  // what the follower was actually told
  clamped:   MotorName[]                         // leader beyond follower's range
  mirroring: boolean
  clutched:  boolean
  hz:        number
  dropped:   number
}

export interface RobotMessage {
  t:         number
  connected: boolean
  joints:    Record<MotorName, JointState>
  leader?:   LeaderState
  recording?: boolean
  playing?:   boolean
  error?:    string
}

const DEFAULT_JOINT: JointState = { pos: 0, load: 0, volt: 0, temp: 0 }

function defaultState(): RobotMessage {
  return {
    t: 0,
    connected: false,
    joints: Object.fromEntries(MOTOR_NAMES.map(n => [n, { ...DEFAULT_JOINT }])) as Record<MotorName, JointState>,
  }
}

export const robotState   = writable<RobotMessage>(defaultState())
export const wsConnected  = writable(false)

// Rolling history for the time plot: last N seconds at 10 Hz
const HISTORY_MAX = 5 * 60 * 10  // 5 min worth of samples

export interface HistoryFrame {
  t: number
  joints: Record<MotorName, number>
  /** Leader-minus-follower per joint, when the leader is connected. */
  error?: Record<MotorName, number>
}

export const jointHistory = writable<HistoryFrame[]>([])

let ws: WebSocket | null = null

export function connectWS() {
  if (ws) return
  // Use current hostname to support remote access (e.g., Pi's IP address)
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = import.meta.env.DEV
    ? `${window.location.hostname}:8000`
    : window.location.host
  const url = `${protocol}//${host}/ws`

  ws = new WebSocket(url)

  ws.onopen = () => {
    wsConnected.set(true)
    // keep-alive ping every 5s
    const ping = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send('ping'), 5000)
    ws!.addEventListener('close', () => clearInterval(ping))
  }

  ws.onclose = () => {
    wsConnected.set(false)
    ws = null
    // reconnect after 2s
    setTimeout(connectWS, 2000)
  }

  ws.onmessage = (ev) => {
    try {
      const raw = JSON.parse(ev.data)
      // Detection frames share this socket but are a different shape; route them
      // to the detection store instead of overwriting the robot frame.
      if (raw?.type === 'detections') {
        pushDetections(raw as DetectionFrame)
        return
      }
      const msg: RobotMessage = raw
      robotState.set(msg)
      if (msg.connected && msg.joints) {
        jointHistory.update(h => {
          const entry: HistoryFrame = {
            t: msg.t,
            joints: Object.fromEntries(
              MOTOR_NAMES.map(n => [n, msg.joints[n]?.pos ?? 0])
            ) as Record<MotorName, number>,
          }
          if (msg.leader?.positions) {
            entry.error = Object.fromEntries(
              MOTOR_NAMES.map(n => {
                const lead = msg.leader!.positions[n]
                const foll = msg.joints[n]?.pos
                return [n, lead != null && foll != null ? lead - foll : 0]
              })
            ) as Record<MotorName, number>
          }
          const next = [...h, entry]
          return next.length > HISTORY_MAX ? next.slice(next.length - HISTORY_MAX) : next
        })
      }
    } catch { /* ignore parse errors */ }
  }
}

export const isConnected = derived(robotState, s => s.connected)

/** Leader block off the live frame, or null when there's no leader to draw. */
export const leaderState = derived(robotState, s => s.leader ?? null)

/** Per-joint leader-minus-follower error, only while the leader is connected. */
export const trackingError = derived(robotState, (s) => {
  if (!s.leader?.positions) return null
  const out = {} as Record<MotorName, number>
  for (const n of MOTOR_NAMES) {
    const lead = s.leader.positions[n]
    const foll = s.joints[n]?.pos
    out[n] = lead != null && foll != null ? lead - foll : 0
  }
  return out
})
