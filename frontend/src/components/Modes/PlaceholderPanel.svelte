<script lang="ts">
  export let label: string
  export let phase: string | undefined = undefined
  export let detail: string = ''
</script>

<div class="placeholder">
  <span class="label">{label}</span>
  {#if phase}<span class="phase">{phase}</span>{/if}
  {#if detail}<p class="detail">{detail}</p>{/if}
</div>

<style>
  .placeholder {
    display: flex; flex-direction: column; align-items: flex-start; gap: 7px;
    padding: 14px; border: 1px dashed #262d34; border-radius: 4px;
  }
  .label { font-size: 13px; color: #7c8892; }
  .phase {
    font-size: 9.5px; letter-spacing: 0.13em; text-transform: uppercase;
    color: #55606a; border: 1px solid #2c343b; border-radius: 2px; padding: 2px 6px;
  }
  .detail { font-size: 11.5px; line-height: 1.55; color: #55606a; }
</style>
