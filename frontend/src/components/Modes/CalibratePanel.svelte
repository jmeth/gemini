<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { loadRig } from '../../stores/rig'
  import { loadRanges } from '../../stores/ranges'

  interface CalibrationStatus {
    active: boolean
    step: string
    motor: string | null
    progress: [number, number]
    instruction: string
  }

  interface Arm {
    name: string
    label: string
    port: string | null
    connected: boolean
    calibrated: boolean
  }

  let status: CalibrationStatus = {
    active: false, step: 'idle', motor: null,
    progress: [0, 6], instruction: 'Choose an arm, then start.',
  }

  let arms: Arm[] = []
  let armName = 'follower'
  let positions: Record<string, number> = {}
  let note: string | null = null
  let poll: ReturnType<typeof setInterval>

  onMount(() => {
    refresh()
    // Angles come from the arm being calibrated, so this has to keep pace with
    // the hand moving the joint.
    poll = setInterval(refresh, 200)
  })

  onDestroy(() => clearInterval(poll))

  async function refresh() {
    await Promise.all([loadArms(), loadStatus(), loadPositions()])
  }

  async function loadArms() {
    try {
      const res = await fetch('/robot/arms')
      if (res.ok) arms = await res.json()
    } catch { /* the rig dots already report the link */ }
  }

  async function loadStatus() {
    try {
      const res = await fetch(`/robot/calibrate/status?arm_name=${armName}`)
      if (res.ok) status = await res.json()
    } catch { /* left as-is */ }
  }

  /** Live angles for the SELECTED arm — not whichever arm the dashboard shows. */
  async function loadPositions() {
    try {
      const res = await fetch(`/robot/calibrate/position?arm_name=${armName}`)
      if (res.ok) {
        const data = await res.json()
        positions = data.positions ?? {}
      } else {
        positions = {}
      }
    } catch {
      positions = {}
    }
  }

  async function post(url: string): Promise<boolean> {
    try {
      const res = await fetch(url, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { note = data.error ?? `Request failed (${res.status})`; return false }
      status = data as CalibrationStatus
      note = null
      return true
    } catch {
      note = 'Could not reach the backend'
      return false
    }
  }

  async function start() {
    await post(`/robot/calibrate/start?arm_name=${armName}`)
    await refresh()
  }

  async function confirm() {
    const ok = await post(`/robot/calibrate/confirm?arm_name=${armName}`)
    if (ok && status.step === 'complete') {
      // Ranges and the rig's calibrated dots both change on save.
      await Promise.all([loadRanges(), loadRig()])
    }
    await loadArms()
  }

  async function cancel() {
    try {
      const res = await fetch(`/robot/calibrate/cancel?arm_name=${armName}`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { note = data.error ?? `Request failed (${res.status})`; return }
      note = null
    } catch {
      note = 'Could not reach the backend'
    }
    await refresh()
  }

  function selectArm(name: string) {
    if (running) return   // don't switch arms mid-run
    armName = name
    positions = {}
    note = null
    refresh()
  }

  $: selected = arms.find(a => a.name === armName)
  $: currentPos = status.motor ? positions[status.motor] : undefined
  $: progressPct = (status.progress[0] / status.progress[1]) * 100
  // A finished run still reports active, but nothing is in progress — the picker
  // and Start button must come back so the other arm (or a redo) can be run.
  $: running = status.active && status.step !== 'complete'
  $: canStart = !!selected?.connected && !running
</script>

<div class="panel">
  <div class="header">
    <h2>Calibration Wizard</h2>
    {#if status.active && status.step !== 'complete'}
      <div class="progress-badge">
        Joint {status.progress[0]} / {status.progress[1]}
      </div>
    {/if}
  </div>

  <div class="arm-picker">
    <span class="field-label">Arm to calibrate</span>
    <div class="arm-options">
      {#each arms as arm}
        <button
          class="arm"
          class:selected={arm.name === armName}
          class:offline={!arm.connected}
          disabled={status.active}
          on:click={() => selectArm(arm.name)}
        >
          <span class="arm-head">
            <span class="dot" class:on={arm.connected}></span>
            <span class="arm-name">{arm.label}</span>
            {#if arm.calibrated}<span class="badge">calibrated</span>{/if}
          </span>
          <span class="arm-port">{arm.port ?? 'no port configured'}</span>
          <span class="arm-state">{arm.connected ? 'connected' : 'not connected'}</span>
        </button>
      {/each}
    </div>

    {#if selected && !selected.connected}
      <p class="warn">
        {selected.label} is not connected, so its joints cannot be read.
        Connect it from the <em>{selected.name}</em> dot in the header first.
      </p>
    {:else if selected?.calibrated && !running}
      <p class="hint">Running this again replaces {selected.label}'s saved calibration.</p>
    {/if}
  </div>

  {#if status.active && status.step !== 'idle'}
    <div class="progress-bar">
      <div class="progress-fill" style="width: {progressPct}%"></div>
    </div>
  {/if}

  <div class="instruction-box">
    <p class="instruction">{status.instruction}</p>

    {#if status.active && status.step !== 'idle' && status.step !== 'complete'}
      <div class="current-motor">
        <span class="label">Joint</span>
        <span class="motor-name">{status.motor?.replace('_', ' ')}</span>
      </div>

      <div class="current-position">
        <span class="label">{selected?.label ?? armName} reading</span>
        {#if currentPos === undefined}
          <span class="position-value dim">no reading</span>
        {:else}
          <span class="position-value">{currentPos.toFixed(1)}°</span>
        {/if}
      </div>
    {/if}
  </div>

  <div class="actions">
    {#if running}
      <button class="primary" on:click={confirm}>
        Confirm Position
      </button>
      <button class="secondary" on:click={cancel}>
        Cancel (keep existing)
      </button>
    {:else}
      {#if status.step === 'complete'}
        <button class="success" disabled>
          ✓ {selected?.label ?? ''} Calibration Complete
        </button>
      {/if}
      <button class="primary" on:click={start} disabled={!canStart}>
        {status.step === 'complete' ? 'Recalibrate' : 'Calibrate'} {selected?.label ?? ''}
      </button>
    {/if}
  </div>

  <div class="help-text">
    <strong>How it works:</strong>
    <ol>
      <li>Torque will be disabled so you can move joints manually</li>
      <li>For each joint, move it to minimum position → Confirm</li>
      <li>Then move it to maximum position → Confirm</li>
      <li>Zero position is calculated as the midpoint (more accurate!)</li>
      <li>Calibration data is saved automatically</li>
    </ol>
  </div>
  {#if note}
    <p class="error-note" role="alert">{note}</p>
  {/if}
</div>

<style>
  .arm-picker { display: flex; flex-direction: column; gap: 8px; }
  .field-label {
    font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; font-weight: 600;
  }
  .arm-options { display: flex; gap: 7px; }

  .arm {
    flex: 1; display: flex; flex-direction: column; gap: 3px;
    padding: 10px 11px; text-align: left; cursor: pointer;
    background: #0d1013; border: 1px solid #2c343b; border-radius: 5px;
    font-family: inherit; color: #dfe6ec;
  }
  .arm:hover:not(:disabled) { border-color: #39424a; }
  .arm.selected { border-color: #1f5560; background: #13282c; }
  .arm.offline { opacity: 0.65; }
  .arm:disabled { cursor: default; }
  .arm:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .arm-head { display: flex; align-items: center; gap: 6px; }
  .arm-name { font-size: 13px; font-weight: 600; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: #55606a; flex: none; }
  .dot.on { background: #5fb87a; }
  .badge {
    margin-left: auto; font-size: 8.5px; letter-spacing: 0.1em;
    text-transform: uppercase; color: #5fb87a;
    border: 1px solid currentColor; border-radius: 2px; padding: 0 3px;
  }
  .arm-port {
    font-size: 10.5px; color: #7c8892;
    font-variant-numeric: tabular-nums; word-break: break-all;
  }
  .arm-state { font-size: 10.5px; color: #55606a; }

  .warn {
    font-size: 11.5px; line-height: 1.5; color: #d8a442;
    background: #2a2113; border: 1px solid #4a3a1c;
    border-radius: 4px; padding: 8px 10px;
  }
  .hint { font-size: 11.5px; line-height: 1.5; color: #55606a; }
  .position-value.dim { color: #55606a; }
  .error-note {
    font-size: 11.5px; line-height: 1.45; color: #e2564a;
    background: #2c1715; border: 1px solid #4a231e;
    border-radius: 4px; padding: 8px 10px;
  }
  .panel {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  h2 {
    font-size: 16px;
    font-weight: 600;
    color: #ddd;
    margin: 0;
  }

  .progress-badge {
    padding: 4px 10px;
    background: #1e1e1e;
    border: 1px solid #2a2a2a;
    border-radius: 12px;
    font-size: 11px;
    color: #888;
    font-variant-numeric: tabular-nums;
  }

  .progress-bar {
    height: 6px;
    background: #1a1a1a;
    border-radius: 3px;
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #4fc3f7, #81c784);
    transition: width 0.3s ease;
  }

  .instruction-box {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .instruction {
    font-size: 14px;
    color: #ddd;
    margin: 0;
    line-height: 1.5;
  }

  .current-motor, .current-position {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: #0f0f0f;
    border-radius: 4px;
  }

  .label {
    font-size: 11px;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .motor-name {
    font-size: 13px;
    color: #4fc3f7;
    font-weight: 500;
    text-transform: capitalize;
  }

  .position-value {
    font-size: 16px;
    color: #81c784;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .actions {
    display: flex;
    gap: 12px;
  }

  button {
    flex: 1;
    padding: 12px 20px;
    border-radius: 6px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    border: none;
  }

  button.primary {
    background: #1e3a5a;
    color: #4fc3f7;
    border: 1px solid #2a4a6a;
  }

  button.primary:hover {
    background: #2a4a6a;
  }

  button.secondary {
    background: transparent;
    color: #9aa7b2;
    border: 1px solid #2c343b;
  }

  button.secondary:hover {
    background: #1c2227;
    color: #dfe6ec;
  }

  button.success {
    background: #1e3a1e;
    color: #81c784;
    border: 1px solid #2a4a2a;
    cursor: default;
  }

  button:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .help-text {
    padding: 12px;
    background: #0f0f0f;
    border-left: 3px solid #2a4a6a;
    border-radius: 4px;
  }

  .help-text strong {
    display: block;
    font-size: 11px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
  }

  .help-text ol {
    margin: 0;
    padding-left: 20px;
    font-size: 12px;
    color: #999;
    line-height: 1.6;
  }

  .help-text li {
    margin-bottom: 4px;
  }

  .arm-selector {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
  }

  .arm-selector label {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .arm-selector input[type="text"] {
    padding: 8px 12px;
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    color: #ddd;
    font-family: monospace;
    font-size: 13px;
  }

  .arm-selector input[type="text"]:focus {
    outline: none;
    border-color: #4fc3f7;
  }

  .arm-selector input[type="text"]:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .saved-calibrations {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .calibration-chips {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .chip {
    padding: 4px 10px;
    background: #0f0f0f;
    border: 1px solid #2a2a2a;
    border-radius: 12px;
    font-size: 11px;
    color: #888;
    cursor: pointer;
    transition: all 0.1s;
  }

  .chip:hover {
    background: #1a1a1a;
    border-color: #3a3a3a;
    color: #aaa;
  }

  .chip.selected {
    background: #1e3a5a;
    border-color: #4fc3f7;
    color: #4fc3f7;
  }

  .calibration-status {
    padding: 8px 12px;
    border-radius: 4px;
    font-size: 12px;
  }

  .calibration-status.ok {
    background: #1e3a1e;
    color: #81c784;
  }

  .calibration-status.warning {
    background: #3a2a1e;
    color: #ffb74d;
  }
</style>
