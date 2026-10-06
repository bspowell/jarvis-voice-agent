/* The orb's centrepieces, as 3D holograms made of points: the classic J.A.R.V.I.S. rings,
   Mjölnir and Stormbreaker, and Iron Man's arc reactors.

   Each maker returns { points, motion }:
   - points: 10 numbers a point (PER_POINT): x, y, z, light, size, tint, the way its surface faces
     (all 0 for edges), and how fast it circles the centre on its own (radians a second, for rings
     that turn by themselves). The band's radius is 1, a centrepiece fits inside ~0.55; y is up,
     z comes towards you.
   - motion: how it turns: `spin` (radians a second, round and round) or `sway` (radians, side to
     side), `tilt` (leaning back), and `glow` (light at its heart, for the reactors).
   Edges are see-through, like a wireframe. Surface details (the trinity knot, the faceplate,
   scan lines) know which way their face points, so they show only while it's turned towards you. */
"use strict";

const PER_POINT = 10;
const STEP = 0.008; // spacing of the points along an edge
const TINT = { cyan: 0, ice: 1, copper: 2, red: 3, gold: 4 };
const DEG = Math.PI / 180;
const EDGE = [0, 0, 0]; // no facing: seen from every side

class Shape {
  constructor() {
    this.points = [];
    this.facing = EDGE;
    this.turning = 0;
  }

  onFace(facing, draw) { // details drawn on a surface that faces `facing`
    this.facing = facing;
    draw();
    this.facing = EDGE;
  }

  turningAt(speed, draw) { // a ring that circles the centre by itself, `speed` radians a second
    this.turning = speed;
    draw();
    this.turning = 0;
  }

  dot([x, y, z], light = 1, size = 1.6, tint = TINT.cyan) {
    this.points.push(x, y, z, light, size, tint, ...this.facing, this.turning);
  }

  drawOn(surface, corners, closed, light, size, tint, step = 0.004) {
    // a line drawn flat (x, y), then laid on a curved surface: `surface(x, y)` → a 3D point
    const last = closed ? corners.length : corners.length - 1;
    for (let i = 0; i < last; i++) {
      const [x1, y1] = corners[i];
      const [x2, y2] = corners[(i + 1) % corners.length];
      const n = Math.max(1, Math.round(Math.hypot(x2 - x1, y2 - y1) / step));
      for (let k = 0; k <= n; k++) this.dot(surface(x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n), light, size, tint);
    }
  }

  line(a, b, light, size, tint, step = STEP) {
    const n = Math.max(1, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) / step));
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      this.dot([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t], light, size, tint);
    }
  }

  path(corners, closed, light, size, tint, step) {
    const last = closed ? corners.length : corners.length - 1;
    for (let i = 0; i < last; i++) this.line(corners[i], corners[(i + 1) % corners.length], light, size, tint, step);
  }

  curve(at, light, size, tint, step = STEP) { // `at(t)` for t from 0 to 1, spaced by length
    let length = 0;
    let before = at(0);
    for (let i = 1; i <= 64; i++) {
      const here = at(i / 64);
      length += Math.hypot(here[0] - before[0], here[1] - before[1], here[2] - before[2]);
      before = here;
    }
    const n = Math.max(2, Math.round(length / step));
    for (let i = 0; i <= n; i++) this.dot(at(i / n), light, size, tint);
  }

  ring(radius, z, light, size, tint, from = 0, to = 360) { // a circle facing you, at depth z
    this.curve((t) => polar(radius, from + (to - from) * t, z), light, size, tint);
  }

  sparkle(count, radius, z, light, tint = TINT.ice) { // fine specks of light filling a disc
    for (let i = 0; i < count; i++) {
      const r = radius * Math.sqrt(Math.random());
      this.dot(polar(r, Math.random() * 360, z), light * (0.4 + 0.6 * Math.random()), 1.2 + Math.random() * 1.2, tint);
    }
  }
}

function polar(radius, degrees, z) {
  return [radius * Math.cos(degrees * DEG), radius * Math.sin(degrees * DEG), z];
}

