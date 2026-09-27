<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { leaderState, robotState, MOTOR_NAMES, type MotorName } from '../../stores/robot'
  import {
    teleopStatus, teleopNote, teleopPost,
    startTeleopPolling, stopTeleopPolling,
  } from '../../stores/teleop'

  let speedLimit = 0.3
  let maxError = 10

  onMount(startTeleopPolling)
  onDestroy(stopTeleopPolling)

  // Follow the server unless the user is mid-drag on the matching slider.
  let draggingSpeed = false
  let draggingError = false
  $: if (!draggingSpeed) speedLimit = $teleopStatus.speed_limit
  $: if (!draggingError) maxError = $teleopStatus.max_error

  const setClutch = (engaged: boolean) => teleopPost('/teleop/clutch', { engaged })
  const updateSpeed = () => { draggingSpeed = false; return teleopPost('/teleop/speed', { limit: speedLimit }) }
  const updateError = () => { draggingError = false; return teleopPost('/teleop/limits', { max_error: maxError }) }
  const toggleCutout = () => teleopPost('/teleop/limits', { stop_on_error: !$teleopStatus.stop_on_error })
  const clearOffsets = () => teleopPost('/teleop/offsets/clear')
  const toggleMatch = () => teleopPost('/teleop/limits', { match_ranges: !$teleopStatus.match_ranges })

  $: leader = $leaderState
  $: joints = $robotState.joints

  /**
   * Joints not doing what they were told, worst first.
   *
   * Measured against the COMMANDED value, not the leader. The speed limit makes
   * the follower lag the leader on purpose, so a leader-based comparison lists
   * every joint the moment you move briskly and means nothing.
   */
  $: notFollowing = leader
    ? MOTOR_NAMES
        .map((n: MotorName) => {
          const cmd = leader!.commands?.[n]
          const foll = joints[n]?.pos
          const clamped = leader!.clamped?.includes(n) ?? false
          const d = cmd != null && foll != null ? cmd - foll : 0
          return { n, d, clamped }
        })
        .filter(x => x.clamped || Math.abs(x.d) >= 3)
        .sort((a, b) => Math.abs(b.d) - Math.abs(a.d))
    : []

  /** How far the leader is ahead — expected while the speed limit is biting. */
  $: leadAhead = leader
    ? Math.max(...MOTOR_NAMES.map((n: MotorName) => {
        const lead = leader!.positions?.[n], foll = joints[n]?.pos
        return lead != null && foll != null ? Math.abs(lead - foll) : 0
      }))
    : 0

  $: canMirror = $teleopStatus.leader_connected
</script>

<div class="actions">
  <button
    class="btn"
    on:mousedown={() => setClutch(true)}
    on:mouseup={() => setClutch(false)}
    on:mouseleave={() => $teleopStatus.clutched && setClutch(false)}
    on:touchstart|preventDefault={() => setClutch(true)}
    on:touchend|preventDefault={() => setClutch(false)}
    disabled={!$teleopStatus.mirroring}
    title="Hold to freeze the follower while you reposition the leader"
  >{$teleopStatus.clutched ? 'Clutched — release' : 'Clutch'}</button>

  <button class="btn" on:click={clearOffsets} disabled={!$teleopStatus.mirroring} title="Map leader angles straight through again">
    Re-zero
  </button>
</div>

<div class="field">
  <div class="row"><span>Speed limit</span><b class="num">{(speedLimit * 100).toFixed(0)}%</b></div>
  <input
    type="range" min="0.05" max="1" step="0.05"
    bind:value={speedLimit} on:input={() => draggingSpeed = true} on:change={updateSpeed}
    disabled={!canMirror} aria-label="Speed limit"
  />
  <p class="hint">How far a joint may move per frame. Lower is smoother and safer.</p>
</div>

