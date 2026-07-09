// hud-background.js
// Vanilla port of the ironman-hud-ui "command lattice" background.
// Source inspiration: jarvis_ui/components/HudBackground.tsx (feat/ironman-hud-ui branch)
// + HudCorners.tsx
//
// Renders a fixed, pointer-events-none full-screen layer behind the dashboard.
// Drives --hud-live for accent glows. Uses subtle particle count by default
// to coexist with the rings canvas on /brain.
//
// API:
//   mountHudBackground({ variant?: 'full' | 'subtle' }) -> { unmount }
//   addHudCorners(container, { accent?: 'cyan'|'gold', size?, inset? })

const PALETTES = [
  { r: 39, g: 224, b: 255 },   // arc-reactor cyan
  { r: 90, g: 200, b: 255 },
  { r: 255, g: 181, b: 61 },   // repulsor gold
  { r: 255, g: 96, b: 40 },
  { r: 255, g: 60, b: 50 },
  { r: 120, g: 180, b: 255 },
];

let mounted = null; // singleton guard { wrapper, raf, cleanup }

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function rgba(c, alpha, ro = 0, go = 0, bo = 0) {
  const ch = (v) => Math.max(0, Math.min(255, Math.round(v)));
  return `rgba(${ch(c.r + ro)}, ${ch(c.g + go)}, ${ch(c.b + bo)}, ${alpha})`;
}

export function addHudCorners(container, { accent = 'cyan', size = 14, inset = 4 } = {}) {
  if (!container || container.querySelector('.hud-corner')) return;
  const color = accent === 'gold'
    ? 'rgba(255,181,61,0.55)'
    : 'rgba(39,224,255,0.55)';
  const dim = `width:${size}px;height:${size}px;`;
  const mk = (pos) => {
    const s = document.createElement('span');
    s.className = 'hud-corner';
    s.style.cssText = `position:absolute;pointer-events:none;border-color:${color};${dim}${pos};z-index:4;filter:drop-shadow(0 0 5px ${color})`;
    return s;
  };
  const tl = mk(`top:${inset}px;left:${inset}px;border-left:2px solid;border-top:2px solid`);
  const tr = mk(`top:${inset}px;right:${inset}px;border-right:2px solid;border-top:2px solid`);
  const bl = mk(`bottom:${inset}px;left:${inset}px;border-left:2px solid;border-bottom:2px solid`);
  const br = mk(`bottom:${inset}px;right:${inset}px;border-right:2px solid;border-bottom:2px solid`);
  container.append(tl, tr, bl, br);
}

