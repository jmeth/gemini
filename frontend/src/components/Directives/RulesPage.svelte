<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import {
    rules, poses, sounds, arms, SIDES, buttonName, saveRule, setRuleEnabled, deleteRule,
    runRule, importCommands, type Rule, type Side,
  } from '../../stores/directives'
  import { takes } from '../../stores/recordings'
  import { startArcadePolling, stopArcadePolling, waitForButton } from '../../stores/arcade'

  onMount(startArcadePolling)
  onDestroy(stopArcadePolling)

  // The editor works on a loosely-typed copy; the backend validates it on save.
  type Draft = { name: string; triggers: any[]; actions: any[]; busy_policy: string; cooldown_s: number }
  let editingId: number | null = null
  let draft: Draft | null = null
  let learning: number | null = null
  let busy = false
  let confirmId: number | null = null
  let note: string | null = null

  const OBJECTS = ['scissors', 'person', 'cup', 'bottle', 'cell phone', 'book', 'banana', 'apple',
                   'remote', 'mouse', 'keyboard', 'teddy bear', 'cat', 'dog', 'laptop']

  const newTrigger = (type: string): any => ({
    voice: { type, phrases: [] }, button: { type, button: 0 },
    timer: { type, mode: 'interval', seconds: 60 },
    object: { type, label: 'scissors', event: 'appears', min_conf: 0.6 }, startup: { type },
  } as Record<string, any>)[type]
  const newAction = (type: string): any => ({
    movement: { type, id: $takes[0]?.id ?? '', arms: [...SIDES], speed: 1 },
    pose: { type, id: $poses[0]?.id ?? 0, arms: [...SIDES], duration_s: 1.5 },
    sound: { type, id: $sounds[0]?.id ?? 0, volume: 1, wait: false },
    wait: { type, seconds: 1 },
  } as Record<string, any>)[type]

  function startNew() {
    editingId = null; note = null
    draft = { name: '', triggers: [newTrigger('button')], actions: [], busy_policy: 'skip', cooldown_s: 0 }
  }
  function startEdit(r: Rule) {
    editingId = r.id; note = null
    draft = JSON.parse(JSON.stringify({ name: r.name, triggers: r.triggers, actions: r.actions,
                                         busy_policy: r.busy_policy, cooldown_s: r.cooldown_s }))
  }
  const cancel = () => { draft = null; editingId = null; learning = null }

  async function save() {
    if (!draft) return
    busy = true
    const saved = await saveRule(editingId, draft as any)
    busy = false
    if (saved) cancel()
  }
  async function remove(id: number) {
    if (confirmId !== id) { confirmId = id; return }
    confirmId = null
    await deleteRule(id)
  }
  async function run(r: Rule) { note = (await runRule(r.id)) ? `Running “${r.name}” — see Activity` : null }
  async function doImport() {
    const d = await importCommands()
    note = d ? (d.imported ? `Imported ${d.imported} command${d.imported === 1 ? '' : 's'} as rules (the old commands are now off)`
                           : 'Nothing new to import') : null
  }

  async function learn(i: number) {
    learning = i
    const b = await waitForButton()
    learning = null
    if (b !== null && draft) draft.triggers[i].button = b
    else note = 'No button press heard — is the arcade encoder connected?'
    draft = draft
  }
  function setPhrases(i: number, text: string) {
    if (!draft) return
    draft.triggers[i].phrases = text.split(',').map(s => s.trim()).filter(Boolean)
  }
  function toggleArm(a: any, s: Side) {
    a.arms = a.arms.includes(s) ? a.arms.filter((x: Side) => x !== s) : [...a.arms, s]
    draft = draft
  }
  function move(i: number, d: number) {
    if (!draft) return
    const j = i + d
    if (j < 0 || j >= draft.actions.length) return
    ;[draft.actions[i], draft.actions[j]] = [draft.actions[j], draft.actions[i]]
    draft = draft
  }
  const drop = (list: 'triggers' | 'actions', i: number) => { if (draft) { draft[list].splice(i, 1); draft = draft } }
  const addTrigger = (e: Event) => {
    const el = e.target as HTMLSelectElement
    if (draft && el.value) { draft.triggers = [...draft.triggers, newTrigger(el.value)] }
    el.value = ''
  }
  const addAction = (e: Event) => {
    const el = e.target as HTMLSelectElement
    if (draft && el.value) { draft.actions = [...draft.actions, newAction(el.value)] }
    el.value = ''
  }

  // ---- plain-language summaries ----------------------------------------
  const armsText = (a: Side[]) => a.length === 2 ? 'both arms' : `${a[0]} arm`
  const takeName = (id: string) => $takes.find(t => t.id === id)?.name ?? '(deleted movement)'
  const poseName = (id: number) => $poses.find(p => p.id === id)?.name ?? '(deleted pose)'
  const soundName = (id: number) => $sounds.find(s => s.id === id)?.name ?? '(deleted sound)'
  function triggerText(t: any): string {
    switch (t.type) {
      case 'voice':   return `Say ${t.phrases.map((p: string) => `“${p}”`).join(' or ')}`
      case 'button':  return `${buttonName(t.button)} pressed`
      case 'timer':   return t.mode === 'daily' ? `Every day at ${t.at}` : `Every ${t.seconds}s`
      case 'object':  return `${t.label} ${t.event} (≥${Math.round(t.min_conf * 100)}%)`
      default:        return 'On startup'
    }
  }
  function actionText(a: any): string {
    switch (a.type) {
      case 'movement': return `Play “${takeName(a.id)}” · ${armsText(a.arms)}${a.speed !== 1 ? ` · ×${a.speed}` : ''}`
      case 'pose':     return `Go to “${poseName(a.id)}” · ${armsText(a.arms)} · ${a.duration_s}s`
      case 'sound':    return `Play “${soundName(a.id)}”${a.wait ? ' and wait' : ''}`
      default:         return `Wait ${a.seconds}s`
    }
  }
  const ago = (iso: string | null) => {
    if (!iso) return 'never'
    const s = (Date.now() - Date.parse(iso)) / 1000
    return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago`
         : s < 86400 ? `${Math.floor(s / 3600)} h ago` : new Date(iso).toLocaleDateString()
  }
</script>

<div class="head">
  <h2>Rules</h2>
  <p class="lede">
    <b>When</b> something happens — a phrase, a button, a timer, an object in the camera —
    <b>then</b> do one or more things in order: play a movement, go to a pose, play a sound, wait.
    Each step says which arm it uses. A rule can have several triggers; any one fires it.
  </p>
</div>

<div class="row">
  <button class="btn pri" on:click={startNew} disabled={!!draft}>＋ New rule</button>
  <button class="btn" on:click={doImport} title="Copy your voice/button commands into rules and switch the old ones off">Import existing commands</button>
  {#if note}<span class="small">{note}</span>{/if}
</div>

{#if draft}
  <div class="card editor">
    <span class="label">{editingId === null ? 'New rule' : 'Edit rule'}</span>
    <input class="text" placeholder="Name (e.g. greet on hello)" bind:value={draft.name} />

    <!-- WHEN -->
    <div class="sect">
      <span class="tag when">WHEN</span>
      <span class="small">any of these happens</span>
    </div>
    {#each draft.triggers as t, i}
      <div class="step">
        <div class="grow col">
          {#if t.type === 'voice'}
            <span class="label">Voice phrase</span>
            <input class="text" placeholder="Phrases, comma separated (e.g. hello, hi wall-e)"
              value={t.phrases.join(', ')} on:input={(e) => setPhrases(i, e.currentTarget.value)} />
          {:else if t.type === 'button'}
            <span class="label">Arcade button</span>
            <div class="row">
              <span class="chip amber">{learning === i ? 'Press a button…' : buttonName(t.button)}</span>
              <button class="btn sm" on:click={() => learn(i)} disabled={learning !== null}>Assign by pressing</button>
              <label class="small row">or number
                <input class="text num" type="number" min="1" max="12" value={t.button + 1}
                  on:input={(e) => { t.button = Math.max(0, (+e.currentTarget.value || 1) - 1); draft = draft }} />
              </label>
            </div>
          {:else if t.type === 'timer'}
            <span class="label">Timer</span>
            <div class="row">
              <select class="sel" bind:value={t.mode}>
                <option value="interval">Every N seconds</option><option value="daily">Daily at a time</option>
              </select>
              {#if t.mode === 'interval'}
                <input class="text num" type="number" min="1" bind:value={t.seconds} /> <span class="small">seconds</span>
              {:else}
                <input class="text" type="time" bind:value={t.at} />
              {/if}
            </div>
          {:else if t.type === 'object'}
            <span class="label">Object seen by the camera</span>
            <div class="row">
              <input class="text" list="objs" bind:value={t.label} placeholder="scissors" />
              <datalist id="objs">{#each OBJECTS as o}<option value={o}></option>{/each}</datalist>
              <select class="sel" bind:value={t.event}>
                <option value="appears">appears</option><option value="disappears">disappears</option>
              </select>
              <label class="small row">at least
                <input class="text num" type="number" min="5" max="100" step="5"
                  value={Math.round(t.min_conf * 100)} on:input={(e) => (t.min_conf = (+e.currentTarget.value || 50) / 100)} />%
              </label>
            </div>
            <span class="small dim">Needs object detection on (Pick tab). It must stay in view for a moment to count.</span>
          {:else}
            <span class="label">On startup</span>
            <span class="small">Runs a few seconds after the backend starts.</span>
          {/if}
        </div>
        <button class="btn sm danger" on:click={() => drop('triggers', i)} aria-label="Remove trigger" disabled={draft.triggers.length === 1}>×</button>
      </div>
    {/each}
    <select class="sel add" on:change={addTrigger} aria-label="Add a trigger">
      <option value="">＋ Add another trigger…</option>
      <option value="voice">Voice phrase</option><option value="button">Arcade button</option>
      <option value="timer">Timer</option><option value="object">Object seen</option><option value="startup">On startup</option>
    </select>

    <!-- THEN -->
    <div class="sect">
      <span class="tag then">THEN</span>
      <span class="small">do these, in order</span>
    </div>
    {#each draft.actions as a, i}
      <div class="step">
        <span class="idx">{i + 1}</span>
        <div class="grow col">
          {#if a.type === 'movement'}
            <span class="label">Play movement</span>
            <div class="row">
              <select class="sel" bind:value={a.id}>
                {#each $takes as t}<option value={t.id}>{t.name}</option>{/each}
                {#if !$takes.some(t => t.id === a.id)}<option value={a.id}>(deleted movement)</option>{/if}
              </select>
              <label class="small row">speed <input class="text num" type="number" min="0.1" max="4" step="0.1" bind:value={a.speed} />×</label>
            </div>
          {:else if a.type === 'pose'}
            <span class="label">Go to pose</span>
            <div class="row">
              <select class="sel" bind:value={a.id}>
                {#each $poses as p}<option value={p.id}>{p.name}</option>{/each}
                {#if !$poses.some(p => p.id === a.id)}<option value={a.id}>(deleted pose)</option>{/if}
              </select>
              <label class="small row">over <input class="text num" type="number" min="0.1" max="30" step="0.1" bind:value={a.duration_s} />s</label>
            </div>
          {:else if a.type === 'sound'}
            <span class="label">Play sound</span>
            <div class="row">
              <select class="sel" bind:value={a.id}>
                {#each $sounds as s}<option value={s.id}>{s.name}</option>{/each}
                {#if !$sounds.some(s => s.id === a.id)}<option value={a.id}>(deleted sound)</option>{/if}
              </select>
              <label class="small row">volume <input type="range" min="0" max="1" step="0.05" bind:value={a.volume} /></label>
              <label class="small row"><input type="checkbox" bind:checked={a.wait} /> wait until it finishes</label>
            </div>
          {:else}
            <span class="label">Wait</span>
            <div class="row"><input class="text num" type="number" min="0" max="600" step="0.5" bind:value={a.seconds} /> <span class="small">seconds</span></div>
          {/if}
          {#if a.arms}
            <div class="row small">
              <span class="dim">Arms:</span>
              {#each SIDES as s}
                <label class="row"><input type="checkbox" checked={a.arms.includes(s)} on:change={() => toggleArm(a, s)} /> {s}
                  {#if $arms && $arms.side !== s}<span class="chip off" title="Not connected yet — skipped when this runs">not connected</span>{/if}
                </label>
              {/each}
            </div>
          {/if}
        </div>
        <div class="col nudge">
          <button class="btn sm" on:click={() => move(i, -1)} disabled={i === 0} aria-label="Move up">↑</button>
          <button class="btn sm" on:click={() => move(i, 1)} disabled={i === draft.actions.length - 1} aria-label="Move down">↓</button>
        </div>
        <button class="btn sm danger" on:click={() => drop('actions', i)} aria-label="Remove action">×</button>
      </div>
    {/each}
    <select class="sel add" on:change={addAction} aria-label="Add an action">
      <option value="">＋ Add an action…</option>
      <option value="movement" disabled={!$takes.length}>Play a movement{$takes.length ? '' : ' (none recorded)'}</option>
      <option value="pose" disabled={!$poses.length}>Go to a pose{$poses.length ? '' : ' (none saved)'}</option>
      <option value="sound" disabled={!$sounds.length}>Play a sound{$sounds.length ? '' : ' (none uploaded)'}</option>
      <option value="wait">Wait</option>
    </select>

    <div class="row opts">
      <label class="small row">If the arm is busy
        <select class="sel" bind:value={draft.busy_policy}>
          <option value="skip">skip this run</option><option value="queue">wait for it, then run</option>
          <option value="interrupt">interrupt what it's doing</option>
        </select>
      </label>
      <label class="small row">Cooldown
        <input class="text num" type="number" min="0" bind:value={draft.cooldown_s} /> s between runs
      </label>
    </div>

    <div class="row">
      <button class="btn pri" on:click={save} disabled={busy}>Save rule</button>
      <button class="btn" on:click={cancel}>Cancel</button>
    </div>
  </div>
{/if}

{#if $rules.length === 0 && !draft}
  <p class="empty">
    No rules yet. Make one: <b>When</b> an arcade button is pressed <b>then</b> play a movement.
    Record the movement on the Movements page first. If you have older voice/button commands, “Import existing commands” brings them over.
  </p>
{:else}
  <ul class="list">
    {#each $rules as r (r.id)}
      <li class:off={!r.enabled}>
        <button class="tog" class:on={r.enabled} role="switch" aria-checked={r.enabled}
          aria-label={r.enabled ? 'Disable rule' : 'Enable rule'}
          title={r.enabled ? 'On — click to turn off' : 'Off — click to turn on'}
          on:click={() => setRuleEnabled(r.id, !r.enabled)}><span class="knob"></span></button>
        <div class="grow col tight">
          <span class="name">{r.name}</span>
          <div class="row small"><span class="tag when sm">WHEN</span>{#each r.triggers as t}<span class="chip amber">{triggerText(t)}</span>{/each}</div>
          <div class="row small"><span class="tag then sm">THEN</span>{#each r.actions as a, i}<span class="chip">{i + 1}. {actionText(a)}</span>{/each}</div>
          <span class="small dim">
            last ran {ago(r.last_fired_at)}
            {#if r.busy_policy !== 'skip'} · if busy: {r.busy_policy}{/if}
            {#if r.cooldown_s} · cooldown {r.cooldown_s}s{/if}
            {#if r.source} · imported from a command{/if}
          </span>
        </div>
        <button class="btn sm" on:click={() => run(r)} title="Run now, ignoring triggers and cooldown">▶ Run</button>
        <button class="btn sm" on:click={() => startEdit(r)} disabled={!!draft}>Edit</button>
        <button class="btn sm danger" class:armed={confirmId === r.id} on:click={() => remove(r.id)}
          on:mouseleave={() => confirmId === r.id && (confirmId = null)}>{confirmId === r.id ? 'Sure?' : '×'}</button>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .editor { gap: 12px; }
  .sect { display: flex; align-items: center; gap: 9px; margin-top: 4px; }
  .tag { font-size: 10px; font-weight: 700; letter-spacing: 0.12em; border-radius: 3px; padding: 2px 7px; }
  .tag.sm { padding: 1px 5px; font-size: 9px; }
  .tag.when { color: #f0d9a0; background: #2a2412; border: 1px solid #4a3f1c; }
  .tag.then { color: #9fe1ea; background: #13343a; border: 1px solid #1f5560; }
  .step { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; background: #0d1013; border: 1px solid #1d2329; border-radius: 6px; }
  .idx { flex: none; width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; color: #9fe1ea; background: #13343a; border: 1px solid #1f5560; margin-top: 2px; }
  .nudge { gap: 3px; }
  .add { align-self: flex-start; color: #7c8892; }
  .opts { padding-top: 4px; border-top: 1px solid #1d2329; gap: 18px; }
  .tight { gap: 4px; }
  .name { font-size: 14px; color: #dfe6ec; }
  label { cursor: pointer; }
</style>
