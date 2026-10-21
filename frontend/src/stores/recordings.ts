import { writable } from 'svelte/store'
import { getJson } from './api'

export interface Take {
  id: string
  name: string
  created_at: string | null
  duration_s: number
  hz: number
  frames: number
  /** Which WALL-E arm the take was recorded on, and whether the leader was captured too. */
  side?: string | null
  leader?: boolean
  /** The arms (left/right) this take actually moves, for the current wiring. */
  moves?: string[]
}

export interface RecorderStatus {
  recording: boolean
  playing: boolean
  name: string | null
  frames: number
  elapsed_s: number
  play_id: string | null
  play_progress: number
  error: string | null
}

const EMPTY: RecorderStatus = {
  recording: false, playing: false, name: null, frames: 0,
  elapsed_s: 0, play_id: null, play_progress: 0, error: null,
}

export const takes = writable<Take[]>([])
export const recorderStatus = writable<RecorderStatus>(EMPTY)
/** Last action failure, shown inline. Cleared by the next successful action. */
export const recordNote = writable<string | null>(null)

export async function loadTakes(): Promise<void> {
  const d = await getJson<Take[]>('/recordings')
  if (d) takes.set(d)
}

export async function loadRecorderStatus(): Promise<void> {
  const d = await getJson<RecorderStatus>('/recordings/status')
  if (d) recorderStatus.set(d)
}

/** POST to a recordings endpoint, then refresh status + list. */
export async function recPost(url: string, body?: unknown): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      recordNote.set((data as any).error ?? `Request failed (${res.status})`)
      return null
    }
    recordNote.set(null)
    return data as Record<string, unknown>
  } catch {
    recordNote.set('Could not reach the backend')
    return null
  } finally {
    await loadRecorderStatus()
    await loadTakes()
  }
}

export async function deleteTake(id: string): Promise<void> {
  try {
    const res = await fetch(`/recordings/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      recordNote.set((data as any).error ?? `Could not delete (${res.status})`)
    } else {
      recordNote.set(null)
    }
  } catch {
    recordNote.set('Could not reach the backend')
  } finally {
    await loadTakes()
  }
}

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0

/** Fine-grained status (frames, elapsed, progress) — the WS frame only carries the bools. */
export function startRecPolling(intervalMs = 500): void {
  refs += 1
  if (timer) return
  loadRecorderStatus()
  loadTakes()
  timer = setInterval(loadRecorderStatus, intervalMs)
}

export function stopRecPolling(): void {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}
