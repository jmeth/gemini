<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { isConnected } from '../../stores/robot'
  import {
    takes, recorderStatus, recordNote, recPost, deleteTake, loadTakes, startRecPolling, stopRecPolling,
  } from '../../stores/recordings'
  import { renameTake } from '../../stores/directives'

  onMount(startRecPolling)
  onDestroy(stopRecPolling)

  let name = ''
  let source: 'hand' | 'leader' = 'hand'
  let speed = 1
  let busy = false
  let confirmId: string | null = null
  let editId: string | null = null
  let editName = ''

  $: st = $recorderStatus
  $: canRecord = $isConnected && !st.playing

  async function toggleRecord() {
    busy = true
    if (st.recording) await recPost('/recordings/stop')
    else if (await recPost('/recordings/start', { name, teleop: source === 'leader' })) name = ''
    busy = false
  }
  const play = (id: string) => recPost(`/recordings/${encodeURIComponent(id)}/play`, { speed })
  const stop = () => recPost('/recordings/stop-play')

  async function remove(id: string) {
    if (confirmId !== id) { confirmId = id; return }
    confirmId = null
    await deleteTake(id)
  }
  async function commitRename(id: string) {
    const n = editName.trim()
    editId = null
    if (n) { await renameTake(id, n); await loadTakes() }
  }
  const ago = (iso: string | null) => {
    if (!iso) return ''
    const s = (Date.now() - Date.parse(iso)) / 1000
    return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago`
         : s < 86400 ? `${Math.floor(s / 3600)} h ago` : new Date(iso).toLocaleDateString()
  }
</script>

<div class="head">
  <h2>Movements</h2>
  <p class="lede">
    Record the arm moving, name it, and replay it. Rules play movements as actions.
    The chips show which arms a take moves. Replaying a take drives <b>both</b> arms for its
    whole duration, so an arm that stayed still while recording is held at its recorded pose.
    Your left arm is the leader hardware, and your right arm is the follower.
  </p>
</div>

<div class="card">
  <span class="label">Record</span>
  <div class="row">
    <input class="text grow" placeholder="Name this take (e.g. wave)" bind:value={name} disabled={st.recording} />
    <div class="segs" role="group" aria-label="How to record">
      <button class:on={source === 'hand'} on:click={() => (source = 'hand')} disabled={st.recording}
        title="Move both arms by hand. The follower goes limp and both arms are captured.">By hand</button>
      <button class:on={source === 'leader'} on:click={() => (source = 'leader')} disabled={st.recording}
        title="Move the leader arm; the follower mirrors it.">Leader drives</button>
    </div>
    <button class="btn pri" on:click={toggleRecord} disabled={busy || (!st.recording && !canRecord)}>
      {st.recording ? `■ Stop · ${st.elapsed_s}s` : '● Record'}
    </button>
  </div>
  {#if !$isConnected}<span class="small">Connect the follower arm to record.</span>{/if}
  {#if st.error}<span class="chip warn">{st.error}</span>{/if}
  {#if $recordNote}<p class="small bad">{$recordNote}</p>{/if}
</div>

<div class="row between">
  <span class="label">Takes</span>
  <label class="row small">Replay speed
    <input class="text num" type="number" min="0.1" max="4" step="0.1" bind:value={speed} />×
  </label>
</div>

{#if $takes.length === 0}
  <p class="empty">No takes yet. Record one above — a short wave is a good first test.</p>
{:else}
  <ul class="list">
    {#each $takes as t (t.id)}
      <li class:playing={st.play_id === t.id}>
        <div class="grow col tight">
          {#if editId === t.id}
            <input class="text" bind:value={editName}
              on:keydown={(e) => { if (e.key === 'Enter') commitRename(t.id); if (e.key === 'Escape') editId = null }} />
          {:else}
            <span class="name">{t.name}</span>
          {/if}
          <span class="small">
            {t.duration_s}s · {ago(t.created_at)}
            {#each t.moves ?? [] as s}<span class="chip">{s} arm</span>{/each}
            {#if t.moves && t.moves.length === 0}<span class="chip off" title="No arm travelled far enough to count as moving">no motion</span>{/if}
          </span>
        </div>
        {#if st.play_id === t.id}
          <button class="btn sm" on:click={stop}>■ Stop {Math.round(st.play_progress * 100)}%</button>
        {:else}
          <button class="btn sm pri" on:click={() => play(t.id)} disabled={!$isConnected || st.recording || st.playing}>▶ Play</button>
        {/if}
        {#if editId === t.id}
          <button class="btn sm" on:click={() => commitRename(t.id)}>Save</button>
        {:else}
          <button class="btn sm" on:click={() => { editId = t.id; editName = t.name }} title="Rename">✎</button>
        {/if}
        <button class="btn sm danger" class:armed={confirmId === t.id} on:click={() => remove(t.id)}
          on:mouseleave={() => confirmId === t.id && (confirmId = null)}>{confirmId === t.id ? 'Sure?' : '×'}</button>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .between { justify-content: space-between; }
  .tight { gap: 3px; }
  .name { font-size: 13.5px; color: #dfe6ec; }
  .bad { color: #e2564a; }
  .segs { display: flex; gap: 4px; }
  .segs button {
    font-family: inherit; font-size: 12px; color: #7c8892; background: #0d1013;
    border: 1px solid #262d34; border-radius: 4px; padding: 6px 11px; cursor: pointer;
  }
  .segs button:hover:not(:disabled) { color: #dfe6ec; }
  .segs button.on { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .segs button:disabled { opacity: 0.5; cursor: default; }
</style>
