<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { isConnected } from '../../stores/robot'
  import {
    takes, recorderStatus, recordNote,
    recPost, deleteTake, startRecPolling, stopRecPolling,
  } from '../../stores/recordings'
  import {
    commands, commandNote, loadCommands,
    createCommand, updateCommand, deleteCommand, setCommandEnabled,
  } from '../../stores/commands'
  import {
    speechSupported, listening, heard, lastAction, toggleListening, stopListening,
  } from '../../stores/speech'
  import {
    voiceState, voiceNote,
    startBackendVoice, stopBackendVoice,
    startVoicePolling, stopVoicePolling,
  } from '../../stores/voice'

  onMount(() => { startRecPolling(); loadCommands(); startVoicePolling() })
  onDestroy(() => { stopRecPolling(); stopVoicePolling() })

  // ---- Voice input source -------------------------------------------------
  // Source and mic device are configured in Settings; this workspace only
  // drives listening on/off for whichever source is active.
  $: vsource = $voiceState.source
  $: if (vsource === 'backend' && $listening) stopListening()  // release browser mic

  async function toggleBackendVoice() {
    if ($voiceState.running) await stopBackendVoice()
    else await startBackendVoice()
  }

  // ---- Record / playback ----------------------------------------------------
  let name = ''
  let speed = 1
  let busy = false
  let confirmId: string | null = null
  // 'leader'  → recording starts teleop mirroring; the leader drives the motion
  //             and the mirrored follower joints are sampled.
  // 'hand'    → follower torque is released and the arm is walked through by hand.
  let mode: 'leader' | 'hand' = 'leader'

  $: st = $recorderStatus
  $: pct = Math.round(st.play_progress * 100)
  $: canRecord = $isConnected && !st.playing
  $: canPlay = $isConnected && !st.recording && !st.playing

  async function toggleRecord() {
    busy = true
    if (st.recording) {
      await recPost('/recordings/stop')
    } else {
      const ok = await recPost('/recordings/start', { name, teleop: mode === 'leader' })
      if (ok) name = ''
    }
    busy = false
  }

  async function relax() {
    busy = true
    try {
      await fetch('/robot/torque/all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: false }),
      })
      recordNote.set('Torque released — move the arm by hand, then Record')
    } catch {
      recordNote.set('Could not reach the backend')
    }
    busy = false
  }

  const play = (id: string) => recPost(`/recordings/${encodeURIComponent(id)}/play`, { speed })
  const stopPlay = () => recPost('/recordings/stop-play')

  async function remove(id: string) {
    if (confirmId !== id) { confirmId = id; return }
    confirmId = null
    await deleteTake(id)
  }

  function ago(iso: string | null): string {
    if (!iso) return ''
    const s = (Date.now() - Date.parse(iso)) / 1000
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`
    return `${Math.floor(s / 86400)}d ago`
  }

  // ---- Commands ------------------------------------------------------------
  let editId: number | null = null
  let cName = ''
  let triggerText = ''
  let recordingId = ''
  let cBusy = false
  let cConfirm: number | null = null

  $: takeName = (id: string | null) =>
    id ? ($takes.find(t => t.id === id)?.name ?? id) : null

  function resetCmd() {
    editId = null; cName = ''; triggerText = ''; recordingId = ''
  }

  function editCmd(c: typeof $commands[number]) {
    editId = c.id
    cName = c.name
    triggerText = c.triggers.join(', ')
    recordingId = c.recording_id ?? ''
  }

  async function saveCmd() {
    const triggers = triggerText.split(',').map(s => s.trim()).filter(Boolean)
    if (!cName.trim() || triggers.length === 0) {
      commandNote.set('A command needs a name and at least one trigger phrase')
      return
    }
    cBusy = true
    const body = { name: cName.trim(), triggers, recording_id: recordingId || null }
    const ok = editId == null
      ? await createCommand(body)
      : await updateCommand(editId, body)
    cBusy = false
    if (ok) resetCmd()
  }

  async function removeCmd(id: number) {
    if (cConfirm !== id) { cConfirm = id; return }
    cConfirm = null
    if (editId === id) resetCmd()
    await deleteCommand(id)
  }
</script>

<div class="ws">
  <!-- ── Record ─────────────────────────────────────────────── -->
  <section class="col rec">
    <div class="head"><h2>Record</h2></div>

    <button
      class="record-btn" class:on={st.recording}
      on:click={toggleRecord}
      disabled={(!canRecord && !st.recording) || busy}
      title={st.recording ? 'Stop and save' : 'Start capturing at 10 Hz'}
    >
      <span class="dot"></span>
      {st.recording ? 'Stop & save' : 'Record'}
    </button>

    {#if st.recording}
      <div class="capture">
        <b class="big num">{st.elapsed_s.toFixed(1)}s</b>
        <span class="num">{st.frames} frames · 10 Hz</span>
      </div>
    {:else}
      <div class="seg" role="group" aria-label="Record source">
        <button class:on={mode === 'leader'} on:click={() => (mode = 'leader')} disabled={busy}>
          Leader (teleop)
        </button>
        <button class:on={mode === 'hand'} on:click={() => (mode = 'hand')} disabled={busy}>
          By hand
        </button>
      </div>

      <input
        class="text"
        placeholder="Take name"
        bind:value={name}
        disabled={busy}
        on:keydown={(e) => e.key === 'Enter' && canRecord && toggleRecord()}
      />

      {#if mode === 'leader'}
        <p class="hint dim">
          Recording starts leader→follower mirroring at full tracking speed
          (the limit is restored when you stop). Move the leader arm; the
          follower mirrors it and its joints are what gets sampled.
        </p>
      {:else}
        <button class="link" on:click={relax} disabled={!$isConnected || busy}>
          Release torque to demo by hand
        </button>
      {/if}
    {/if}

    <div class="rule"></div>

    <div class="sub">Playback</div>
    <div class="row"><span>Speed</span><b class="num">{speed.toFixed(2)}×</b></div>
    <input type="range" min="0.25" max="2" step="0.25" bind:value={speed} aria-label="Playback speed" />

    {#if st.playing}
      <div class="row">
        <span class="trunc">Playing{st.play_id ? ` — ${st.play_id}` : ''}</span>
        <button class="btn sm" on:click={stopPlay}>Stop</button>
      </div>
      <div class="bar"><i style="width:{pct}%"></i></div>
    {/if}

    <div class="rule"></div>
    <p class="armline" class:ok={$isConnected}>
      {$isConnected
        ? 'Playback stops mirroring and re-arms the follower'
        : 'Follower offline — connect to play back'}
    </p>

    {#if $recordNote || st.error}
      <p class="err" role="alert">{$recordNote ?? st.error}</p>
    {/if}
  </section>

  <!-- ── Takes ──────────────────────────────────────────────── -->
  <section class="col takes">
    <div class="head">
      <h2>Takes</h2>
      <span class="count">{$takes.length}</span>
    </div>

    {#if $takes.length === 0}
      <p class="empty">No takes yet. Name one, hit Record, and drive the motion from the leader arm (or switch to “By hand”).</p>
    {:else}
      <ul class="takelist">
        {#each $takes as t (t.id)}
          <li class:playing={st.playing && st.play_id === t.id}>
            <div class="tmeta">
              <span class="tname">{t.name}</span>
              <span class="tsub num">{t.duration_s.toFixed(1)}s · {t.frames}f · {ago(t.created_at)}</span>
              {#if st.playing && st.play_id === t.id}
                <div class="bar mini"><i style="width:{pct}%"></i></div>
              {/if}
            </div>
            <button
              class="play" on:click={() => play(t.id)}
              disabled={!canPlay}
              title="Play on the follower"
            >▶</button>
            <button
              class="del" class:armed={confirmId === t.id}
              on:click={() => remove(t.id)}
              on:mouseleave={() => confirmId === t.id && (confirmId = null)}
              title="Delete take"
            >{confirmId === t.id ? 'Sure?' : '×'}</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- ── Commands & voice ───────────────────────────────────── -->
  <section class="col cmd">
    <div class="head"><h2>Commands &amp; voice</h2></div>

    {#if vsource === 'backend'}
      <p class="hint dim">
        Input: Raspberry Pi microphone{$voiceState.mic ? ` (${$voiceState.mic})` : ''}
        — change it in Settings.
      </p>

      <button class="mic" class:on={$voiceState.running} on:click={toggleBackendVoice}>
        {$voiceState.running ? '● Listening (Pi)' : 'Start listening'}
      </button>
      {#if $voiceState.running && $voiceState.grammar}
        <p class="hint dim">
          Pinned to {$voiceState.grammar.length} command phrase{$voiceState.grammar.length === 1 ? '' : 's'}:
          <span class="said">{$voiceState.grammar.join(' · ')}</span>
        </p>
      {:else if $voiceState.running && $voiceState.grammar_enabled}
        <p class="hint dim">No enabled command phrases yet — recognising open vocabulary.</p>
      {/if}
      {#if $voiceState.last_text}
        <p class="hint">Heard “<span class="said">{$voiceState.last_text}</span>”</p>
      {/if}
      {#if $voiceState.last_action}
        <p class="hint act">{$voiceState.last_action}</p>
      {/if}
      {#if $voiceState.error || $voiceNote}
        <p class="err" role="alert">{$voiceNote ?? $voiceState.error}</p>
      {/if}
      <p class="hint dim">
        Recognition runs on the Pi (Vosk, offline) from a local ALSA mic — a USB
        mic or webcam works. A matched phrase plays its bound take on the
        follower — arm it first.
      </p>
    {:else if speechSupported}
      <button class="mic" class:on={$listening} on:click={toggleListening}>
        {$listening ? '● Listening' : 'Start listening'}
      </button>
      {#if $heard}
        <p class="hint">Heard “<span class="said">{$heard}</span>”</p>
      {/if}
      {#if $lastAction}
        <p class="hint act">{$lastAction}</p>
      {/if}
      <p class="hint dim">
        Recognition runs in the browser. A matched phrase plays its bound take on
        the follower — arm it first.
      </p>
    {:else}
      <p class="hint">
        Voice needs the Web Speech API (Chrome or Edge) — or switch the voice
        input source to the Raspberry Pi microphone in Settings. You can still
        bind takes to commands here and play them from the Takes list.
      </p>
    {/if}

    <div class="rule"></div>

    {#if $commands.length === 0}
      <p class="empty">No commands yet. Add one below and give it trigger phrases.</p>
    {:else}
      <ul class="cmdlist">
        {#each $commands as c (c.id)}
          <li class:editing={editId === c.id} class:off={!c.enabled}>
            <button
              class="toggle" class:on={c.enabled}
              role="switch" aria-checked={c.enabled}
              on:click={() => setCommandEnabled(c.id, !c.enabled)}
              title={c.enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}
            ><span class="knob"></span></button>
            <div class="cinfo">
              <span class="cname">{c.name}</span>
              <span class="chips">
                {#each c.triggers as t}<span class="chip">{t}</span>{/each}
              </span>
              <span class="bound">
                {#if c.recording_id}▶ {takeName(c.recording_id)}{:else}<i>no take bound</i>{/if}
              </span>
            </div>
            <button class="mini" on:click={() => editCmd(c)} title="Edit">✎</button>
            <button
              class="mini danger" class:armed={cConfirm === c.id}
              on:click={() => removeCmd(c.id)}
              on:mouseleave={() => cConfirm === c.id && (cConfirm = null)}
              title="Delete command"
            >{cConfirm === c.id ? 'Sure?' : '×'}</button>
          </li>
        {/each}
      </ul>
    {/if}

    <div class="rule"></div>

    <div class="sub">{editId == null ? 'New command' : 'Edit command'}</div>
    <input class="text" placeholder="Name (e.g. grab red block)" bind:value={cName} />
    <input class="text" placeholder="Trigger phrases, comma separated" bind:value={triggerText} />
    <select class="text" bind:value={recordingId}>
      <option value="">— no take —</option>
      {#each $takes as t (t.id)}
        <option value={t.id}>{t.name}</option>
      {/each}
    </select>
    <div class="frow">
      <button class="btn pri" on:click={saveCmd} disabled={cBusy}>
        {editId == null ? 'Add' : 'Save'}
      </button>
      {#if editId != null}
        <button class="btn" on:click={resetCmd} disabled={cBusy}>Cancel</button>
      {/if}
    </div>

    {#if $commandNote}
      <p class="err" role="alert">{$commandNote}</p>
    {/if}
  </section>
</div>

<style>
  .ws {
    flex: 1;
    display: grid; gap: 8px; padding: 7px;
    grid-template-columns: 290px minmax(0, 1fr) 340px;
    grid-template-rows: minmax(0, 1fr);
    grid-template-areas: "rec takes cmd";
    min-height: 0; min-width: 0; overflow: hidden;
  }
  .col.rec   { grid-area: rec; }
  .col.takes { grid-area: takes; }
  .col.cmd   { grid-area: cmd; }

  @media (max-width: 1180px) {
    .ws {
      grid-template-columns: 1fr 1fr;
      grid-template-rows: minmax(0, 1.35fr) minmax(0, 1fr);
      grid-template-areas: "takes takes" "rec cmd";
    }
  }
  @media (max-width: 720px) {
    .ws {
      grid-template-columns: 1fr;
      grid-template-rows: none;
      grid-auto-rows: min-content;
      grid-template-areas: "rec" "takes" "cmd";
      overflow-y: auto;
    }
  }

  .col {
    background: #111; border: 1px solid #1d2329; border-radius: 6px;
    padding: 12px; display: flex; flex-direction: column; gap: 10px;
    overflow-y: auto; min-height: 0; min-width: 0;
  }

  .head { display: flex; align-items: baseline; gap: 8px; }
  .head h2 {
    font-size: 11px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #7c8892; font-weight: 600;
  }
  .count {
    font-size: 10px; color: #55606a; font-variant-numeric: tabular-nums;
    background: #0d1013; border: 1px solid #1d2329; border-radius: 10px;
    padding: 1px 7px;
  }

  .sub {
    font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; font-weight: 600;
  }
  .rule { height: 1px; background: #1d2329; margin: 2px 0; }

  .num { font-variant-numeric: tabular-nums; }
  .trunc { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Record button + capture ─────────────────────────── */
  .record-btn {
    display: flex; align-items: center; justify-content: center; gap: 9px;
    font-family: inherit; font-size: 15px; font-weight: 600;
    padding: 16px; border-radius: 6px; cursor: pointer;
    background: #151b20; border: 1px solid #2c343b; color: #dfe6ec;
  }
  .record-btn:hover:not(:disabled) { background: #1c242a; }
  .record-btn:disabled { opacity: 0.4; cursor: default; }
  .record-btn:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .record-btn .dot {
    width: 11px; height: 11px; border-radius: 50%; background: #e2564a; flex: none;
  }
  .record-btn.on {
    background: #2c1715; border-color: #4a231e; color: #ff6b5e;
  }
  .record-btn.on .dot { animation: pulse 1s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.2; } }

  .capture {
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    padding: 6px 0; color: #55606a; font-size: 11px;
  }
  .capture .big { font-size: 30px; color: #ff6b5e; font-weight: 600; }

  .armline {
    font-size: 11px; color: #7c5a50; display: flex; align-items: center; gap: 6px;
  }
  .armline::before {
    content: ''; width: 7px; height: 7px; border-radius: 50%;
    background: currentColor; flex: none;
  }
  .armline.ok { color: #4f9d8a; }

  /* ── shared form controls ────────────────────────────── */
  .text {
    font-family: inherit; font-size: 12px; width: 100%;
    background: #0d1013; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 8px 9px;
  }
  .text:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .text:disabled { opacity: 0.5; }

  input[type=range] { width: 100%; accent-color: #45c3d1; }

  .seg { display: flex; gap: 4px; }
  .seg button {
    flex: 1; font-family: inherit; font-size: 11.5px; cursor: pointer;
    background: #0d1013; border: 1px solid #2c343b; color: #7c8892;
    border-radius: 4px; padding: 7px 6px;
  }
  .seg button:hover:not(:disabled) { background: #151b20; color: #dfe6ec; }
  .seg button.on { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .seg button:disabled { opacity: 0.4; cursor: default; }
  .seg button:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .row {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; font-size: 12px; color: #7c8892;
  }
  .row b { color: #dfe6ec; font-weight: 500; }

  .btn {
    font-family: inherit; font-size: 12px; cursor: pointer;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 7px 13px;
  }
  .btn:hover:not(:disabled) { background: #262d34; }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .btn:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .btn.sm { padding: 4px 10px; }
  .btn.pri { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .btn.pri:hover:not(:disabled) { background: #184048; }
  .frow { display: flex; gap: 6px; }

  .bar {
    height: 6px; background: #0d1013; border: 1px solid #2c343b;
    border-radius: 3px; overflow: hidden;
  }
  .bar.mini { height: 4px; margin-top: 5px; }
  .bar i { display: block; height: 100%; background: #45c3d1; transition: width 0.1s linear; }

  .link {
    background: none; border: none; padding: 0; align-self: flex-start;
    color: #45c3d1; font-family: inherit; font-size: 11.5px; cursor: pointer;
    text-decoration: underline; text-underline-offset: 2px; text-align: left;
  }
  .link:disabled { opacity: 0.4; cursor: default; text-decoration: none; }

  .hint { font-size: 11px; line-height: 1.5; color: #7c8892; }
  .hint.dim { color: #55606a; }
  .hint.act { color: #9fe1ea; }
  .said { color: #dfe6ec; }

  .empty { font-size: 11.5px; line-height: 1.55; color: #55606a; }

  .err {
    font-size: 11.5px; line-height: 1.45; color: #e2564a;
    background: #2c1715; border: 1px solid #4a231e;
    border-radius: 4px; padding: 8px 10px;
  }

  /* ── Takes list ──────────────────────────────────────── */
  .takelist { list-style: none; display: flex; flex-direction: column; gap: 5px; }
  .takelist li {
    display: flex; align-items: center; gap: 9px;
    padding: 10px 11px; border-radius: 5px;
    background: #0d1013; border: 1px solid #1d2329;
  }
  .takelist li.playing { border-color: #1f5560; background: #0e1a1d; }
  .tmeta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .tname {
    font-size: 13px; color: #dfe6ec;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tsub { font-size: 10px; color: #55606a; }

  .play {
    flex: none; font-family: inherit; font-size: 14px; line-height: 1;
    background: #13343a; border: 1px solid #1f5560; color: #9fe1ea;
    border-radius: 5px; padding: 9px 13px; cursor: pointer;
  }
  .play:hover:not(:disabled) { background: #184048; }
  .play:disabled { opacity: 0.3; cursor: default; }
  .play:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .del {
    flex: none; font-family: inherit; font-size: 13px; line-height: 1;
    background: #1e242a; border: 1px solid #2c343b; color: #7c8892;
    border-radius: 5px; padding: 9px 11px; cursor: pointer;
  }
  .del:hover { color: #e2564a; }
  .del.armed { color: #e2564a; border-color: #4a231e; background: #2c1715; font-size: 11px; }
  .del:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  /* ── Commands list ───────────────────────────────────── */
  .mic {
    font-family: inherit; font-size: 13px; cursor: pointer; font-weight: 500;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 5px; padding: 11px; text-align: center;
  }
  .mic:hover { background: #262d34; }
  .mic:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .mic.on { background: #123036; border-color: #1f5560; color: #45c3d1; }
  .mic.on { animation: blink 1.2s ease-in-out infinite; }
  @keyframes blink { 50% { opacity: 0.5; } }

  .cmdlist { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .cmdlist li {
    display: flex; align-items: flex-start; gap: 7px;
    padding: 8px 9px; border-radius: 4px;
    background: #0d1013; border: 1px solid #1d2329;
  }
  .cmdlist li.editing { border-color: #1f5560; }
  .cmdlist li.off { opacity: 0.55; }
  .cmdlist li.off .cname { text-decoration: line-through; }

  /* enable/disable switch */
  .toggle {
    flex: none; margin-top: 2px; width: 26px; height: 15px; padding: 0;
    border-radius: 8px; cursor: pointer;
    background: #1e242a; border: 1px solid #2c343b;
    position: relative; transition: background 0.12s, border-color 0.12s;
  }
  .toggle .knob {
    position: absolute; top: 1px; left: 1px; width: 11px; height: 11px;
    border-radius: 50%; background: #55606a; transition: left 0.12s, background 0.12s;
  }
  .toggle.on { background: #13343a; border-color: #1f5560; }
  .toggle.on .knob { left: 12px; background: #45c3d1; }
  .toggle:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .cinfo { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .cname { font-size: 12px; color: #dfe6ec; }
  .chips { display: flex; flex-wrap: wrap; gap: 3px; }
  .chip {
    font-size: 10px; color: #9fe1ea;
    background: #13282c; border: 1px solid #1f4248;
    border-radius: 3px; padding: 1px 5px;
  }
  .bound { font-size: 10px; color: #55606a; }
  .bound i { color: #444; }

  .mini {
    flex: none; font-family: inherit; font-size: 12px; line-height: 1;
    background: #1e242a; border: 1px solid #2c343b; color: #9fe1ea;
    border-radius: 4px; padding: 5px 8px; cursor: pointer;
  }
  .mini:hover:not(:disabled) { background: #262d34; }
  .mini:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .mini.danger { color: #7c8892; }
  .mini.danger:hover { color: #e2564a; }
  .mini.danger.armed { color: #e2564a; border-color: #4a231e; background: #2c1715; }

  @media (prefers-reduced-motion: reduce) {
    .record-btn.on .dot, .mic.on { animation: none; }
    .bar i, .toggle, .toggle .knob { transition: none; }
  }
</style>
