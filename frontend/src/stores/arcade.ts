import { writable, get } from 'svelte/store'
import { getJson } from './api'

export interface ArcadeState {
  connected: boolean
  device: string | null
  error: string | null
  last_button: number | null
  last_press_at: number
  /** Bumps on every press, so pressing the same button twice is still seen. */
  press_seq: number
  last_action: string | null
}

export const arcade = writable<ArcadeState>({
  connected: false, device: null, error: null,
  last_button: null, last_press_at: 0, press_seq: 0, last_action: null,
})

/** Buttons are stored 0-based; people count from 1. */
export const buttonLabel = (n: number) => `Button ${n + 1}`

export async function loadArcade(): Promise<void> {
  const d = await getJson<ArcadeState>('/arcade/status')
  if (d) arcade.set(d)
}

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0

export function startArcadePolling(intervalMs = 300): void {
  refs += 1
  if (timer) return
  loadArcade()
  timer = setInterval(loadArcade, intervalMs)
}

export function stopArcadePolling(): void {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}

/**
 * Resolve with the next button pressed after this call, or null on timeout.
 * Used to "learn" a button for a command: press it and it is captured.
 */
export function waitForButton(timeoutMs = 10000): Promise<number | null> {
  const startSeq = get(arcade).press_seq
  return new Promise(resolve => {
    const done = (v: number | null) => { unsub(); clearTimeout(t); resolve(v) }
    const unsub = arcade.subscribe(s => {
      if (s.press_seq !== startSeq && s.last_button !== null) done(s.last_button)
    })
    const t = setTimeout(() => done(null), timeoutMs)
  })
}