const plus = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const minus = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const times = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const between = (a, b, t) => plus(a, times(minus(b, a), t));
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => times(a, 1 / (Math.hypot(...a) || 1));

function tube(s, from, to, radius, { gap = 0.024, sides = 6, light = 0.3, size = 1.2, tint = TINT.cyan } = {}) {
  // a limb or a handle: rings round the line from `from` to `to`, joined by lengthwise lines.
  // `radius(t)` gives [across, front-to-back] at t (0 at `from`, 1 at `to`).
  const along = unit(minus(to, from));
  const across = Math.abs(along[2]) > 0.9 ? [1, 0, 0] : unit(cross(along, [0, 0, 1]));
  const forward = cross(across, along); // towards you, for an upright tube
  const at = (t, a) => {
    const [ru, rv] = radius(t);
    return plus(between(from, to, t), plus(times(across, ru * Math.cos(a)), times(forward, rv * Math.sin(a))));
  };
  const rings = Math.max(2, Math.round(Math.hypot(...minus(to, from)) / gap));
  for (let i = 0; i <= rings; i++) s.curve((t) => at(i / rings, t * 2 * Math.PI), light, size, tint, 0.007);
  for (let k = 0; k < sides; k++) s.curve((t) => at(t, (k + 0.5) / sides * 2 * Math.PI), light * 0.8, size, tint, 0.008);
  return { at, across, forward, along };
}

function extrude(shape, outline, front, back, light, size, tint) {
  // a flat outline with depth: front and back copies, joined at the corners
  shape.path(outline.map(([x, y]) => [x, y, front]), true, light, size, tint);
  shape.path(outline.map(([x, y]) => [x, y, back]), true, light * 0.45, size * 0.9, tint);
  for (const [x, y] of outline) shape.line([x, y, front], [x, y, back], light * 0.5, size * 0.9, tint);
}

// --- Mjölnir: resting head-down on a rock, its handle leaning, the strap hanging ---
function triquetra(shape, centreX, centreY, z, size) {
  // Three circles whose centres make an equilateral triangle, each passing through the other two
  // centres; the trinity knot is where each pair overlaps: three pointed leaves (six 120° arcs).
  const centres = [90, 210, 330].map((a) => [centreX + size * Math.cos(a * DEG), centreY + size * Math.sin(a * DEG)]);
  const radius = size * Math.sqrt(3);
  for (let i = 0; i < 3; i++) {
    for (const j of [(i + 1) % 3, (i + 2) % 3]) {
      const [cx, cy] = centres[i];
      const towards = Math.atan2(centres[j][1] - cy, centres[j][0] - cx);
      shape.curve((t) => {
        const a = towards + (t - 0.5) * (2 * Math.PI / 3);
        return [cx + radius * Math.cos(a), cy + radius * Math.sin(a), z];
      }, 1.2, 1.8, TINT.ice, 0.005);
    }
  }
}

function aroundSection(section, t) { // a point t (0 to 1) of the way round the head's cross-section
  const along = t * section.length;
  const i = Math.floor(along) % section.length;
  const f = along - Math.floor(along);
  const [y1, z1] = section[i];
  const [y2, z2] = section[(i + 1) % section.length];
  return [y1 + (y2 - y1) * f, z1 + (z2 - z1) * f];
}

