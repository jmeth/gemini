<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { isConnected } from '../../stores/robot'
  import { rig } from '../../stores/rig'
  import { vision, detections, setVision } from '../../stores/detection'
  import {
    pickCal, pickObjects, pickNote,
    startPickPolling, stopPickPolling,
    recordTouch, deletePoint, clearPoints, setFollowerTorque,
    type LockedTarget,
  } from '../../stores/pick'

  onMount(startPickPolling)
  onDestroy(stopPickPolling)

  let busy = false
  let limp = false                       // we released torque and have not re-armed
  let selectedId: number | null = null
  let confirmClear = false
  // The object's pixel, captured while the detector could still see it. The arm
  // covers the object on the way to touching it, so recording cannot depend on
  // live detection.
  let locked: LockedTarget | null = null

  $: cal = $pickCal
  $: nPoints = cal?.points.length ?? 0
  $: need = cal?.needed ?? 4
  $: want = cal?.recommended ?? 8
  $: overhead = $rig.cameras.overhead

  // Detection has to be running on the camera we calibrate against.
  $: detecting = $vision?.enabled === true && $vision?.camera !== null
  $: onCamera = detecting && (cal?.camera == null || cal.camera === $vision?.camera)

  $: inView = ($detections && $vision && $detections.camera === $vision.camera)
    ? $detections.objects : []
  // With one object in view it is unambiguous; with several, the operator picks.
  $: if (inView.length === 1) selectedId = inView[0].id
  $: if (selectedId !== null && !inView.some(o => o.id === selectedId)) selectedId = null
  $: target = inView.length === 1 ? inView[0] : inView.find(o => o.id === selectedId)

  $: canLock = detecting && onCamera && !!target && !busy
  $: lockBlocker =
      !detecting    ? 'Turn object detection on'
    : !onCamera     ? `Detection is on camera ${$vision?.camera}, points are on camera ${cal?.camera}`
    : inView.length === 0 ? 'No object in view — put one on the table'
    : !target       ? 'Several objects in view — choose one'
    : null

  $: canRecord = $isConnected && !!locked && !busy
  $: blocker = !$isConnected ? 'Follower arm is not connected'
             : !locked ? 'Lock an object first (step 1)' : null

  $: pct = Math.min(100, Math.round((nPoints / want) * 100))
  $: fit = cal?.table ?? null
  // How steeply the table leans against the arm's base plane — a sanity check
  // against how the arm is actually mounted.
  $: tilt = fit ? Math.round(Math.acos(Math.min(1, Math.abs(fit.normal[2]))) * 180 / Math.PI) : null

  const mm = (m: number) => Math.round(m * 1000)

  async function run(fn: () => Promise<unknown>) {
    busy = true
    try { await fn() } finally { busy = false }
  }

  const release = () => run(async () => { if (await setFollowerTorque(false)) limp = true })
  const hold    = () => run(async () => { if (await setFollowerTorque(true)) limp = false })
  function lock() {
    if (!target) return
    locked = { pixel: [target.center[0], target.center[1]], label: target.label, id: target.id }
  }
  const touch = () => run(async () => {
    if (!locked) return
    // The next touch is a different spot, so the object has to be locked again.
    if (await recordTouch(locked)) locked = null
  })
  const turnOnDetection = () =>
    run(() => setVision({ enabled: true, camera: overhead }))

  async function clearAll() {
    if (!confirmClear) { confirmClear = true; return }
    confirmClear = false
    await run(clearPoints)
  }
</script>