export function mountHudBackground({ variant = 'subtle' } = {}) {
  if (mounted) return mounted.api;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const subtle = variant === 'subtle';

  // Wrapper (fixed, lowest z, no pointer)
  const wrapper = document.createElement('div');
  wrapper.className = 'hud-bg-layer';
  wrapper.setAttribute('aria-hidden', 'true');
  wrapper.style.cssText = [
    'position:fixed',
    'inset:0',
    'z-index:0',
    'pointer-events:none',
    'overflow:hidden',
    subtle ? 'opacity:0.42' : 'opacity:1'
  ].join(';');

  // Starfield
  const starfield = document.createElement('div');
  starfield.style.cssText = [
    'position:absolute',
    'inset:0',
    'background-image:radial-gradient(circle, rgba(201,231,255,0.45) 0 1px, transparent 1.5px), radial-gradient(circle, rgba(111,201,255,0.28) 0 1px, transparent 1.3px)',
    'background-position:7% 11%, 68% 19%',
    'background-size:171px 179px, 263px 211px',
    'opacity:0.22'
  ].join(';');

  // Orb glow (color driven by loop)
  const orb = document.createElement('div');
  orb.style.cssText = [
    'position:absolute',
    'left:50%',
    'top:50%',
    'transform:translate(-50%,-50%)',
    'width:min(78vw,960px)',
    'aspect-ratio:1',
    'border-radius:9999px',
    'filter:blur(18px)',
    'opacity:0.9'
  ].join(';');

  // Neural lattice canvas
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'position:absolute;inset:0;display:block;width:100%;height:100%;filter:saturate(1.15) contrast(1.06)';

  // Perspective grid floor
  const grid = document.createElement('div');
  grid.style.cssText = [
    'position:absolute',
    'left:-8vw',
    'right:-8vw',
    'bottom:-7vh',
    'height:23vh',
    'transform:perspective(390px) rotateX(65deg)',
    'transform-origin:bottom center',
    'background-image:linear-gradient(rgba(57,200,255,0.26) 1px, transparent 1px), linear-gradient(90deg, rgba(57,200,255,0.22) 1px, transparent 1px)',
    'background-size:44px 27px',
    'border-top:1px solid rgba(80,210,255,0.36)',
    'box-shadow:0 -20px 46px rgba(39,224,255,0.12)',
    'opacity:0.72'
  ].join(';');

  wrapper.append(starfield, orb, canvas, grid);
  document.body.appendChild(wrapper);

  const ctx = canvas.getContext('2d', { alpha: true });
  let raf = 0;
  let particles = [];
  let rotationX = 0.00145, rotationY = 0.00225, rotationZ = -0.0009;
  let targetRotationX = rotationX, targetRotationY = rotationY, targetRotationZ = rotationZ;
  let nextSpinShift = 0;
  let colorIndex = 0, nextColorIndex = 1, transition = 0;
  let sphereRadius = 0, sphereCenterY = 0.51;
  let PARTICLE_COUNT = 650;
  let MAX_DISTANCE = 82;
  let lastInitW = 0, lastInitH = 0;

  class Particle {
    constructor(core = false) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(Math.random() * 2 - 1);
      const shell = core ? 0.14 + Math.random() * 0.58 : 0.66 + Math.random() * 0.36;
      const r = sphereRadius * shell;
      this.x3d = r * Math.sin(phi) * Math.cos(theta);
      this.y3d = r * Math.sin(phi) * Math.sin(theta);
      this.z3d = r * Math.cos(phi);
      this.core = core;
      this.pulse = Math.random() * Math.PI * 2;
      this.pulseSpeed = 0.012 + Math.random() * 0.035;
      this.baseSize = core ? 1.05 + Math.random() * 3.15 : 1.55 + Math.random() * 4.05;
      this.x = 0; this.y = 0; this.scale = 1;
    }
    rotate() {
      const cosX = Math.cos(rotationX), sinX = Math.sin(rotationX);
      const cosY = Math.cos(rotationY), sinY = Math.sin(rotationY);
      const cosZ = Math.cos(rotationZ), sinZ = Math.sin(rotationZ);
      const y = this.y3d * cosX - this.z3d * sinX;
      const z = this.z3d * cosX + this.y3d * sinX;
      const x = this.x3d * cosY - z * sinY;
      const z2 = z * cosY + this.x3d * sinY;
      this.x3d = x * cosZ - y * sinZ;
      this.y3d = y * cosZ + x * sinZ;
      this.z3d = z2;
    }
    project(w, h) {
      const perspective = Math.max(w, h) * 0.72;
      const cameraDistance = Math.max(w, h) * 0.83;
      const scale = perspective / (perspective + this.z3d + cameraDistance);
      this.x = this.x3d * scale + w / 2;
      this.y = this.y3d * scale + h * sphereCenterY;
      this.scale = scale;
      this.pulse += this.pulseSpeed;
    }
  }

  function configureDensity() {
    const w = window.innerWidth;
    let count = w < 760 ? 300 : w < 1100 ? 450 : 650;
    const cores = navigator.hardwareConcurrency || 8;
    if (cores <= 4) count = Math.min(count, 260);
    if (prefersReduced) count = Math.min(count, 220);
    if (subtle) count = Math.round(count * 0.55);
    PARTICLE_COUNT = count;
    MAX_DISTANCE = sphereRadius * 0.16;
  }

  function initParticles() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(new Particle(i < PARTICLE_COUNT * 0.48));
    }
  }

  function resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    sphereRadius = Math.min(w, h) * (w < 760 ? 0.5 : 0.47);
    sphereCenterY = w < 760 ? 0.34 : 0.51;
    configureDensity();

    const bigW = w !== lastInitW;
    const bigH = Math.abs(h - lastInitH) > lastInitH * 0.15;
    if (bigW || bigH || particles.length === 0) {
      lastInitW = w;
      lastInitH = h;
      initParticles();
    }
  }

  function currentColor() {
    transition += 0.0022;
    if (transition >= 1) {
      transition = 0;
      colorIndex = nextColorIndex;
      nextColorIndex = (nextColorIndex + 1) % PALETTES.length;
    }
    const from = PALETTES[colorIndex];
    const to = PALETTES[nextColorIndex];
    return {
      r: Math.round(lerp(from.r, to.r, transition)),
      g: Math.round(lerp(from.g, to.g, transition)),
      b: Math.round(lerp(from.b, to.b, transition)),
    };
  }

  function updateSpin() {
    if (prefersReduced) return;
    const now = performance.now();
    if (now > nextSpinShift) {
      targetRotationX = (Math.random() - 0.5) * 0.006;
      targetRotationY = (Math.random() - 0.5) * 0.006;
      targetRotationZ = (Math.random() - 0.5) * 0.0032;
      nextSpinShift = now + 1500 + Math.random() * 2600;
    }
    rotationX += (targetRotationX - rotationX) * 0.018;
    rotationY += (targetRotationY - rotationY) * 0.018;
    rotationZ += (targetRotationZ - rotationZ) * 0.018;
  }

  function drawScene() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const color = currentColor();
    updateSpin();

    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = 'rgba(3, 2, 10, 0.28)';
    ctx.fillRect(0, 0, w, h);

    // Live accent color for CSS use (borders, glows)
    document.documentElement.style.setProperty('--hud-live', `rgb(${color.r}, ${color.g}, ${color.b})`);
    orb.style.background = `radial-gradient(circle, rgba(${color.r},${color.g},${color.b},0.3) 0%, rgba(${color.r},${color.g},${color.b},0.13) 32%, transparent 66%)`;

    particles.forEach(p => { p.rotate(); p.project(w, h); });

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    // connections
    for (let i = 0; i < particles.length; i++) {
      const p1 = particles[i];
      for (let j = i + 1; j < particles.length; j++) {
        const p2 = particles[j];
        const dx = p1.x3d - p2.x3d, dy = p1.y3d - p2.y3d, dz = p1.z3d - p2.z3d;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (dist < MAX_DISTANCE) {
          const depth = Math.min(1.4, (p1.scale + p2.scale) * 0.75);
          const alpha = (1 - dist / MAX_DISTANCE) * depth * 0.33;
          ctx.beginPath();
          ctx.moveTo(p1.x, p1.y);
          ctx.lineTo(p2.x, p2.y);
          ctx.strokeStyle = rgba(color, alpha, 35, 28, 20);
          ctx.lineWidth = (p1.core && p2.core) ? 0.42 : 0.72;
          ctx.stroke();
        }
      }
    }

    // particles
    particles.forEach(p => {
      const flicker = 0.65 + Math.sin(p.pulse) * 0.35;
      const size = p.baseSize * p.scale * flicker;
      const centerBias = Math.abs(p.x3d) + Math.abs(p.y3d) < sphereRadius * 0.42;
      const alpha = (p.core || centerBias) ? 0.72 : 0.54;
      ctx.beginPath();
      ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
      ctx.fillStyle = centerBias
        ? `rgba(255,252,255,${alpha})`
        : rgba(color, alpha, 52, 42, 34);
      ctx.fill();
      if (size > 1.35) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, size * 3.2, 0, Math.PI * 2);
        ctx.fillStyle = rgba(color, p.core ? 0.055 : 0.1);
        ctx.fill();
      }
    });

    // central halo
    const haloY = h * (sphereCenterY + 0.01);
    const halo = ctx.createRadialGradient(w / 2, haloY, 0, w / 2, haloY, sphereRadius * 1.05);
    halo.addColorStop(0, rgba(color, 0.16, 30, 20, 10));
    halo.addColorStop(0.42, rgba(color, 0.055));
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, w, h);

    ctx.restore();
    raf = requestAnimationFrame(drawScene);
  }

  const onResize = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 150);
  };
  let resizeTimer = null;

  window.addEventListener('resize', onResize, { passive: true });
  window.addEventListener('orientationchange', onResize, { passive: true });
  if (window.visualViewport) window.visualViewport.addEventListener('resize', onResize, { passive: true });

  resize();

  if (prefersReduced) {
    // static frame
    const w = window.innerWidth, h = window.innerHeight;
    particles.forEach(p => p.project(w, h));
    drawScene();
  } else {
    drawScene();
  }

  const unmount = () => {
    cancelAnimationFrame(raf);
    clearTimeout(resizeTimer);
    window.removeEventListener('resize', onResize);
    window.removeEventListener('orientationchange', onResize);
    if (window.visualViewport) window.visualViewport.removeEventListener('resize', onResize);
    if (wrapper && wrapper.parentNode) wrapper.parentNode.removeChild(wrapper);
    // reset live var to avoid leaking
    document.documentElement.style.removeProperty('--hud-live');
    mounted = null;
  };

  mounted = {
    wrapper,
    api: { unmount }
  };
  return mounted.api;
}

export function unmountHudBackground() {
  if (mounted && mounted.api) mounted.api.unmount();
}
