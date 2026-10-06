/* Jarvis's orb: rings of light that show what he's doing, drawn with WebGL.

   The desktop app calls orb.update({...}) whenever something changes (any field may be left out):
   - state: "away" (he isn't connected) or LiveKit's "initializing" / "listening" / "thinking" /
     "speaking"
   - awake: true while the wake-word gate is open, i.e. he's listening to you
   - level: how loud his voice is right now, 0 to 1
   - core: the centrepiece in the middle, from cores.js: "jarvis", "mjolnir", "stormbreaker",
     "iron-man-1" or "infinity-war"

   Layers, outside in: a band of wavy strands (they ripple with his voice), drifting dust, a dotted
   ring, ticks, thin rings, and the centrepiece: a 3D hologram (cores.js) that a line of light
   sweeps while he thinks. All of it is points of light: the GPU moves them, so each frame
   JavaScript only sets a few numbers. */
"use strict";

const TAU = Math.PI * 2;

// How each mood looks. The orb eases from one to the next; nothing jumps.
const LOOKS = {
  away: { size: 0.42, light: 0.22, spin: 0.04, wobble: 0.0, scan: 0, grey: 1 },
  asleep: { size: 0.48, light: 0.45, spin: 0.08, wobble: 0.012, scan: 0, grey: 0 },
  listening: { size: 1.0, light: 0.85, spin: 0.25, wobble: 0.03, scan: 0, grey: 0 },
  thinking: { size: 0.94, light: 1.0, spin: 1.1, wobble: 0.05, scan: 1, grey: 0 },
  speaking: { size: 1.0, light: 1.0, spin: 0.35, wobble: 0.03, scan: 0, grey: 0 },
};
const EASE_SECONDS = 0.35; // how quickly the orb moves to a new look
const IDLE_FPS = 15; // asleep or away, once settled: easy on the GPU
const ACTIVE_FPS = 60;
const VOICE_WOBBLE = 0.2; // how far his loudest syllables push the strands (share of the radius)
const LIGHT_COVER = 0.35; // how much the light hides what's behind it (0: it only adds light)
const GLASS = 0.62; // the dark glass behind the orb, so its light reads on bright screens
const FIT = 0.36; // the band's radius at full size, as a share of the canvas (room for ripples)
const SWAP_SECONDS = 0.18; // a new core: the old one fades out, then the new one fades in

// --- What the desktop app tells us ---
const told = { state: "away", awake: false };
let look = LOOKS.away;
let loudness = 0; // what we were told
let level = 0; // what we show: rises fast, falls slowly, like a VU meter
let wantedCore = "jarvis"; // what we were told to show in the middle

function mood({ state, awake }) {
  if (state === "away" || state === "thinking" || state === "speaking") return state;
  return awake ? "listening" : "asleep"; // "listening" or "initializing" with the gate closed
}

const clamp01 = (x) => Math.min(1, Math.max(0, Number(x) || 0));

window.orb = {
  update({ state, awake, level: voice, core } = {}) {
    if (state !== undefined) told.state = state;
    if (awake !== undefined) told.awake = Boolean(awake);
    if (voice !== undefined) loudness = clamp01(voice);
    if (core in CORES) wantedCore = core;
    look = LOOKS[mood(told)] ?? LOOKS.asleep;
  },
};

