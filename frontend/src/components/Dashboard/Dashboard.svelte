<script lang="ts">
  import { settings } from '../../stores/settings'
  import { overlay, type Mode } from '../../stores/ui'
  import { leaderState } from '../../stores/robot'
  import CameraFeed from './CameraFeed.svelte'
  import Arm3D from './Arm3D.svelte'
  import TimePlot from './TimePlot.svelte'
  import RecordWorkspace from '../Record/RecordWorkspace.svelte'

  export let mode: Mode = 'manual'

  $: overhead = $settings.camera_overhead
  $: ee       = $settings.camera_ee

  // Pick is the one mode that genuinely needs the overhead view, so it gets the
  // big tile there when configured. Everywhere else the arm keeps it.
  $: overheadIsMain = mode === 'pick' && overhead !== null

  // Which camera, if any, earns the side tile in this mode.
  $: sideCamera =
      mode === 'pick'   ? (overheadIsMain ? ee : overhead) :
      mode === 'teleop' ? ee :
      overhead

  $: sideCameraLabel =
      mode === 'teleop' || overheadIsMain ? 'End effector' : 'Overhead'

  $: showSide = sideCamera !== null

  // Unconfigured cameras give their space back and become one line of prompt.
  $: prompts = [
      overhead === null && (mode === 'pick' || mode === 'manual')
        ? 'Overhead camera not configured' : null,
      ee === null && (mode === 'teleop' || mode === 'pick')
        ? 'End effector camera not configured' : null,
    ].filter(Boolean) as string[]
</script>

{#if mode === 'record'}
  <div class="stage record">
    <RecordWorkspace />
  </div>
{:else}
  <div class="stage">
    <div class="row top">
      <div class="cell main">
        {#if overheadIsMain}
          <CameraFeed label="Overhead — workspace" cameraIndex={overhead} />
        {:else}
          <Arm3D
            showLeader={mode === 'teleop'}
            label={mode === 'teleop' ? 'Leader over follower' : 'Arm — kinematic view'}
          />
        {/if}
      </div>

      {#if showSide}
        <div class="cell side">
          <CameraFeed label={sideCameraLabel} cameraIndex={sideCamera} />
        </div>
      {/if}
    </div>

    <div class="row plot">
      <div class="cell">
        <TimePlot
          kind={mode === 'teleop' && $leaderState ? 'error' : 'position'}
        />
      </div>
    </div>

    {#if prompts.length}
      <div class="prompts">
        {#each prompts as p}
          <button class="prompt" on:click={() => overlay.set('settings')}>
            <span class="ico">□</span>
            {p}
            <span class="act">Choose a camera</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .stage {
    flex: 1;
    display: flex; flex-direction: column;
    gap: 7px; padding: 7px;
    overflow: hidden; min-height: 0; min-width: 0;
  }
  .stage.record { padding: 0; gap: 0; }

  .row {
    display: flex; gap: 7px;
    min-height: 0; min-width: 0;
  }

  /* The stage gives most of its height to whatever the mode actually shows. */
  .top  { flex: 1 1 62%; }
  .plot { flex: 1 1 38%; }

  .cell { flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
  .cell.main { flex: 1.55; }
  .cell.side { flex: 1; }

  .prompts { display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }

  .prompt {
    display: flex; align-items: center; gap: 9px;
    width: 100%; padding: 7px 11px;
    background: #111; border: 1px dashed #262d34; border-radius: 4px;
    color: #55606a; font-family: inherit; font-size: 11.5px;
    cursor: pointer; text-align: left;
  }
  .prompt:hover { border-color: #39424a; color: #7c8892; }
  .prompt:focus-visible { outline: 2px solid #45c3d1; outline-offset: 1px; }
  .ico { opacity: 0.5; }
  .act { margin-left: auto; color: #45c3d1; text-decoration: underline; text-underline-offset: 2px; }

  @media (max-width: 900px) {
    .row.top { flex-direction: column; }
    .cell.main, .cell.side { flex: 1; }
  }
</style>
