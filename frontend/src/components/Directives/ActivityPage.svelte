<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { events, startEventPolling, stopEventPolling } from '../../stores/directives'

  onMount(startEventPolling)
  onDestroy(stopEventPolling)

  let hideInputs = false
  $: shown = [...$events].filter(e => !(hideInputs && e.kind === 'trigger')).reverse()
  const time = (t: number) => new Date(t * 1000).toLocaleTimeString()
  const KIND: Record<string, string> = {
    trigger: 'input', match: 'match', rule: 'rule', action: 'action', skip: 'skipped', error: 'error',
  }
</script>

<div class="head">
  <h2>Activity</h2>
  <p class="lede">
    What came in, which rules it set off, what each one did, and what was skipped and why
    (busy arm, cooldown, an arm that isn't connected). Kept in memory — it clears when the backend restarts.
  </p>
</div>

<div class="row">
  <label class="small row"><input type="checkbox" bind:checked={hideInputs} /> hide raw inputs</label>
  <button class="btn sm" on:click={() => events.set([])}>Clear view</button>
</div>

{#if shown.length === 0}
  <p class="empty">Nothing has happened yet.</p>
{:else}
  <ul class="list tight">
    {#each shown as e (e.seq)}
      <li class={e.kind}>
        <span class="mono">{time(e.t)}</span>
        <span class="chip" class:warn={e.kind === 'skip'} class:err={e.kind === 'error'} class:amber={e.kind === 'match' || e.kind === 'trigger'}>{KIND[e.kind] ?? e.kind}</span>
        <span class="grow">{e.text}</span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .tight > li { padding: 6px 11px; font-size: 12.5px; color: #b6c0c8; }
  li.error { border-color: #4a231e !important; background: #1d1211 !important; }
  .chip.err { color: #e2564a; background: #2c1715; border-color: #4a231e; }
</style>
