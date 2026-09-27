<script lang="ts">
  import { onMount, onDestroy } from 'svelte'
  import * as THREE from 'three'
  import { robotState, leaderState, MOTOR_NAMES, type MotorName } from '../../stores/robot'
  import { ranges, loadRanges } from '../../stores/ranges'
  import { getJson } from '../../stores/api'

  /** Overlay the leader arm's pose as a ghost — teleop only. */
  export let showLeader = false
  export let label = 'Arm — kinematic view'

  interface UrdfJoint {
    name: string
    type: string
    parent: string
    child: string
    xyz: [number, number, number]
    rpy: [number, number, number]
    axis: [number, number, number]
    lower: number | null
    upper: number | null
  }

  interface Chain {
    root: string
    tip: string
    joints: UrdfJoint[]
    chain: string[]
    actuated: string[]
    branches: string[]
  }

  let canvas: HTMLCanvasElement
  let renderer: THREE.WebGLRenderer
  let scene: THREE.Scene
  let camera: THREE.PerspectiveCamera
  let animId: number
  let chain: Chain | null = null
  let chainError: string | null = null

  // URDF is Z-up; three.js is Y-up. Everything kinematic lives under this group
  // so the arm stands on the grid instead of lying across it.
  let robotRoot: THREE.Group
  let bones: THREE.Mesh[] = []
  let ghostBones: THREE.Mesh[] = []
  let jointDots: THREE.Mesh[] = []
  // The gripper's moving jaw, which has no segment of its own in the URDF —
  // see deriveJaw().
  let jawBone: THREE.Mesh | null = null
  let ghostJawBone: THREE.Mesh | null = null

  const COLOR_BY_JOINT: Record<string, number> = {
    shoulder_pan: 0x5ecbf5, shoulder_lift: 0x86e08a, elbow_flex: 0xffc046,
    wrist_flex: 0xff7ba6, wrist_roll: 0xd6a6ee, gripper: 0x5fe0d0,
  }
  const COLOR_FIXED = 0x9fb0bd
  const COLOR_GHOST = 0xa8b6c2

  // Bones are drawn as tubes rather than lines. WebGL ignores
  // LineBasicMaterial.linewidth on every desktop driver, so a "thicker line" is
  // only achievable with real geometry — which also gives the view some depth.
  const R_BONE = 0.0052
  const R_HAND = 0.0034
  const R_GHOST = 0.0030

  // Orbit under the pointer, not on a timer. A view that spins on its own cannot
  // be compared against the arm in front of you.
  let yaw = 0.9, pitch = 0.5, dist = 0.62
  let dragging = false, lastX = 0, lastY = 0

  async function loadChain() {
    // getJson distinguishes a real backend failure from a path missing in the
    // dev proxy, which otherwise returns index.html with a 200 and reads as
    // "backend unreachable" when the backend is perfectly fine.
    const data = await getJson<Chain & { error?: string }>('/kinematics/chain')
    if (!data) {
      chainError = 'Could not load the kinematic chain — see the browser console'
      return
    }
    if (data.error || !data.joints?.length) {
      chainError = data.error ?? 'URDF has no joints'
      return
    }
    chain = data
    chainError = null
    deriveJaw()
    buildBones()
  }

  // ------------------------------------------------------------------
  // Geometry helpers
  // ------------------------------------------------------------------

  const UP = new THREE.Vector3(0, 1, 0)

  function makeTube(color: number, radius: number, ghost: boolean): THREE.Mesh {
    const geo = new THREE.CylinderGeometry(radius, radius, 1, 14, 1, false)
    // Move the origin to the base so scale.y is simply the segment length.
    geo.translate(0, 0.5, 0)
    const mat = ghost
      ? new THREE.MeshBasicMaterial({
          color, transparent: true, opacity: 0.38, depthWrite: false,
        })
      : new THREE.MeshStandardMaterial({
          color, roughness: 0.5, metalness: 0.05,
          // Enough self-illumination that a bone facing away from both lights
          // still reads against the dark background.
          emissive: color, emissiveIntensity: 0.35,
        })
    const mesh = new THREE.Mesh(geo, mat)
    mesh.visible = false
    return mesh
  }

  const _dir = new THREE.Vector3()

  function orient(mesh: THREE.Mesh | null, a: THREE.Vector3, b: THREE.Vector3) {
    if (!mesh) return
    _dir.subVectors(b, a)
    const len = _dir.length()
    if (len < 1e-6) { mesh.visible = false; return }
    mesh.visible = true
    mesh.position.copy(a)
    mesh.quaternion.setFromUnitVectors(UP, _dir.divideScalar(len))
    mesh.scale.set(1, len, 1)
  }

  function disposeMesh(m: THREE.Mesh) {
    robotRoot.remove(m)
    m.geometry.dispose()
    ;(m.material as THREE.Material).dispose()
  }

  /**
   * One segment per joint in the URDF tree. Colour comes from the actuated joint
   * the segment belongs to, so the 3D view and the joint console agree.
   */
  function buildBones() {
    if (!chain || !scene) return
    for (const m of [...bones, ...ghostBones, ...jointDots]) disposeMesh(m)
    if (jawBone) disposeMesh(jawBone)
    if (ghostJawBone) disposeMesh(ghostJawBone)
    bones = []; ghostBones = []; jointDots = []; jawBone = null; ghostJawBone = null

    for (const j of chain.joints) {
      const color = COLOR_BY_JOINT[j.name] ?? COLOR_FIXED
      // The gripper assembly is small; full-width bones there swallow the jaws.
      const hand = j.parent === jawJoint?.parent || j.name === jawJoint?.name

      const bone = makeTube(color, hand ? R_HAND : R_BONE, false)
      robotRoot.add(bone); bones.push(bone)

      const ghost = makeTube(COLOR_GHOST, R_GHOST, true)
      robotRoot.add(ghost); ghostBones.push(ghost)

      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(j.type === 'revolute' ? 0.0095 : 0.006, 16, 12),
        new THREE.MeshStandardMaterial({
          color, roughness: 0.35, emissive: color, emissiveIntensity: 0.5,
        }),
      )
      robotRoot.add(dot); jointDots.push(dot)
    }

    if (jawJoint && jawTipLocal) {
      const color = COLOR_BY_JOINT[jawJoint.name] ?? COLOR_FIXED
      jawBone = makeTube(color, R_HAND, false)
      robotRoot.add(jawBone)
      ghostJawBone = makeTube(COLOR_GHOST, R_GHOST, true)
      robotRoot.add(ghostJawBone)
    }
  }

  /**
   * Forward kinematics exactly as the URDF defines it:
   *   T_child = T_parent * Translate(origin.xyz) * RPY(origin.rpy) * Rot(axis, q)
   * The rpy term is the part the old hand-copied version dropped — four of these
   * joints carry a +/-90 degree frame rotation, which is why the rendered pose
   * did not match the real arm.
   */
  function solve(angles: Record<string, number>): Map<string, THREE.Matrix4> {
    const frames = new Map<string, THREE.Matrix4>()
    if (!chain) return frames
    frames.set(chain.root, new THREE.Matrix4())

    const euler = new THREE.Euler()
    const axis = new THREE.Vector3()

    // Parents always precede children in a URDF chain walk, but resolve
    // defensively so a branch cannot be skipped.
    let remaining = [...chain.joints]
    let guard = remaining.length + 1
    while (remaining.length && guard-- > 0) {
      const next: UrdfJoint[] = []
      for (const j of remaining) {
        const parent = frames.get(j.parent)
        if (!parent) { next.push(j); continue }

        const T = parent.clone()
        T.multiply(new THREE.Matrix4().makeTranslation(j.xyz[0], j.xyz[1], j.xyz[2]))
        // URDF rpy is fixed-axis XYZ, which is three.js intrinsic 'ZYX'.
        euler.set(j.rpy[0], j.rpy[1], j.rpy[2], 'ZYX')
        T.multiply(new THREE.Matrix4().makeRotationFromEuler(euler))

        if (j.type === 'revolute' || j.type === 'continuous') {
          const q = angles[j.name] ?? 0
          axis.set(j.axis[0], j.axis[1], j.axis[2]).normalize()
          T.multiply(new THREE.Matrix4().makeRotationAxis(axis, q))
        }
        frames.set(j.child, T)
      }
      remaining = next
    }
    return frames
  }

  // ------------------------------------------------------------------
  // The moving jaw
  // ------------------------------------------------------------------

  let jawJoint: UrdfJoint | null = null
  let jawTipLocal: THREE.Vector3 | null = null

  /**
   * The gripper joint's own segment runs from the gripper body to the jaw's
   * *pivot*, and a pivot does not move when you rotate about it — which is why
   * nothing on screen opened or closed. Nothing hangs off the jaw in the URDF
   * either, so there was no geometry downstream to carry the motion.
   *
   * Give the jaw a segment of its own, sized from the URDF rather than guessed:
   * take the fixed jaw's tip, expressed in the moving jaw's frame at the joint's
   * zero. The two tips then meet when the gripper is shut, and swinging the
   * joint carries the moving tip away along the arc the real jaw travels.
   */
  function deriveJaw() {
    jawJoint = null
    jawTipLocal = null
    if (!chain) return

    const gj = chain.joints.find(j => j.name === 'gripper' && j.type === 'revolute')
    if (!gj) return
    // The fixed jaw is the other thing hanging off the same gripper body.
    const fixed = chain.joints.find(j => j.parent === gj.parent && j.name !== gj.name)
    const tipLink = fixed?.child ?? chain.tip

    const shut = solve({})   // every joint at its URDF zero
    const jawM = shut.get(gj.child)
    const tipM = shut.get(tipLink)
    if (!jawM || !tipM) return

    jawJoint = gj
    jawTipLocal = positionOf(tipM).applyMatrix4(jawM.clone().invert())
  }

  /**
   * The URDF measures the gripper from shut (0) to fully open (+100 deg).
   * Calibration zeroes every joint at mid-travel instead, so the follower
   * reports the gripper as roughly -66..+66. Feeding that in raw would draw the
   * moving jaw halfway open at rest and swung clean through the fixed jaw at the
   * bottom of its travel, so map calibrated travel onto the URDF's own limits.
   *
   * Which end of the calibrated range is "shut" is not recorded anywhere, so
   * assume the low end, matching the nominal 0..100 fallback range.
   */
  function gripperRad(deg: number, r: { min: number; max: number } | undefined): number {
    const j = jawJoint
    if (!j || j.lower == null || j.upper == null || !r || r.max <= r.min) {
      return deg * Math.PI / 180
    }
    const f = Math.min(1, Math.max(0, (deg - r.min) / (r.max - r.min)))
    return j.lower + f * (j.upper - j.lower)
  }

  function positionOf(m: THREE.Matrix4): THREE.Vector3 {
    return new THREE.Vector3().setFromMatrixPosition(m)
  }

  function anglesFrom(source: Partial<Record<MotorName, number>>): Record<string, number> {
    const out: Record<string, number> = {}
    for (const n of MOTOR_NAMES) {
      const deg = source[n]
      if (deg == null) continue
      out[n] = n === 'gripper'
        ? gripperRad(deg, $ranges.gripper)
        : deg * Math.PI / 180
    }
    return out
  }

  function drawInto(tubes: THREE.Mesh[], dots: THREE.Mesh[] | null,
                    jaw: THREE.Mesh | null, angles: Record<string, number>) {
    if (!chain) return
    const frames = solve(angles)
    chain.joints.forEach((j, i) => {
      const a = frames.get(j.parent), b = frames.get(j.child)
      if (!a || !b) return
      const pb = positionOf(b)
      orient(tubes[i], positionOf(a), pb)
      if (dots && dots[i]) dots[i].position.copy(pb)
    })

    if (jaw && jawJoint && jawTipLocal) {
      const m = frames.get(jawJoint.child)
      if (m) orient(jaw, positionOf(m), jawTipLocal.clone().applyMatrix4(m))
    }
  }

  function hideGhost() {
    for (const g of ghostBones) g.visible = false
    if (ghostJawBone) ghostJawBone.visible = false
  }

  function buildScene() {
    scene = new THREE.Scene()
    scene.background = new THREE.Color(0x111111)

    camera = new THREE.PerspectiveCamera(45, 1, 0.001, 20)

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

    // Tubes need light to read as solid. Two directionals plus a generous
    // ambient keeps every face of the arm legible from any orbit angle.
    scene.add(new THREE.AmbientLight(0xffffff, 1.8))
    const key = new THREE.DirectionalLight(0xffffff, 2.4)
    key.position.set(0.5, 1.0, 0.7)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0x9fc4ff, 1.2)
    fill.position.set(-0.6, 0.3, -0.5)
    scene.add(fill)

    const grid = new THREE.GridHelper(0.6, 12, 0x3d4854, 0x252d35)
    scene.add(grid)

    robotRoot = new THREE.Group()
    // Z-up (URDF) -> Y-up (three.js).
    robotRoot.rotation.x = -Math.PI / 2
    scene.add(robotRoot)

    resize()
  }

  function resize() {
    if (!renderer || !canvas.clientWidth) return
    const { clientWidth: w, clientHeight: h } = canvas
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }

  function placeCamera() {
    camera.position.set(
      Math.sin(yaw) * Math.cos(pitch) * dist,
      Math.sin(pitch) * dist,
      Math.cos(yaw) * Math.cos(pitch) * dist,
    )
    camera.lookAt(0, 0.12, 0)
  }

  function animate() {
    animId = requestAnimationFrame(animate)
    resize()
    placeCamera()
    renderer.render(scene, camera)
  }

  function onPointerDown(e: PointerEvent) {
    dragging = true; lastX = e.clientX; lastY = e.clientY
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging) return
    yaw -= (e.clientX - lastX) * 0.01
    pitch = Math.max(-1.2, Math.min(1.4, pitch + (e.clientY - lastY) * 0.01))
    lastX = e.clientX; lastY = e.clientY
  }
  function onPointerUp() { dragging = false }
  function onWheel(e: WheelEvent) {
    e.preventDefault()
    dist = Math.max(0.25, Math.min(1.6, dist + e.deltaY * 0.0006))
  }

  onMount(() => {
    buildScene()
    animate()
    // Shared with the joint console; the call is de-duplicated.
    loadRanges()
    loadChain()
    window.addEventListener('resize', resize)
  })

  onDestroy(() => {
    cancelAnimationFrame(animId)
    renderer?.dispose()
    window.removeEventListener('resize', resize)
  })

  $: if (chain && bones.length && $ranges) {
    drawInto(bones, jointDots, jawBone, anglesFrom(
      Object.fromEntries(MOTOR_NAMES.map(n => [n, $robotState.joints[n]?.pos])) as any))
  }

  $: if (chain && ghostBones.length) {
    const lead = showLeader ? $leaderState?.positions : null
    if (lead) drawInto(ghostBones, null, ghostJawBone, anglesFrom(lead))
    else hideGhost()
  }