// --- The points, at rest (unit circle = the band) ---
function makePoints() {
  const base = []; // x, y
  const info = []; // layer, seed, extra (the strand), size in CSS pixels
  const add = (x, y, layer, seed, extra, size) => {
    base.push(x, y);
    info.push(layer, seed, extra, size);
  };
  const around = (count, radius, layer, size) => {
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * TAU;
      add(Math.cos(angle) * radius, Math.sin(angle) * radius, layer, Math.random(), 0, size);
    }
  };

  for (let strand = 0; strand < 7; strand++) { // 0: the band
    const radius = 1 + (strand - 3) * 0.016;
    for (let i = 0; i < 640; i++) {
      const angle = (i / 640) * TAU;
      add(Math.cos(angle) * radius, Math.sin(angle) * radius, 0, Math.random(), strand, 2.2);
    }
  }
  for (let i = 0; i < 420; i++) { // 1: dust
    const angle = Math.random() * TAU;
    const radius = 0.86 + Math.random() * 0.3;
    add(Math.cos(angle) * radius, Math.sin(angle) * radius, 1, Math.random(), 0, 1.4 + Math.random() * 1.4);
  }
  around(180, 0.68, 2, 2.2); // 2: dotted ring
  for (let i = 0; i < 72; i++) { // 3: ticks every 5°, longer every 30°
    const angle = (i / 72) * TAU;
    const steps = i % 6 === 0 ? 9 : 4;
    for (let k = 0; k < steps; k++) {
      const radius = 0.74 + k * 0.008;
      add(Math.cos(angle) * radius, Math.sin(angle) * radius, 3, 0, 0, 1.6);
    }
  }
  around(600, 0.6, 4, 1.4); // 4: thin rings
  around(720, 1.12, 4, 1.3);
  return { base: new Float32Array(base), info: new Float32Array(info), count: base.length / 2 };
}

// --- Shaders: where each point goes, and how bright it is ---
const POINTS_VERTEX = `#version 300 es
in vec2 a_base;
in vec4 a_info;          // layer, seed, extra (strand or cell), size
uniform vec2 u_res;      // canvas size, device pixels
uniform float u_dpr;
uniform float u_radius;  // the band's radius at full size, device pixels
uniform float u_scale;   // the look's size, breathing included
uniform float u_time;
uniform float u_phase;   // runs faster while he speaks, so the ripples travel
uniform float u_wobble;  // how far the strands leave the circle
uniform float u_level;
uniform float u_light;
uniform vec3 u_turn;     // rotation of the band, dotted ring, ticks
out vec4 v_color;

const vec3 CYAN = vec3(0.36, 0.88, 1.0);
const vec3 ICE = vec3(0.90, 0.98, 1.0);
const vec3 TEAL = vec3(0.06, 0.64, 0.72);

float hash(float n) { return fract(sin(n) * 43758.5453); }
mat2 turn(float a) { float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }

void main() {
  int layer = int(a_info.x + 0.5);
  float seed = a_info.y;
  vec2 p = a_base;
  vec3 color = CYAN;
  float light;

  if (layer == 0) {  // the band: each strand ripples a little differently, so they part as he speaks
    float strand = a_info.z;
    float angle = atan(p.y, p.x);
    float wave = 0.55 * sin(3.0 * angle + u_phase + strand * 0.9)
               + 0.30 * sin(5.0 * angle - u_phase * 1.4 + strand * 2.1)
               + 0.15 * sin(9.0 * angle + u_phase * 2.3 + strand * 3.3)
               + 0.25 * u_level * sin(17.0 * angle - u_phase * 3.1 + strand * 1.3);
    float radius = length(p) * (1.0 + u_wobble * wave * (0.6 + 0.4 * sin(strand * 1.7 + u_time * 0.3)));
    angle += u_turn.x + strand * 0.05;
    p = vec2(cos(angle), sin(angle)) * radius;
    float grain = pow(hash(seed * 7.13), 6.0);  // a few bright grains, as in the film
    float sweep = 0.5 + 0.5 * sin(angle * 2.0 - u_time * 0.8 + strand);
    light = 0.3 + 0.45 * sweep + 0.9 * grain;
    color = mix(CYAN, ICE, grain);
  } else if (layer == 1) {  // dust drifting round the band, twinkling
    float twinkle = 0.5 + 0.5 * sin(u_time * (1.0 + seed * 2.0) + seed * 40.0);
    p = turn(u_turn.x * 0.6) * p * (1.0 + u_wobble * 0.6 * sin(seed * 20.0 + u_phase));
    light = 0.15 + 0.6 * twinkle * twinkle;
    color = ICE;
  } else if (layer == 2) {  // dotted ring
    p = turn(u_turn.y) * p;
    light = 0.55;
  } else if (layer == 3) {  // ticks, turning the other way
    p = turn(u_turn.z) * p;
    light = 0.5;
    color = mix(TEAL, CYAN, 0.6);
  } else {  // thin rings
    light = 0.35;
    color = TEAL;
  }

  gl_Position = vec4(p * u_radius * u_scale / (u_res * 0.5), 0.0, 1.0);
  gl_PointSize = a_info.w * u_dpr;
  v_color = vec4(color, light * u_light);
}`;

