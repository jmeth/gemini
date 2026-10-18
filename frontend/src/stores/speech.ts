/**
 * Web Speech API mic listener. Recognition runs entirely in the browser; we
 * only POST the final transcript to /commands/match and, on a hit, play the
 * bound recording. Zero server-side voice dependency.
 */
import { writable, get } from 'svelte/store'
import { matchCommand } from './commands'

export const speechSupported =
  typeof window !== 'undefined' &&
  ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)

export const listening = writable(false)
/** Last final transcript the recogniser produced. */
export const heard = writable<string>('')
/** Human-readable outcome of the last utterance. */
export const lastAction = writable<string | null>(null)

let recog: any = null
let manualStop = false

export function toggleListening(): void {
  get(listening) ? stopListening() : startListening()
}

export function startListening(): void {
  if (!speechSupported || recog) return
  const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  recog = new Ctor()
  recog.continuous = true
  recog.interimResults = false
  recog.lang = 'en-US'
  manualStop = false

  recog.onresult = async (e: any) => {
    const res = e.results[e.results.length - 1]
    if (!res.isFinal) return
    const transcript = (res[0]?.transcript ?? '').trim()
    if (!transcript) return
    heard.set(transcript)
    // Rules (Directives) listen to what was heard, alongside the older commands.
    fetch('/directives/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: transcript }),
    }).catch(() => {})

    const m = await matchCommand(transcript)
    if (!m) { lastAction.set(`“${transcript}” — no match`); return }

    if (m.command.recording_id) {
      lastAction.set(`“${transcript}” → ${m.command.name} — playing`)
      await fetch(`/recordings/${encodeURIComponent(m.command.recording_id)}/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: 1.0 }),
      }).catch(() => {})
    } else {
      lastAction.set(`“${transcript}” → ${m.command.name} — no take bound`)
    }
  }

  recog.onerror = (e: any) => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      lastAction.set('Microphone permission denied')
      stopListening()
    } else if (e.error === 'no-speech' || e.error === 'aborted') {
      // benign — onend will restart
    } else {
      lastAction.set(`Speech error: ${e.error}`)
    }
  }

  recog.onend = () => {
    // Chrome ends a continuous session every ~minute; restart unless we asked it to stop.
    if (!manualStop && recog) {
      try { recog.start() } catch { /* already starting */ }
    } else {
      listening.set(false)
    }
  }

  try {
    recog.start()
    listening.set(true)
  } catch {
    listening.set(false)
  }
}

export function stopListening(): void {
  manualStop = true
  if (recog) {
    try { recog.stop() } catch { /* ignore */ }
    recog = null
  }
  listening.set(false)
}
