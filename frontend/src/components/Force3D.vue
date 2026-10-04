<template>
  <div class="force3d-wrap">
    <div ref="mountRef" class="force3d-mount"></div>
    <div v-if="!settled" class="f3d-hint">正在计算 3D 力导布局…</div>
    <div class="f3d-ops">
      <a-tooltip title="复位视角">
        <a-button size="small" @click="resetView">🎯 复位</a-button>
      </a-tooltip>
      <a-tooltip :title="autoRotate ? '暂停自动旋转' : '开启自动旋转'">
        <a-button size="small" @click="autoRotate = !autoRotate">{{ autoRotate ? '⏸ 停转' : '▶ 旋转' }}</a-button>
      </a-tooltip>
    </div>
    <div class="f3d-tip">拖拽旋转 · 滚轮缩放 · 点击节点查看</div>
    <div v-if="hoverText" class="f3d-hover">{{ hoverText }}</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export interface FNode {
  id: string
  label: string
  sub?: string
  color?: string
  /** 相对半径权重，默认 1 */
  size?: number
}
export interface FEdge {
  source: string
  target: string
  color?: string
}

const props = withDefaults(
  defineProps<{
    nodes: FNode[]
    edges?: FEdge[]
    /** 超过该数量的节点不渲染文字标签，默认 140 */
    labelLimit?: number
  }>(),
  { edges: () => [], labelLimit: 140 },
)
const emit = defineEmits<{ (e: 'node-click', id: string): void }>()

const mountRef = ref<HTMLDivElement>()
const settled = ref(false)
const hoverText = ref('')
const autoRotate = ref(true)
let rotating = true

// ---------- three 资源 ----------
let renderer: THREE.WebGLRenderer
let scene: THREE.Scene
let camera: THREE.PerspectiveCamera
let controls: OrbitControls
let animId = 0
let rafRunning = false

const clickable = new THREE.Group()
const edgeGroup = new THREE.Group()
const nodeMeshes = new Map<string, THREE.Mesh>()
const nodeSprites = new Map<string, THREE.Sprite>()

const PALETTE = [
  '#5b9bd5', '#ed7d31', '#a5a5a5', '#ffc000', '#70ad47', '#c00000',
  '#4bacc6', '#8064a2', '#f2a7c2', '#2f5597', '#548235', '#9e480e',
]
function hashColor(seed: string): string {
  let h = 7
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}
function colorOf(n: FNode) {
  return n.color || hashColor(n.id)
}

// ---------- 布局状态 ----------
interface P3 { x: number; y: number; z: number; vx: number; vy: number; vz: number }
const posById = new Map<string, P3>()
const nodesCache = reactive<FNode[]>([])
const edgesCache = reactive<FEdge[]>([])
let layoutIter = 0

function resetLayoutData(nodes: FNode[]) {
  posById.clear()
  layoutIter = 0
  const n = Math.max(1, nodes.length)
  const R = 4 + Math.sqrt(n) * 1.6
  nodes.forEach((nd, i) => {
    const y = (i / n) * 2 - 1
    const phi = Math.acos(-1 + 2 * y)
    const theta = i * 2.399963 * Math.PI
    const rr = R * Math.sqrt(1 - y * y)
    posById.set(nd.id, {
      x: rr * Math.cos(theta) + (Math.random() - 0.5) * R * 0.4,
      y: R * y + (Math.random() - 0.5) * R * 0.3,
      z: rr * Math.sin(theta) + (Math.random() - 0.5) * R * 0.4,
      vx: 0, vy: 0, vz: 0,
    })
  })
}

function degreeOf(id: string): number {
  let d = 0
  for (const e of edgesCache) if (e.source === id || e.target === id) d++
  return Math.max(1, d)
}

function radiusOf(nd: FNode, deg: number): number {
  const w = nd.size ?? 1
  return (0.5 + 0.5 * Math.cbrt(Math.max(0.4, w))) * Math.min(6, 0.6 + Math.sqrt(deg) * 0.5)
}

// ---------- 场景构建 ----------
function makeLabelSprite(text: string, color: string): THREE.Sprite {
  const canvas = document.createElement('canvas')
  const w = 512
  const h = 96
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')!
  ctx.clearRect(0, 0, w, h)
  ctx.font = '600 52px "Microsoft YaHei","PingFang SC",sans-serif'
  let final = text
  while (ctx.measureText(final).width > w - 60 && final.length > 1) final = final.slice(0, -1)
  if (final !== text) final += '…'
  ctx.font = '600 52px "Microsoft YaHei","PingFang SC",sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.shadowColor = 'rgba(0,0,0,0.95)'
  ctx.shadowBlur = 12
  ctx.fillStyle = '#ffffff'
  ctx.fillText(final, w / 2, h / 2 + 4)
  ctx.shadowBlur = 0
  ctx.fillStyle = color
  ctx.fillText(final, w / 2, h / 2 + 4)
  const tex = new THREE.CanvasTexture(canvas)
  tex.minFilter = THREE.LinearFilter
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false })
  const sprite = new THREE.Sprite(mat)
  const aspect = w / h
  sprite.scale.set((text.length * 0.5 + 1.2) * (aspect / 5.33), text.length * 0.5 + 1.2, 1)
  sprite.renderOrder = 10
  return sprite
}