const POINTS_FRAGMENT = `#version 300 es
precision mediump float;
in vec4 v_color;
uniform float u_grey;
uniform float u_cover;
out vec4 out_color;

void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;  // 0 in the middle of the dot, 1 at its edge
  float a = v_color.a * (1.0 - smoothstep(0.2, 1.0, d));
  vec3 c = mix(v_color.rgb, vec3(dot(v_color.rgb, vec3(0.3, 0.59, 0.11)) * 0.8), u_grey);
  out_color = vec4(c * a, a * u_cover);  // premultiplied: light adds to what's behind
}`;

// A 3D centrepiece (cores.js): turned, seen in perspective, the far side dimmer.
const CORE_VERTEX = `#version 300 es
in vec3 a_pos;
in vec3 a_look;          // light, size, tint (0 cyan, 1 ice, 2 copper)
in vec3 a_facing;        // the way its surface faces (0, 0, 0 for an edge: seen from every side)
in float a_turn;         // how fast it circles the centre on its own (rings that turn by themselves)
uniform float u_core_time; // the centrepiece's clock (it runs faster while he thinks)
uniform vec2 u_res;
uniform float u_dpr;
uniform float u_radius;
uniform float u_scale;
uniform float u_yaw;     // turned round its upright axis
uniform float u_pitch;   // leaning back
uniform float u_time;
uniform float u_light;
uniform float u_level;
uniform float u_scan;    // while he thinks, a line of light sweeps up and down it
uniform float u_fade;    // 0 to 1, when one centrepiece makes way for another
out vec4 v_color;

const vec3 CYAN = vec3(0.36, 0.88, 1.0);
const vec3 ICE = vec3(0.90, 0.98, 1.0);
const vec3 COPPER = vec3(1.0, 0.58, 0.32);
const vec3 RED = vec3(1.0, 0.26, 0.2);    // Iron Man's suit
const vec3 GOLD = vec3(1.0, 0.76, 0.32);

vec3 turned(vec3 v) {  // round the upright axis (yaw), then leaning back (pitch)
  float cy = cos(u_yaw), sy = sin(u_yaw);
  v = vec3(cy * v.x + sy * v.z, v.y, cy * v.z - sy * v.x);
  float cp = cos(u_pitch), sp = sin(u_pitch);
  return vec3(v.x, cp * v.y - sp * v.z, sp * v.y + cp * v.z);
}

void main() {
  float own = a_turn * u_core_time;  // first its own turn, round the centre, flat
  vec3 p = turned(vec3(mat2(cos(own), sin(own), -sin(own), cos(own)) * a_pos.xy, a_pos.z));
  float near = 2.6 / (2.6 - p.z);  // perspective: nearer is bigger
  gl_Position = vec4(p.xy * near * u_radius * u_scale / (u_res * 0.5), 0.0, 1.0);
  gl_PointSize = a_look.y * u_dpr * near;
  float depth = clamp(0.55 + p.z * 2.0, 0.15, 1.0);  // the far side is dimmer
  // Surface details show only while their face is turned towards you (z is towards you).
  float shown = dot(a_facing, a_facing) > 0.5 ? smoothstep(-0.05, 0.35, turned(a_facing).z) : 1.0;
  float scan = u_scan * 1.6 * exp(-pow((a_pos.y - 0.5 * sin(u_time * 2.2)) * 16.0, 2.0));
  float tint = a_look.z;
  vec3 color = tint < 0.5 ? CYAN : tint < 1.5 ? ICE : tint < 2.5 ? COPPER : tint < 3.5 ? RED : GOLD;
  float light = (a_look.x * depth * (1.0 + 0.5 * u_level) + scan) * shown;
  v_color = vec4(mix(color, ICE, min(scan, 1.0)), light * u_light * u_fade);
}`;

