import { writable, get } from 'svelte/store'
import { MOTOR_NAMES, type MotorName } from './robot'
import { getJson } from './api'

export interface Range { min: number; max: number }

/**
 * Calibrated travel per joint, fetched once and shared. Previously the readout
 * bars and the control sliders each kept their own idea of a joint's range, so
 * the same position could render in two different places.
 */
const FALLBACK: Record<MotorName, Range> = {
  shoulder_pan:  { min: -180, max: 180 },
  shoulder_lift: { min: -180, max: 180 },
  elbow_flex:    { min: -180, max: 180 },
  wrist_flex:    { min: -180, max: 180 },
  wrist_roll:    { min: -180, max: 180 },
  gripper:       { min: 0,    max: 100 },
}

export const ranges = writable<Record<MotorName, Range>>({ ...FALLBACK })

let inflight: Promise<void> | null = null

export function loadRanges(armName = 'follower'): Promise<void> {
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const data = await getJson<Record<string, Range>>(
        `/robot/calibrate/ranges?arm_name=${armName}`)
      if (!data) return
      const next = { ...get(ranges) }
      for (const name of MOTOR_NAMES) {
        const r = data[name]
        if (r && Number.isFinite(r.min) && Number.isFinite(r.max) && r.max > r.min) {
          next[name] = { min: r.min, max: r.max }
        }
      }
      ranges.set(next)
    } catch {
      // Keep the fallback; the console still works, just on nominal travel.
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** Position as 0-100% of a joint's calibrated travel. */
export function toPct(r: Range, value: number): number {
  if (r.max === r.min) return 0
  return Math.max(0, Math.min(100, ((value - r.min) / (r.max - r.min)) * 100))
}
