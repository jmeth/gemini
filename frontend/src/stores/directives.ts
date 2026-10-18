import { writable, get } from 'svelte/store'
import { getJson } from './api'

export type Side = 'left' | 'right'
export const SIDES: Side[] = ['left', 'right']

export interface ArmsInfo { sides: Side[]; side: Side; connected: boolean; audio_player: string | null }
export interface Pose {
  id: number; name: string; created_at: string
  arms: Partial<Record<Side, Record<string, number>>>
}
export interface Sound {
  id: number; name: string; filename: string; size: number
  duration_s: number | null; created_at: string
}

export type Trigger =
  | { type: 'voice'; phrases: string[] }
  | { type: 'button'; button: number }
  | { type: 'timer'; mode: 'interval'; seconds: number }
  | { type: 'timer'; mode: 'daily'; at: string }
  | { type: 'object'; label: string; event: 'appears' | 'disappears'; min_conf: number }
  | { type: 'startup' }

export type Action =
  | { type: 'movement'; id: string; arms: Side[]; speed: number }
  | { type: 'pose'; id: number; arms: Side[]; duration_s: number }
  | { type: 'sound'; id: number; volume: number; wait: boolean }
  | { type: 'wait'; seconds: number }

export type BusyPolicy = 'skip' | 'queue' | 'interrupt'

export interface Rule {
  id: number; name: string; enabled: boolean
  triggers: Trigger[]; actions: Action[]
  busy_policy: BusyPolicy; cooldown_s: number
  source: string | null; created_at: string; last_fired_at: string | null
}
export type RuleDraft = Pick<Rule, 'name' | 'triggers' | 'actions' | 'busy_policy' | 'cooldown_s'>

export interface EngineEvent { seq: number; t: number; kind: string; text: string; rule_id?: number; manual?: boolean }

export const arms   = writable<ArmsInfo | null>(null)
export const poses  = writable<Pose[]>([])
export const sounds = writable<Sound[]>([])
export const rules  = writable<Rule[]>([])
export const events = writable<EngineEvent[]>([])
/** Last failure from any Directives action, shown inline on the page. */
export const dirNote = writable<string | null>(null)

/** Buttons are stored 0-based; people count from 1. */
export const buttonName = (n: number) => `Button ${n + 1}`

async function call<T = any>(url: string, method: string, body?: unknown, raw?: BodyInit): Promise<T | null> {
  try {
    const res = await fetch(url, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) { dirNote.set((data as any).error ?? `Request failed (${res.status})`); return null }
    dirNote.set(null)
    return data as T
  } catch {
    dirNote.set('Could not reach the backend')
    return null
  }
}

export async function loadArms()   { const d = await getJson<ArmsInfo>('/directives/arms'); if (d) arms.set(d) }
export async function loadPoses()  { const d = await getJson<Pose[]>('/poses');   if (d) poses.set(d) }
export async function loadSounds() { const d = await getJson<Sound[]>('/sounds'); if (d) sounds.set(d) }
export async function loadRules()  { const d = await getJson<Rule[]>('/rules');   if (d) rules.set(d) }

export async function setSide(side: Side) {
  const d = await call<ArmsInfo>('/directives/arms', 'POST', { side })
  if (d) arms.set(d)
}

// -- poses
export async function capturePose(name: string, poseId?: number) {
  const d = await call('/poses/capture', 'POST', poseId ? { pose_id: poseId } : { name })
  await loadPoses(); return d
}
export async function updatePose(id: number, patch: Partial<Pick<Pose, 'name' | 'arms'>>) {
  const d = await call(`/poses/${id}`, 'PUT', patch); await loadPoses(); return d
}
export async function deletePose(id: number) { await call(`/poses/${id}`, 'DELETE'); await loadPoses() }
export const goToPose = (id: number, duration_s: number) => call(`/poses/${id}/go`, 'POST', { duration_s })

// -- sounds
export async function uploadSound(file: File, name: string) {
  const q = new URLSearchParams({ name: name || file.name.replace(/\.[^.]+$/, ''), filename: file.name })
  const d = await call(`/sounds?${q}`, 'POST', undefined, file)
  await loadSounds(); return d
}
export async function renameSound(id: number, name: string) {
  await call(`/sounds/${id}`, 'PUT', { name }); await loadSounds()
}
export async function deleteSound(id: number) { await call(`/sounds/${id}`, 'DELETE'); await loadSounds() }
export const playSound  = (id: number, volume = 1) => call(`/sounds/${id}/play`, 'POST', { volume })
export const stopSounds = () => call('/sounds/stop', 'POST', {})

// -- takes (the rename the Movements page needs; the rest lives in stores/recordings)
export const renameTake = (id: string, name: string) => call(`/recordings/${encodeURIComponent(id)}`, 'PUT', { name })

// -- rules
export async function saveRule(id: number | null, draft: RuleDraft): Promise<Rule | null> {
  const d = id === null ? await call<Rule>('/rules', 'POST', draft) : await call<Rule>(`/rules/${id}`, 'PUT', draft)
  await loadRules(); return d
}
export async function setRuleEnabled(id: number, enabled: boolean) {
  await call(`/rules/${id}`, 'PUT', { enabled }); await loadRules()
}
export async function deleteRule(id: number) { await call(`/rules/${id}`, 'DELETE'); await loadRules() }
export const runRule = (id: number) => call(`/rules/${id}/run`, 'POST', {})
export async function importCommands() {
  const d = await call<{ imported: number; skipped: number }>('/rules/import-commands', 'POST', {})
  await loadRules(); return d
}
export const fireTest = (body: Record<string, unknown>) =>
  call<{ matched: number[] }>('/directives/fire', 'POST', body)

// -- events: polled by the pages that show them
let lastSeq = 0
export async function loadEvents() {
  const d = await getJson<{ events: EngineEvent[] }>(`/directives/events?after=${lastSeq}`)
  if (!d || !d.events.length) return
  lastSeq = d.events[d.events.length - 1].seq
  events.set([...get(events), ...d.events].slice(-400))
}

let timer: ReturnType<typeof setInterval> | null = null
let refs = 0
export function startEventPolling(ms = 700) {
  refs += 1
  if (timer) return
  loadEvents()
  timer = setInterval(loadEvents, ms)
}
export function stopEventPolling() {
  refs = Math.max(0, refs - 1)
  if (refs === 0 && timer) { clearInterval(timer); timer = null }
}
