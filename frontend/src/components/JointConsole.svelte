<script lang="ts">
  import { onMount } from 'svelte'
  import { robotState, isConnected, leaderState, MOTOR_NAMES, type MotorName } from '../stores/robot'
  import { ranges, loadRanges, toPct } from '../stores/ranges'
  import { teleopStatus } from '../stores/teleop'

  /** Draw the leader marker and the tracking delta alongside the follower. */
  export let dual = false

  const COLORS: Record<MotorName, string> = {
    shoulder_pan:  '#4fc3f7',
    shoulder_lift: '#81c784',
    elbow_flex:    '#ffb74d',
    wrist_flex:    '#f06292',
    wrist_roll:    '#ce93d8',
    gripper:       '#80cbc4',
  }

  const WARN_DELTA = 3    // degrees — the gap starts reading as lag
  const HOT_TEMP   = 60
  const HOT_LOAD   = 55

  // Keep showing a commanded value until the joint gets there, or until it is
  // clear it is not going to. Without this the next 10 Hz frame snaps the handle
  // back to where the motor still is and the drag looks like it did nothing.
  const HOLD_MS = 2000
  const SETTLE_DEG = 2

  let sliders = Object.fromEntries(MOTOR_NAMES.map(n => [n, 0])) as Record<MotorName, number>
  let torque  = Object.fromEntries(MOTOR_NAMES.map(n => [n, true])) as Record<MotorName, boolean>
  // While a slider is being dragged, incoming state must not yank it back.
  let dragging = Object.fromEntries(MOTOR_NAMES.map(n => [n, false])) as Record<MotorName, boolean>
  let commanded = Object.fromEntries(MOTOR_NAMES.map(n => [n, null])) as Record<MotorName, number | null>
  let commandedAt = Object.fromEntries(MOTOR_NAMES.map(n => [n, 0])) as Record<MotorName, number>

  onMount(loadRanges)

  $: joints = $robotState.joints
  $: leader = $leaderState
  // Only pair the readouts once there is a leader to pair them with.
  $: showDual = dual && leader !== null
  // Only meaningful in teleop, and teleop status is only polled there — reading
  // it in manual mode would use whatever value was left behind on the way out.
  $: leaderDriving = dual && $teleopStatus.mirroring && !$teleopStatus.clutched

  $: syncFromRobot(joints)

  function syncFromRobot(js: typeof joints) {
    const now = Date.now()
    for (const n of MOTOR_NAMES) {
      if (dragging[n]) continue
      const pos = js[n]?.pos
      if (pos == null) continue

      const target = commanded[n]
      if (target !== null) {
        const arrived = Math.abs(pos - target) <= SETTLE_DEG
        const expired = now - commandedAt[n] > HOLD_MS
        if (!arrived && !expired) continue   // still travelling; leave the handle alone
        commanded[n] = null                  // arrived, or it is not going to
      }
      sliders[n] = pos
    }
  }

  /**
   * A range input silently clamps its value to [min, max]. A joint reading past
   * its recorded travel would make that clamp fight the incoming state every
   * frame, and the slider could not be moved at all — so widen the track to take
   * in wherever the joint actually is.
   */
  function span(r: { min: number; max: number }, pos: number) {
    return { min: Math.min(r.min, Math.floor(pos)), max: Math.max(r.max, Math.ceil(pos)) }
  }

  /**
   * How far a joint sits outside the travel recorded during calibration, and
   * which end. This is not the same as the arm being uncalibrated: a properly
   * calibrated joint reads past its end stop whenever the limit was recorded a
   * little short of the true mechanical stop.
   */
  function pastLimit(r: { min: number; max: number }, pos: number) {
    if (pos > r.max) return { end: 'max', by: pos - r.max }
    if (pos < r.min) return { end: 'min', by: r.min - pos }
    return null
  }

  $: avgVolt = (MOTOR_NAMES.reduce((s, n) => s + (joints[n]?.volt ?? 0), 0) / MOTOR_NAMES.length)
  $: maxTemp = Math.max(...MOTOR_NAMES.map(n => joints[n]?.temp ?? 0))
  $: meanDelta = showDual ? meanOf(MOTOR_NAMES.filter(n => !isClamped(n)).map(n => Math.abs(delta(n)))) : 0
  $: clampedCount = showDual ? MOTOR_NAMES.filter(isClamped).length : 0

  function meanOf(xs: number[]): number {
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
  }

  function leaderPos(n: MotorName): number | null {
    const v = leader?.positions?.[n]
    return v == null ? null : v
  }

  function delta(n: MotorName): number {
    const lead = leaderPos(n)
    return lead == null ? 0 : lead - (joints[n]?.pos ?? 0)
  }

  function isClamped(n: MotorName): boolean {
    return !!leader?.clamped?.includes(n)
  }

  function fmt(n: MotorName, v: number): string {
    return n === 'gripper' ? `${v.toFixed(0)}%` : `${v.toFixed(1)}°`
  }

  function unit(n: MotorName): string {
    return n === 'gripper' ? '%' : '°'
  }

  async function setPosition(name: MotorName, val: number) {
    await fetch(`/robot/joint/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pos: val }),
    })
  }

  function onSliderInput(name: MotorName, val: number) {
    dragging[name] = true
    commanded[name] = val
    commandedAt[name] = Date.now()
    setPosition(name, val)
  }

  function onSliderRelease(name: MotorName) {
    // Brief grace period so the next frame doesn't snap the handle back.
    setTimeout(() => { dragging[name] = false }, 150)
  }

  async function toggleTorque(name: MotorName) {
    torque[name] = !torque[name]
    await fetch(`/robot/torque/${name}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: torque[name] }),
    })
  }
