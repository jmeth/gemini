import { writable } from 'svelte/store'
import { loadRig } from './rig'
import { getJson } from './api'

export interface TeleopStatus {
  leader_connected: boolean
  mirroring: boolean
  speed_limit: number
  clutched: boolean
  max_error: number
  stop_on_error: boolean
  match_ranges: boolean
  hz: number
  dropped: number
  jitter_ms: number
  clamped: string[]
  error: string | null
}

const EMPTY: TeleopStatus = {
  leader_connected: false, mirroring: false, speed_limit: 0.3,
  clutched: false, max_error: 10, stop_on_error: true, match_ranges: true,
  hz: 0, dropped: 0, jitter_ms: 0, clamped: [], error: null,
}

export const teleopStatus = writable<TeleopStatus>(EMPTY)
/** Last action failure, shown inline. Cleared by the next successful action. */
export const teleopNote = writable<string | null>(null)

export async function loadTeleopStatus(): Promise<void> {
  const data = await getJson<TeleopStatus>('/teleop/status')
  if (data) teleopStatus.set(data)
}

/** Shared by the rail bar and the teleop pane so only one poll runs. */
export async function teleopPost(url: string, body?: unknown): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      teleopNote.set(data.error ?? `Request failed (${res.status})`)
      return false
    }
    teleopNote.set(null)
    return true
  } catch {
    teleopNote.set('Could not reach the backend')
    return false
  } finally {
    await loadTeleopStatus()
    await loadRig()
  }
}

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0

export function startTeleopPolling(intervalMs = 1000): void {
  refs += 1
  if (timer) return
  loadTeleopStatus()
  timer = setInterval(loadTeleopStatus, intervalMs)
}

export function stopTeleopPolling(): void {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}