// The backing: a faint dark disc (so the light reads on white pages) and a soft heart of light.
const BACKING_VERTEX = `#version 300 es
in vec2 a_corner;
out vec2 v_at;
void main() { v_at = a_corner; gl_Position = vec4(a_corner, 0.0, 1.0); }`;

const BACKING_FRAGMENT = `#version 300 es
precision mediump float;
in vec2 v_at;
uniform vec2 u_res;
uniform float u_radius;
uniform float u_scale;
uniform float u_light;
uniform float u_level;
uniform float u_glass;
uniform float u_core_glow;  // an arc reactor's light, at its heart
out vec4 out_color;

void main() {
  float r = length(v_at * u_res * 0.5) / (u_radius * u_scale);  // 1 = the band
  float glass = u_glass * (1.0 - smoothstep(0.9, 1.45, r));
  float heart = (0.10 + 0.25 * u_level) * u_light * exp(-r * r * 18.0)
              + u_core_glow * (0.3 + 0.3 * u_level) * u_light * exp(-r * r * 160.0);
  vec3 tint = vec3(0.0, 0.025, 0.045) * glass;  // a hint of blue: tinted glass, not grey haze
  out_color = vec4(tint + vec3(0.36, 0.88, 1.0) * heart, glass);  // premultiplied: dims, then adds
}`;

// --- WebGL plumbing ---
const canvas = document.getElementById("orb");
const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true, antialias: false });
canvas.addEventListener("webglcontextlost", () => location.reload());

function program(vertex, fragment) {
  const made = gl.createProgram();
  for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    gl.attachShader(made, shader);
  }
  gl.linkProgram(made);
  if (!gl.getProgramParameter(made, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(made));
  const uniforms = {};
  for (let i = 0; i < gl.getProgramParameter(made, gl.ACTIVE_UNIFORMS); i++) {
    const { name } = gl.getActiveUniform(made, i);
    uniforms[name] = gl.getUniformLocation(made, name);
  }
  return { made, uniforms };
}

function attribute(target, name, data, size) {
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  const where = gl.getAttribLocation(target.made, name);
  gl.enableVertexAttribArray(where);
  gl.vertexAttribPointer(where, size, gl.FLOAT, false, 0, 0);
}

const points = program(POINTS_VERTEX, POINTS_FRAGMENT);
const backing = program(BACKING_VERTEX, BACKING_FRAGMENT);
const shape = makePoints();
const pointsVao = gl.createVertexArray();
gl.bindVertexArray(pointsVao);
attribute(points, "a_base", shape.base, 2);
attribute(points, "a_info", shape.info, 4);
const backingVao = gl.createVertexArray();
gl.bindVertexArray(backingVao);
attribute(backing, "a_corner", new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), 2);
const core = program(CORE_VERTEX, POINTS_FRAGMENT);
const coreVao = gl.createVertexArray();
const coreBuffer = gl.createBuffer();
gl.bindVertexArray(coreVao);
gl.bindBuffer(gl.ARRAY_BUFFER, coreBuffer);
const coreStride = 10 * 4; // x, y, z · light, size, tint · facing x, y, z · turn
const coreAttributes = [["a_pos", 3, 0], ["a_look", 3, 12], ["a_facing", 3, 24], ["a_turn", 1, 36]];
for (const [name, size, offset] of coreAttributes) {
  const where = gl.getAttribLocation(core.made, name);
  gl.enableVertexAttribArray(where);
  gl.vertexAttribPointer(where, size, gl.FLOAT, false, coreStride, offset);
}
gl.enable(gl.BLEND);

const made = {}; // centrepieces built so far (built when first shown)
let shownCore = null; // none yet: the first one fades in
let coreCount = 0; // its points
let coreMotion = {};

