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
 *
 * The shader itself and the numbers it is built from live in fogShader.ts,
 * next door: the colour ramp is read outside this component too.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { bufferSizeFor } from "@/components/backdrop/mistBuffer";
import { useUiStore } from "@/lib/store/uiStore";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import {
  makeFragSrc,
  PANEL_BANDS,
  VERT_SRC,
} from "@/components/backdrop/fogShader";

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

/**
 * Frosted-glass side panels - a fog instance per panel under a MILKIER veil
 * than the chat canvas, because the panels hold solid, readable chrome and
 * can take more frosting than prose can. Each veil replicates its panel's
 * own surface gradient
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
      // Null at zero size, never a window-sized guess - see bufferSizeFor.
      // Keeping the last good buffer means re-opening a panel does not have
      // to rebuild one.
      const next = bufferSizeFor(canvas.clientWidth, canvas.clientHeight, maxEdge);
      if (!next) return;
      const { width: nextW, height: nextH } = next;
      // Skip no-ops, and repaint immediately after a real change so a
      // window drag never flashes dark canvases. (The guard is right; the
      // reason it used to give was a 2D-canvas rule. For WebGL the drawing
      // buffer is cleared when the SIZE CHANGES, not on every assignment -
      // Khronos WebGL 1.0 2.2 - so an unchanged value would be harmless
      // here. It is skipped anyway: a size change also drops the GPU's
      // buffer-recycle cache, and this now runs on every frame of a 420ms
      // panel sweep rather than only on window resizes.)
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
    // The ELEMENT, not the window. Every one of these canvases changes size
    // without the window doing anything: both side panels animate their width
    // over `--duration-layout`, and the chat canvas is their flex sibling, so
    // collapsing a panel widens it by ~320px. A window listener sees none of
    // that, so the drawing buffer kept a stale aspect and the compositor
    // stretched the bitmap to cover the new box. Observing the canvas also
    // subsumes the window case: the page instance fills a 100vw box, so a real
    // window resize reaches it here too.
    //
    // ResizeObserver fires at most once per frame per element, so a 300ms
    // width transition costs about 18 callbacks - each one a redraw at the
    // panel's own small size. Debouncing to the end of the transition would
    // be worse and visibly so: the panel would show the stale, stretched
    // buffer for the whole animation and then snap.
    //
    // `contain: layout paint` on .es-side-panel is not SIZE containment, so
    // the canvas box is still reported normally.
    const ro =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    if (ro) ro.observe(canvas);
    else window.addEventListener("resize", resize);

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
      if (ro) ro.disconnect();
      else window.removeEventListener("resize", resize);
    };
  }, [onPermanentFailure, rampLo, rampHi, maxEdge, phase]);

  return <canvas ref={ref} aria-hidden="true" className={className} />;
}
