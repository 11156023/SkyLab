import { useEffect, useRef } from "react"
import * as THREE from "three"

interface Background3DProps {
  isDark: boolean
}

// Colour palettes for each theme
const DARK = {
  fog: 0x0a1020,
  ridge1: 0x172260, // far  – deep navy
  ridge2: 0x271c55, // mid  – indigo
  ridge3: 0x2d1438, // near – dark plum
  ground: 0x0c1230,
  star: 0xddeeff,
  starOpacity: 0.85,
  fly: 0xffa040,
  ambient: { color: 0x111133, intensity: 2.5 },
  sun: { color: 0xd45520, intensity: 4.0, pos: new THREE.Vector3(0, 3, -18) },
  fill: { color: 0x2255bb, intensity: 1.0 },
  hemi: { sky: 0x1a2060, ground: 0x0d1030, intensity: 0.6 },
}

const LIGHT = {
  fog: 0xc8dff5,
  ridge1: 0x7aaac8, // far  – soft sky-blue
  ridge2: 0x8faab8, // mid  – muted slate
  ridge3: 0x6e8fa4, // near – deeper muted blue-slate
  ground: 0x9abdd4,
  star: 0xffffff,
  starOpacity: 0, // no stars during daytime
  fly: 0xfff0c0, // warm white dust motes
  ambient: { color: 0x99b8d8, intensity: 4.0 },
  sun: { color: 0xffe090, intensity: 3.5, pos: new THREE.Vector3(2, 12, -5) },
  fill: { color: 0xc8e0f8, intensity: 1.2 },
  hemi: { sky: 0xb8d8f0, ground: 0xd4c8a0, intensity: 1.0 },
}