function buildScene() {
  const el = mountRef.value!
  const w = el.clientWidth || 640
  const h = el.clientHeight || 480
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(w, h)
  el.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  scene.background = new THREE.Color('#101c2e')

  scene.add(new THREE.AmbientLight(0xffffff, 1.15))
  const pl = new THREE.PointLight(0xffffff, 1.4, 0, 0)
  pl.position.set(60, 90, 70)
  scene.add(pl)
  const dl = new THREE.DirectionalLight(0x9cc4ff, 0.6)
  dl.position.set(-60, -40, -80)
  scene.add(dl)

  camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 4000)
  camera.position.set(0, 26, 60)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.autoRotateSpeed = 0.7
  controls.maxDistance = 2600
  controls.minDistance = 1
  controls.addEventListener('start', () => {
    rotating = false
    autoRotate.value = false
  })
  controls.addEventListener('end', () => {
    rotating = autoRotate.value
  })

  scene.add(clickable)
  scene.add(edgeGroup)
  renderer.domElement.addEventListener('pointermove', onPointerMove)
  renderer.domElement.addEventListener('pointerdown', onPointerDown)
}

function buildObjects() {
  clearObjects()
  const showLabels = nodesCache.length <= props.labelLimit
  const sphereGeo = new THREE.SphereGeometry(1, 22, 22)
  nodesCache.forEach((nd) => {
    const p = posById.get(nd.id)
    if (!p) return
    const deg = degreeOf(nd.id)
    const r = radiusOf(nd, deg)
    const col = new THREE.Color(colorOf(nd))
    const mat = new THREE.MeshStandardMaterial({
      color: col,
      roughness: 0.5,
      metalness: 0.12,
      emissive: col.clone().multiplyScalar(0.15),
    })
    const mesh = new THREE.Mesh(sphereGeo, mat)
    mesh.position.set(p.x, p.y, p.z)
    mesh.scale.setScalar(r)
    mesh.userData.fid = nd.id
    mesh.userData.fname = nd.sub ? `${nd.label} · ${nd.sub}` : nd.label
    mesh.userData.r = r
    clickable.add(mesh)
    nodeMeshes.set(nd.id, mesh)
    if (showLabels) {
      const sp = makeLabelSprite(nd.label, colorOf(nd))
      sp.position.set(p.x, p.y + r + 1.3, p.z)
      scene.add(sp)
      nodeSprites.set(nd.id, sp)
    }
  })
  rebuildEdgeGeometry()
}

function clearObjects() {
  clickable.clear()
  edgeGroup.clear()
  nodeMeshes.forEach((m) => {
    m.geometry.dispose()
    ;(m.material as THREE.Material).dispose()
  })
  nodeMeshes.clear()
  nodeSprites.forEach((s) => {
    s.material.map?.dispose()
    s.material.dispose()
    scene?.remove(s)
  })
  nodeSprites.clear()
}

function rebuildEdgeGeometry() {
  // 清空旧线段（含几何）
  edgeGroup.children.forEach((c) => {
    const g = (c as THREE.LineSegments).geometry
    if (g) g.dispose()
  })
  edgeGroup.clear()
  const groups = new Map<string, { source: string; target: string }[]>()
  const DEFAULT = '#6f94c9'
  for (const e of edgesCache) {
    if (!posById.has(e.source) || !posById.has(e.target)) continue
    const key = e.color || DEFAULT
    const list = groups.get(key) || []
    list.push({ source: e.source, target: e.target })
    groups.set(key, list)
  }
  for (const [color, list] of groups) {
    const arr = new Float32Array(list.length * 6)
    let k = 0
    for (const e of list) {
      const a = posById.get(e.source)!
      const b = posById.get(e.target)!
      arr[k++] = a.x; arr[k++] = a.y; arr[k++] = a.z
      arr[k++] = b.x; arr[k++] = b.y; arr[k++] = b.z
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(arr, 3))
    const mat = new THREE.LineBasicMaterial({
      color: new THREE.Color(color),
      transparent: true,
      opacity: color === DEFAULT ? 0.4 : 0.32,
    })
    edgeGroup.add(new THREE.LineSegments(geo, mat))
  }
}

