/**
 * Backend voice listener (Vosk + a local microphone on the host). The browser
 * path still lives in speech.ts; this store only drives the server-side one and
 * the source/device selector. Status is polled while the Record workspace is up.
 */
import { writable } from 'svelte/store'
import { getJson } from './api'

export type VoiceSource = 'browser' | 'backend'

export interface VoiceMic {
  device: string   // ALSA identifier, e.g. "plughw:1,0"
  label: string
}

export interface VoiceState {
  source: VoiceSource
  mic: string | null
  grammar_enabled: boolean
  running: boolean
  model_loaded: boolean
  device: string | null
  grammar: string[] | null   // phrases the live recogniser is pinned to; null = open vocab
  last_text: string
  last_action: string | null
  error: string | null
}

const EMPTY: VoiceState = {
  source: 'browser', mic: null, grammar_enabled: true, running: false,
  model_loaded: false, device: null, grammar: null,
  last_text: '', last_action: null, error: null,
}

export const voiceState = writable<VoiceState>(EMPTY)
export const voiceMics = writable<VoiceMic[]>([])
/** Last action failure, shown inline. Cleared by the next successful action. */
export const voiceNote = writable<string | null>(null)

export async function loadVoiceStatus(): Promise<void> {
  const d = await getJson<VoiceState>('/voice/status')
  if (d) voiceState.set(d)
}

export async function loadVoiceMics(): Promise<void> {
  const d = await getJson<VoiceMic[]>('/voice/mics')
  if (d) voiceMics.set(d)
}

async function post(url: string, body?: unknown): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      voiceNote.set((data as any).error ?? `Request failed (${res.status})`)
      return false
    }
    voiceNote.set(null)
    return true
  } catch {
    voiceNote.set('Could not reach the backend')
    return false
  } finally {
    await loadVoiceStatus()
  }
}

export const setVoiceSource = (source: VoiceSource) => post('/voice/source', { source })
export const setVoiceMic = (device: string | null) => post('/voice/mic', { device })
export const startBackendVoice = () => post('/voice/start')
export const stopBackendVoice = () => post('/voice/stop')

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0

export function startVoicePolling(intervalMs = 1000): void {
  refs += 1
  if (timer) return
  loadVoiceStatus()
  loadVoiceMics()
  timer = setInterval(loadVoiceStatus, intervalMs)
}

export function stopVoicePolling(): void {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}