function seeded(seed) { // the same "random" numbers every time (mulberry32), so the rock keeps its shape
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const HEAD = { half: 0.27, h: 0.15, chamfer: 0.045, bevel: 0.035, face: 0.8 };
const HANDLE = { length: 0.5, radius: 0.04 };
const LEAN = -30 * DEG; // the handle leans right, so the head's right end goes down
const SINK = 0.035; // how far the head's lowest corner is sunk into the rock

function hammer(s) { // upright, head at the origin running along x, the handle up y
  const { half, h, chamfer: c, bevel, face } = HEAD;
  const section = [ // the head's cross-section (y, z): a square with its long edges cut
    [h, h - c], [h - c, h], [-(h - c), h], [-h, h - c],
    [-h, -(h - c)], [-(h - c), -h], [h - c, -h], [h, -(h - c)],
  ];
  const body = half - bevel; // where the ends start to bevel in
  for (const [y, z] of section) s.line([-body, y, z], [body, y, z], 0.9, 1.8); // long edges
  for (const end of [-1, 1]) {
    const x = end * half;
    s.path(section.map(([y, z]) => [end * body, y, z]), true, 0.85, 1.7);
    s.path(section.map(([y, z]) => [x, y * face, z * face]), true, 1.0, 1.8); // the striking face
    for (const [y, z] of section) s.line([end * body, y, z], [x, y * face, z * face], 0.8, 1.6); // bevel
    s.onFace([end, 0, 0], () => { // knotwork on the striking face
      s.path(section.map(([y, z]) => [x, y * face * 0.72, z * face * 0.72]), true, 0.5, 1.3);
      for (const way of [1, -1]) {
        s.curve((t) => {
          const [y, z] = aroundSection(section, t);
          const weave = face * (0.86 + way * 0.07 * Math.sin(t * 2 * Math.PI * 14));
          return [x, y * weave, z * weave];
        }, 0.6, 1.3, TINT.ice, 0.006);
      }
    });
  }
  for (const z of [h, -h]) { // the broad faces: scan lines, a border, the trinity knot
    s.onFace([0, 0, Math.sign(z)], () => {
      for (let y = -(h - c) + 0.02; y < h - c - 0.01; y += 0.028) {
        s.line([-body + 0.012, y, z], [body - 0.012, y, z], 0.1, 1.2, TINT.cyan, 0.014);
      }
      const edge = h - c - 0.016;
      s.path([[-body + 0.02, -edge, z], [body - 0.02, -edge, z], [body - 0.02, edge, z], [-body + 0.02, edge, z]], true, 0.4, 1.2);
      triquetra(s, 0, -0.005, z, 0.052);
    });
  }
  for (const y of [h, -h]) { // top and bottom faces: scan lines
    s.onFace([0, Math.sign(y), 0], () => {
      for (let z = -(h - c) + 0.02; z < h - c - 0.01; z += 0.028) {
        s.line([-body + 0.012, y, z], [body - 0.012, y, z], 0.08, 1.1, TINT.cyan, 0.018);
      }
    });
  }

  const bottom = h; // the handle: wrapped in leather, with silver chevron bands
  const top = h + HANDLE.length;
  const r = HANDLE.radius;
  for (let k = 0; k < 10; k++) {
    const a = k * 36 * DEG;
    s.line([r * Math.cos(a), bottom, r * Math.sin(a)], [r * Math.cos(a), top, r * Math.sin(a)], 0.42, 1.3);
  }
  s.curve((t) => { // the leather, wound round and round
    const a = t * 2 * Math.PI * 9;
    return [r * 1.03 * Math.cos(a), bottom + t * (top - bottom), r * 1.03 * Math.sin(a)];
  }, 0.22, 1.1, TINT.cyan, 0.006);
  for (let y = bottom + 0.06; y < top - 0.03; y += 0.1) {
    s.curve((t) => {
      const a = t * 2 * Math.PI;
      return [r * 1.14 * Math.cos(a), y - 0.024 * Math.abs(Math.sin(a)), r * 1.14 * Math.sin(a)];
    }, 1.0, 1.6, TINT.ice, 0.005);
  }
  const end = top + 0.042; // the pommel
  for (const [y, radius] of [[top, 0.052], [top + 0.04, 0.052], [end, 0.028]]) {
    s.curve((t) => [radius * Math.cos(t * 2 * Math.PI), y, radius * Math.sin(t * 2 * Math.PI)], 0.95, 1.5, TINT.ice, 0.006);
  }
  for (let k = 0; k < 10; k++) {
    const a = k * 36 * DEG;
    s.line([0.052 * Math.cos(a), top, 0.052 * Math.sin(a)], [0.052 * Math.cos(a), top + 0.04, 0.052 * Math.sin(a)], 0.7, 1.3, TINT.ice);
  }
  return [0, end, 0];
}

function leaned(points, angle, [dx, dy]) { // every point turned about z, then moved
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  for (let i = 0; i < points.length; i += PER_POINT) {
    const [x, y, fx, fy] = [points[i], points[i + 1], points[i + 6], points[i + 7]];
    points[i] = x * cos - y * sin + dx;
    points[i + 1] = x * sin + y * cos + dy;
    points[i + 6] = fx * cos - fy * sin; // its facing turns with it
    points[i + 7] = fx * sin + fy * cos;
  }
  return points;
}

function lowest(points) {
  let y = Infinity;
  for (let i = 1; i < points.length; i += PER_POINT) y = Math.min(y, points[i]);
  return y;
}

function rock(s, top) { // a low, rugged slab with cracks, and shards pushed up by the hammer
  const random = seeded(7);
  const rim = [];
  const base = [];
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * 2 * Math.PI + (random() - 0.5) * 0.25;
    const k = 0.85 + random() * 0.3;
    rim.push([0.4 * k * Math.cos(a), top + (random() - 0.5) * 0.025, 0.22 * k * Math.sin(a)]);
    base.push([0.44 * k * Math.cos(a), top - 0.11, 0.25 * k * Math.sin(a)]);
  }
  s.path(rim, true, 0.75, 1.6);
  s.path(base, true, 0.4, 1.3);
  rim.forEach((point, i) => { // its sides, each kinked a little on the way down
    const kink = 1.04 + random() * 0.06;
    const middle = [(point[0] + base[i][0]) / 2 * kink, (point[1] + base[i][1]) / 2, (point[2] + base[i][2]) / 2 * kink];
    s.path([point, middle, base[i]], false, 0.4, 1.3);
  });
  s.onFace([0, 1, 0], () => { // the top: faint scan lines, then cracks
    for (let z = -0.18; z <= 0.18; z += 0.035) s.line([-0.34, top, z], [0.34, top, z], 0.07, 1.1, TINT.cyan, 0.018);
  });
  for (let k = 0; k < 6; k++) {
    let point = [(random() - 0.5) * 0.3, top + 0.003, (random() - 0.5) * 0.18];
    const crack = [point];
    let heading = random() * 2 * Math.PI;
    for (let step = 0; step < 4; step++) {
      heading += (random() - 0.5) * 1.2;
      point = [point[0] + 0.06 * Math.cos(heading), top + 0.003, point[2] + 0.035 * Math.sin(heading)];
      crack.push(point);
    }
    s.path(crack, false, 0.45, 1.2, TINT.ice, 0.006);
  }
  const shard = (corners, thickness) => { // a broken plate, standing up at an angle
    const back = corners.map(([x, y, z]) => [x - thickness, y, z]);
    s.path(corners, true, 0.65, 1.5);
    s.path(back, true, 0.35, 1.3);
    corners.forEach((corner, i) => s.line(corner, back[i], 0.4, 1.2));
  };
  shard([[0.3, top, -0.13], [0.3, top, 0.05], [0.37, top + 0.12, 0.06], [0.36, top + 0.09, -0.11]], 0.025);
  shard([[0.21, top, 0.09], [0.35, top, 0.13], [0.37, top + 0.075, 0.16], [0.24, top + 0.06, 0.13]], 0.02);
  shard([[-0.05, top, -0.16], [0.12, top, -0.18], [0.1, top + 0.05, -0.2], [-0.03, top + 0.035, -0.19]], 0.018);
}

