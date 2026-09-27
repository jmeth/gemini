<script lang="ts">
  import { isConnected } from '../../stores/robot'
  import { rig } from '../../stores/rig'
  import { overlay } from '../../stores/ui'

  let busy = false
  let note: string | null = null

  async function goHome() {
    busy = true
    try {
      const res = await fetch('/robot/home', { method: 'POST' })
      note = res.ok ? 'Moving to home' : 'Could not move home — is the arm connected?'
    } catch {
      note = 'Could not reach the backend'
    }
    busy = false
  }

  async function setHome() {
    busy = true
    try {
      const res = await fetch('/robot/home/set', { method: 'POST' })
      note = res.ok ? 'Current pose saved as home' : 'Could not save home'
    } catch {
      note = 'Could not reach the backend'
    }
    busy = false
  }

  async function relaxAll() {
    busy = true
    try {
      await fetch('/robot/torque/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: false }),
      })
      note = 'Torque released on all joints'
    } catch {
      note = 'Could not reach the backend'
    }
    busy = false
  }
</script>

<div class="actions">
  <button class="btn pri" on:click={goHome} disabled={!$isConnected || busy}>Go home</button>
  <button class="btn" on:click={setHome} disabled={!$isConnected || busy}>Set home</button>
  <button class="btn narrow" on:click={relaxAll} disabled={!$isConnected || busy} title="Release torque on all joints">
    Relax
  </button>
</div>

{#if note}
  <p class="note live" role="status">{note}</p>
{/if}

{#if !$rig.follower.calibrated}
  <div class="callout">
    <strong>Sliders are on nominal travel.</strong>
    No follower calibration is saved, so joints report raw motor angles — which
    run past ±180° and make the sliders fight the incoming state.
    <button class="link" on:click={() => overlay.set('calibrate')}>Calibrate the arm</button>
  </div>
{/if}

<p class="note">
  Each slider writes straight to the joint as you drag. Release a joint's
  <span class="mono">T</span> to move it by hand.
</p>

<style>
  .actions { display: flex; gap: 6px; }

  .btn {
    flex: 1; font-family: inherit; font-size: 12px;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 7px 11px; cursor: pointer;
  }
  .btn:hover:not(:disabled) { background: #262d34; }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .btn.pri { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .btn.pri:hover:not(:disabled) { background: #184048; }
  .btn.narrow { flex: 0 0 auto; padding: 7px 10px; }
  .btn:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .note { font-size: 11.5px; line-height: 1.55; color: #55606a; }
  .note.live { color: #7c8892; }
  .mono { font-family: inherit; color: #7c8892; }

  .callout {
    font-size: 11.5px; line-height: 1.55; color: #b89a5a;
    background: #2a2113; border: 1px solid #4a3a1c;
    border-radius: 4px; padding: 9px 11px;
  }
  .callout strong { color: #d8a442; font-weight: 600; display: block; margin-bottom: 3px; }

  .link {
    background: none; border: none; padding: 0; margin-top: 5px;
    color: #45c3d1; font-family: inherit; font-size: 11.5px;
    cursor: pointer; text-decoration: underline; text-underline-offset: 2px;
    display: block;
  }
</style>
