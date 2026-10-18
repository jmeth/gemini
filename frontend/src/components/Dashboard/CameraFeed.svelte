<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { overlay } from '../../stores/ui'
  import { wsConnected } from '../../stores/robot'
  import { vision, objectsFor, loadVision, setVision } from '../../stores/detection'

  export let label: string
  export let cameraIndex: number | null = null

  // Detection is server-side (boxes are drawn into the stream); this just drives
  // the operator toggle and the live object count for THIS feed.
  $: objects = objectsFor(cameraIndex)
  $: detecting = $vision?.enabled === true && $vision?.camera === cameraIndex
  $: modelLoading = detecting && $vision?.model?.loading === true
  $: modelError = detecting ? ($vision?.model?.error ?? null) : null
  let busy = false
  let toggleError = ''

  onMount(loadVision)

  async function toggleDetect() {
    if (cameraIndex === null || busy) return
    busy = true
    toggleError = ''
    try {
      await setVision(detecting ? { enabled: false } : { enabled: true, camera: cameraIndex })
    } catch (err) {
      toggleError = err instanceof Error ? err.message : 'Failed'
    } finally {
      busy = false
    }
  }

  type State = 'unconfigured' | 'connecting' | 'live' | 'offline'

  let img: HTMLImageElement | undefined
  let attempt = 0
  let state: State = 'unconfigured'
  let detail = ''

  // A cache-buster per attempt: without it the browser reuses the dead
  // connection instead of asking for a fresh stream.
  $: src = cameraIndex === null ? '' : `/camera/${cameraIndex}/stream?a=${attempt}`
  $: if (cameraIndex === null) { state = 'unconfigured' } else if (state === 'unconfigured') { state = 'connecting' }

  // Re-arm whenever the selected camera changes.
  $: if (cameraIndex !== null) { cameraIndex; state = 'connecting'; detail = '' }

  function onLoad() {
    state = 'live'
  }

  async function onError() {
    state = 'offline'
    // The stream endpoint reports *why* in JSON; the <img> tag cannot show it.
    if (cameraIndex === null) return
    try {
      const res = await fetch(`/camera/${cameraIndex}/snapshot`)
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        detail = body?.error ?? `HTTP ${res.status}`
      }
    } catch {
      detail = 'Backend unreachable'
    }
  }

  function retry() {
    detail = ''
    state = 'connecting'
    attempt += 1
  }

  // The stream dies silently when the backend restarts (dev auto-reload, a crash):
  // the <img> keeps its last frame and fires no error, so it just looks frozen and
  // the backend, having lost its viewer, closes the camera. The shared /ws socket
  // does notice the restart, so when it comes back, reattach.
  let wasUp = $wsConnected
  $: {
    const up = $wsConnected
    if (up && !wasUp && cameraIndex !== null) retry()
    wasUp = up
  }

  // Backstop for any other way the stream can drop without telling the page: ask
  // the backend whether anyone is still watching this camera.
  let watchdog: ReturnType<typeof setInterval> | undefined
  async function checkAlive() {
    if (state !== 'live' || cameraIndex === null) return
    try {
      const res = await fetch('/camera/status')
      if (!res.ok) return
      const stream = (await res.json()).streams?.find((x: any) => x.index === cameraIndex)
      if (!stream || !stream.open || stream.viewers === 0) retry()
    } catch { /* backend unreachable: the /ws reconnect handles it when it returns */ }
  }
  onMount(() => { watchdog = setInterval(checkAlive, 4000) })
  onDestroy(() => clearInterval(watchdog))

  function stop() {
    // Closes the HTTP connection; an MJPEG stream stays open otherwise.
    if (img) img.src = ''
  }

  onDestroy(stop)
</script>