function fit(points, radius) { // centred on the origin, and scaled to reach `radius` at most
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < points.length; i += PER_POINT) {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], points[i + k]);
      hi[k] = Math.max(hi[k], points[i + k]);
    }
  }
  const centre = lo.map((low, k) => (low + hi[k]) / 2);
  let far = 0;
  for (let i = 0; i < points.length; i += PER_POINT) {
    far = Math.max(far, Math.hypot(points[i] - centre[0], points[i + 1] - centre[1], points[i + 2] - centre[2]));
  }
  for (let i = 0; i < points.length; i += PER_POINT) {
    for (let k = 0; k < 3; k++) points[i + k] = (points[i + k] - centre[k]) * radius / far;
  }
  return points;
}

function mjolnir() {
  const upright = new Shape();
  const pommelEnd = hammer(upright);
  const turned = leaned(upright.points, LEAN, [0, 0]);
  const rise = -SINK - lowest(turned); // the rock's top is at y = 0: sink the head's corner into it
  const s = new Shape();
  s.points = leaned(turned, 0, [0, rise]);
  rock(s, 0);
  const [x0, y0] = pommelEnd; // the strap: a leather loop hanging from the pommel
  const ax = x0 * Math.cos(LEAN) - y0 * Math.sin(LEAN);
  const ay = x0 * Math.sin(LEAN) + y0 * Math.cos(LEAN) + rise;
  for (const offset of [0, 0.008]) { // two edges: a flat strap
    s.curve((t) => {
      const u = t * 2 * Math.PI;
      const down = (1 - Math.cos(u)) / 2; // 0 at the pommel, 1 at the bottom of the loop
      return [ax - 0.03 * down + 0.026 * Math.sin(u) + offset, ay + 0.01 - 0.3 * down, 0.07 * down + 0.014 * Math.sin(u)];
    }, 0.85, 1.5, TINT.copper, 0.005);
  }
  return { points: fit(s.points, 0.53), motion: { spin: 0.3, tilt: 0.22 } };
}

