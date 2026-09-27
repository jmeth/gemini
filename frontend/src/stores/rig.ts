import { writable } from 'svelte/store'
import { getJson } from './api'

export interface ArmRig {
  connected: boolean
  port: string | null
  error: string | null
  calibrated: boolean
  mirroring?: boolean
  clutched?: boolean
}

export interface Rig {
  follower: ArmRig
  leader: ArmRig
  cameras: {
    overhead: number | null
    ee: number | null
    /** False when the stored index is no longer a video capture device. */
    overhead_valid: boolean
    ee_valid: boolean
  }
  calibrations: string[]
}

const EMPTY: Rig = {
  follower: { connected: false, port: null, error: null, calibrated: false },
  leader:   { connected: false, port: null, error: null, calibrated: false },
  cameras:  { overhead: null, ee: null, overhead_valid: true, ee_valid: true },
  calibrations: [],
}

export const rig = writable<Rig>(EMPTY)

export async function loadRig(): Promise<void> {
  const data = await getJson<Rig>('/rig')
  if (data) rig.set(data)
}

let timer: ReturnType<typeof setInterval> | null = null

/** Poll rig readiness. Slow on purpose — this is setup state, not telemetry. */
export function startRigPolling(intervalMs = 2000): void {
  if (timer) return
  loadRig()
  timer = setInterval(loadRig, intervalMs)
}

export function stopRigPolling(): void {
  if (timer) { clearInterval(timer); timer = null }
}
