<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { connectWS } from './stores/robot'
  import { loadSettings } from './stores/settings'
  import { loadRanges } from './stores/ranges'
  import { startRigPolling, stopRigPolling } from './stores/rig'
  import { mode, overlay, railOpen } from './stores/ui'

  import Header from './components/Header.svelte'
  import JointConsole from './components/JointConsole.svelte'
  import Dashboard from './components/Dashboard/Dashboard.svelte'
  import ManualPanel from './components/Modes/ManualPanel.svelte'
  import TeleopPanel from './components/Modes/TeleopPanel.svelte'
  import TeleopBar from './components/Modes/TeleopBar.svelte'
  import PickPanel from './components/Modes/PickPanel.svelte'
  import SettingsModal from './components/Settings/SettingsModal.svelte'
  import CalibrateModal from './components/Settings/CalibrateModal.svelte'

  onMount(async () => {
    await loadSettings()
    loadRanges()
    startRigPolling()
    connectWS()
  })

  onDestroy(stopRigPolling)

  // In teleop the console shows both arms on one track.
  $: dual = $mode === 'teleop'
</script>

<div class="app">
  <Header />

  <div class="body">
    <!-- Stage: what the current mode wants you looking at -->
    <main class="stage" aria-label="Stage">
      <Dashboard mode={$mode} />
    </main>

    <!-- Rail: a flex sibling, so the stage reflows instead of being covered -->
    <aside class="rail" class:open={$railOpen} aria-label="Controls">
      <button
        class="handle"
        on:click={() => railOpen.update(v => !v)}
        title={$railOpen ? 'Hide controls' : 'Show controls'}
        aria-expanded={$railOpen}
      >
        <span class="chev">{$railOpen ? '▶' : '◀'}</span>
      </button>

      <div class="rail-inner">
        {#if $mode === 'teleop'}
          <TeleopBar />
        {/if}

        <div class="console-slot">
          <JointConsole {dual} />
        </div>

        {#if $mode !== 'record' && $mode !== 'directives'}
          <div class="pane">
            {#if $mode === 'manual'}
              <ManualPanel />
            {:else if $mode === 'teleop'}
              <TeleopPanel />
            {:else if $mode === 'pick'}
              <PickPanel />
            {/if}
          </div>
        {/if}
      </div>
    </aside>
  </div>
</div>

{#if $overlay === 'settings'}
  <SettingsModal onClose={() => overlay.set(null)} />
{/if}

{#if $overlay === 'calibrate'}
  <CalibrateModal onClose={() => overlay.set(null)} />
{/if}

<style>
  .app {
    height: 100vh;
    display: flex; flex-direction: column;
    overflow: hidden;
  }

  .body {
    flex: 1;
    display: flex;
    overflow: hidden; min-height: 0;
  }

  .stage {
    flex: 1;
    display: flex; flex-direction: column;
    overflow: hidden; min-width: 0; min-height: 0;
  }

  /* Not an overlay: the rail takes its own column and the stage gives way. */
  .rail {
    position: relative;
    flex: 0 0 0px; width: 0;
    background: #14181c;
    border-left: 1px solid #262d34;
    display: flex;
    transition: flex-basis 0.2s ease, width 0.2s ease;
    min-height: 0;
  }
  .rail.open { flex-basis: 336px; width: 336px; }

  .rail-inner {
    flex: 1;
    display: flex; flex-direction: column;
    overflow-y: auto; overflow-x: hidden;
    min-height: 0; min-width: 0;
  }

  .console-slot { border-bottom: 1px solid #262d34; flex-shrink: 0; }

  .pane {
    display: flex; flex-direction: column; gap: 13px;
    padding: 13px;
  }

  .handle {
    position: absolute;
    left: -26px; top: 50%;
    transform: translateY(-50%);
    width: 26px; height: 64px;
    background: #191e23;
    border: 1px solid #262d34; border-right: none;
    border-radius: 6px 0 0 6px;
    color: #55606a; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    z-index: 5;
  }
  .handle:hover { background: #232a31; color: #dfe6ec; }
  .handle:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .chev { font-size: 10px; line-height: 1; }

  /* Narrow screens: the rail becomes a bottom sheet. */
  @media (max-width: 900px) {
    .body { flex-direction: column; }

    .rail {
      flex: 0 0 auto; width: auto;
      border-left: none; border-top: 1px solid #262d34;
      height: 0; overflow: hidden;
      transition: height 0.2s ease;
    }
    .rail.open { height: 58vh; width: auto; flex-basis: auto; }

    .handle {
      left: 50%; top: -25px;
      transform: translateX(-50%);
      width: 64px; height: 25px;
      border: 1px solid #262d34; border-bottom: none;
      border-radius: 6px 6px 0 0;
    }
    .chev { transform: rotate(90deg); }
  }

  @media (prefers-reduced-motion: reduce) {
    .rail { transition: none; }
  }
</style>
