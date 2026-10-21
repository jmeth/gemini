<script lang="ts">
  import { onMount } from 'svelte'
  import {
    arms, loadArms, setSide, dirNote, SIDES,
    loadPoses, loadSounds, loadRules, rules, poses, sounds,
  } from '../../stores/directives'
  import { takes, loadTakes } from '../../stores/recordings'
  import MovementsPage from './MovementsPage.svelte'
  import PosesPage from './PosesPage.svelte'
  import SoundsPage from './SoundsPage.svelte'
  import RulesPage from './RulesPage.svelte'
  import TestBenchPage from './TestBenchPage.svelte'
  import ActivityPage from './ActivityPage.svelte'

  const PAGES = [
    { id: 'movements', label: 'Movements', hint: 'Record and replay motion' },
    { id: 'poses',     label: 'Poses',     hint: 'Named arm positions' },
    { id: 'sounds',    label: 'Sounds',    hint: 'Audio the robot can play' },
    { id: 'rules',     label: 'Rules',     hint: 'When this happens, do that' },
    { id: 'bench',     label: 'Test bench', hint: 'Watch inputs, fire them by hand' },
    { id: 'activity',  label: 'Activity',  hint: 'What fired, and what it did' },
  ] as const
  type Page = typeof PAGES[number]['id']

  const KEY = 'so101.directives.page'
  function initial(): Page {
    try {
      const v = localStorage.getItem(KEY) as Page | null
      if (v && PAGES.some(p => p.id === v)) return v
    } catch { /* private mode */ }
    return 'rules'
  }
  let page: Page = initial()
  function go(p: Page) {
    page = p
    try { localStorage.setItem(KEY, p) } catch { /* private mode */ }
  }

  onMount(() => { loadArms(); loadPoses(); loadSounds(); loadRules(); loadTakes() })

  $: counts = { movements: $takes.length, poses: $poses.length, sounds: $sounds.length, rules: $rules.length } as Record<string, number>
</script>

