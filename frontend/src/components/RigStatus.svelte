<script lang="ts">
  import { rig, loadRig } from '../stores/rig'
  import { overlay } from '../stores/ui'
  import { settings } from '../stores/settings'

  /**
   * One cluster answers "is the rig ready?". Each dot is a button that opens the
   * thing that fixes it, rather than sending you hunting through three panels.
   */
  let open: string | null = null

  function toggle(id: string) { open = open === id ? null : id }
  function close() { open = null }

  async function post(url: string) {
    try { await fetch(url, { method: 'POST' }) } catch { /* rig poll shows the result */ }
    await loadRig()
  }

  $: f = $rig.follower
  $: l = $rig.leader
  $: cams = [$rig.cameras.overhead, $rig.cameras.ee].filter(c => c !== null).length
  // A configured index that is no longer a capture device is worse than none:
  // it looks set up but can never produce a frame.
  $: camsBad = (!$rig.cameras.overhead_valid) || (!$rig.cameras.ee_valid)
</script>

<svelte:window on:keydown={(e) => e.key === 'Escape' && close()} />

<div class="rig">
  <!-- Follower -->
  <div class="slot">
    <button class="item" class:active={open === 'follower'} on:click={() => toggle('follower')}>
      <span class="dot" class:on={f.connected} class:bad={!!f.error && !f.connected}></span>
      follower
    </button>
    {#if open === 'follower'}
      <div class="pop">
        <div class="pop-head">Follower arm</div>
        <div class="kv"><span>Port</span><b class="num">{f.port ?? 'not set'}</b></div>
        <div class="kv"><span>State</span><b class:ok={f.connected}>{f.connected ? 'connected' : 'disconnected'}</b></div>
        <div class="kv"><span>Calibration</span><b class:ok={f.calibrated}>{f.calibrated ? 'saved' : 'none'}</b></div>
        {#if f.error}<div class="err">{f.error}</div>{/if}
        <div class="acts">
          {#if f.connected}
            <button on:click={() => post('/robot/disconnect')}>Disconnect</button>
          {:else}
            <button class="pri" on:click={() => post('/robot/connect')} disabled={!f.port}>Connect</button>
          {/if}
          <button on:click={() => { overlay.set('settings'); close() }}>Port…</button>
        </div>
      </div>
    {/if}
  </div>

  <!-- Leader -->
  <div class="slot">
    <button class="item" class:active={open === 'leader'} on:click={() => toggle('leader')}>
      <span class="dot" class:on={l.connected} class:live={l.mirroring}></span>
      leader
    </button>
    {#if open === 'leader'}
      <div class="pop">
        <div class="pop-head">Leader arm</div>
        <div class="kv"><span>Port</span><b class="num">{l.port ?? 'not set'}</b></div>
        <div class="kv"><span>State</span><b class:ok={l.connected}>{l.connected ? 'connected' : 'disconnected'}</b></div>
        <div class="kv"><span>Calibration</span><b class:ok={l.calibrated}>{l.calibrated ? 'saved' : 'none'}</b></div>
        {#if l.error}<div class="err">{l.error}</div>{/if}
        <div class="acts">
          {#if l.connected}
            <button on:click={() => post('/teleop/disconnect')}>Disconnect</button>
          {:else}
            <button class="pri" on:click={() => post('/teleop/connect')} disabled={!l.port}>Connect</button>
          {/if}
          <button on:click={() => { overlay.set('settings'); close() }}>Port…</button>
        </div>
      </div>
    {/if}
  </div>

  <!-- Calibration -->
  <div class="slot">
    <button class="item" class:active={open === 'cal'} on:click={() => toggle('cal')}>
      <span class="dot" class:on={f.calibrated} class:warn={f.calibrated && !l.calibrated}></span>
      calibrated
    </button>
    {#if open === 'cal'}
      <div class="pop">
        <div class="pop-head">Calibration</div>
        {#if $rig.calibrations.length}
          <div class="kv"><span>Saved</span><b class="num">{$rig.calibrations.join(', ')}</b></div>
        {:else}
          <div class="kv"><span>Saved</span><b>none yet</b></div>
        {/if}
        <p class="note">Teleop needs both arms calibrated separately — the two do not share a zero.</p>
        <div class="acts">
          <button class="pri" on:click={() => { overlay.set('calibrate'); close() }}>Open wizard</button>
        </div>
      </div>
    {/if}
  </div>

  <span class="sep"></span>

  <!-- Cameras -->
  <div class="slot">
    <button class="item" class:active={open === 'cams'} on:click={() => toggle('cams')}>
      <span class="dot" class:on={cams > 0 && !camsBad} class:warn={cams === 1 && !camsBad} class:bad={camsBad}></span>
      cameras
    </button>
    {#if open === 'cams'}
      <div class="pop">
        <div class="pop-head">Cameras</div>
        <div class="kv">
          <span>Overhead</span>
          <b class="num" class:ok={$rig.cameras.overhead !== null && $rig.cameras.overhead_valid}>
            {$rig.cameras.overhead === null ? 'not set' : `video${$rig.cameras.overhead}`}
          </b>
        </div>
        <div class="kv">
          <span>End effector</span>
          <b class="num" class:ok={$rig.cameras.ee !== null && $rig.cameras.ee_valid}>
            {$rig.cameras.ee === null ? 'not set' : `video${$rig.cameras.ee}`}
          </b>
        </div>
        {#if camsBad}
          <div class="err">
            A selected index is not a video capture device, so it can never send a
            frame. A webcam's metadata node opens fine but carries no video.
          </div>
        {:else}
          <p class="note">One capture per device, shared by every viewer. The device is released a few seconds after the last one stops watching.</p>
        {/if}
        <div class="acts">
          <button class="pri" on:click={() => { overlay.set('settings'); close() }}>Choose cameras…</button>
        </div>
      </div>
    {/if}
  </div>
</div>

{#if open}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
  <div class="scrim" on:click={close}></div>
{/if}

<style>
  .rig { display: flex; align-items: center; gap: 2px; }
  .slot { position: relative; }

  .item {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 9px; border-radius: 4px; cursor: pointer;
    border: 1px solid transparent; background: none;
    font-family: inherit; font-size: 12px; color: #7c8892;
  }
  .item:hover, .item.active { background: #191e23; border-color: #262d34; color: #dfe6ec; }

  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #55606a; flex: none;
  }
  .dot.on   { background: #5fb87a; box-shadow: 0 0 0 2.5px rgba(95,184,122,0.16); }
  .dot.warn { background: #d8a442; box-shadow: 0 0 0 2.5px rgba(216,164,66,0.16); }
  .dot.bad  { background: #e2564a; box-shadow: 0 0 0 2.5px rgba(226,86,74,0.16); }
  .dot.live { background: #45c3d1; box-shadow: 0 0 0 2.5px rgba(69,195,209,0.18); }

  .sep { width: 1px; height: 17px; background: #262d34; margin: 0 4px; }

  .pop {
    position: absolute; top: calc(100% + 7px); left: 0; z-index: 60;
    width: 258px; padding: 12px 13px;
    background: #14181c; border: 1px solid #2c343b; border-radius: 6px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; gap: 7px;
  }

  .pop-head {
    font-size: 10px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; font-weight: 600; margin-bottom: 2px;
  }

  .kv { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; color: #7c8892; }
  .kv b { color: #dfe6ec; font-weight: 500; text-align: right; word-break: break-all; }
  .kv b.ok { color: #5fb87a; }
  .num { font-variant-numeric: tabular-nums; }

  .note { font-size: 11px; line-height: 1.5; color: #55606a; margin-top: 2px; }
  .err {
    font-size: 11px; line-height: 1.45; color: #e2564a;
    background: #2c1715; border: 1px solid #4a231e;
    border-radius: 3px; padding: 6px 8px;
  }

  .acts { display: flex; gap: 6px; margin-top: 4px; }
  .acts button {
    flex: 1; font-family: inherit; font-size: 11.5px;
    background: #1e242a; border: 1px solid #2c343b; color: #dfe6ec;
    border-radius: 4px; padding: 6px 10px; cursor: pointer;
  }
  .acts button:hover:not(:disabled) { background: #262d34; }
  .acts button.pri { background: #13343a; border-color: #1f5560; color: #9fe1ea; }
  .acts button.pri:hover:not(:disabled) { background: #184048; }
  .acts button:disabled { opacity: 0.4; cursor: default; }

  .scrim { position: fixed; inset: 0; z-index: 50; }
</style>
