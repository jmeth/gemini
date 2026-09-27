<script lang="ts">
  import { wsConnected } from '../stores/robot'
  import { mode, overlay, MODES, MODE_LABELS, MODE_HINTS, type Mode } from '../stores/ui'
  import RigStatus from './RigStatus.svelte'
  import EStop from './EStop.svelte'

  function select(m: Mode) { mode.set(m) }

  function onTabKey(e: KeyboardEvent, i: number) {
    const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!dir) return
    e.preventDefault()
    const next = (i + dir + MODES.length) % MODES.length
    select(MODES[next])
    const el = document.querySelector<HTMLButtonElement>(`[data-mode="${MODES[next]}"]`)
    el?.focus()
  }
</script>

<header>
  <span class="logo">SO-101</span>

  <RigStatus />

  <div class="tabs" role="tablist" aria-label="Mode">
    {#each MODES as m, i}
      <button
        class="tab"
        role="tab"
        data-mode={m}
        aria-selected={$mode === m}
        title={MODE_HINTS[m]}
        tabindex={$mode === m ? 0 : -1}
        on:click={() => select(m)}
        on:keydown={(e) => onTabKey(e, i)}
      >{MODE_LABELS[m]}</button>
    {/each}
  </div>

  {#if !$wsConnected}
    <span class="offline" title="No WebSocket connection to the backend">backend offline</span>
  {/if}

  <EStop />

  <button class="gear" on:click={() => overlay.set('settings')} title="Settings" aria-label="Settings">⚙</button>
</header>

<style>
  header {
    display: flex; align-items: center; gap: 18px;
    padding: 0 12px 0 15px; height: 46px;
    background: #14181c; border-bottom: 1px solid #262d34;
    flex-shrink: 0;
  }

  .logo { font-weight: 700; font-size: 14px; color: #fff; letter-spacing: 0.07em; }

  .tabs {
    display: flex; gap: 2px; margin-left: auto;
    background: #0d1013; padding: 3px; border-radius: 6px;
    border: 1px solid #1d2329;
  }

  .tab {
    font-family: inherit; font-size: 12.5px;
    color: #7c8892; background: none; border: none; cursor: pointer;
    padding: 5px 15px; border-radius: 4px;
  }
  .tab:hover { color: #dfe6ec; }
  .tab[aria-selected="true"] {
    background: #191e23; color: #45c3d1;
    box-shadow: inset 0 0 0 1px #262d34;
  }
  .tab:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .offline { font-size: 11px; color: #d8a442; white-space: nowrap; }

  .gear {
    background: none; border: none; color: #55606a; cursor: pointer;
    font-size: 16px; padding: 4px 6px; border-radius: 4px; line-height: 1;
  }
  .gear:hover { color: #dfe6ec; }
  .gear:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  @media (max-width: 900px) {
    header { gap: 10px; padding: 0 8px; }
    .logo { display: none; }
    .tab { padding: 5px 10px; font-size: 12px; }
  }
</style>