<div class="pick">
  <div class="head">
    <span class="title">Table calibration</span>
    {#if cal?.solved && fit}
      <span class="chip ok">calibrated · {(fit.rms_in_plane_m * 1000).toFixed(1)} mm</span>
    {:else}
      <span class="chip">{nPoints} / {need} points</span>
    {/if}
  </div>

  <p class="note">
    Teaches the arm where the table is, however it is tilted. Lock an object while the
    camera can see it, guide the gripper tip onto its centre, then record the touch.
    Repeat across the table — aim for {want}.
  </p>

  <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax={want} aria-valuenow={nPoints}>
    <i style="width:{pct}%"></i>
  </div>

  <!-- 1. detection + lock -->
  <div class="step">
    <span class="n">1</span>
    <div class="body">
      {#if !detecting}
        <span class="label">Detection is off</span>
        <button class="btn" on:click={turnOnDetection} disabled={busy || overhead === null}
          title={overhead === null ? 'No overhead camera configured' : ''}>
          Turn on detection
        </button>
      {:else if locked}
        <span class="label">Locked: {locked.label}{locked.id !== null ? ` #${locked.id}` : ''}</span>
        <span class="sub mono">px {Math.round(locked.pixel[0])}, {Math.round(locked.pixel[1])}</span>
        <button class="btn" on:click={() => (locked = null)} disabled={busy}>Unlock</button>
        <span class="sub">Leave the object where it is — the arm will hide it from the camera.</span>
      {:else}
        <span class="label">
          Detecting on camera {$vision?.camera} ·
          {inView.length} object{inView.length === 1 ? '' : 's'} in view
        </span>
        {#if inView.length > 1}
          <select class="sel" bind:value={selectedId}>
            <option value={null}>— choose the object to touch —</option>
            {#each inView as o (o.id)}
              <option value={o.id}>{o.label} #{o.id}{o.conf ? ` · ${Math.round(o.conf * 100)}%` : ''}</option>
            {/each}
          </select>
        {:else if target}
          <span class="sub">{target.label} #{target.id}</span>
        {/if}
        <button class="btn" on:click={lock} disabled={!canLock}>Lock object</button>
        {#if lockBlocker}<span class="sub warn">{lockBlocker}</span>{/if}
      {/if}
    </div>
  </div>

  <!-- 2. guide the arm -->
  <div class="step">
    <span class="n">2</span>
    <div class="body">
      <span class="label">{limp ? 'Arm is limp — guide the tip onto the object' : 'Release the arm, then guide the tip onto the locked object'}</span>
      <div class="row">
        <button class="btn" on:click={release} disabled={!$isConnected || busy || limp}>Release</button>
        <button class="btn" on:click={hold} disabled={!$isConnected || busy || !limp}
          title="Re-arm where the arm is now — it will not snap back">Hold</button>
      </div>
      {#if cal?.tool}
        <span class="sub mono">tip  x {mm(cal.tool[0])}  y {mm(cal.tool[1])}  z {mm(cal.tool[2])} mm</span>
      {/if}
    </div>
  </div>

  <!-- 3. record -->
  <div class="step">
    <span class="n">3</span>
    <div class="body">
      <button class="btn pri" on:click={touch} disabled={!canRecord}>Record touch</button>
      {#if blocker}<span class="sub warn">{blocker}</span>{/if}
    </div>
  </div>

  {#if $pickNote || cal?.note}
    <p class="msg" class:err={!!$pickNote} role="status">{$pickNote ?? cal?.note}</p>
  {/if}

  {#if fit}
    <div class="fit">
      <div class="kv"><span>In-plane error</span><b>{(fit.rms_in_plane_m * 1000).toFixed(1)} mm rms · {(fit.max_in_plane_m * 1000).toFixed(1)} max</b></div>
      <div class="kv"><span>Off-plane scatter</span><b>{(fit.rms_off_plane_m * 1000).toFixed(1)} mm</b></div>
      {#if tilt !== null}<div class="kv"><span>Table tilt vs arm base</span><b>{tilt}°</b></div>{/if}
      {#each fit.warnings as w}<p class="msg warnbox">{w}</p>{/each}
    </div>
  {/if}

  {#if nPoints}
    <div class="sect">
      <span class="label">Points</span>
      <button class="link" on:click={clearAll} on:mouseleave={() => (confirmClear = false)}>
        {confirmClear ? 'Sure? clear all' : 'Clear all'}
      </button>
    </div>
    <ul class="pts">
      {#each cal?.points ?? [] as p, i}
        <li>
          <span class="idx">{i + 1}</span>
          <span class="mono">
            px {Math.round(p.pixel[0])},{Math.round(p.pixel[1])}
            → {mm(p.xyz[0])}, {mm(p.xyz[1])}, {mm(p.xyz[2])}
          </span>
          <button class="x" on:click={() => run(() => deletePoint(i))} title="Remove this point" aria-label="Remove point {i + 1}">×</button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if cal?.solved}
    <div class="sect"><span class="label">Located objects</span></div>
    {#if $pickObjects.length === 0}
      <p class="note">Nothing in view. Objects appear here with their position in the arm's frame — move the gripper tip to one to check the calibration.</p>
    {:else}
      <ul class="pts">
        {#each $pickObjects as o (o.id ?? `${o.pixel}`)}
          <li>
            <span class="idx">#{o.id ?? '–'}</span>
            <span class="mono">{o.label} → {mm(o.xyz[0])}, {mm(o.xyz[1])}, {mm(o.xyz[2])} mm</span>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</div>

<style>
  .pick { display: flex; flex-direction: column; gap: 10px; }

  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { font-size: 13px; color: #dfe6ec; }
  .chip {
    font-size: 10px; letter-spacing: 0.04em; color: #9aa7b2;
    border: 1px solid #2c343b; border-radius: 3px; padding: 2px 7px;
  }
  .chip.ok { color: #81c784; border-color: #2a4a2a; background: #14241a; }

  .note { font-size: 11.5px; line-height: 1.55; color: #55606a; }

  .bar { height: 3px; background: #1d2329; border-radius: 2px; overflow: hidden; }
  .bar i { display: block; height: 100%; background: #45c3d1; transition: width 0.2s; }

  .step { display: flex; gap: 9px; align-items: flex-start; }
  .n {
    flex: none; width: 18px; height: 18px; border-radius: 50%;
    display: grid; place-items: center; margin-top: 1px;
    font-size: 10px; color: #9fe1ea; background: #13343a; border: 1px solid #1f5560;
  }
  .body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .label { font-size: 12px; color: #b6c0c8; }
  .sub { font-size: 11px; color: #7c8892; }
  .sub.warn { color: #b89a5a; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; color: #7c8892; }
  .row { display: flex; gap: 6px; }

  .btn {
    flex: 1; font-family: inherit; font-size: 12px;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 7px 11px; cursor: pointer;
  }
  .btn:hover:not(:disabled) { background: #262d34; }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .btn.pri { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .btn.pri:hover:not(:disabled) { background: #184048; }
  .btn:focus-visible, .x:focus-visible, .link:focus-visible, .sel:focus-visible {
    outline: 2px solid #45c3d1; outline-offset: 1px;
  }

  .sel {
    font-family: inherit; font-size: 12px; background: #0d1013; color: #dfe6ec;
    border: 1px solid #2c343b; border-radius: 4px; padding: 6px 8px;
  }

  .msg {
    font-size: 11.5px; line-height: 1.45; color: #7c8892;
    border-radius: 4px; padding: 7px 10px; background: #0d1013; border: 1px solid #1d2329;
  }
  .msg.err { color: #e2564a; background: #2c1715; border-color: #4a231e; }
  .msg.warnbox { color: #b89a5a; background: #2a2113; border-color: #4a3a1c; }

  .fit { display: flex; flex-direction: column; gap: 5px; }
  .kv { display: flex; justify-content: space-between; gap: 8px; font-size: 11.5px; }
  .kv span { color: #55606a; }
  .kv b { color: #b6c0c8; font-weight: 500; text-align: right; }

  .sect { display: flex; align-items: center; justify-content: space-between; margin-top: 2px; }
  .link {
    background: none; border: none; padding: 0; color: #55606a; cursor: pointer;
    font-family: inherit; font-size: 11px; text-decoration: underline; text-underline-offset: 2px;
  }
  .link:hover { color: #e2564a; }

  .pts { list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .pts li {
    display: flex; align-items: center; gap: 8px; padding: 6px 8px;
    background: #0d1013; border: 1px solid #1d2329; border-radius: 4px;
  }
  .idx { flex: none; min-width: 18px; font-size: 10.5px; color: #55606a; }
  .pts li .mono { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .x {
    flex: none; background: none; border: none; color: #55606a; cursor: pointer;
    font-size: 14px; line-height: 1; padding: 0 3px;
  }
  .x:hover { color: #e2564a; }

  @media (prefers-reduced-motion: reduce) { .bar i { transition: none; } }
</style>
