<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { arcade, buttonLabel, startArcadePolling, stopArcadePolling } from '../../stores/arcade'
  import { detections } from '../../stores/detection'
  import { heard } from '../../stores/speech'
  import {
    events, rules, fireTest, startEventPolling, stopEventPolling, loadRules, buttonName,
  } from '../../stores/directives'

  onMount(() => { startArcadePolling(); startEventPolling(); loadRules() })
  onDestroy(() => { stopArcadePolling(); stopEventPolling() })

  let button = 1
  let phrase = ''
  let label = 'scissors'
  let objEvent: 'appears' | 'disappears' = 'appears'
  let result: string | null = null

  $: inView = $detections?.objects ?? []
  $: recent = [...$events].filter(e => ['trigger', 'match', 'skip'].includes(e.kind)).slice(-30).reverse()
  const time = (t: number) => new Date(t * 1000).toLocaleTimeString()

  async function fire(body: Record<string, unknown>) {
    const d = await fireTest(body)
    if (!d) { result = null; return }
    const names = d.matched.map(id => $rules.find(r => r.id === id)?.name ?? `#${id}`)
    result = names.length ? `Triggered: ${names.join(', ')}` : 'No rule listens for that'
  }
</script>

<div class="head">
  <h2>Test bench</h2>
  <p class="lede">
    See what the robot is sensing, and fire any input by hand to check a rule without
    waiting for the real thing. A test fire does exactly what the real input would — an
    enabled rule that matches will run its actions, so the arm can move.
  </p>
</div>

<div class="card">
  <span class="label">Live inputs</span>
  <div class="grid">
    <div class="col">
      <span class="small dim">Arcade</span>
      {#if !$arcade.connected}<span class="chip warn">encoder not connected</span>
      {:else}
        <span class="chip off">{$arcade.device ?? 'connected'}</span>
        <span class="small">last press: {$arcade.last_button !== null ? buttonLabel($arcade.last_button) : '—'}</span>
      {/if}
    </div>
    <div class="col">
      <span class="small dim">Voice (browser mic)</span>
      <span class="small">{$heard ? `“${$heard}”` : 'nothing heard yet'}</span>
    </div>
    <div class="col">
      <span class="small dim">Camera sees</span>
      {#if inView.length === 0}<span class="small">nothing</span>
      {:else}<div class="row">{#each inView as o}<span class="chip">{o.label}{o.conf ? ` ${Math.round(o.conf * 100)}%` : ''}</span>{/each}</div>{/if}
    </div>
  </div>
</div>

<div class="card">
  <span class="label">Fire an input by hand</span>
  <div class="row">
    <span class="w">Button</span>
    <input class="text num" type="number" min="1" max="12" bind:value={button} />
    <button class="btn sm" on:click={() => fire({ type: 'button', button: button - 1 })}>Press</button>
  </div>
  <div class="row">
    <span class="w">Voice</span>
    <input class="text grow" placeholder="Say something…" bind:value={phrase}
      on:keydown={(e) => e.key === 'Enter' && phrase && fire({ type: 'voice', text: phrase })} />
    <button class="btn sm" on:click={() => fire({ type: 'voice', text: phrase })} disabled={!phrase}>Say it</button>
  </div>
  <div class="row">
    <span class="w">Object</span>
    <input class="text" bind:value={label} />
    <select class="sel" bind:value={objEvent}><option value="appears">appears</option><option value="disappears">disappears</option></select>
    <button class="btn sm" on:click={() => fire({ type: 'object', label, event: objEvent })} disabled={!label}>Fire</button>
  </div>
  <div class="row">
    <span class="w">Startup</span>
    <button class="btn sm" on:click={() => fire({ type: 'startup' })}>Fire startup</button>
  </div>
  {#if result}<span class="small res">{result}</span>{/if}
  <span class="small dim">Timers fire themselves on their schedule; use “▶ Run” on the Rules page to run one now.</span>
</div>

<span class="label">Recent inputs</span>
{#if recent.length === 0}
  <p class="empty">Nothing yet. Press an arcade button, speak, or fire an input above.</p>
{:else}
  <ul class="list tight">
    {#each recent as e (e.seq)}
      <li>
        <span class="mono">{time(e.t)}</span>
        <span class="chip" class:warn={e.kind === 'skip'} class:amber={e.kind === 'match'}>{e.kind === 'trigger' ? 'input' : e.kind}</span>
        <span class="grow">{e.text}</span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; }
  .w { width: 58px; font-size: 12px; color: #7c8892; }
  .res { color: #9fe1ea; }
  .tight > li { padding: 6px 11px; font-size: 12.5px; color: #b6c0c8; }
</style>
