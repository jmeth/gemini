import { writable } from 'svelte/store'
import { getJson } from './api'

export interface Command {
  id: number
  name: string
  triggers: string[]
  recording_id: string | null
  created_at: string
  enabled: boolean
  /** Arcade button (0-based) that plays this command, if any. */
  button: number | null
}

export const commands = writable<Command[]>([])
/** Last action failure, shown inline. */
export const commandNote = writable<string | null>(null)

export async function loadCommands(): Promise<void> {
  const d = await getJson<Command[]>('/commands')
  if (d) commands.set(d)
}

export function createCommand(body: Partial<Command>) {
  return send('/commands', 'POST', body)
}
export function updateCommand(id: number, body: Partial<Command>) {
  return send(`/commands/${id}`, 'PUT', body)
}
export function setCommandEnabled(id: number, enabled: boolean) {
  return send(`/commands/${id}`, 'PUT', { enabled })
}
export function deleteCommand(id: number) {
  return send(`/commands/${id}`, 'DELETE')
}

async function send(url: string, method: string, body?: unknown): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      commandNote.set((data as any).error ?? `Request failed (${res.status})`)
      return false
    }
    commandNote.set(null)
    return true
  } catch {
    commandNote.set('Could not reach the backend')
    return false
  } finally {
    await loadCommands()
  }
}

export interface MatchResult {
  command: Command
  score: number
  matched_trigger: string
}

/** Ask the backend which command, if any, a transcript triggers. Null on no match. */
export async function matchCommand(transcript: string): Promise<MatchResult | null> {
  try {
    const res = await fetch('/commands/match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript }),
    })
    if (!res.ok) return null
    return await res.json() as MatchResult
  } catch {
    return null
  }
}
