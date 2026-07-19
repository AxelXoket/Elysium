/**
 * MistCanvas - the living mist backdrop (Elysium Azure's signature ambiance).
 *
 * Self-contained WebGL fog (no libraries, no assets) - mounted twice: as the
 * page backdrop (MistCanvas) and inside the chat canvas under a milky veil
 * (CanvasMist, the "clear frosted glass" look). Each instance owns its
 * context/listeners/failure state. The shader renders domain-warped FBM fog,
 * colorized through a POSITION-LOCKED diagonal ramp
 * using the exact stops of the static shell gradient - so left stays blue,
 * right dissolves to white, and fog-off ↔ fog-on is seamless. Hue travel is
 * clamped to ±6% of the ramp, which is the structural guarantee that a
 * billow can deepen its region's color but never leave it.
 *
 * Performance contract (the flicker lesson applies):
 *  - one self-painting canvas layer at z:-1 inside the shell; NOTHING else
 *    animates, no filter/backdrop-filter/will-change is introduced
 *  - internal resolution ≤ 960 on the long edge, DPR clamped to 1 (fog is
 *    intrinsically soft; bilinear upscale is invisible and cheap)
 *  - 24 fps elapsed-time gate; fog features drift sub-pixel per frame
 *  - rAF cancelled when the tab is hidden; context released on unmount
 *    (StrictMode double-mount would otherwise briefly hold two contexts)
 *
 * Fallback ladder - every rung lands on today's static gradient:
 *  toggle off → reduced motion → viewport ≤ 900px (fog is fully covered by
 *  the app frame there) → WebGL unavailable / repeatedly lost context.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useReducedMotion } from "@/components/motion/ReducedMotion";

const DESKTOP_QUERY = "(min-width: 901px)";

function subscribeDesktop(callback: () => void): () => void {
  const mql = window.matchMedia(DESKTOP_QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribeDesktop,
    () => window.matchMedia(DESKTOP_QUERY).matches,
    () => false,
  );
}

const FPS_INTERVAL = 1000 / 24;
const MAX_LONG_EDGE = 960;

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
const HUE_TRAVEL = 1.1;
const RAMP_COMPRESS = 0.5;
/** Weather layer: a HUGE, slow, independent noise cell field added to the
 * ramp coordinate - it wanders the color regions themselves (a white cell
 * can settle top-left, a blue one drift right) so the distribution reads
 * randomized yet coherent. The diagonal stays as a mild tendency, not a
 * rule. Scale = feature size (lower = bigger cells); AMOUNT = how far the
 * weather can move a region's color along the ramp. */
const WEATHER_AMOUNT = 0.45;
const WEATHER_SCALE = 0.55;
/** Bright billows blend toward ramp(d + LIFT_REACH) with weight ≤ LIFT -
 * the asymmetric mixer: WHITE wisps travel deep into the blue side while
 * dark dips stay bounded (mist over a blue world, not storms over white). */
const LIFT = 0.75;
const LIFT_LO = 0.42;
const LIFT_REACH = 0.45;
/** Faster fine-wisp layer: its 2.2× drift against the slow bulk creates the
 * parallax that makes fog motion READ as motion. Blend weight of the layer. */
const DETAIL = 0.25;
/** Contrast normalization: raw 4-octave FBM concentrates in ~[0.3, 0.7],
 * which starves both the deep-blue dips and the white billows. smoothstep
 * remaps that band to the full [0,1] so extremes actually occur. */
const V_LO = 0.32;
const V_HI = 0.72;

const VERT_SRC = `
attribute vec2 a_pos;
void main() {
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`;

/* Domain-warped FBM fog (iq pattern), 3 fbm calls × 4 octaves. The ramp
   stops are the shell gradient's exact colors; d is the bottom-left→top-right
   diagonal coordinate. rampLo/rampHi confine an instance to a SUB-BAND of
   the ramp (panel instances: sidebar lives in the dark-blue band, right
   panel in the light band) - every ramp-space excursion (compress, weather,
   hue travel, lift) scales by the band's span, and the lift clamps to
   rampHi, so fog can never leave its region's palette. (0,1) reproduces
   the original full-range math exactly. */
