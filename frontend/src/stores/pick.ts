import { writable } from 'svelte/store'
import { getJson } from './api'

/** One recorded touch: where the camera saw the object, where the gripper tip was. */
export interface PickPoint {
  pixel: [number, number]
  xyz:   [number, number, number]   // metres, arm base frame
  label: string | null
}

export interface TableFit {
  rms_in_plane_m:  number
  max_in_plane_m:  number
  rms_off_plane_m: number
  n_points:        number
  normal:          [number, number, number]
  warnings:        string[]
}

/** Mirrors GET /pick/calibration. */
export interface PickCalibration {
  camera:      number | null
  points:      PickPoint[]
  solved:      boolean
  table:       TableFit | null
  tool:        [number, number, number] | null   // live gripper tip, metres
  needed:      number
  recommended: number
  note:        string | null
}

/** A detection with its position in the arm's base frame (GET /pick/objects). */
export interface PickObject {
  id:    number | null
  label: string
  conf:  number | null
  pixel: [number, number]
  xyz:   [number, number, number]
}

export const pickCal     = writable<PickCalibration | null>(null)
export const pickObjects = writable<PickObject[]>([])
/** Last action failure, shown inline. Cleared by the next success. */
export const pickNote    = writable<string | null>(null)

export async function loadPickCal(): Promise<void> {
  const d = await getJson<PickCalibration>('/pick/calibration')
  if (d) pickCal.set(d)
}

/** 409 just means "not calibrated yet", so it is not logged like a real failure. */
export async function loadPickObjects(): Promise<void> {
  try {
    const res = await fetch('/pick/objects')
    if (!res.ok) { pickObjects.set([]); return }
    const d = await res.json()
    pickObjects.set(d.objects ?? [])
  } catch {
    pickObjects.set([])
  }
}

async function send(url: string, method: string, body?: unknown): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      pickNote.set((data as any).error ?? `Request failed (${res.status})`)
      return false
    }
    pickCal.set(data as PickCalibration)
    pickNote.set(null)
    return true
  } catch {
    pickNote.set('Could not reach the backend')
    return false
  }
}

/** An object whose pixel was captured while it was still visible. */
export interface LockedTarget {
  pixel: [number, number]
  label: string
  id:    number | null
}

/**
 * Record a touch at a pixel captured earlier. The arm hides the object from the
 * detector by the time the tip reaches it, so the pixel is locked beforehand.
 */
export const recordTouch = (t: LockedTarget) =>
  send('/pick/calibration/point', 'POST', { pixel: t.pixel, label: t.label })
export const deletePoint = (i: number) => send(`/pick/calibration/point/${i}`, 'DELETE')
export const clearPoints = () => send('/pick/calibration/clear', 'POST')

/** Release or re-arm the follower. Re-arming holds where the arm is. */
export async function setFollowerTorque(enabled: boolean): Promise<boolean> {
  try {
    const res = await fetch('/robot/torque/all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      pickNote.set((data as any).error ?? 'Could not change torque')
      return false
    }
    pickNote.set(null)
    return true
  } catch {
    pickNote.set('Could not reach the backend')
    return false
  }
}

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0

export function startPickPolling(intervalMs = 700): void {
  refs += 1
  if (timer) return
  const tick = () => { loadPickCal(); loadPickObjects() }
  tick()
  timer = setInterval(tick, intervalMs)
}

export function stopPickPolling(): void {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}