</script>

<div class="arm3d-wrap">
  <div class="label">{label}</div>

  {#if showLeader && $leaderState}
    <div class="key">
      <span class="k-follower">follower</span>
      <span class="k-leader">leader</span>
    </div>
  {/if}

  {#if chainError}
    <div class="err">
      <span>3D view unavailable</span>
      <span class="why">{chainError}</span>
    </div>
  {/if}

  <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
  <canvas
    bind:this={canvas}
    on:pointerdown={onPointerDown}
    on:pointermove={onPointerMove}
    on:pointerup={onPointerUp}
    on:pointercancel={onPointerUp}
    on:wheel={onWheel}
    class:grabbing={dragging}
  ></canvas>
</div>

<style>
  .arm3d-wrap {
    position: relative; width: 100%; height: 100%;
    border-radius: 4px; overflow: hidden; background: #111;
  }
  .label {
    position: absolute; top: 8px; left: 10px;
    font-size: 10px; letter-spacing: 0.11em; text-transform: uppercase;
    color: #6b7681; font-weight: 600; z-index: 1; pointer-events: none;
  }
  canvas { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
  canvas.grabbing { cursor: grabbing; }

  .key {
    position: absolute; top: 8px; right: 10px;
    display: flex; gap: 10px; font-size: 10px; z-index: 1; pointer-events: none;
  }
  .k-follower { color: #5fe0d0; }
  .k-leader { color: #a8b6c2; }
  .k-follower::before, .k-leader::before {
    content: ''; display: inline-block; width: 12px; height: 4px;
    margin-right: 4px; vertical-align: middle; border-radius: 2px;
    background: currentColor;
  }
  .k-leader::before { opacity: 0.35; }

  .err {
    position: absolute; inset: 0; z-index: 2;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 6px; background: rgba(13,16,19,0.9);
    color: #b6c0c8; font-size: 12px; text-align: center; padding: 16px;
  }
  .err .why { font-size: 11px; color: #d8a442; }
</style>
