<script lang="ts">
  import { isConnected } from '../stores/robot'
  import { loadRig } from '../stores/rig'

  /**
   * Releases torque on every joint immediately. No confirmation dialog — a stop
   * button you have to confirm is not a stop button. Recovery is explicit: the
   * button latches into a released state until you re-arm.
   */
  let released = false
  let busy = false

  async function stop() {
    busy = true
    try {
      const res = await fetch('/robot/estop', { method: 'POST' })
      if (res.ok) released = true
    } catch { /* surfaced by the latch not engaging */ }
    busy = false
    loadRig()
  }

  async function rearm() {
    busy = true
    try {
      const res = await fetch('/robot/torque/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      })
      if (res.ok) released = false
    } catch { /* stays latched, which is the safe failure */ }
    busy = false
    loadRig()
  }

  // A disconnect clears the latch — there is no torque to hold.
  $: if (!$isConnected) released = false
</script>

{#if released}
  <button class="rearm" on:click={rearm} disabled={busy} title="Re-enable torque on all joints">
    <span class="dot"></span>
    Torque off — re-arm
  </button>
{:else}
  <button
    class="stop"
    on:click={stop}
    disabled={busy || !$isConnected}
    title="Release torque on all joints immediately"
  >Stop</button>
{/if}

<style>
  .stop, .rearm {
    font-family: inherit; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.15em; font-size: 11px;
    border-radius: 4px; cursor: pointer; white-space: nowrap;
    transition: background 0.1s;
  }

  .stop {
    color: #ffd9d4; background: #7d241b;
    border: 1px solid #a5342a; border-bottom-width: 3px;
    padding: 6px 17px;
  }
  .stop:hover:not(:disabled) { background: #992c21; }
  .stop:active:not(:disabled) { border-bottom-width: 1px; margin-top: 2px; }
  .stop:disabled { opacity: 0.4; cursor: default; }

  .rearm {
    display: flex; align-items: center; gap: 7px;
    color: #d8a442; background: #2a2113;
    border: 1px solid #4a3a1c;
    padding: 6px 13px; letter-spacing: 0.08em;
  }
  .rearm:hover:not(:disabled) { background: #3a2d18; }
  .rearm:disabled { opacity: 0.5; cursor: default; }

  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: currentColor; flex: none;
    animation: pulse 1.4s ease-in-out infinite;
  }

  @keyframes pulse { 50% { opacity: 0.3; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style>