function showCore(name) {
  shownCore = name;
  made[name] ??= CORES[name]();
  const { points, motion } = made[name];
  gl.bindBuffer(gl.ARRAY_BUFFER, coreBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(points), gl.STATIC_DRAW);
  coreCount = points.length / 10;
  coreMotion = motion;
}

function fitCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const width = Math.round(canvas.clientWidth * dpr);
  const height = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return dpr;
}

// --- Animation ---
const shown = { ...LOOKS.away }; // the look on screen, easing towards `look`
const turns = [0, 0, 0];
let phase = 0;
let time = 0;
let last = 0;
let coreFade = 0; // the centrepiece on screen: 1 shown, 0 gone (making way for another)
let coreTime = 0; // the centrepiece's own clock: it turns faster while he thinks
const calm = window.matchMedia("(prefers-reduced-motion: reduce)");

function step(dt) {
  const ease = 1 - Math.exp(-dt / EASE_SECONDS);
  for (const key of Object.keys(shown)) shown[key] += (look[key] - shown[key]) * ease;
  const speed = loudness > level ? 0.05 : 0.16; // seconds: rises fast, falls slowly
  level += (loudness - level) * (1 - Math.exp(-dt / speed));
  const spin = shown.spin * (calm.matches ? 0.3 : 1);
  phase += dt * (0.5 + 4 * level);
  turns[0] += dt * spin * 0.4;
  turns[1] += dt * spin;
  turns[2] -= dt * spin * 0.6;
  time += dt;
  coreTime += dt * (0.7 + spin * 0.6);
  const swapped = shownCore === wantedCore;
  coreFade += ((swapped ? 1 : 0) - coreFade) * (1 - Math.exp(-dt / SWAP_SECONDS));
  if (!swapped && coreFade < 0.03) showCore(wantedCore);
}

function settled() {
  const easing = Object.keys(shown).some((key) => Math.abs(look[key] - shown[key]) >= 0.005);
  return level < 0.01 && !easing && shownCore === wantedCore && coreFade > 0.99;
}

function draw() {
  const dpr = fitCanvas();
  const radius = Math.min(canvas.width, canvas.height) * FIT;
  const breath = calm.matches ? 1 : 1 + 0.02 * Math.sin(time * 1.6);
  const scale = shown.size * breath;
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);

  gl.useProgram(backing.made);
  gl.bindVertexArray(backingVao);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const b = backing.uniforms;
  gl.uniform2f(b.u_res, canvas.width, canvas.height);
  gl.uniform1f(b.u_radius, radius);
  gl.uniform1f(b.u_scale, scale);
  gl.uniform1f(b.u_light, shown.light);
  gl.uniform1f(b.u_level, level);
  gl.uniform1f(b.u_glass, GLASS * (1 - shown.grey * 0.5));
  gl.uniform1f(b.u_core_glow, coreCount ? (coreMotion.glow ?? 0) * coreFade : 0);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

  gl.useProgram(points.made);
  gl.bindVertexArray(pointsVao);
  gl.blendFunc(gl.ONE, gl.ONE); // light adds up
  const p = points.uniforms;
  gl.uniform2f(p.u_res, canvas.width, canvas.height);
  gl.uniform1f(p.u_dpr, dpr);
  gl.uniform1f(p.u_radius, radius);
  gl.uniform1f(p.u_scale, scale);
  gl.uniform1f(p.u_time, time);
  gl.uniform1f(p.u_phase, phase);
  gl.uniform1f(p.u_wobble, shown.wobble + VOICE_WOBBLE * level);
  gl.uniform1f(p.u_level, level);
  gl.uniform1f(p.u_light, shown.light);
  gl.uniform1f(p.u_grey, shown.grey);
  gl.uniform1f(p.u_cover, LIGHT_COVER);
  gl.uniform3f(p.u_turn, ...turns);
  gl.drawArrays(gl.POINTS, 0, shape.count);

  if (coreCount === 0) return;
  gl.useProgram(core.made);
  gl.bindVertexArray(coreVao);
  const c = core.uniforms;
  const swaying = Math.sin(coreTime * 0.45) * (coreMotion.sway ?? 0);
  gl.uniform2f(c.u_res, canvas.width, canvas.height);
  gl.uniform1f(c.u_dpr, dpr);
  gl.uniform1f(c.u_radius, radius);
  gl.uniform1f(c.u_scale, scale);
  gl.uniform1f(c.u_yaw, coreTime * (coreMotion.spin ?? 0) + swaying);
  gl.uniform1f(c.u_pitch, (coreMotion.tilt ?? 0) + Math.sin(coreTime * 0.31) * 0.06);
  gl.uniform1f(c.u_time, time);
  gl.uniform1f(c.u_core_time, coreTime);
  gl.uniform1f(c.u_light, shown.light);
  gl.uniform1f(c.u_level, level);
  gl.uniform1f(c.u_scan, shown.scan);
  gl.uniform1f(c.u_fade, coreFade);
  gl.uniform1f(c.u_grey, shown.grey);
  gl.uniform1f(c.u_cover, LIGHT_COVER);
  gl.drawArrays(gl.POINTS, 0, coreCount);
}