export function Background3D({ isDark }: Background3DProps) {
  const mountRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    let w = mount.clientWidth
    let h = mount.clientHeight
    const P = isDark ? DARK : LIGHT

    // ─── Renderer ─────────────────────────────────────────────────────────────
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(w, h)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(0x000000, 0)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(P.fog, isDark ? 0.03 : 0.015)

    const camera = new THREE.PerspectiveCamera(58, w / h, 0.1, 120)
    camera.position.set(0, 2.5, 10)
    camera.lookAt(0, 0, 0)

    // ─── Mountain ridges ──────────────────────────────────────────────────────
    function buildRidge(
      segments: number,
      planeW: number,
      planeD: number,
      heightScale: number,
      color: number,
      zPos: number,
      yBase: number,
    ) {
      const geo = new THREE.PlaneGeometry(planeW, planeD, segments, segments)
      geo.rotateX(-Math.PI / 2)
      const pos = geo.attributes.position as THREE.BufferAttribute
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i)
        const z = pos.getZ(i)
        const elevation =
          Math.sin(x * 0.4) * Math.cos(z * 0.3) * 1.6 +
          Math.sin(x * 0.85 + 1.3) * 0.9 +
          Math.sin(x * 0.22 + z * 0.12) * 0.55 +
          Math.max(0, Math.sin(x * 0.62 + 0.5)) * 0.75
        pos.setY(i, elevation * heightScale)
      }
      pos.needsUpdate = true
      geo.computeVertexNormals()
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: isDark ? 0.88 : 0.75,
        metalness: isDark ? 0.05 : 0.0,
      })
      const mesh = new THREE.Mesh(geo, mat)
      mesh.position.set(0, yBase, zPos)
      return mesh
    }

    const ridge1 = buildRidge(50, 40, 18, 1.9, P.ridge1, -10, -1.0)
    const ridge2 = buildRidge(50, 32, 14, 1.6, P.ridge2, -3, -1.2)
    const ridge3 = buildRidge(40, 26, 10, 1.3, P.ridge3, 3, -1.5)
    scene.add(ridge1, ridge2, ridge3)

    // Ground / lake
    const groundGeo = new THREE.PlaneGeometry(60, 30)
    groundGeo.rotateX(-Math.PI / 2)
    const groundMat = new THREE.MeshStandardMaterial({
      color: P.ground,
      roughness: isDark ? 0.15 : 0.4,
      metalness: isDark ? 0.9 : 0.3,
    })
    const ground = new THREE.Mesh(groundGeo, groundMat)
    ground.position.set(0, -2.8, 5)
    scene.add(ground)

    // ─── Stars (only visible in dark mode) ────────────────────────────────────
    const STAR_COUNT = 360
    const starPos = new Float32Array(STAR_COUNT * 3)
    for (let i = 0; i < STAR_COUNT; i++) {
      starPos[i * 3 + 0] = (Math.random() - 0.5) * 80
      starPos[i * 3 + 1] = Math.random() * 18 + 4
      starPos[i * 3 + 2] = (Math.random() - 0.5) * 50 - 5
    }
    const starGeo = new THREE.BufferGeometry()
    starGeo.setAttribute("position", new THREE.BufferAttribute(starPos, 3))
    const starMat = new THREE.PointsMaterial({
      color: P.star,
      size: 0.07,
      transparent: true,
      opacity: P.starOpacity,
      sizeAttenuation: true,
    })
    scene.add(new THREE.Points(starGeo, starMat))

    // ─── Floating particles (fireflies / dust motes) ──────────────────────────
    const FLY_COUNT = 80
    const flyPos = new Float32Array(FLY_COUNT * 3)
    for (let i = 0; i < FLY_COUNT; i++) {
      flyPos[i * 3 + 0] = (Math.random() - 0.5) * 20
      flyPos[i * 3 + 1] = Math.random() * 4 - 1
      flyPos[i * 3 + 2] = (Math.random() - 0.5) * 12 + 2
    }
    const flyGeo = new THREE.BufferGeometry()
    flyGeo.setAttribute("position", new THREE.BufferAttribute(flyPos, 3))
    const flyMat = new THREE.PointsMaterial({
      color: P.fly,
      size: 0.04,
      transparent: true,
      opacity: 0.7,
    })
    const particles = new THREE.Points(flyGeo, flyMat)
    scene.add(particles)

    // ─── Lights ───────────────────────────────────────────────────────────────
    scene.add(new THREE.AmbientLight(P.ambient.color, P.ambient.intensity))

    const sun = new THREE.DirectionalLight(P.sun.color, P.sun.intensity)
    sun.position.copy(P.sun.pos)
    scene.add(sun)

    const fill = new THREE.DirectionalLight(P.fill.color, P.fill.intensity)
    fill.position.set(0, 6, 12)
    scene.add(fill)

    scene.add(
      new THREE.HemisphereLight(P.hemi.sky, P.hemi.ground, P.hemi.intensity),
    )

    // ─── Mouse tracking ───────────────────────────────────────────────────────
    const mouse = { x: 0, y: 0 }
    const camTarget = { x: 0, y: 2.5 }

    const onMouseMove = (e: MouseEvent) => {
      mouse.x = (e.clientX / w - 0.5) * 2
      mouse.y = (e.clientY / h - 0.5) * 2
    }
    window.addEventListener("mousemove", onMouseMove)

    // ─── Animation loop ───────────────────────────────────────────────────────
    let animId: number
    let t = 0

    const animate = () => {
      animId = requestAnimationFrame(animate)
      t += 0.004

      if (isDark) {
        starMat.opacity = 0.65 + Math.sin(t * 1.8) * 0.2
      }

      flyMat.opacity = (isDark ? 0.5 : 0.35) + Math.sin(t * 3.2 + 1) * 0.2
      const fp = flyGeo.attributes.position as THREE.BufferAttribute
      for (let i = 0; i < FLY_COUNT; i++) {
        fp.setY(i, fp.getY(i) + Math.sin(t + i * 0.7) * 0.002)
        fp.setX(i, fp.getX(i) + Math.cos(t * 0.5 + i * 0.3) * 0.001)
      }
      fp.needsUpdate = true

      ridge1.position.x = Math.sin(t * 0.18) * 0.25
      ridge2.position.x = Math.sin(t * 0.13 + 1.2) * 0.18
      ridge3.position.x = Math.sin(t * 0.09 + 2.4) * 0.1

      camTarget.x += (mouse.x * 1.8 - camTarget.x) * 0.045
      camTarget.y += (-mouse.y * 0.6 + 2.5 - camTarget.y) * 0.045
      camera.position.x = camTarget.x
      camera.position.y = camTarget.y
      camera.lookAt(camTarget.x * 0.25, 0, 0)

      renderer.render(scene, camera)
    }
    animate()

    // ─── Resize ───────────────────────────────────────────────────────────────
    const onResize = () => {
      w = mount.clientWidth
      h = mount.clientHeight
      renderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
    window.addEventListener("resize", onResize)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("resize", onResize)
      renderer.dispose()
      if (mount.contains(renderer.domElement))
        mount.removeChild(renderer.domElement)
    }
  }, [isDark])

  return (
    <div
      ref={mountRef}
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: "none" }}
    />
  )
}