<div class="dv">
  <nav class="side" aria-label="Directives sections">
    <div class="brand">Directives</div>
    {#each PAGES as p}
      <button class="nav" class:on={page === p.id} aria-current={page === p.id ? 'page' : undefined}
        title={p.hint} on:click={() => go(p.id)}>
        <span>{p.label}</span>
        {#if counts[p.id] !== undefined}<span class="n">{counts[p.id]}</span>{/if}
      </button>
    {/each}

    <div class="arm">
      <span class="armlabel">Follower port is the</span>
      <div class="seg" role="group" aria-label="Which side the follower port is">
        {#each SIDES as s}
          <button class:on={$arms?.side === s} on:click={() => setSide(s)}
            title="The arm on the follower port is WALL·E's {s} arm">{s === 'left' ? 'Left' : 'Right'}</button>
        {/each}
      </div>
      <span class="armnote">
        …so the leader port is the {$arms?.leader_side ?? 'other'} arm.
        Poses capture both; rules can target either or both.
      </span>
    </div>
  </nav>

  <section class="main">
    {#if $dirNote}<p class="err" role="alert">{$dirNote}</p>{/if}
    {#if page === 'movements'}<MovementsPage />
    {:else if page === 'poses'}<PosesPage />
    {:else if page === 'sounds'}<SoundsPage />
    {:else if page === 'rules'}<RulesPage />
    {:else if page === 'bench'}<TestBenchPage />
    {:else}<ActivityPage />{/if}
  </section>
</div>

<style>
  .dv { flex: 1; display: flex; min-height: 0; min-width: 0; background: #0f1215; }

  .side {
    flex: 0 0 188px; display: flex; flex-direction: column; gap: 3px;
    padding: 14px 10px; background: #14181c; border-right: 1px solid #262d34; overflow-y: auto;
  }
  .brand { font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase; color: #55606a; padding: 0 8px 8px; }
  .nav {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    font-family: inherit; font-size: 12.5px; color: #7c8892; background: none;
    border: none; border-radius: 5px; padding: 8px 10px; cursor: pointer; text-align: left;
  }
  .nav:hover { color: #dfe6ec; background: #191e23; }
  .nav.on { color: #45c3d1; background: #191e23; box-shadow: inset 0 0 0 1px #262d34; }
  .nav:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .n { font-size: 10.5px; color: #55606a; }
  .nav.on .n { color: #2f8a95; }

  .arm { margin-top: auto; display: flex; flex-direction: column; gap: 6px; padding: 14px 8px 0; border-top: 1px solid #1d2329; }
  .armlabel { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #55606a; }
  .armnote { font-size: 10.5px; line-height: 1.45; color: #55606a; }
  .seg { display: flex; gap: 4px; }
  .seg button {
    flex: 1; font-family: inherit; font-size: 12px; color: #7c8892; background: #0d1013;
    border: 1px solid #262d34; border-radius: 4px; padding: 5px 0; cursor: pointer;
  }
  .seg button:hover { color: #dfe6ec; }
  .seg button.on { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .seg button:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .main { flex: 1; min-width: 0; overflow-y: auto; padding: 22px 26px 40px; display: flex; flex-direction: column; gap: 14px; }

  .err {
    font-size: 12px; line-height: 1.45; color: #e2564a; background: #2c1715;
    border: 1px solid #4a231e; border-radius: 4px; padding: 8px 11px;
  }

  /* Shared by every Directives page. Scoped under .dv so nothing leaks out. */
  :global(.dv h2) { font-size: 16px; font-weight: 600; color: #dfe6ec; }
  :global(.dv .lede) { font-size: 12.5px; line-height: 1.6; color: #7c8892; max-width: 70ch; }
  :global(.dv .head) { display: flex; flex-direction: column; gap: 5px; }
  :global(.dv .card) { background: #14181c; border: 1px solid #232a30; border-radius: 7px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  :global(.dv .row) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  :global(.dv .grow) { flex: 1; min-width: 0; }
  :global(.dv .col) { display: flex; flex-direction: column; gap: 8px; }
  :global(.dv .label) { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #55606a; }
  :global(.dv .dim) { color: #55606a; }
  :global(.dv .small) { font-size: 11.5px; line-height: 1.5; color: #7c8892; }
  :global(.dv .mono) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: #7c8892; }
  :global(.dv .empty) { font-size: 12.5px; line-height: 1.6; color: #55606a; padding: 18px; border: 1px dashed #262d34; border-radius: 7px; }

  :global(.dv .btn) {
    font-family: inherit; font-size: 12px; background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 6px 12px; cursor: pointer; white-space: nowrap;
  }
  :global(.dv .btn:hover:not(:disabled)) { background: #262d34; }
  :global(.dv .btn:disabled) { opacity: 0.4; cursor: default; }
  :global(.dv .btn.pri) { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  :global(.dv .btn.pri:hover:not(:disabled)) { background: #184048; }
  :global(.dv .btn.danger) { color: #7c8892; }
  :global(.dv .btn.danger:hover:not(:disabled)), :global(.dv .btn.armed) { color: #e2564a; border-color: #4a231e; background: #2c1715; }
  :global(.dv .btn.sm) { padding: 3px 9px; font-size: 11.5px; }
  :global(.dv .btn:focus-visible), :global(.dv .text:focus-visible), :global(.dv .sel:focus-visible),
  :global(.dv .tog:focus-visible) { outline: 2px solid #45c3d1; outline-offset: 1px; }

  :global(.dv .text), :global(.dv .sel) {
    font-family: inherit; font-size: 12.5px; color: #dfe6ec; background: #0d1013;
    border: 1px solid #2c343b; border-radius: 4px; padding: 6px 9px; min-width: 0;
  }
  :global(.dv .text.num) { width: 84px; }
  :global(.dv .text:disabled) { opacity: 0.5; }

  :global(.dv .chip) {
    font-size: 10.5px; color: #9fe1ea; background: #13282c; border: 1px solid #1f4248;
    border-radius: 3px; padding: 1px 6px; white-space: nowrap;
  }
  :global(.dv .chip.off) { color: #55606a; background: none; border-color: #262d34; }
  :global(.dv .chip.warn) { color: #d8a442; background: #2a2113; border-color: #4a3a1c; }
  :global(.dv .chip.amber) { color: #f0d9a0; background: #2a2412; border-color: #4a3f1c; }

  :global(.dv .list) { list-style: none; display: flex; flex-direction: column; gap: 6px; }
  :global(.dv .list > li) {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 10px 13px; background: #14181c; border: 1px solid #232a30; border-radius: 6px;
  }
  :global(.dv .list > li.playing) { border-color: #1f5560; background: #0e1a1d; }
  :global(.dv .list > li.off) { opacity: 0.55; }

  :global(.dv .tog) {
    flex: none; width: 28px; height: 16px; border-radius: 8px; border: 1px solid #2c343b;
    background: #0d1013; position: relative; cursor: pointer; padding: 0;
  }
  :global(.dv .tog .knob) { position: absolute; top: 2px; left: 2px; width: 10px; height: 10px; border-radius: 50%; background: #55606a; transition: left 0.12s; }
  :global(.dv .tog.on) { background: #13343a; border-color: #1f5560; }
  :global(.dv .tog.on .knob) { left: 14px; background: #45c3d1; }

  @media (max-width: 800px) {
    .dv { flex-direction: column; }
    .side { flex: 0 0 auto; flex-direction: row; flex-wrap: wrap; border-right: none; border-bottom: 1px solid #262d34; }
    .brand, .arm { display: none; }
    .main { padding: 16px; }
  }
  @media (prefers-reduced-motion: reduce) { :global(.dv .tog .knob) { transition: none; } }
</style>