// 布局期间低频同步：节点/标签位置 + 线段端点
function syncPositions() {
  nodeMeshes.forEach((m) => {
    const p = posById.get(m.userData.fid as string)
    if (p) m.position.set(p.x, p.y, p.z)
  })
  nodeSprites.forEach((sp, id) => {
    const p = posById.get(id)
    if (!p) return
    const m = nodeMeshes.get(id)
    const r = (m?.userData.r as number) || 1
    sp.position.set(p.x, p.y + r + 1.3, p.z)
  })
  rebuildEdgeGeometry()
}

// ---------- 力导向布局 ----------
function layoutStep() {
  if (!nodesCache.length) return
  layoutIter++
  const list: P3[] = []
  nodesCache.forEach((nd) => {
    const p = posById.get(nd.id)
    if (p) list.push(p)
  })
  const n = list.length
  const sparse = n > 300
  const stride = sparse ? Math.max(2, Math.floor(n / 120)) : 1
  // 两两斥力
  for (let i = 0; i < n; i += stride) {
    const a = list[i]
    for (let j = i + 1; j < n; j++) {
      const b = list[j]
      const dx = a.x - b.x
      const dy = a.y - b.y
      const dz = a.z - b.z
      const d2 = Math.max(0.5, dx * dx + dy * dy + dz * dz)
      const d = Math.sqrt(d2)
      const f = Math.min(3.2, 240 / d2)
      const fx = (dx / d) * f
      const fy = (dy / d) * f
      const fz = (dz / d) * f
      a.vx += fx; a.vy += fy; a.vz += fz
      b.vx -= fx; b.vy -= fy; b.vz -= fz
    }
  }
  // 弹簧
  for (const e of edgesCache) {
    const a = posById.get(e.source)
    const b = posById.get(e.target)
    if (!a || !b) continue
    const dx = b.x - a.x
    const dy = b.y - a.y
    const dz = b.z - a.z
    const d = Math.max(0.01, Math.sqrt(dx * dx + dy * dy + dz * dz))
    const f = (d - 5.2) * 0.01
    const fx = (dx / d) * f
    const fy = (dy / d) * f
    const fz = (dz / d) * f
    a.vx += fx; a.vy += fy; a.vz += fz
    b.vx -= fx; b.vy -= fy; b.vz -= fz
  }
  let energy = 0
  for (const p of list) {
    p.vx *= 0.88
    p.vy *= 0.88
    p.vz *= 0.88
    p.vx -= p.x * 0.0022
    p.vy -= p.y * 0.0022
    p.vz -= p.z * 0.0022
    p.x = Math.max(-1000, Math.min(1000, p.x + p.vx))
    p.y = Math.max(-1000, Math.min(1000, p.y + p.vy))
    p.z = Math.max(-1000, Math.min(1000, p.z + p.vz))
    energy += Math.abs(p.vx) + Math.abs(p.vy) + Math.abs(p.vz)
  }
  const avgE = energy / n
  if (layoutIter % 2 === 0) syncPositions()
  if (layoutIter > 240 || avgE < 0.06) {
    syncPositions()
    settled.value = true
    fitView(1.55)
  }
}

function fitView(pad = 1.5) {
  if (!posById.size) return
  let minX = Infinity, maxX = -Infinity
  let minY = Infinity, maxY = -Infinity
  let minZ = Infinity, maxZ = -Infinity
  posById.forEach((p) => {
    if (p.x < minX) minX = p.x
    if (p.x > maxX) maxX = p.x
    if (p.y < minY) minY = p.y
    if (p.y > maxY) maxY = p.y
    if (p.z < minZ) minZ = p.z
    if (p.z > maxZ) maxZ = p.z
  })
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  const cz = (minZ + maxZ) / 2
  const radius = Math.max(9, Math.max(maxX - minX, maxY - minY, maxZ - minZ) / 2)
  controls.target.set(cx, cy, cz)
  const len = radius * pad
  camera.position.set(cx + len * 0.75, cy + len * 0.6, cz + len)
  controls.update()
}

// ---------- 交互 ----------
const pointer = new THREE.Vector2()
const raycaster = new THREE.Raycaster()
function toNdc(e: PointerEvent) {
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
}
function pickNode(e: PointerEvent): THREE.Mesh | null {
  toNdc(e)
  raycaster.setFromCamera(pointer, camera)
  const hits = raycaster.intersectObjects(clickable.children, false)
  return hits.length ? (hits[0].object as THREE.Mesh) : null
}
let hovered: THREE.Mesh | null = null
function onPointerMove(e: PointerEvent) {
  if (!nodeMeshes.size) return
  const hit = pickNode(e)
  if (hit === hovered) return
  if (hovered) hovered.scale.setScalar(hovered.userData.r as number)
  hovered = hit
  if (hit) {
    const r = hit.userData.r as number
    hit.scale.setScalar(r * 1.15)
    hoverText.value = String(hit.userData.fname || hit.userData.fid || '')
  } else {
    hoverText.value = ''
  }
}
let downX = 0
let downY = 0
let downId = ''
function onPointerDown(e: PointerEvent) {
  const hit = pickNode(e)
  downId = hit ? (hit.userData.fid as string) : ''
  downX = e.clientX
  downY = e.clientY
  const up = (ev: PointerEvent) => {
    renderer.domElement.removeEventListener('pointerup', up)
    if (downId && Math.abs(ev.clientX - downX) < 6 && Math.abs(ev.clientY - downY) < 6) {
      emit('node-click', downId)
    }
  }
  renderer.domElement.addEventListener('pointerup', up)
}

