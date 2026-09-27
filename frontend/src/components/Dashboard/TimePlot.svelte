<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import { jointHistory, MOTOR_NAMES, type MotorName } from '../../stores/robot'

  /**
   * 'position' plots absolute joint angles.
   * 'error' plots leader-minus-follower, zero-centred, with a tolerance band —
   * in teleop the deviation is the story, not the pose.
   */
  export let kind: 'position' | 'error' = 'position'
  /** Tolerance band half-width in degrees, drawn in error mode. */
  export let tolerance = 3

  let canvas: HTMLCanvasElement
  let ctx: CanvasRenderingContext2D
  let animId: number

  // Window options in seconds
  const WINDOWS = [10, 30, 60, 300] as const
  let windowSec = 30

  const COLORS: Record<MotorName, string> = {
    shoulder_pan:  '#4fc3f7',
    shoulder_lift: '#81c784',
    elbow_flex:    '#ffb74d',
    wrist_flex:    '#f06292',
    wrist_roll:    '#ce93d8',
    gripper:       '#80cbc4',
  }

  const POS_MIN = -190, POS_MAX = 190
  const ERR_SPAN = 20   // degrees either side of zero

  $: yMin = kind === 'error' ? -ERR_SPAN : POS_MIN
  $: yMax = kind === 'error' ?  ERR_SPAN : POS_MAX
  $: gridLines = kind === 'error' ? [-tolerance, tolerance] : [-180, -90, 0, 90, 180]

  function valueOf(p: typeof $jointHistory[number], name: MotorName): number | null {
    if (kind === 'error') return p.error ? p.error[name] : null
    return p.joints[name]
  }

  function draw(history: typeof $jointHistory) {
    if (!ctx) return
    const { width: W, height: H } = canvas
    ctx.clearRect(0, 0, W, H)

    const now = history.length ? history[history.length - 1].t : Date.now() / 1000
    const tMin = now - windowSec

    const pts = history.filter(h => h.t >= tMin)
    const usable = kind === 'error' ? pts.filter(p => p.error) : pts

    ctx.fillStyle = '#111'
    ctx.fillRect(0, 0, W, H)

    if (usable.length < 2) {
      ctx.fillStyle = '#333'
      ctx.font = '11px monospace'
      ctx.textAlign = 'center'
      ctx.fillText(kind === 'error' ? 'waiting for the leader' : 'no data', W / 2, H / 2)
      ctx.textAlign = 'left'
      return
    }

    const yOf = (v: number) => H - ((v - yMin) / (yMax - yMin)) * H

    // Tolerance band — a trace inside it is tracking fine.
    if (kind === 'error') {
      ctx.fillStyle = 'rgba(69,195,209,0.05)'
      ctx.fillRect(0, yOf(tolerance), W, yOf(-tolerance) - yOf(tolerance))
    }

    ctx.strokeStyle = '#1e1e1e'
    ctx.lineWidth = 1
    for (const v of gridLines) {
      const y = yOf(v)
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
    }

    // Zero line
    ctx.strokeStyle = kind === 'error' ? '#39424a' : '#2a2a2a'
    ctx.beginPath(); ctx.moveTo(0, yOf(0)); ctx.lineTo(W, yOf(0)); ctx.stroke()

    for (const name of MOTOR_NAMES) {
      ctx.beginPath()
      ctx.strokeStyle = COLORS[name]
      ctx.lineWidth = 1.5
      let first = true
      for (const p of usable) {
        const v = valueOf(p, name)
        if (v == null) continue
        const x = ((p.t - tMin) / windowSec) * W
        const y = yOf(Math.max(yMin, Math.min(yMax, v)))
        if (first) { ctx.moveTo(x, y); first = false }
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }

    // Legend (small swatches + names at right edge)
    let ly = 14
    ctx.font = '10px monospace'
    for (const name of MOTOR_NAMES) {
      ctx.fillStyle = COLORS[name]
      ctx.fillRect(W - 88, ly - 6, 8, 8)
      ctx.fillStyle = '#555'
      ctx.fillText(name.replace('_', ' '), W - 76, ly)
      ly += 14
    }

    if (kind === 'error') {
      ctx.fillStyle = '#55606a'
      ctx.fillText(`±${tolerance}°`, 5, yOf(tolerance) - 4)
    }
  }

  function loop() {
    animId = requestAnimationFrame(loop)
    const { clientWidth: w, clientHeight: h } = canvas
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h
    }
    draw($jointHistory)
  }

  onMount(() => {
    ctx = canvas.getContext('2d')!
    loop()
  })

  onDestroy(() => cancelAnimationFrame(animId))
</script>

<div class="plot-wrap">
  <div class="plot-header">
    <span class="label">
      {kind === 'error' ? 'Tracking error — leader minus follower' : 'Joint history'}
    </span>
    <div class="window-btns">
      {#each WINDOWS as w}
        <button class:active={windowSec === w} on:click={() => windowSec = w}>
          {w < 60 ? `${w}s` : `${w / 60}m`}
        </button>
      {/each}
    </div>
  </div>
  <canvas bind:this={canvas}></canvas>
</div>

<style>
  .plot-wrap {
    width: 100%; height: 100%;
    background: #111; border-radius: 4px;
    display: flex; flex-direction: column;
    padding: 6px 8px 4px; gap: 4px;
  }

  .plot-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; flex-shrink: 0;
  }

  .label {
    font-size: 10px; letter-spacing: 0.11em; text-transform: uppercase;
    color: #55606a; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .window-btns { display: flex; gap: 2px; flex-shrink: 0; }
  .window-btns button {
    background: none; border: 1px solid #2a2a2a; color: #555;
    cursor: pointer; font-size: 10px; font-family: inherit;
    padding: 1px 6px; border-radius: 3px;
  }
  .window-btns button.active { border-color: #444; color: #bbb; }
  .window-btns button:hover { color: #999; }
  .window-btns button:focus-visible { outline: 1px solid #45c3d1; }

  canvas { flex: 1; width: 100%; min-height: 0; display: block; }
</style>