// --- Iron Man's arc reactors: facing you, swaying to show their depth ---
function triangle(radius, pointing = 270) {
  return [pointing, pointing + 120, pointing + 240].map((a) => [radius * Math.cos(a * DEG), radius * Math.sin(a * DEG)]);
}

function ironMan1() { // the first: copper coils round a glowing ring
  const s = new Shape();
  for (const [radius, z, light] of [[0.5, 0.04, 0.9], [0.5, -0.04, 0.4], [0.47, 0.05, 0.6]]) s.ring(radius, z, light, 1.6);
  for (let k = 0; k < 10; k++) {
    const a = k * 36 + 18;
    const block = [polar(0.33, a - 13, 0), polar(0.46, a - 14, 0), polar(0.46, a + 14, 0), polar(0.33, a + 13, 0)];
    extrude(s, block.map(([x, y]) => [x, y]), 0.06, -0.02, 0.85, 1.6, TINT.cyan);
    for (let w = 0; w <= 16; w++) { // the copper winding
      const along = a - 11 + w * (22 / 16);
      s.line(polar(0.345, along, 0.065), polar(0.448, along, 0.065), 0.55, 1.2, TINT.copper, 0.006);
    }
    for (let i = 0; i < 40; i++) { // the light between the coils
      s.dot(polar(0.34 + Math.random() * 0.12, a + 18 + (Math.random() - 0.5) * 6, 0.02), 0.25 + Math.random() * 0.5, 1.3 + Math.random(), TINT.ice);
    }
  }
  s.ring(0.315, 0.07, 0.9, 1.6);
  s.ring(0.215, 0.07, 0.9, 1.6);
  for (let k = 0; k < 20; k++) { // the slotted inner ring
    const a = k * 18 + 9;
    const slot = [polar(0.245, a - 4, 0.075), polar(0.29, a - 4, 0.075), polar(0.29, a + 4, 0.075), polar(0.245, a + 4, 0.075)];
    s.path(slot, true, 0.5, 1.2, TINT.cyan, 0.006);
  }
  for (const a of [90, 210, 330]) { // three brackets across it
    const bracket = [polar(0.2, a - 7, 0), polar(0.335, a - 5, 0), polar(0.335, a + 5, 0), polar(0.2, a + 7, 0)];
    extrude(s, bracket.map(([x, y]) => [x, y]), 0.09, 0.05, 0.75, 1.5, TINT.ice);
  }
  for (const [radius, z, light] of [[0.18, 0.04, 0.7], [0.15, 0.01, 0.6], [0.12, -0.02, 0.5], [0.095, -0.05, 0.45]]) {
    s.ring(radius, z, light, 1.5, TINT.ice); // rings going deeper, like a tunnel of light
  }
  s.ring(0.075, 0.03, 1.1, 1.8, TINT.ice);
  s.sparkle(90, 0.07, 0.01, 0.8);
  return { points: s.points, motion: { sway: 0.6, tilt: 0.12, glow: 1 } };
}

