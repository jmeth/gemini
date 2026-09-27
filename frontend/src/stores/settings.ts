import { writable } from 'svelte/store'
import { getJson } from './api'

export interface Settings {
  follower_port:   string | null
  leader_port:     string | null
  camera_overhead: number | null
  camera_ee:       number | null
  camera_width:    number
  camera_height:   number
  camera_fps:      number
  camera_quality:  number
  voice_source:    'browser' | 'backend'
  voice_mic:       string | null
  voice_grammar:   boolean
  detect_enabled:  boolean
  detect_camera:   number | null
  detect_fps:      number
  detect_conf:     number
  detect_imgsz:    number
  detect_classes:  number[] | null
}

/** A video capture device, as reported by /system/cameras. */
export interface CameraDevice {
  index:  number
  path:   string
  label:  string
  driver: string
}

export const settings = writable<Settings>({
  follower_port: null, leader_port: null,
  camera_overhead: null, camera_ee: null,
  camera_width: 640, camera_height: 480, camera_fps: 15, camera_quality: 70,
  voice_source: 'browser', voice_mic: null, voice_grammar: true,
  detect_enabled: false, detect_camera: null, detect_fps: 5,
  detect_conf: 0.25, detect_imgsz: 416, detect_classes: null,
})

export async function loadSettings() {
  const data = await getJson<Settings>('/settings')
  if (data) settings.set(data)
}

export async function saveSettings(patch: Partial<Settings>) {
  const res = await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (res.ok) settings.set(await res.json())
}
