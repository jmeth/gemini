<script lang="ts">
  import {
    sounds, arms, uploadSound, renameSound, deleteSound, playSound, stopSounds, dirNote,
  } from '../../stores/directives'

  let files: FileList | null = null
  let name = ''
  let busy = false
  let confirmId: number | null = null
  let editId: number | null = null
  let editName = ''
  let note: string | null = null
  const volumes: Record<number, number> = {}

  async function upload() {
    const f = files?.[0]
    if (!f) return
    busy = true
    const ok = await uploadSound(f, name.trim())
    if (ok) { name = ''; files = null; note = `Added “${(ok as any).name}”` }
    busy = false
  }
  async function playOnRobot(id: number) {
    note = (await playSound(id, volumes[id] ?? 1)) ? 'Playing on the robot' : null
  }
  async function remove(id: number) {
    if (confirmId !== id) { confirmId = id; return }
    confirmId = null
    await deleteSound(id)
  }
  async function commitRename(id: number) {
    const n = editName.trim(); editId = null
    if (n) await renameSound(id, n)
  }
  const size = (b: number) => b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`
</script>

<div class="head">
  <h2>Sounds</h2>
  <p class="lede">
    Upload audio clips (wav, mp3, ogg, m4a, aiff, flac — up to 25 MB). “Play on robot” uses the
    speaker on the machine running the backend, so sounds work from buttons, timers and the
    camera even with no browser open. The player below in each row only previews in this page.
  </p>
</div>

{#if $arms && !$arms.audio_player}
  <p class="warnbox">
    The robot's host has no audio player. Install <span class="mono">ffmpeg</span> (gives
    <span class="mono">ffplay</span>), or on a Pi <span class="mono">alsa-utils</span> or
    <span class="mono">mpg123</span>. Previews in this page still work.
  </p>
{:else if $arms?.audio_player}
  <span class="small">Robot speaker via <span class="mono">{$arms.audio_player}</span>.</span>
{/if}

<div class="card">
  <span class="label">Add a sound</span>
  <div class="row">
    <input class="grow file" type="file" accept="audio/*,.wav,.mp3,.ogg,.m4a,.aac,.aiff,.aif,.flac" bind:files />
    <input class="text" placeholder="Name (optional)" bind:value={name} />
    <button class="btn pri" on:click={upload} disabled={busy || !files?.length}>{busy ? 'Uploading…' : 'Upload'}</button>
  </div>
  {#if note}<span class="small">{note}</span>{/if}
</div>

<div class="row between">
  <span class="label">Library</span>
  <button class="btn sm" on:click={stopSounds}>■ Stop all on robot</button>
</div>

{#if $sounds.length === 0}
  <p class="empty">No sounds yet. Upload a clip above.</p>
{:else}
  <ul class="list">
    {#each $sounds as s (s.id)}
      <li>
        <div class="grow col tight">
          {#if editId === s.id}
            <input class="text" bind:value={editName}
              on:keydown={(e) => { if (e.key === 'Enter') commitRename(s.id); if (e.key === 'Escape') editId = null }} />
          {:else}
            <span class="name">{s.name}</span>
          {/if}
          <span class="small">{s.duration_s ? `${s.duration_s}s · ` : ''}{size(s.size)} · {s.filename.split('.').pop()}</span>
          <!-- svelte-ignore a11y-media-has-caption -->
          <audio controls preload="none" src={`/sounds/${s.id}/file`}></audio>
        </div>
        <label class="col small vol">Volume
          <input type="range" min="0" max="1" step="0.05" bind:value={volumes[s.id]} on:focus={() => (volumes[s.id] ??= 1)} />
        </label>
        <button class="btn sm pri" on:click={() => playOnRobot(s.id)}>▶ Play on robot</button>
        {#if editId === s.id}
          <button class="btn sm" on:click={() => commitRename(s.id)}>Save</button>
        {:else}
          <button class="btn sm" on:click={() => { editId = s.id; editName = s.name }} title="Rename">✎</button>
        {/if}
        <button class="btn sm danger" class:armed={confirmId === s.id} on:click={() => remove(s.id)}
          on:mouseleave={() => confirmId === s.id && (confirmId = null)}>{confirmId === s.id ? 'Sure?' : '×'}</button>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .between { justify-content: space-between; }
  .tight { gap: 4px; }
  .name { font-size: 13.5px; color: #dfe6ec; }
  .file { font-family: inherit; font-size: 12px; color: #9aa7b2; }
  .vol { width: 96px; gap: 2px; }
  .warnbox { font-size: 12px; line-height: 1.55; color: #b89a5a; background: #2a2113; border: 1px solid #4a3a1c; border-radius: 4px; padding: 9px 12px; }
  audio { height: 30px; max-width: 320px; }
</style>
