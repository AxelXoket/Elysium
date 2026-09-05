/**
 * fogClassNames.tsx - the class names the mist layers ACTUALLY render.
 *
 * css-contract.test.ts carried a hand-typed copy of the three fog class
 * names, so a rename in the component with no matching edit to the
 * stylesheet left the reduced-transparency rule pointing at nothing and the
 * test still green. That is the identical shape of the hole the rule was
 * written to close (.ambient-fog-canvas / .ambient-mist / .chat-fog existed
 * only inside that selector list), which is why the live half of the list is
 * read from a render now instead of retyped.
 *
 * Three things stand between jsdom and a mounted fog layer, and all three are
 * environment, not behaviour:
 *  - the gate asks for a desktop viewport and for a reader who has not turned
 *    motion down, and jsdom's matchMedia answers false to everything;
 *  - the toggle lives in the ui store, which persists across tests;
 *  - there is no WebGL at all, and a missing context is a PERMANENT failure
 *    rung: FogGL reports it, the gate flips, and the whole layer unmounts to
 *    the static gradient before the caller can read a single class.
 *
 * The headless context below exists only to let `build()` succeed so the
 * mounted markup survives its own effect. Nothing here reads a pixel, draws,
 * or asserts anything about WebGL.
 */
import { render, cleanup } from "@testing-library/react";
import {
  MistCanvas,
  CanvasMist,
  PanelMist,
} from "@/components/backdrop/MistCanvas";
import { useUiStore } from "@/lib/store/uiStore";
import type { ReactElement } from "react";

/** One fog layer's root element: the node the stylesheet has to be able to
 *  hide, and every class actually spelled on it. */
export interface FogRoot {
  component: string;
  classes: string[];
}

/**
 * A WebGL context that answers every call FogGL makes and does nothing.
 *
 * Written as a Proxy rather than an object literal: the only calls that need
 * a real answer are the two status queries and the four creators, and a
 * literal would have to keep a list of thirty no-ops in step with a shader
 * setup this module has no opinion about.
 */
function headlessWebGL(): WebGLRenderingContext {
  const stub = new Proxy(
    {},
    {
      get(_target, prop: string) {
        // Compilation and linking must report success or build() bails out
        // and the caller sees the permanent-failure fallback instead.
        if (prop === "getShaderParameter" || prop === "getProgramParameter") {
          return () => true;
        }
        // A live context: the restore path is for a real GPU reset and has
        // nothing to say about class names.
        if (prop === "isContextLost") return () => false;
        if (
          prop === "createShader" ||
          prop === "createProgram" ||
          prop === "createBuffer" ||
          prop === "getUniformLocation"
        ) {
          return () => ({});
        }
        if (prop === "getAttribLocation") return () => 0;
        if (prop === "getExtension") return () => null;
        return () => undefined;
      },
    },
  );
  return stub as unknown as WebGLRenderingContext;
}

/** matchMedia answering as a desktop that has not asked for less motion -
 *  the one rung combination on which the fog is supposed to be visible. */
function fogVisibleMatchMedia(query: string): MediaQueryList {
  return {
    matches: query.includes("min-width: 901px"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}

/**
 * Renders every fog layer and returns each one's root element classes.
 *
 * Globals are saved and put back by hand rather than through
 * vi.unstubAllGlobals, because a caller that stubbed something of its own
 * would lose it.
 */
export function fogRootClassNames(): FogRoot[] {
  const realMatchMedia = window.matchMedia;
  const realGetContext = HTMLCanvasElement.prototype.getContext;
  const realFogOn = useUiStore.getState().ambientFogOn;

  window.matchMedia = fogVisibleMatchMedia as typeof window.matchMedia;
  HTMLCanvasElement.prototype.getContext = function getContext(id: string) {
    return id === "webgl" ? headlessWebGL() : null;
  } as typeof HTMLCanvasElement.prototype.getContext;
  useUiStore.setState({ ambientFogOn: true });

  try {
    const layers: [string, () => ReactElement][] = [
      ["MistCanvas", () => <MistCanvas />],
      ["CanvasMist", () => <CanvasMist />],
      ["PanelMist(left)", () => <PanelMist side="left" />],
      ["PanelMist(right)", () => <PanelMist side="right" />],
    ];
    const out: FogRoot[] = [];
    for (const [component, element] of layers) {
      const { container } = render(element());
      const root = container.firstElementChild;
      // A layer that rendered nothing is reported as itself with no classes.
      // Swallowing it here would hand the caller a shorter list and let an
      // unmounted fog pass as a covered one.
      out.push({
        component,
        classes: root ? Array.from(root.classList) : [],
      });
      cleanup();
    }
    return out;
  } finally {
    cleanup();
    window.matchMedia = realMatchMedia;
    HTMLCanvasElement.prototype.getContext = realGetContext;
    useUiStore.setState({ ambientFogOn: realFogOn });
  }
}
