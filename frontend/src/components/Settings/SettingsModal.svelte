<script lang="ts">
  import { onMount } from 'svelte'
  import { settings, saveSettings, type CameraDevice } from '../../stores/settings'
  import type { VoiceMic } from '../../stores/voice'
  import { isConnected } from '../../stores/robot'
  import { loadRig } from '../../stores/rig'

  export let onClose: () => void

  let ports: string[] = []
  let cameras: CameraDevice[] = []
  let mics: VoiceMic[] = []
  let loading = false
  let scanning = false
  let scanningMic = false

  let follower_port   = $settings.follower_port
  let leader_port     = $settings.leader_port
  let camera_overhead = $settings.camera_overhead
  let camera_ee       = $settings.camera_ee
  let voice_source    = $settings.voice_source
  let voice_mic       = $settings.voice_mic
  let voice_grammar   = $settings.voice_grammar
  let detect_fps      = $settings.detect_fps
  let detect_conf     = $settings.detect_conf
  let detect_imgsz    = $settings.detect_imgsz

  // Live-capture probe for the mic status dot: { ok, dbfs?, peak?, error? }.
  type MicProbe = { ok: boolean; rms?: number; dbfs?: number; peak?: number; error?: string | null }
  let micProbe: MicProbe | null = null
  let probing = false

  onMount(async () => {
    loading = true
    // Degrade one field at a time — a single failing endpoint must not wedge
    // the whole modal on the loading spinner.
    ;[ports, cameras, mics] = await Promise.all([
      fetch('/system/ports').then(r => r.json()).catch(() => []),
      fetch('/system/cameras').then(r => r.json()).catch(() => []),
      fetch('/voice/mics').then(r => r.json()).catch(() => []),
    ])
    loading = false
    if (voice_source === 'backend') testMic()
  })

  async function testMic() {
    probing = true
    micProbe = null
    try {
      const q = voice_mic ? `?device=${encodeURIComponent(voice_mic)}` : ''
      const res = await fetch(`/voice/mic/test${q}`)
      micProbe = res.ok ? await res.json() : { ok: false, error: `probe failed (${res.status})` }
    } catch {
      micProbe = { ok: false, error: 'Could not reach the backend' }
    } finally {
      probing = false
    }
  }

  async function refreshMics() {
    scanningMic = true
    try {
      const res = await fetch('/voice/mics')
      if (res.ok) mics = await res.json()
    } finally {
      scanningMic = false
    }
    if (voice_source === 'backend') testMic()
  }

  function changeVoiceSource() {
    // A USB mic is rarely ALSA card 0, so default to the first real capture
    // device the first time the Pi mic is turned on.
    if (voice_source === 'backend') {
      if (!voice_mic && mics.length) voice_mic = mics[0].device
      testMic()
    } else {
      micProbe = null
    }
  }

  async function save() {
    await saveSettings({
      follower_port, leader_port, camera_overhead, camera_ee,
      voice_source, voice_mic, voice_grammar,
      detect_fps: Number(detect_fps),
      detect_conf: Number(detect_conf),
      detect_imgsz: Number(detect_imgsz),
    })
    await loadRig()
    onClose()
  }

  /**
   * A stored index can outlive the device it pointed at — and the old picker
   * offered metadata nodes as if they were cameras, so a saved index may never
   * have been a camera at all. Say so rather than showing a black tile.
   */
  function isStale(index: number | null): boolean {
    return index !== null && !cameras.some(c => c.index === index)
  }

  async function connect() {
    await saveSettings({ follower_port })
    await fetch('/robot/connect', { method: 'POST' })
  }

  async function disconnect() {
    await fetch('/robot/disconnect', { method: 'POST' })
  }

  function refreshPorts() {
    fetch('/system/ports').then(r => r.json()).then(p => { ports = p })
  }

  async function refreshCameras() {
    scanning = true
    try {
      const res = await fetch('/system/cameras')
      if (res.ok) cameras = await res.json()
    } finally {
      scanning = false
    }
  }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="overlay" role="dialog" aria-modal="true" on:click|self={onClose}>
  <div class="modal">
    <div class="modal-header">
      <span>Settings</span>
      <button class="close" on:click={onClose}>✕</button>
    </div>

    <div class="modal-body">
      {#if loading}
        <div class="loading">Scanning ports and cameras…</div>
      {:else}
        <!-- Motor ports -->
        <section>
          <h3>Motor Bus</h3>

          <label>
            Follower arm port
            <div class="select-row">
              <select bind:value={follower_port}>
                <option value={null}>— none —</option>
                {#each ports as p}
                  <option value={p}>{p}</option>
                {/each}
              </select>
              <button class="sm" on:click={refreshPorts} title="Refresh ports">↻</button>
            </div>
          </label>

          <div class="connect-row">
            <button on:click={connect} disabled={!follower_port}>Connect</button>
            <button on:click={disconnect} disabled={!$isConnected}>Disconnect</button>
            <span class="status" class:ok={$isConnected}>
              {$isConnected ? '● connected' : '○ disconnected'}
            </span>
          </div>

          <label>
            Leader arm port
            <div class="select-row">
              <select bind:value={leader_port}>
                <option value={null}>— none —</option>
                {#each ports as p}
                  <option value={p}>{p}</option>
                {/each}
              </select>
            </div>
          </label>
        </section>

        <!-- Cameras -->
        <section>
          <h3>
            Cameras
            <button class="sm" on:click={refreshCameras} disabled={scanning} title="Re-scan for cameras">
              {scanning ? 'scanning…' : '↻ scan'}
            </button>
          </h3>

          {#if cameras.length === 0}
            <p class="hint">
              No video capture devices found. Only real capture nodes are listed —
              a webcam's metadata node is not a camera, even though it opens.
            </p>
          {/if}

          {#each [
            { key: 'overhead', title: 'Overhead camera', value: camera_overhead },
            { key: 'ee',       title: 'End effector camera', value: camera_ee },
          ] as slot}
            <div class="cam-slot">
              <label>
                {slot.title}
                {#if slot.key === 'overhead'}
                  <select bind:value={camera_overhead}>
                    <option value={null}>— none —</option>
                    {#each cameras as c}
                      <option value={c.index}>{c.label} (video{c.index})</option>
                    {/each}
                  </select>
                {:else}
                  <select bind:value={camera_ee}>
                    <option value={null}>— none —</option>
                    {#each cameras as c}
                      <option value={c.index}>{c.label} (video{c.index})</option>
                    {/each}
                  </select>
                {/if}
              </label>

              {#if isStale(slot.value)}
                <p class="stale">
                  Saved index {slot.value} is not a video capture device. Pick one above.
                </p>
              {/if}

              {#key slot.value}
                {#if slot.value !== null && !isStale(slot.value)}
                  <div class="preview">
                    <img src={`/camera/${slot.value}/stream?p=${slot.key}`} alt="{slot.title} preview" />
                  </div>
                {/if}
              {/key}
            </div>
          {/each}

          <p class="hint">
            Capture runs at {$settings.camera_width}x{$settings.camera_height},
            {$settings.camera_fps} fps, JPEG quality {$settings.camera_quality}.
            One capture is shared by every viewer.
          </p>
        </section>

        <!-- Object detection -->
        <section>
          <h3>Object Detection</h3>

          <p class="hint">
            YOLO26n tracking with bounding boxes, drawn into the feed on the Pi.
            Turn it on from the ◉ detect button on a camera tile — it runs only
            while you are watching that feed, and refreshes slowly to leave the
            arm its CPU. These tune it:
          </p>

          <div class="grid3">
            <label>
              Refresh (fps)
              <input type="number" min="1" max="15" step="1" bind:value={detect_fps} />
            </label>
            <label>
              Confidence
              <input type="number" min="0.05" max="0.95" step="0.05" bind:value={detect_conf} />
            </label>
            <label>
              Inference size
              <select bind:value={detect_imgsz}>
                <option value={320}>320 — fastest</option>
                <option value={416}>416 — balanced</option>
                <option value={640}>640 — most accurate</option>
              </select>
            </label>
          </div>

          <p class="hint">
            Lower fps and a smaller inference size are cheaper on the Pi 5;
            pick-and-place rarely needs more than a few frames a second. Detection
            follows the overhead camera unless you enable it on another tile.
          </p>
        </section>

        <!-- Microphone -->
        <section>
          <h3>
            Microphone
            <button class="sm" on:click={refreshMics} disabled={scanningMic} title="Re-scan for microphones">
              {scanningMic ? 'scanning…' : '↻ scan'}
            </button>
          </h3>

          <label>
            Voice input source
            <select bind:value={voice_source} on:change={changeVoiceSource}>
              <option value="browser">Browser microphone</option>
              <option value="backend">Raspberry Pi microphone</option>
            </select>
          </label>

          {#if voice_source === 'backend'}
            <div class="cam-slot">
              <label>
                Capture device
                <select bind:value={voice_mic} on:change={testMic}>
                  <option value={null}>ALSA default</option>
                  {#each mics as m (m.device)}
                    <option value={m.device}>{m.label}</option>
                  {/each}
                </select>
              </label>

              <div class="mic-status">
                <span
                  class="dot"
                  class:ok={micProbe?.ok}
                  title={micProbe?.ok ? 'Capturing audio' : 'No signal'}
                ></span>
                {#if probing}
                  <span class="ms-text">Testing capture…</span>
                {:else if micProbe?.ok}
                  <span class="ms-text"
                    >Live{micProbe.dbfs != null ? ` — signal ${micProbe.dbfs} dBFS` : ''}</span
                  >
                {:else if micProbe}
                  <span class="ms-text warn">{micProbe.error ?? 'No signal'}</span>
                {:else}
                  <span class="ms-text dim">Not tested</span>
                {/if}
                <button class="sm" on:click={testMic} disabled={probing}>Test</button>
              </div>

              {#if mics.length === 0}
                <p class="hint">
                  No ALSA capture devices found. Plug in a USB mic or webcam and
                  re-scan.
                </p>
              {/if}
            </div>

            <label class="check">
              <input type="checkbox" bind:checked={voice_grammar} />
              Restrict recognition to command phrases
            </label>
            <p class="hint">
              Pins the recogniser to the trigger phrases of your enabled
              commands — far more accurate, but it won't transcribe anything
              else. Turn off for open-vocabulary dictation. Updates live as you
              edit commands.
            </p>

            <p class="hint">
              Recognition runs on the Pi (Vosk, offline). Start and stop
              listening from the Record workspace.
            </p>
          {:else}
            <p class="hint">
              Recognition runs in the browser (Web Speech API — Chrome or Edge).
            </p>
          {/if}
        </section>
      {/if}
    </div>

    <div class="modal-footer">
      <button class="cancel" on:click={onClose}>Cancel</button>
      <button class="primary" on:click={save}>Save</button>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }

  .modal {
    background: #161616;
    border: 1px solid #2a2a2a;
    border-radius: 8px;
    width: 420px;
    max-width: 95vw;
    display: flex;
    flex-direction: column;
    max-height: 90vh;
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid #222;
    font-size: 13px;
    color: #ccc;
  }

  .close {
    background: none; border: none; color: #666; cursor: pointer;
    font-size: 14px; padding: 2px 6px;
  }
  .close:hover { color: #ccc; }

  .modal-body {
    padding: 16px;
    overflow-y: auto;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .loading { color: #555; font-size: 12px; text-align: center; padding: 20px; }

  section { display: flex; flex-direction: column; gap: 10px; }

  h3 {
    font-size: 11px;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .cam-slot { display: flex; flex-direction: column; gap: 7px; }

  .preview {
    background: #0d1013; border: 1px solid #2a2a2a; border-radius: 4px;
    overflow: hidden; aspect-ratio: 4 / 3; max-height: 150px;
    display: flex; align-items: center; justify-content: center;
  }
  .preview img { width: 100%; height: 100%; object-fit: contain; display: block; }

  .hint { font-size: 11px; line-height: 1.5; color: #666; }

  .mic-status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: #888;
  }
  .mic-status .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #444; flex: none;
    transition: background 0.15s, box-shadow 0.15s;
  }
  .mic-status .dot.ok { background: #4caf50; box-shadow: 0 0 5px #4caf5099; }
  .ms-text.warn { color: #d8a442; }
  .ms-text.dim { color: #555; }
  .mic-status button.sm { margin-left: auto; }

  label.check {
    flex-direction: row;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    color: #bbb;
    cursor: pointer;
  }
  label.check input { cursor: pointer; margin: 0; }

  .stale {
    font-size: 11px; line-height: 1.45; color: #d8a442;
    background: #2a2113; border: 1px solid #4a3a1c;
    border-radius: 3px; padding: 6px 8px;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: #888;
  }

  select, input[type="number"] {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    color: #ccc;
    border-radius: 4px;
    padding: 6px 8px;
    font-family: inherit;
    font-size: 12px;
    flex: 1;
    width: 100%;
    box-sizing: border-box;
  }

  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

  .select-row {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .connect-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .status { font-size: 11px; color: #555; }
  .status.ok { color: #4caf50; }

  button {
    background: #1e1e1e;
    border: 1px solid #2a2a2a;
    color: #bbb;
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    font-size: 12px;
    padding: 5px 12px;
    transition: background 0.1s;
  }
  button:hover:not(:disabled) { background: #2a2a2a; }
  button:disabled { opacity: 0.4; cursor: default; }

  button.sm { padding: 3px 8px; font-size: 11px; }
  button.primary { background: #1a2a3a; border-color: #2a4a6a; color: #7ab8e8; }
  button.primary:hover { background: #1e3248; }
  button.cancel { color: #666; }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid #222;
  }
</style>
