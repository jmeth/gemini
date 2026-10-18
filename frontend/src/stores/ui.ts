import { writable } from 'svelte/store'

export const MODES = ['manual', 'teleop', 'record', 'pick', 'directives'] as const
export type Mode = typeof MODES[number]

export const MODE_LABELS: Record<Mode, string> = {
  manual: 'Manual',
  teleop: 'Teleop',
  record: 'Record',
  pick:   'Pick',
  directives: 'Directives',
}

export const MODE_HINTS: Record<Mode, string> = {
  manual: 'Jog each joint directly',
  teleop: 'Leader arm drives the follower',
  record: 'Capture and replay trajectories',
  pick:   'Vision-guided pick and place',
  directives: 'Movements, poses, sounds, and rules that trigger them',
}

/** Modes whose backend is not built yet — the pane says so instead of pretending. */
export const MODE_PHASE: Partial<Record<Mode, string>> = {
  pick:   'Phase 4-5',
}

export const mode = writable<Mode>('manual')

/** Which overlay is open, if any. Only one at a time. */
export type Overlay = null | 'settings' | 'calibrate'
export const overlay = writable<Overlay>(null)

/** Rail open/closed. Persisted so it survives a reload. */
const RAIL_KEY = 'so101.rail.open'

function initialRail(): boolean {
  try {
    const v = localStorage.getItem(RAIL_KEY)
    return v === null ? true : v === '1'
  } catch {
    return true
  }
}

export const railOpen = writable<boolean>(initialRail())

railOpen.subscribe(v => {
  try { localStorage.setItem(RAIL_KEY, v ? '1' : '0') } catch { /* private mode */ }
})