<div class="field">
  <div class="row">
    <span>Stop if a joint falls behind</span>
    <button
      class="toggle" class:on={$teleopStatus.stop_on_error}
      on:click={toggleCutout}
      role="switch" aria-checked={$teleopStatus.stop_on_error}
      aria-label="Stop when tracking error exceeds the limit"
    ><i></i></button>
  </div>
  {#if $teleopStatus.stop_on_error}
    <div class="row sub"><span>Limit</span><b class="num">{maxError.toFixed(0)}°</b></div>
    <input
      type="range" min="2" max="45" step="1"
      bind:value={maxError} on:input={() => draggingError = true} on:change={updateError}
      disabled={!canMirror} aria-label="Tracking error limit"
    />
  {/if}
</div>

<div class="field">
  <div class="row">
    <span>Match arm travel</span>
    <button
      class="toggle" class:on={$teleopStatus.match_ranges}
      on:click={toggleMatch}
      role="switch" aria-checked={$teleopStatus.match_ranges}
      aria-label="Match the leader's travel to the follower's"
    ><i></i></button>
  </div>
  <p class="hint">
    The two arms are calibrated separately and their recorded travel differs — the
    gripper by 31&deg;. Matching maps full leader deflection to full follower
    deflection. Turn it off to pass degrees straight through.
  </p>
</div>

<div class="field">
  <div class="lbl">Not following</div>
  {#if !leader}
    <p class="hint">Waiting for the leader.</p>
  {:else if notFollowing.length === 0}
    <p class="hint ok">All six joints doing what they were told.</p>
    {#if leadAhead >= 5}
      <p class="hint">
        Leader is {leadAhead.toFixed(0)}&deg; ahead — the speed limit is holding the
        follower back, not a fault. Raise it if the arm feels sluggish.
      </p>
    {/if}
  {:else}
    <ul class="list">
      {#each notFollowing as x}
        <li class:crit={x.clamped}>
          <span>{x.n.replace('_', ' ')}{x.clamped ? ' at limit' : ''}</span>
          <b class="num">Δ {Math.abs(x.d).toFixed(1)}°</b>
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if $teleopNote || $teleopStatus.error}
  <p class="err" role="alert">{$teleopNote ?? $teleopStatus.error}</p>
{/if}

<style>
  .actions { display: flex; gap: 6px; }

  .btn {
    flex: 1; font-family: inherit; font-size: 12px;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 7px 11px; cursor: pointer;
  }
  .btn:hover:not(:disabled) { background: #262d34; }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .btn:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .field { display: flex; flex-direction: column; gap: 6px; }

  .lbl {
    font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; font-weight: 600;
  }

  .row {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; font-size: 12.5px; color: #7c8892;
  }
  .row b { color: #dfe6ec; font-weight: 500; }
  .row.sub { font-size: 11.5px; }
  .num { font-variant-numeric: tabular-nums; }

  input[type=range] { width: 100%; accent-color: #45c3d1; }
  input[type=range]:disabled { opacity: 0.35; }

  .toggle {
    width: 34px; height: 19px; border-radius: 10px; flex: none;
    background: #1d2329; border: 1px solid #2c343b;
    position: relative; cursor: pointer; padding: 0;
  }
  .toggle i {
    position: absolute; top: 2px; left: 2px;
    width: 13px; height: 13px; border-radius: 50%;
    background: #55606a; transition: left 0.16s, background 0.16s;
  }
  .toggle.on { background: #14383e; border-color: #1f5560; }
  .toggle.on i { left: 17px; background: #45c3d1; }
  .toggle:focus-visible { outline: 2px solid #45c3d1; outline-offset: 2px; }

  .hint { font-size: 11px; line-height: 1.5; color: #55606a; }
  .hint.ok { color: #5fb87a; }

  .list { list-style: none; display: flex; flex-direction: column; gap: 2px; }
  .list li {
    display: flex; align-items: center; justify-content: space-between; gap: 9px;
    padding: 6px 9px; border-radius: 4px;
    background: #0d1013; border: 1px solid #2a2113;
    font-size: 12px; color: #d8a442; text-transform: capitalize;
  }
  .list li.crit { border-color: #4a231e; color: #e2564a; }

  .callout {
    font-size: 11.5px; line-height: 1.55; color: #b89a5a;
    background: #2a2113; border: 1px solid #4a3a1c;
    border-radius: 4px; padding: 9px 11px;
  }
  .callout strong { color: #d8a442; font-weight: 600; display: block; margin-bottom: 3px; }

  .link {
    background: none; border: none; padding: 0; margin-top: 5px;
    color: #45c3d1; font-family: inherit; font-size: 11.5px;
    cursor: pointer; text-decoration: underline; text-underline-offset: 2px;
    display: block;
  }

  .err {
    font-size: 11.5px; line-height: 1.45; color: #e2564a;
    background: #2c1715; border: 1px solid #4a231e;
    border-radius: 4px; padding: 8px 10px;
  }

  @media (prefers-reduced-motion: reduce) { .toggle i { transition: none; } }
</style>