const makeFragSrc = (rampLo: number, rampHi: number): string => {
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
  col = mix(col, ramp(min(d + ${LIFT_REACH} * ${SPAN}, ${HI})), smoothstep(${LIFT_LO}, 0.85, v) * ${LIFT});
  col += (hash(gl_FragCoord.xy) - 0.5) / 255.0;
  gl_FragColor = vec4(col, 1.0);
}
`;
};

/** Shared gating ladder - every rung lands on the static fallback look. */
function useFogGate(): { show: boolean; fail: () => void } {
  const on = useUiStore((s) => s.ambientFogOn);
  const reduced = useReducedMotion();
  const desktop = useIsDesktop();
  const [failed, setFailed] = useState(false);
  const fail = useCallback(() => setFailed(true), []);
  return { show: on && !reduced && desktop && !failed, fail };
}

/** Page-backdrop fog (behind the app frame). Runs at half internal
 * resolution: it is visible only through the soft padding band around the
 * frame, where the halved detail is imperceptible and the fill-rate saving
 * (~20% of the whole fog fleet) is real. */
export function MistCanvas() {
  const { show, fail } = useFogGate();
  if (!show) return null;
  return (
    <FogGL
      className="mist-canvas"
      maxEdge={480}
      phase={0}
      onPermanentFailure={fail}
    />
  );
}

/**
 * In-canvas fog - the "clear frosted glass" chat backdrop. A second fog
 * instance renders INSIDE the chat canvas under a milky (blur-free) white
 * veil, so the living mist shows through the paper. Deliberately NOT done
 * by making the app frame transparent: that would put live pixels behind
 * the sidebar/right-panel backdrop-filter glass and re-blur them every fog
 * frame. Localizing the fog keeps those glasses over opaque ground. The
 * composer's small glass dock does sample this layer - a bounded, measured
 * cost. A user-set chat background image paints over this and wins.
 */
export function CanvasMist() {
  const { show, fail } = useFogGate();
  if (!show) return null;
  return (
    <div className="canvas-mist" aria-hidden="true">
      <FogGL className="canvas-mist-gl" phase={0.25} onPermanentFailure={fail} />
      <div className="canvas-mist-milk" />
    </div>
  );
}

/** Ramp sub-bands for the side panels: the sidebar breathes in the deep-blue
 * region of the palette, the right panel in the light region - matching the
 * ground each panel occupies on the diagonal. */
const PANEL_BANDS = {
  left: [0.0, 0.52],
  right: [0.42, 1.0],
} as const;

/**
 * Frosted-glass side panels - a fog instance per panel under a MILKIER veil
 * than the chat canvas (panels hold solid, readable chrome; the user asked
 * for "more frosted"). Each veil replicates its panel's own surface gradient
 * slightly translucent, so the color theme is preserved and fog-off falls
 * back to today's exact look.
 */
export function PanelMist({ side }: { side: "left" | "right" }) {
  const { show, fail } = useFogGate();
  if (!show) return null;
  const [lo, hi] = PANEL_BANDS[side];
  return (
    <div className={`panel-mist panel-mist-${side}`} aria-hidden="true">
      <FogGL
        className="panel-mist-gl"
        rampLo={lo}
        rampHi={hi}
        phase={side === "left" ? 0.5 : 0.75}
        onPermanentFailure={fail}
      />
      <div className="panel-mist-veil" />
    </div>
  );
}

function FogGL({
  className,
  rampLo = 0,
  rampHi = 1,
  maxEdge = MAX_LONG_EDGE,
  phase = 0,
  onPermanentFailure,
}: {
  className: string;
  rampLo?: number;
  rampHi?: number;
  /** Internal-resolution cap for this instance (long edge, px). */
  maxEdge?: number;
  /** 0..1 fraction of the frame interval - staggers the four instances'
   * draw ticks across vsyncs instead of bunching one GPU spike per ~42ms. */
  phase?: number;
  onPermanentFailure: () => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    let raf = 0;
    let last = 0;
    let lostCount = 0;
    // Per-instance clock epoch: the shader time wraps at 4096s (float32
    // precision); anchoring each instance to its own mount time staggers the
    // once-per-68min wrap snap instead of all four fields jumping together.
    const epoch = performance.now();

    const gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
    });
    if (!gl) {
      onPermanentFailure();
      return;
    }

    let timeLoc: WebGLUniformLocation | null = null;
    let resLoc: WebGLUniformLocation | null = null;
    let ready = false;

    const build = (): boolean => {
      const compile = (type: number, src: string): WebGLShader | null => {
        const shader = gl.createShader(type);
        if (!shader) return null;
        gl.shaderSource(shader, src);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          gl.deleteShader(shader);
          return null;
        }
        return shader;
      };
      const vs = compile(gl.VERTEX_SHADER, VERT_SRC);
      const fs = compile(gl.FRAGMENT_SHADER, makeFragSrc(rampLo, rampHi));
      if (!vs || !fs) return false;
      const program = gl.createProgram();
      if (!program) return false;
      gl.attachShader(program, vs);
      gl.attachShader(program, fs);
      gl.linkProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        gl.deleteProgram(program);
        return false;
      }
      gl.useProgram(program);
      // One oversized triangle covers the viewport without a second one.
      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 3, -1, -1, 3]),
        gl.STATIC_DRAW,
      );
      const posLoc = gl.getAttribLocation(program, "a_pos");
      gl.enableVertexAttribArray(posLoc);
      gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
      timeLoc = gl.getUniformLocation(program, "u_time");
      resLoc = gl.getUniformLocation(program, "u_res");
      ready = true;
      return true;
    };

    const draw = (now: number) => {
      if (!ready) return;
      // mod 4096 keeps float32 shader time precise; epoch-relative so the
      // wrap snap lands at a different moment per instance.
      gl.uniform1f(timeLoc, ((now - epoch) / 1000) % 4096);
      gl.uniform2f(resLoc, canvas.width, canvas.height);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const resize = () => {
      const w = canvas.clientWidth || window.innerWidth;
      const h = canvas.clientHeight || window.innerHeight;
      const long = Math.max(w, h);
      const k = long > maxEdge ? maxEdge / long : 1;
      const nextW = Math.max(1, Math.round(w * k));
      const nextH = Math.max(1, Math.round(h * k));
      // Assigning width/height CLEARS the buffer to spec-black even when the
      // value is unchanged - skip no-ops, and repaint immediately after a
      // real change so window-drag never flashes dark canvases.
      if (nextW === canvas.width && nextH === canvas.height) return;
      canvas.width = nextW;
      canvas.height = nextH;
      gl.viewport(0, 0, nextW, nextH);
      draw(performance.now());
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (now - last < FPS_INTERVAL) return;
      last = now;
      draw(now);
    };

    const start = () => {
      cancelAnimationFrame(raf);
      // Synchronous first draw: an alpha:false buffer is spec-black until the
      // first present - never rely on the compositor hiding that frame. The
      // phase offset then staggers this instance's steady-state ticks.
      draw(performance.now());
      last = performance.now() - FPS_INTERVAL * (1 - phase);
      raf = requestAnimationFrame(frame);
    };

    const onLost = (event: Event) => {
      // preventDefault signals we intend to handle the restore.
      event.preventDefault();
      ready = false;
      cancelAnimationFrame(raf);
      lostCount += 1;
      if (lostCount > 2) onPermanentFailure();
    };
    const onRestored = () => {
      // Every GL object died with the old context - rebuild all state.
      if (build()) {
        resize();
        start();
      } else {
        onPermanentFailure();
      }
    };
    const onVisibility = () => {
      if (document.hidden) cancelAnimationFrame(raf);
      // Not ready = context currently lost: onRestored starts the loop itself;
      // restarting here would just spin no-op rAF callbacks until then.
      else if (ready) start();
    };

    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", resize);

    if (gl.isContextLost()) {
      // Rare: context already lost at mount (e.g. a GPU reset mid-load).
      // Best-effort restore - onRestored (registered above) rebuilds and
      // starts the loop when it lands.
      gl.getExtension("WEBGL_lose_context")?.restoreContext();
    } else if (build()) {
      resize();
      start();
    } else {
      // Parent unmounts us via the failure flag; the cleanup below still
      // runs and removes every listener registered above.
      onPermanentFailure();
    }

    return () => {
      // Deliberately NO loseContext() here: losing the context fires the
      // lost event into a listener-less gap (nobody preventDefaults it),
      // after which the browser refuses restoration - StrictMode's second
      // mount then inherits a permanently dead context on the SAME canvas
      // node. One ambient canvas can rely on normal GC; the restore path
      // above remains for genuine GPU resets.
      cancelAnimationFrame(raf);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", resize);
    };
  }, [onPermanentFailure, rampLo, rampHi, maxEdge, phase]);

  return <canvas ref={ref} aria-hidden="true" className={className} />;
}