function frame(now) {
  const dt = last ? Math.min((now - last) / 1000, 0.1) : 1 / ACTIVE_FPS;
  last = now;
  if (demo) demo.tick(dt);
  step(dt);
  draw();
  if (demo) return requestAnimationFrame(frame); // in a browser, for trying looks: every frame
  // Sleep until the next frame is due. Asking for every frame would wake WebKit at the screen's
  // refresh rate (120 Hz here) even when nothing is drawn: most of the orb's cost while asleep.
  const fps = settled() && (look === LOOKS.asleep || look === LOOKS.away) ? IDLE_FPS : ACTIVE_FPS;
  setTimeout(() => requestAnimationFrame(frame), Math.max(0, 1000 / fps - 4));
}

// --- Demo mode: orb.html#demo, optionally &look=speaking&core=mjolnir&level=0.6&voice=0
// &backdrop=light&at=3 ---
const demo = location.hash.startsWith("#demo") ? startDemo() : null;

function startDemo() {
  document.body.classList.add("demo");
  const form = document.querySelector(".controls");
  const asked = new URLSearchParams(location.hash.slice(1));
  for (const [name, value] of asked) {
    const field = form.elements[name];
    if (field instanceof RadioNodeList) field.value = value;
    else if (field?.type === "checkbox") field.checked = value !== "0";
    else if (field) field.value = value;
  }
  const states = {
    away: { state: "away" },
    asleep: { state: "listening", awake: false },
    listening: { state: "listening", awake: true },
    thinking: { state: "thinking", awake: true },
    speaking: { state: "speaking", awake: true },
  };
  let syllable = 0;
  let peak = 0;
  const apply = () => {
    document.body.classList.toggle("light", form.elements.backdrop.value === "light");
    document.body.classList.toggle("busy", form.elements.backdrop.value === "busy");
    orb.update({ ...states[form.elements.look.value], level: 0, core: form.elements.core.value });
  };
  form.addEventListener("input", apply);
  apply();
  const player = {
    tick(dt) { // while speaking, a pretend voice: syllables with short pauses between words
      if (form.elements.look.value !== "speaking") return;
      const loudest = Number(form.elements.level.value);
      if (!form.elements.voice.checked) return orb.update({ level: loudest });
      syllable -= dt;
      if (syllable <= 0) {
        const pause = Math.random() < 0.18;
        syllable = pause ? 0.25 + Math.random() * 0.35 : 0.09 + Math.random() * 0.16;
        peak = pause ? 0 : 0.35 + Math.random() * 0.65;
      }
      orb.update({ level: loudest * peak * (0.6 + 0.4 * Math.random()) });
    },
  };
  const ahead = Number(asked.get("at") ?? 0); // &at=3: start 3 s in (same picture every time)
  for (let t = 0; t < ahead; t += 1 / 60) {
    player.tick(1 / 60);
    step(1 / 60);
  }
  return player;
}

requestAnimationFrame(frame);