function infinityWar() { // the nano reactor: an angular triangle, in layers
  const s = new Shape();
  const clipped = (radius, clip) => { // a triangle, point down, with its corners cut off
    const corners = triangle(radius);
    const outline = [];
    corners.forEach((corner, i) => {
      for (const neighbour of [corners[(i + 2) % 3], corners[(i + 1) % 3]]) {
        const length = Math.hypot(neighbour[0] - corner[0], neighbour[1] - corner[1]);
        outline.push([corner[0] + (neighbour[0] - corner[0]) * clip / length, corner[1] + (neighbour[1] - corner[1]) * clip / length]);
      }
    });
    return outline;
  };
  const outer = clipped(0.55, 0.13);
  const inner = clipped(0.34, 0.06);
  extrude(s, outer, 0.06, -0.06, 0.95, 1.8, TINT.cyan);
  extrude(s, clipped(0.45, 0.1), 0.08, 0.02, 0.75, 1.6, TINT.ice);
  s.path(inner.map(([x, y]) => [x, y, 0.09]), true, 0.6, 1.4, TINT.cyan);
  for (let i = 0; i < 6; i++) s.line([...inner[i], 0.09], [...outer[i], 0.06], 0.55, 1.3, TINT.cyan); // ribs
  for (const x of [-0.11, 0.11]) { // chevrons on the top bar
    s.path([[x - 0.06, 0.21, 0.085], [x, 0.15, 0.085], [x + 0.06, 0.21, 0.085]], false, 0.7, 1.4, TINT.ice);
  }
  extrude(s, triangle(0.15), 0.1, 0.04, 1.15, 1.8, TINT.ice);
  s.path(triangle(0.075).map(([x, y]) => [x, y, 0.11]), true, 1.0, 1.6, TINT.ice);
  s.sparkle(80, 0.13, 0.06, 0.75);
  return { points: s.points, motion: { sway: 0.65, tilt: 0.14, glow: 0.9 } };
}