</script>

<section class="console">
  <header class="head">
    <span class="lbl">{showDual ? 'Leader → follower' : 'Joints'}</span>
    {#if showDual}
      <span class="meta num" class:warn={clampedCount > 0}>
        Δ {meanDelta.toFixed(1)}° mean{clampedCount ? ` · ${clampedCount} clamped` : ''}
      </span>
    {:else}
      <span class="meta num" class:warn={maxTemp >= HOT_TEMP}>
        {avgVolt.toFixed(1)} V · {maxTemp}°C max
      </span>
    {/if}
  </header>

  <div class="rows">
    {#each MOTOR_NAMES as name}
      {@const j = joints[name]}
      {@const cal = $ranges[name]}
      {@const r = span(cal, j?.pos ?? 0)}
      {@const past = pastLimit(cal, j?.pos ?? 0)}
      {@const c = COLORS[name]}
      {@const fp = toPct(r, j?.pos ?? 0)}
      {@const lead = showDual ? leaderPos(name) : null}
      {@const lp = lead == null ? null : toPct(r, lead)}
      {@const d = showDual ? delta(name) : 0}
      {@const clamped = showDual && isClamped(name)}
      {@const zeroPct = name === 'gripper' ? null : toPct(r, 0)}

      <div class="row" class:dual={showDual}>
        <button
          class="tq"
          class:off={!torque[name]}
          title={torque[name] ? 'Torque on — click to release' : 'Torque released — click to hold'}
          on:click={() => toggleTorque(name)}
          disabled={!$isConnected}
        >T</button>

        <div class="track-col">
          <div class="name-row">
            <span class="name">{name.replace('_', ' ')}</span>
            {#if clamped}<span class="tag">clamped</span>{/if}
            {#if past}
              <span
                class="tag"
                title="{past.by.toFixed(1)}{unit(name)} past the {past.end === 'max' ? 'upper' : 'lower'} end of the travel recorded when this arm was calibrated. The arm is calibrated — this joint's limit was just recorded short of its true mechanical stop. Re-run the wizard and take this joint all the way to its stop to widen it."
              >past {past.end}</span>
            {/if}
          </div>

          <div class="track">
            {#if zeroPct !== null}
              <span class="zero" style="left: {zeroPct}%"></span>
            {/if}
            <span class="fill" style="width: {fp}%; background: {c}"></span>

            {#if lp !== null && Math.abs(lp - fp) > 0.4}
              <!-- The gap between the two markers is the tracking error. -->
              <span
                class="err"
                class:crit={clamped}
                style="left: {Math.min(fp, lp)}%; width: {Math.abs(lp - fp)}%"
              ></span>
            {/if}
            {#if lp !== null}
              <span
                class="lead"
                class:clamped
                style="left: {lp}%; color: {c}"
                title="leader {fmt(name, lead ?? 0)}"
              ></span>
            {/if}
            <span class="knob" style="left: {fp}%; color: {c}"></span>

            <input
              type="range"
              class="slider"
              min={r.min} max={r.max} step="0.5"
              bind:value={sliders[name]}
              on:input={(e) => onSliderInput(name, parseFloat(e.currentTarget.value))}
              on:change={() => onSliderRelease(name)}
              on:mouseup={() => onSliderRelease(name)}
              on:touchend={() => onSliderRelease(name)}
              disabled={!$isConnected || !torque[name] || leaderDriving}
              aria-label="{name.replace('_', ' ')} position"
              style="--c: {c}"
            />
          </div>

          <div class="limits num">
            <span>{r.min.toFixed(0)}{unit(name)}</span>
            <span>{r.max.toFixed(0)}{unit(name)}</span>
          </div>
        </div>

        <div class="read">
          <div class="val num" style="color: {c}">{fmt(name, j?.pos ?? 0)}</div>
          {#if showDual}
            <div class="delta num" class:warn={!clamped && Math.abs(d) >= WARN_DELTA} class:crit={clamped}>
              {#if clamped}
                at limit
              {:else}
                Δ {d >= 0 ? '+' : '−'}{Math.abs(d).toFixed(1)}{unit(name)}
              {/if}
            </div>
          {:else}
            <div class="pips num">
              <span class:hot={(j?.load ?? 0) >= HOT_LOAD}>{(j?.load ?? 0).toFixed(0)}%</span>
              <span class:hot={(j?.temp ?? 0) >= HOT_TEMP}>{j?.temp ?? 0}°</span>
            </div>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</section>

<style>
  .console { display: flex; flex-direction: column; }

  .head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; padding: 10px 13px 8px;
  }
  .lbl {
    font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; font-weight: 600;
  }
  .meta { font-size: 10px; color: #7c8892; }
  .meta.warn { color: #d8a442; }
  .num { font-variant-numeric: tabular-nums; }

  .rows { display: flex; flex-direction: column; gap: 1px; padding: 0 9px 10px; }

  .row {
    display: grid;
    grid-template-columns: 18px 1fr 62px;
    gap: 0 9px; align-items: center;
    padding: 5px 4px; border-radius: 4px;
  }
  .row.dual { grid-template-columns: 18px 1fr 78px; }
  .row:hover { background: #191e23; }

  .tq {
    width: 18px; height: 18px; border-radius: 3px; padding: 0;
    border: 1px solid #2f4a3a; background: #1a2a20; color: #5fb87a;
    font-family: inherit; font-size: 10px; cursor: pointer;
    display: grid; place-items: center; line-height: 1;
  }
  .tq.off { border-color: #3a3f45; background: #1c2126; color: #55606a; }
  .tq:disabled { opacity: 0.35; cursor: default; }

  .track-col { min-width: 0; }

  .name-row { display: flex; align-items: center; gap: 5px; margin-bottom: 3px; }
  .name {
    font-size: 11px; color: #7c8892; text-transform: capitalize;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .tag {
    font-size: 8px; letter-spacing: 0.1em; text-transform: uppercase;
    color: #d8a442; border: 1px solid currentColor; border-radius: 2px;
    padding: 0 3px; flex-shrink: 0;
  }

  /* One track carries the readout, the control, and (in teleop) both arms. */
  .track {
    position: relative; height: 6px; border-radius: 3px;
    background: #1d2329; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.35);
  }
  .zero {
    position: absolute; top: -2px; bottom: -2px; width: 1px;
    background: #55606a; opacity: 0.55;
  }
  .fill {
    position: absolute; top: 0; bottom: 0; left: 0;
    border-radius: 3px; opacity: 0.45; pointer-events: none;
  }
  .err {
    position: absolute; top: 50%; height: 3px; margin-top: -1.5px;
    border-radius: 2px; background: #d8a442; opacity: 0.9; pointer-events: none;
  }
  .err.crit { background: #e2564a; }
  .lead {
    position: absolute; top: 50%; width: 10px; height: 10px;
    margin: -5px 0 0 -5px; border-radius: 50%;
    border: 1.5px solid currentColor; background: #0d1013;
    opacity: 0.85; pointer-events: none;
  }
  .lead.clamped { border-color: #d8a442; border-style: dashed; opacity: 1; }

  /* Follower handle, positioned on the same scale as .lead so the two coincide
     exactly when tracking is perfect — including at the end stops. */
  .knob {
    position: absolute; top: 50%; width: 10px; height: 10px;
    margin: -5px 0 0 -5px; border-radius: 50%;
    background: #14181c; border: 2px solid currentColor;
    pointer-events: none;
  }

  /* The native input sits on top of the track and supplies the handle. */
  .slider {
    position: absolute; inset: -6px 0; width: 100%; height: 18px;
    margin: 0; background: none; -webkit-appearance: none; appearance: none;
    cursor: pointer;
  }
  .slider:disabled { cursor: default; }
  .row:has(.slider:disabled) .knob { opacity: 0.6; }
  .slider::-webkit-slider-runnable-track { background: none; height: 18px; }
  .slider::-moz-range-track { background: none; height: 18px; }
  .slider::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 16px; height: 16px; border-radius: 50%; margin-top: 1px;
    background: transparent; border: none;
  }
  .slider::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: transparent; border: none;
  }
  .slider:focus-visible { outline: 2px solid #45c3d1; outline-offset: 3px; border-radius: 3px; }

  .limits {
    display: flex; justify-content: space-between;
    margin-top: 3px; font-size: 9px; color: #55606a;
  }

  .read { text-align: right; }
  .val { font-size: 12.5px; }
  .delta { font-size: 10.5px; margin-top: 3px; color: #55606a; }
  .delta.warn { color: #d8a442; }
  .delta.crit { color: #e2564a; }
  .pips {
    display: flex; gap: 5px; justify-content: flex-end;
    margin-top: 3px; font-size: 9.5px; color: #55606a;
  }
  .pips .hot { color: #d8a442; }
</style>
