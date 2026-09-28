/**
 * fogShader.ts - the fog's GLSL and the numbers it is built from.
 *
 * This lives beside MistCanvas rather than inside it for two reasons. The
 * component file exports components, and a module that exports components
 * cannot also export tables and functions without costing fast refresh. And
 * the numbers here are not the component's: they are the look, and the
 * scrollbar contrast guard has to evaluate the same ramp the shader walks in
 * order to know how light the fog behind a bar can get. Two copies of that
 * table would drift, so there is one, and the GLSL below is written from the
 * same page it is written on.
 */
/* ── Fog tuning knobs (eye-calibrated) ─────────────────────────────
   WIND: uv-units/second of horizontal/vertical drift. The warp field
   evolves at 0.3× wind, which is what makes billows visibly CHURN
   rather than slide as a rigid sheet.
   HUE_TRAVEL: how far (in ramp units) fog density swings the color.
   HIGH values mix white wisps into the blue side and blue billows into
   the light side (user-chosen look); low values lock colors regionally
   but render the fog nearly invisible against the base ramp.
   RAMP_COMPRESS: squeezes the base diagonal toward the middle - softens
   the hard light/dark divide so billows, not the ramp, dominate.
   LIFT / LIFT_LO: strength and onset of the bright-billow lift. */
const WIND_X = 0.085;
const WIND_Y = 0.028;
export const HUE_TRAVEL = 1.1;
export const RAMP_COMPRESS = 0.5;
/** Weather layer: a HUGE, slow, independent noise cell field added to the
 * ramp coordinate - it wanders the color regions themselves (a white cell
 * can settle top-left, a blue one drift right) so the distribution reads
 * randomized yet coherent. The diagonal stays as a mild tendency, not a
 * rule. Scale = feature size (lower = bigger cells); AMOUNT = how far the
 * weather can move a region's color along the ramp. */
export const WEATHER_AMOUNT = 0.45;
const WEATHER_SCALE = 0.55;
/** Bright billows blend toward ramp(d + LIFT_REACH) with weight ≤ LIFT -
 * the asymmetric mixer: WHITE wisps travel deep into the blue side while
 * dark dips stay bounded (mist over a blue world, not storms over white). */
export const LIFT = 0.75;
export const LIFT_LO = 0.42;
export const LIFT_REACH = 0.45;
/** Upper end of the lift ramp: above this much fog density the billow
 * lift is at its full LIFT weight. */
export const LIFT_HI = 0.85;
/** Faster fine-wisp layer: its 2.2× drift against the slow bulk creates the
 * parallax that makes fog motion READ as motion. Blend weight of the layer. */
const DETAIL = 0.25;
/** Contrast normalization: raw 4-octave FBM concentrates in ~[0.3, 0.7],
 * which starves both the deep-blue dips and the white billows. smoothstep
 * remaps that band to the full [0,1] so extremes actually occur. */
const V_LO = 0.32;
const V_HI = 0.72;

export const VERT_SRC = `
attribute vec2 a_pos;
void main() {
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`;

/** The colour ramp `ramp()` walks, as data. The GLSL below writes the same
 * five colours and four thresholds inline, because a shader is a string and
 * this is the readable form of the same table. Anything that evaluates the
 * ramp outside the shader reads it from here, and proves the two agree
 * against the source this module really generates before it does. */
export const FOG_RAMP_BASE = [0.173, 0.259, 0.349] as const;
export const FOG_RAMP_STEPS = [
  { lo: 0.0, hi: 0.26, colour: [0.235, 0.345, 0.471] },
  { lo: 0.26, hi: 0.5, colour: [0.361, 0.502, 0.651] },
  { lo: 0.5, hi: 0.74, colour: [0.784, 0.855, 0.925] },
  { lo: 0.74, hi: 1.0, colour: [0.957, 0.973, 0.988] },
] as const;

/* Domain-warped FBM fog (iq pattern), 3 fbm calls × 4 octaves. The ramp
   stops are the shell gradient's exact colors; d is the bottom-left→top-right
   diagonal coordinate. rampLo/rampHi confine an instance to a SUB-BAND of
   the ramp (panel instances: sidebar lives in the dark-blue band, right
   panel in the light band) - every ramp-space excursion (compress, weather,
   hue travel, lift) scales by the band's span, and the lift clamps to
   rampHi, so fog can never leave its region's palette. (0,1) reproduces
   the original full-range math exactly. */
export const makeFragSrc = (rampLo: number, rampHi: number): string => {
  const LO = rampLo.toFixed(4);
  const HI = rampHi.toFixed(4);
  const SPAN = (rampHi - rampLo).toFixed(4);
  return `
precision mediump float;
uniform vec2 u_res;
uniform float u_time;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 4; i++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(17.0, 9.2);
    a *= 0.5;
  }
  return v;
}

vec3 ramp(float t) {
  t = clamp(t, 0.0, 1.0);
  vec3 c = mix(vec3(0.173, 0.259, 0.349), vec3(0.235, 0.345, 0.471), smoothstep(0.0, 0.26, t));
  c = mix(c, vec3(0.361, 0.502, 0.651), smoothstep(0.26, 0.50, t));
  c = mix(c, vec3(0.784, 0.855, 0.925), smoothstep(0.50, 0.74, t));
  c = mix(c, vec3(0.957, 0.973, 0.988), smoothstep(0.74, 1.0, t));
  return c;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  vec2 p = uv * vec2(u_res.x / u_res.y, 1.0) * 2.2;
  vec2 wind = vec2(${WIND_X}, ${WIND_Y}) * u_time;
  vec2 q = vec2(
    fbm(p + wind),
    fbm(p + wind + vec2(5.2, 1.3))
  );
  float v = fbm(p + 2.0 * q + 0.3 * wind);
  // Fine wisps drifting 2.2x faster than the bulk - parallax motion cue.
  float v2 = fbm(p * 2.6 + wind * 2.2 + vec2(7.7, 3.1));
  v = mix(v, v2, ${DETAIL});
  // Stretch the noise's natural band to full range (contrast normalization).
  v = smoothstep(${V_LO}, ${V_HI}, v);
  float diag = 0.55 * uv.x + 0.45 * uv.y;
  // Weather cells: huge, slow, independent of the fog bulk - they relocate
  // the blue/white tendency itself over minutes.
  float w3 = fbm(p * ${WEATHER_SCALE} + wind * 0.5 + vec2(21.0, 13.0));
  w3 = smoothstep(0.35, 0.65, w3);
  float d = ${LO} + ${SPAN} * (0.5 + (diag - 0.5) * ${RAMP_COMPRESS} + (w3 - 0.5) * ${WEATHER_AMOUNT});
  vec3 col = ramp(d + (v - 0.5) * ${HUE_TRAVEL} * ${SPAN});
  col = mix(col, ramp(min(d + ${LIFT_REACH} * ${SPAN}, ${HI})), smoothstep(${LIFT_LO}, ${LIFT_HI}, v) * ${LIFT});
  col += (hash(gl_FragCoord.xy) - 0.5) / 255.0;
  gl_FragColor = vec4(col, 1.0);
}
`;
};

/** Ramp sub-bands for the side panels: the sidebar breathes in the deep-blue
 * region of the palette, the right panel in the light region - matching the
 * ground each panel occupies on the diagonal. */
export const PANEL_BANDS = {
  left: [0.0, 0.52],
  right: [0.42, 1.0],
} as const;
