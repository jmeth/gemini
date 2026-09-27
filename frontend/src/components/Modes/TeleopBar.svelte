<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { rig, loadRig } from '../../stores/rig'
  import { overlay } from '../../stores/ui'
  import {
    teleopStatus, teleopNote, teleopPost,
    startTeleopPolling, stopTeleopPolling,
  } from '../../stores/teleop'

  onMount(startTeleopPolling)
  onDestroy(stopTeleopPolling)

  let busy = false

  /**
   * Exactly what is standing between here and mirroring, in the order you would
   * fix it — each with the action that fixes it. "Not ready" on its own tells
   * you nothing when three different things can cause it.
   */
  $: blockers = [
    !$rig.follower.connected && {
      text: 'Follower arm is not connected',
      cta: 'Connect follower',
      run: () => act('/robot/connect'),
    },
    !$rig.leader.connected && {
      text: 'Leader arm is not connected',
      cta: $rig.leader.port ? 'Connect leader' : 'Set leader port',
      run: $rig.leader.port
        ? () => act('/teleop/connect')
        : () => overlay.set('settings'),
    },
    !$rig.follower.calibrated && {
      text: 'Follower has no saved calibration',
      cta: 'Calibrate follower',
      run: () => overlay.set('calibrate'),
    },
    !$rig.leader.calibrated && {
      text: 'Leader has no saved calibration',
      cta: 'Calibrate leader',
      run: () => overlay.set('calibrate'),
    },
  ].filter(Boolean) as { text: string; cta: string; run: () => void }[]

  $: blocker = blockers[0] ?? null
  $: ready = blockers.length === 0
  $: live = $teleopStatus.mirroring && !$teleopStatus.clutched

  async function act(url: string) {
    busy = true
    await teleopPost(url)
    await loadRig()
    busy = false
  }

  const toggle = () => act($teleopStatus.mirroring ? '/teleop/stop' : '/teleop/start')
</script>

<div class="bar" class:live class:held={$teleopStatus.clutched} class:blocked={!ready}>
  <div class="state">
    <div class="s1">
      {#if $teleopStatus.clutched}
        Clutched
      {:else if $teleopStatus.mirroring}
        Mirroring
      {:else if blocker}
        Not ready
      {:else}
        Ready to mirror
      {/if}
    </div>
    <div class="s2 num">
      {#if $teleopStatus.mirroring}
        {$teleopStatus.hz.toFixed(0)} Hz · speed {($teleopStatus.speed_limit * 100).toFixed(0)}%{$teleopStatus.dropped ? ` · ${$teleopStatus.dropped} dropped` : ''}{$teleopStatus.jitter_ms > 5 ? ` · ${$teleopStatus.jitter_ms.toFixed(0)}ms late` : ''}
      {:else if blocker}
        {blocker.text}
      {:else}
        Both arms connected and calibrated
      {/if}
    </div>
  </div>

  {#if blocker}
    <button class="fix" on:click={blocker.run} disabled={busy}>
      {busy ? 'working…' : blocker.cta}
    </button>
  {:else}
    <button
      class="toggle" class:on={$teleopStatus.mirroring}
      on:click={toggle} disabled={busy}
      role="switch" aria-checked={$teleopStatus.mirroring}
      aria-label="Mirror the leader arm"
      title={$teleopStatus.mirroring ? 'Stop mirroring' : 'Start mirroring'}
    ><i></i></button>
  {/if}
</div>

{#if blockers.length > 1}
  <ul class="rest">
    {#each blockers.slice(1) as b}
      <li>{b.text}</li>
    {/each}
  </ul>
{/if}

{#if $teleopNote}
  <p class="note" role="alert">{$teleopNote}</p>
{/if}

<style>
  .bar {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 13px;
    background: #1e242a; border-bottom: 1px solid #2c343b;
    flex-shrink: 0;
  }
  .bar.live    { background: #123036; box-shadow: inset 3px 0 0 #45c3d1; }
  .bar.held    { background: #2a2113; box-shadow: inset 3px 0 0 #d8a442; }
  .bar.blocked { background: #1a1a1e; box-shadow: inset 3px 0 0 #55606a; }

  .state { flex: 1; min-width: 0; }
  .s1 { font-size: 13px; font-weight: 600; color: #dfe6ec; }
  .bar.live .s1    { color: #45c3d1; }
  .bar.held .s1    { color: #d8a442; }
  .bar.blocked .s1 { color: #7c8892; }
  .s2 { font-size: 10.5px; color: #7c8892; margin-top: 1px; }
  .num { font-variant-numeric: tabular-nums; }

  .fix {
    flex: none; font-family: inherit; font-size: 11.5px; font-weight: 500;
    background: #13343a; border: 1px solid #1f5560; color: #9fe1ea;
    border-radius: 4px; padding: 6px 11px; cursor: pointer; white-space: nowrap;
  }
  .fix:hover:not(:disabled) { background: #184048; }
  .fix:disabled { opacity: 0.5; cursor: default; }
  .fix:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }

  .toggle {
    width: 44px; height: 24px; border-radius: 13px; flex: none;
    background: #1d2329; border: 1px solid #2c343b;
    position: relative; cursor: pointer; padding: 0;
  }
  .toggle i {
    position: absolute; top: 2px; left: 2px;
    width: 18px; height: 18px; border-radius: 50%;
    background: #55606a; transition: left 0.16s, background 0.16s;
  }
  .toggle.on { background: #14383e; border-color: #1f5560; }
  .toggle.on i { left: 22px; background: #45c3d1; }
  .toggle:disabled { opacity: 0.4; cursor: default; }
  .toggle:focus-visible { outline: 2px solid #45c3d1; outline-offset: 2px; }

  .rest {
    list-style: none; margin: 0; padding: 8px 13px;
    border-bottom: 1px solid #2c343b; background: #16191d;
    display: flex; flex-direction: column; gap: 3px;
  }
  .rest li { font-size: 11px; color: #55606a; }
  .rest li::before { content: '· '; }

  .note {
    margin: 9px 13px 0;
    font-size: 11.5px; line-height: 1.45; color: #e2564a;
    background: #2c1715; border: 1px solid #4a231e;
    border-radius: 4px; padding: 8px 10px;
  }

  @media (prefers-reduced-motion: reduce) { .toggle i { transition: none; } }
</style>
