<script lang="ts">
  import CalibratePanel from '../Modes/CalibratePanel.svelte'
  import { loadRanges } from '../../stores/ranges'
  import { loadRig } from '../../stores/rig'

  export let onClose: () => void

  function close() {
    // Ranges and rig readiness both change when a calibration is saved.
    loadRanges()
    loadRig()
    onClose()
  }
</script>

<svelte:window on:keydown={(e) => e.key === 'Escape' && close()} />

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
<div class="overlay" on:click|self={close}>
  <div class="modal" role="dialog" aria-modal="true" aria-label="Calibration wizard">
    <div class="head">
      <span>Calibration</span>
      <button class="close" on:click={close} aria-label="Close">✕</button>
    </div>
    <div class="body">
      <CalibratePanel />
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(0,0,0,0.65);
    display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }

  .modal {
    background: #14181c; border: 1px solid #2c343b; border-radius: 8px;
    width: 520px; max-width: 100%; max-height: 100%;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }

  .head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 13px 16px; border-bottom: 1px solid #232a30;
    font-size: 13px; color: #dfe6ec; flex-shrink: 0;
  }

  .close {
    background: none; border: none; color: #55606a;
    cursor: pointer; font-size: 14px; padding: 2px 6px; line-height: 1;
  }
  .close:hover { color: #dfe6ec; }
  .close:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .body { overflow-y: auto; padding: 16px; min-height: 0; }
</style>
