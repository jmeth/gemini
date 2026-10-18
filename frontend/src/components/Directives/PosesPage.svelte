<script lang="ts">
  import { isConnected } from '../../stores/robot'
  import {
    poses, arms, SIDES, capturePose, updatePose, deletePose, goToPose, type Pose, type Side,
  } from '../../stores/directives'

  let name = ''
  let duration = 1.5
  let busy = false
  let confirmId: number | null = null
  let editId: number | null = null
  let editName = ''
  let openId: number | null = null
  let note: string | null = null

  $: side = $arms?.side ?? 'right'

  async function capture() {
    busy = true
    const ok = await capturePose(name.trim() || `Pose ${$poses.length + 1}`)
    if (ok) { name = ''; note = `Saved the ${side} arm's position` }
    busy = false
  }
  async function addArm(p: Pose) { busy = true; await capturePose('', p.id); busy = false }
  async function go(p: Pose) {
    busy = true
    note = (await goToPose(p.id, duration)) ? `Moving to “${p.name}”` : null
    busy = false
  }
  async function remove(id: number) {
    if (confirmId !== id) { confirmId = id; return }
    confirmId = null
    await deletePose(id)
  }
  async function commitRename(id: number) {
    const n = editName.trim(); editId = null
    if (n) await updatePose(id, { name: n })
  }
  const label = (p: Pose, s: Side) => (p.arms[s] ? `${s === 'left' ? 'L' : 'R'} ✓` : `${s === 'left' ? 'L' : 'R'} –`)
</script>

<div class="head">
  <h2>Poses</h2>
  <p class="lede">
    A pose is a named arm position: “home”, “arms up”, “ready to grab”. Move the arm where you
    want it, capture it, then use it as a Rules action. A pose holds values for each arm, so one
    name can set up both sides.
  </p>
</div>

<div class="card">
  <span class="label">Capture the {side} arm's current position</span>
  <div class="row">
    <input class="text grow" placeholder="Name (e.g. arms up)" bind:value={name}
      on:keydown={(e) => e.key === 'Enter' && $isConnected && capture()} />
    <button class="btn pri" on:click={capture} disabled={!$isConnected || busy}>Capture pose</button>
  </div>
  <span class="small">
    {#if !$isConnected}Connect the follower arm to capture.
    {:else}Tip: release the arm on the Manual tab, pose it by hand, then capture.{/if}
  </span>
</div>

<div class="row between">
  <span class="label">Saved poses</span>
  <label class="row small">Move time
    <input class="text num" type="number" min="0.1" max="30" step="0.1" bind:value={duration} />s
  </label>
</div>
{#if note}<p class="small">{note}</p>{/if}

{#if $poses.length === 0}
  <p class="empty">No poses yet. Capture one above.</p>
{:else}
  <ul class="list">
    {#each $poses as p (p.id)}
      <li class="stack">
        <div class="row full">
          <div class="grow col tight">
            {#if editId === p.id}
              <input class="text" bind:value={editName}
                on:keydown={(e) => { if (e.key === 'Enter') commitRename(p.id); if (e.key === 'Escape') editId = null }} />
            {:else}
              <span class="name">{p.name}</span>
            {/if}
            <span class="row small">
              {#each SIDES as s}<span class="chip" class:off={!p.arms[s]}>{label(p, s)}</span>{/each}
              <button class="link" on:click={() => (openId = openId === p.id ? null : p.id)}>
                {openId === p.id ? 'hide values' : 'show values'}
              </button>
            </span>
          </div>
          {#if !p.arms[side]}
            <button class="btn sm" on:click={() => addArm(p)} disabled={!$isConnected || busy}
              title="Save the {side} arm's current position into this pose">+ {side} arm</button>
          {/if}
          <button class="btn sm pri" on:click={() => go(p)} disabled={!$isConnected || busy || !p.arms[side]}
            title={p.arms[side] ? 'Move the connected arm to this pose' : `No ${side} arm values in this pose`}>Go</button>
          {#if editId === p.id}
            <button class="btn sm" on:click={() => commitRename(p.id)}>Save</button>
          {:else}
            <button class="btn sm" on:click={() => { editId = p.id; editName = p.name }} title="Rename">✎</button>
          {/if}
          <button class="btn sm danger" class:armed={confirmId === p.id} on:click={() => remove(p.id)}
            on:mouseleave={() => confirmId === p.id && (confirmId = null)}>{confirmId === p.id ? 'Sure?' : '×'}</button>
        </div>
        {#if openId === p.id}
          <div class="vals">
            {#each SIDES as s}
              {#if p.arms[s]}
                <div><span class="label">{s}</span>
                  <span class="mono">{Object.entries(p.arms[s] ?? {}).map(([j, v]) => `${j.replace('_', ' ')} ${v}°`).join('  ·  ')}</span>
                </div>
              {/if}
            {/each}
          </div>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<style>
  .between { justify-content: space-between; }
  .tight { gap: 3px; }
  .name { font-size: 13.5px; color: #dfe6ec; }
  .stack { flex-direction: column; align-items: stretch !important; gap: 8px !important; }
  .full { flex-wrap: nowrap; }
  .vals { display: flex; flex-direction: column; gap: 5px; padding: 8px 10px; background: #0d1013; border-radius: 4px; }
  .vals div { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .link { background: none; border: none; padding: 0; color: #45c3d1; font-family: inherit; font-size: 11.5px; cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
</style>