// --- Stormbreaker: Thor's axe, a blade and a hammer on a branch of Groot, bound by roots ---
function stormbreaker() {
  const s = new Shape();
  const top = 0.33; // the head's centre
  const handle = (t) => [0.016 * Math.sin(t * Math.PI * 1.4), -0.47 + t * 0.88, 0.008 * Math.sin(t * 7)];
  const thick = (t) => 0.029 + 0.005 * Math.sin(t * 23) + 0.003 * Math.sin(t * 41 + 1) + 0.014 * Math.max(0, 0.08 - t) / 0.08;
  for (let i = 0; i <= 38; i++) { // the handle: gnarled wood, a little crooked, knotted at the end
    const t = i / 38;
    const [cx, cy, cz] = handle(t);
    s.curve((u) => [cx + thick(t) * Math.cos(u * 2 * Math.PI), cy, cz + thick(t) * Math.sin(u * 2 * Math.PI)], 0.42, 1.3, TINT.copper, 0.007);
  }
  for (let k = 0; k < 7; k++) { // its grain, twisting along it
    const a0 = (k / 7) * 2 * Math.PI;
    s.curve((t) => {
      const [cx, cy, cz] = handle(t);
      const a = a0 + 0.5 * Math.sin(t * 6 + a0 * 2);
      return [cx + thick(t) * Math.cos(a), cy, cz + thick(t) * Math.sin(a)];
    }, 0.65, 1.3, TINT.copper, 0.007);
  }
  tube(s, handle(0.05), plus(handle(0.05), [0.06, -0.035, 0.02]), (t) => [0.012 - 0.006 * t, 0.012 - 0.006 * t],
    { gap: 0.012, sides: 4, light: 0.35, tint: TINT.copper }); // a snapped-off twig

  const hub = [[-0.035, -0.062], [0.035, -0.062], [0.035, 0.062], [-0.035, 0.062]]; // where it all meets
  extrude(s, hub.map(([x, y]) => [x, top + y]), 0.048, -0.048, 0.75, 1.5, TINT.cyan);

  const h = 0.058; // the hammer, to the right: a block with a bevelled striking face
  const d = 0.05;
  const end = 0.215;
  const body = end - 0.024;
  const section = [[h, d - 0.012], [h - 0.012, d], [-(h - 0.012), d], [-h, d - 0.012], [-h, -(d - 0.012)], [-(h - 0.012), -d], [h - 0.012, -d], [h, -(d - 0.012)]];
  for (const [y, z] of section) s.line([0.035, top + y, z], [body, top + y, z], 1.0, 1.8);
  s.path(section.map(([y, z]) => [body, top + y, z]), true, 0.9, 1.7);
  s.path(section.map(([y, z]) => [end, top + y * 0.8, z * 0.8]), true, 1.0, 1.7);
  for (const [y, z] of section) s.line([body, top + y, z], [end, top + y * 0.8, z * 0.8], 0.75, 1.5);
  s.onFace([1, 0, 0], () => s.path(section.map(([y, z]) => [end, top + y * 0.55, z * 0.55]), true, 0.55, 1.3));
  for (const z of [d, -d]) {
    s.onFace([0, 0, Math.sign(z)], () => {
      s.path([[0.05, top - 0.035, z], [body - 0.015, top - 0.035, z], [body - 0.015, top + 0.035, z], [0.05, top + 0.035, z]], true, 0.45, 1.2);
      for (let y = -0.03; y <= 0.03; y += 0.015) s.line([0.055, top + y, z], [body - 0.02, top + y, z], 0.1, 1.1, TINT.cyan, 0.014);
    });
  }

  const outline = []; // the blade, to the left: flaring out to a long curved edge
  const bezier = (a, b, c, t) => [(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * b[0] + t * t * c[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * b[1] + t * t * c[1]];
  for (let i = 0; i <= 20; i++) outline.push([...bezier([-0.035, 0.05], [-0.12, 0.07], [-0.24, 0.175], i / 20), 0]); // upper edge
  for (let i = 1; i <= 30; i++) outline.push([...bezier([-0.24, 0.175], [-0.36, 0.0], [-0.22, -0.185], i / 30), 1]); // the cutting edge
  for (let i = 1; i <= 20; i++) outline.push([...bezier([-0.22, -0.185], [-0.11, -0.07], [-0.035, -0.05], i / 20), 0]); // lower edge
  for (const side of [1, -1]) {
    const faceZ = ([x, y]) => side * 0.016 * Math.max(0, 1 - (-x - 0.035) / 0.3); // thinning to the edge
    s.path(outline.map(([x, y]) => [x, top + y, faceZ([x, y])]), false, 0.95, 1.7, TINT.cyan);
    s.onFace([0, 0, side], () => {
      const bevel = outline.filter(([, , edge]) => edge).map(([x, y]) => [x + 0.045, y * 0.86]);
      s.path(bevel.map(([x, y]) => [x, top + y, faceZ([x, y])]), false, 0.6, 1.3, TINT.ice);
      for (const [a, b] of [[[-0.05, 0.03], [-0.18, 0.12]], [[-0.06, 0.0], [-0.255, 0.0]], [[-0.05, -0.03], [-0.17, -0.13]]]) {
        s.line([a[0], top + a[1], faceZ(a)], [b[0], top + b[1], faceZ(b)], 0.45, 1.2, TINT.cyan);
      }
    });
  }
  for (let v = 0; v < 4; v++) { // the roots binding the head on
    s.curve((t) => {
      const a = (v * 90 + t * 540) * DEG;
      const r = 0.045 + 0.03 * Math.sin(t * Math.PI) + 0.004 * Math.sin(t * 40 + v);
      return [r * Math.cos(a), top - 0.1 + t * 0.2, r * Math.sin(a) * 0.9];
    }, 0.6, 1.4, TINT.copper, 0.006);
  }
  return { points: fit(leaned(s.points, 16 * DEG, [0, 0]), 0.53), motion: { spin: 0.32, tilt: 0.12 } };
}

// --- The classic J.A.R.V.I.S. rings: his name in the middle, rings round it turning by themselves ---
const LETTERS = { // a small stroke font: lines in a box 0.6 wide and 1 tall
  J: [[[0.15, 1], [0.6, 1]], [[0.45, 1], [0.45, 0.25], [0.4, 0.07], [0.28, 0], [0.15, 0.03], [0.05, 0.15]]],
  A: [[[0, 0], [0.3, 1], [0.6, 0]], [[0.12, 0.38], [0.48, 0.38]]],
  R: [[[0, 0], [0, 1], [0.4, 1], [0.55, 0.9], [0.55, 0.65], [0.4, 0.55], [0, 0.55]], [[0.25, 0.55], [0.58, 0]]],
  V: [[[0, 1], [0.3, 0], [0.6, 1]]],
  I: [[[0.3, 0], [0.3, 1]], [[0.15, 1], [0.45, 1]], [[0.15, 0], [0.45, 0]]],
  S: [[[0.55, 0.85], [0.45, 0.98], [0.25, 1], [0.08, 0.9], [0.06, 0.7], [0.2, 0.57], [0.42, 0.47], [0.56, 0.32], [0.55, 0.12], [0.38, 0], [0.15, 0.01], [0.02, 0.12]]],
};

function lettering(s, text, height, light) { // a line of text, centred on the middle
  const advance = (c) => (c === "." ? 0.32 : 0.82);
  let x = -([...text].reduce((sum, c) => sum + advance(c), 0) - 0.22) * height / 2;
  for (const c of text) {
    if (c === ".") s.dot([x + 0.05 * height, -0.5 * height, 0.01], light, 2.4, TINT.ice);
    else for (const stroke of LETTERS[c]) s.path(stroke.map(([u, v]) => [x + u * height, (v - 0.5) * height, 0.01]), false, light, 1.6, TINT.ice, 0.004);
    x += advance(c) * height;
  }
}

function jarvis() {
  const s = new Shape();
  for (const r of [0.315, 0.325, 0.335]) s.ring(r, 0, r === 0.325 ? 1.3 : 0.8, 1.7, TINT.ice); // the bright ring
  s.ring(0.29, 0, 0.3, 1.2);
  lettering(s, "J.A.R.V.I.S", 0.08, 1.15);
  s.turningAt(0.25, () => { // capsules along an arc above, and another below
    for (const [from, to] of [[25, 115], [205, 335]]) {
      for (let a = from; a <= to; a += 7) {
        s.path([polar(0.37, a - 2.2, 0), polar(0.395, a - 2.2, 0), polar(0.395, a + 2.2, 0), polar(0.37, a + 2.2, 0)], true, 0.75, 1.3, TINT.cyan, 0.004);
      }
    }
  });
  s.turningAt(-0.18, () => { // a level meter on the left
    for (let a = 150; a <= 212; a += 4.5) s.line(polar(0.415, a, 0), polar(0.445, a, 0), a < 185 ? 1.0 : 0.45, 1.6, TINT.ice, 0.005);
  });
  s.turningAt(0.12, () => { // a thin ring with one thick, bright arc
    s.ring(0.465, 0, 0.35, 1.2);
    for (const r of [0.475, 0.485, 0.495]) s.ring(r, 0, 1.1, 1.8, TINT.ice, -55, 40);
  });
  s.turningAt(-0.08, () => { // dashes along the top, dots all the way round
    for (let a = 60; a < 150; a += 6) s.ring(0.525, 0, 0.7, 1.3, TINT.cyan, a, a + 3.5);
    for (let a = 0; a < 360; a += 4) s.dot(polar(0.44, a, 0), 0.45, 1.5);
  });
  s.turningAt(0.06, () => {
    s.ring(0.545, 0, 0.5, 1.3, TINT.cyan, 170, 250);
    s.ring(0.545, 0, 0.5, 1.3, TINT.cyan, 290, 330);
  });
  return { points: s.points, motion: { glow: 0.35 } };
}

window.CORES = {
  jarvis,
  mjolnir,
  stormbreaker,
  "iron-man-1": ironMan1,
  "infinity-war": infinityWar,
};
