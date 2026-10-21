<script lang="ts">
  import { isConnected } from '../../stores/robot'
  import {
    poses, arms, SIDES, capturePose, updatePose, deletePose, goToPose, setLeaderTorque, type Pose, type Side,
  } from '../../stores/directives'

  let name = ''
  let duration = 1.5
  let busy = false
  let confirmId: number | null = null
  let editId: number | null = null
  let editName = ''
  let openId: number | null = null
  let note: string | null = null

  // Which side each physical port stands for: the follower port is `side`, the
  // leader port is the other one.
  $: fSide = ($arms?.side ?? 'right') as Side
  const portName = (s: Side) => (s === fSide ? 'follower port' : 'leader port')
  const isUp = (s: Side) => (s === fSide ? $arms?.follower_connected : $arms?.leader_connected) ?? false

  let pick: Record<Side, boolean> = { left: true, right: true }
  $: toCapture = SIDES.filter(s => pick[s] && isUp(s))
  $: anyUp = SIDES.some(isUp)

  async function capture() {
    busy = true
    const d = await capturePose(name.trim() || `Pose ${$poses.length + 1}`, undefined, toCapture)
    if (d) { name = ''; note = `Saved the ${d.captured.join(' and ')} arm${d.captured.length > 1 ? 's' : ''}` }
    busy = false
  }
  async function addArm(p: Pose, s: Side) {
    busy = true
    const d = await capturePose('', p.id, [s])
    if (d) note = `Added the ${s} arm to “${p.name}”`
    busy = false
  }
  // The arms a pose would move right now: connected, and the pose has values for them.
  const movers = (p: Pose) => SIDES.filter(s => p.arms[s] && isUp(s))
  $: lSide = ($arms?.leader_side ?? 'left') as Side

  async function go(p: Pose) {
    busy = true
    const d = await goToPose(p.id, duration)
    note = d ? `Moving the ${d.moved.join(' and ')} arm${d.moved.length > 1 ? 's' : ''} to “${p.name}”` : null
    busy = false
  }
  async function releaseLeader() {
    busy = true
    if (await setLeaderTorque(false)) note = `The ${lSide} arm is limp again — move it by hand`
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
  <span class="label">Capture the arms' current positions</span>
  <div class="row picks">
    {#each SIDES as s}
      <label class="pick" class:down={!isUp(s)}>
        <input type="checkbox" bind:checked={pick[s]} disabled={!isUp(s)} />
        <span class="cap">{s === 'left' ? 'Left' : 'Right'} arm</span>
        <span class="chip" class:off={!isUp(s)}>{portName(s)} · {isUp(s) ? 'connected' : 'not connected'}</span>
      </label>
    {/each}
  </div>
  <div class="row">
    <input class="text grow" placeholder="Name (e.g. arms up)" bind:value={name}
      on:keydown={(e) => e.key === 'Enter' && toCapture.length && capture()} />
    <button class="btn pri" on:click={capture} disabled={!toCapture.length || busy}>
      Capture {toCapture.length === 2 ? 'both arms' : toCapture.length === 1 ? `${toCapture[0]} arm` : 'pose'}
    </button>
  </div>
  <span class="small">
    {#if !anyUp}Connect an arm to capture.
    {:else}The follower port is the <b>{fSide}</b> arm and the leader port is the <b>{fSide === 'left' ? 'right' : 'left'}</b> arm
      — change this with “Follower port is” in the sidebar. Release an arm on the Manual tab to pose it by hand.{/if}
  </span>
</div>

{#if $arms?.leader_powered}
  <div class="holding" role="status">
    <span>The <b>{lSide}</b> arm is powered and holding its pose. It stays that way until you release it,
      start teleop, or record by hand.</span>
    <button class="btn sm" on:click={releaseLeader} disabled={busy}>Release {lSide} arm</button>
  </div>
{/if}

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
          {#each SIDES as s}
            {#if !p.arms[s] && isUp(s)}
              <button class="btn sm" on:click={() => addArm(p, s)} disabled={busy}
                title="Save the {s} arm's current position into this pose">+ {s} arm</button>
            {/if}
          {/each}
          <button class="btn sm pri" on:click={() => go(p)} disabled={busy || movers(p).length === 0}
            title={movers(p).length ? `Move the ${movers(p).join(' and ')} arm${movers(p).length > 1 ? 's' : ''} to this pose`
                                    : 'No connected arm has values in this pose'}>
            Go{movers(p).length === 2 ? ' · both' : ''}</button>
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
  .holding {
    display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    font-size: 12px; line-height: 1.5; color: #b89a5a; background: #2a2113;
    border: 1px solid #4a3a1c; border-radius: 6px; padding: 9px 12px;
  }
  .picks { gap: 18px; }
  .pick { display: flex; align-items: center; gap: 8px; cursor: pointer; }
  .pick.down { opacity: 0.6; cursor: default; }
  .cap { font-size: 13px; color: #dfe6ec; }
  .link { background: none; border: none; padding: 0; color: #45c3d1; font-family: inherit; font-size: 11.5px; cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
</style>