// ---------- 对外 API ----------
function resetView() {
  fitView(1.55)
}
function focusNode(id: string) {
  const p = posById.get(id)
  if (!p) return
  const dir = camera.position.clone().sub(controls.target)
  const dist = Math.max(14, dir.length())
  const dest = new THREE.Vector3(p.x, p.y, p.z)
  const startPos = camera.position.clone()
  const startTarget = controls.target.clone()
  const offset = dir.normalize().multiplyScalar(dist)
  const t0 = performance.now()
  const dur = 620
  const step = () => {
    const k = Math.min(1, (performance.now() - t0) / dur)
    const ease = 1 - Math.pow(1 - k, 3)
    camera.position.lerpVectors(startPos, dest.clone().add(offset), ease)
    controls.target.lerpVectors(startTarget, dest, ease)
    controls.update()
    if (k < 1) animId = requestAnimationFrame(step)
    else renderer.render(scene, camera)
  }
  cancelAnimationFrame(animId)
  step()
}

// ---------- 渲染循环 ----------
function loop() {
  if (!settled.value) {
    layoutStep()
  }
  controls.autoRotate = autoRotate.value && rotating && settled.value
  controls.update()
  renderer.render(scene, camera)
  animId = requestAnimationFrame(loop)
}

function applyData(nodes: FNode[], edges: FEdge[]) {
  nodesCache.length = 0
  edgesCache.length = 0
  nodes.forEach((n) => nodesCache.push({ ...n }))
  edges.forEach((e) => edgesCache.push({ ...e }))
  if (!nodesCache.length) {
    clearObjects()
    settled.value = true
    return
  }
  resetLayoutData(nodesCache)
  buildObjects()
  settled.value = false
  if (!rafRunning && scene) {
    rafRunning = true
    loop()
  }
}

watch(() => props.nodes, (v) => applyData(v, props.edges))
watch(() => props.edges, (v) => applyData(props.nodes, v))

function onResize() {
  if (!renderer) return
  const el = mountRef.value
  if (!el) return
  const w = el.clientWidth
  const h = el.clientHeight
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

onMounted(() => {
  buildScene()
  window.addEventListener('resize', onResize)
  applyData(props.nodes, props.edges)
  rafRunning = true
  loop()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animId)
  window.removeEventListener('resize', onResize)
  renderer?.domElement.removeEventListener('pointermove', onPointerMove)
  renderer?.domElement.removeEventListener('pointerdown', onPointerDown)
  controls?.dispose()
  clearObjects()
  edgeGroup.children.forEach((c) => (c as THREE.LineSegments).geometry?.dispose())
  scene?.traverse((o) => {
    const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined
    if (g && g.dispose) g.dispose()
  })
  renderer?.dispose()
  renderer?.domElement.remove()
})

defineExpose({ focusNode, resetView })
</script>

<style scoped>
.force3d-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 460px;
  border-radius: 10px;
  overflow: hidden;
  background: #101c2e;
}
.force3d-mount {
  width: 100%;
  height: 100%;
}
.f3d-hint {
  position: absolute;
  left: 12px;
  top: 10px;
  color: #cfe3ff;
  font-size: 13px;
  background: rgba(10, 24, 46, 0.72);
  padding: 4px 10px;
  border-radius: 6px;
  pointer-events: none;
}
.f3d-ops {
  position: absolute;
  right: 12px;
  top: 10px;
  display: flex;
  gap: 6px;
}
.f3d-tip {
  position: absolute;
  left: 12px;
  bottom: 10px;
  color: rgba(220, 235, 255, 0.78);
  font-size: 12px;
  pointer-events: none;
  background: rgba(10, 24, 46, 0.55);
  padding: 3px 10px;
  border-radius: 6px;
}
.f3d-hover {
  position: absolute;
  right: 12px;
  bottom: 34px;
  color: #fff;
  font-size: 13px;
  max-width: 46%;
  text-align: right;
  background: rgba(10, 24, 46, 0.75);
  padding: 4px 10px;
  border-radius: 6px;
  pointer-events: none;
  word-break: break-all;
}
</style>