<div class="feed">
  <div class="bar">
    <span class="label">{label}</span>
    <div class="bar-right">
      {#if detecting}
        <span class="objcount" title="Objects tracked in view">
          {#if modelLoading}loading model…
          {:else if modelError}model error
          {:else}{$objects.length} object{$objects.length === 1 ? '' : 's'}{/if}
        </span>
      {/if}
      {#if cameraIndex !== null}
        <button
          class="detect"
          class:on={detecting}
          disabled={busy}
          on:click={toggleDetect}
          title={detecting ? 'Turn object detection off' : 'Detect and track objects (YOLO)'}
        >
          {detecting ? '◉ detect' : '○ detect'}
        </button>
      {/if}
      {#if state === 'live'}
        <span class="live"><span class="dot"></span>live</span>
      {/if}
    </div>
  </div>

  {#if modelError || toggleError}
    <div class="detect-err">{modelError ?? toggleError}</div>
  {/if}

  {#if cameraIndex === null}
    <button class="empty" on:click={() => overlay.set('settings')}>
      <span class="ico">□</span>
      <span>No camera selected</span>
      <span class="act">Choose a camera</span>
    </button>
  {:else}
    {#key src}
      <img
        bind:this={img}
        {src}
        alt={label}
        class="stream"
        class:hidden={state !== 'live'}
        on:load={onLoad}
        on:error={onError}
      />
    {/key}

    {#if state === 'connecting'}
      <div class="veil"><span class="spin"></span>opening camera {cameraIndex}…</div>
    {:else if state === 'offline'}
      <div class="veil err">
        <span>Camera {cameraIndex} is not streaming</span>
        {#if detail}<span class="why">{detail}</span>{/if}
        <div class="acts">
          <button on:click={retry}>Retry</button>
          <button on:click={() => overlay.set('settings')}>Change camera</button>
        </div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .feed {
    position: relative; width: 100%; height: 100%;
    background: #111; border-radius: 4px; overflow: hidden;
    display: flex; flex-direction: column;
  }

  .bar {
    position: absolute; top: 0; left: 0; right: 0; z-index: 2;
    display: flex; align-items: center; justify-content: space-between;
    padding: 7px 10px; pointer-events: none;
    background: linear-gradient(rgba(0,0,0,0.55), transparent);
  }
  .label {
    font-size: 10px; letter-spacing: 0.11em; text-transform: uppercase;
    color: #b6c0c8; font-weight: 600;
  }
  .bar-right { display: flex; align-items: center; gap: 8px; pointer-events: auto; }
  .objcount { font-size: 10px; color: #45c3d1; letter-spacing: 0.04em; }
  .detect {
    font-family: inherit; font-size: 10px; letter-spacing: 0.06em;
    background: rgba(20,26,32,0.7); border: 1px solid #2c343b; color: #b6c0c8;
    border-radius: 3px; padding: 3px 8px; cursor: pointer;
  }
  .detect:hover:not(:disabled) { background: rgba(38,45,52,0.85); }
  .detect.on { background: #123; border-color: #2a6a7a; color: #45c3d1; }
  .detect:disabled { opacity: 0.5; cursor: default; }
  .detect:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .detect-err {
    position: absolute; top: 30px; left: 10px; z-index: 3;
    font-size: 10px; color: #e08a8a; background: rgba(13,16,19,0.85);
    padding: 3px 7px; border-radius: 3px; max-width: 80%;
  }

  .live { display: flex; align-items: center; gap: 5px; font-size: 10px; color: #5fb87a; }
  .dot {
    width: 6px; height: 6px; border-radius: 50%; background: currentColor;
    animation: pulse 1.8s ease-in-out infinite;
  }
  @keyframes pulse { 50% { opacity: 0.35; } }

  .stream { width: 100%; height: 100%; object-fit: contain; background: #0d1013; display: block; }
  .stream.hidden { visibility: hidden; }

  .empty, .veil {
    position: absolute; inset: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 8px;
    color: #55606a; font-family: inherit; font-size: 12px; text-align: center;
    padding: 16px; background: #111; border: none;
  }
  .empty { cursor: pointer; }
  .empty:hover { color: #7c8892; }
  .empty:focus-visible { outline: 2px solid #45c3d1; outline-offset: -2px; }
  .ico { font-size: 19px; opacity: 0.5; }
  .act { color: #45c3d1; text-decoration: underline; text-underline-offset: 2px; }

  .veil { background: rgba(13,16,19,0.92); }
  .veil.err { color: #b6c0c8; }
  .why { font-size: 11px; color: #d8a442; max-width: 90%; }

  .acts { display: flex; gap: 6px; margin-top: 4px; }
  .acts button {
    font-family: inherit; font-size: 11.5px;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 5px 11px; cursor: pointer;
  }
  .acts button:hover { background: #262d34; }
  .acts button:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .spin {
    width: 13px; height: 13px; border-radius: 50%;
    border: 2px solid #2c343b; border-top-color: #45c3d1;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  @media (prefers-reduced-motion: reduce) {
    .spin, .dot { animation: none; }
  }
</style>
