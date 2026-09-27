import { writable, derived } from 'svelte/store'
import { getJson } from './api'

/** One tracked object from the backend YOLO26 detector. */
export interface DetectedObject {
  id:     number | null      // ByteTrack id, stable across frames
  cls:    number             // COCO class index
  label:  string
  conf:   number | null
  box:    [number, number, number, number]   // x1,y1,x2,y2 in frame pixels
  center: [number, number]                   // cx,cy in frame pixels
}

/** Latest detections, tagged with the camera they came from. */
export interface DetectionFrame {
  camera:  number
  objects: DetectedObject[]
  t:       number
}

/** Detector settings + model state, mirrored from GET /vision/status. */
export interface VisionState {
  enabled: boolean
  camera:  number | null
  fps:     number
  conf:    number
  imgsz:   number
  classes: number[] | null
  objects: number
  model: {
    model: string
    backend: string
    ready: boolean
    loading: boolean
    error: string | null
    classes: number
  }
}

export const detections = writable<DetectionFrame | null>(null)
export const vision     = writable<VisionState | null>(null)

/** Boxes for a given camera index, or [] when the latest frame is another camera. */
export function objectsFor(index: number | null) {
  return derived(detections, d =>
    d && index !== null && d.camera === index ? d.objects : [])
}

/** Fed from the shared /ws socket in robot.ts. */
export function pushDetections(frame: DetectionFrame) {
  detections.set(frame)
}

export async function loadVision() {
  const s = await getJson<VisionState>('/vision/status')
  if (s) vision.set(s)
}

/** Turn detection on/off or retune it; the backend persists the change. */
export async function setVision(patch: Partial<{
  enabled: boolean; camera: number | null; fps: number; conf: number; imgsz: number
}>) {
  const res = await fetch('/vision/detect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (res.ok) {
    vision.set(await res.json())
  } else {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error ?? `HTTP ${res.status}`)
  }
}
