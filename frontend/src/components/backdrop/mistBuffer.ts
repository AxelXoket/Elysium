/**
 * mistBuffer.ts - how big a fog canvas' drawing buffer should be.
 *
 * Its own module rather than a second export from MistCanvas.tsx: a component
 * file that also exports a plain function breaks Fast Refresh, and the lint
 * gate says so. Being separable is the point anyway - this is arithmetic, so
 * it can be pinned by input and output without a GPU, which is the only way
 * the defect below is testable at all.
 */

/**
 * The drawing-buffer size for a canvas whose CSS box is `w` x `h`, or null
 * when there is nothing to draw.
 *
 * Pulled out of the effect because it is the whole of the bug that made the
 * side panels show a squashed backdrop, and because it is arithmetic: same
 * input, same answer, testable without a GPU. Two rules, both load-bearing:
 *
 *  - A ZERO measurement returns null. It used to fall back to the window,
 *    which is wrong in the one case that actually produces a zero: a
 *    collapsed side panel, whose collapsed state is persisted. Starting the
 *    app with a panel shut measured 0, took the window's 1600px, and handed
 *    the panel a 960px-wide buffer. Canvases have no object-fit by default,
 *    so `fill` then scaled the two axes independently and the fog arrived
 *    ~3x compressed horizontally with its drift ~5x too slow, while vertical
 *    stayed correct - which is what makes it read as distortion rather than
 *    as a different fog.
 *  - The cap is applied to the LONG edge and to both axes together, so the
 *    buffer keeps the element's aspect. Any drift between the two is the
 *    stretch above, in miniature.
 */
export function bufferSizeFor(
  w: number,
  h: number,
  maxEdge: number,
): { width: number; height: number } | null {
  if (!(w > 0) || !(h > 0)) return null;
  const long = Math.max(w, h);
  const k = long > maxEdge ? maxEdge / long : 1;
  return {
    width: Math.max(1, Math.round(w * k)),
    height: Math.max(1, Math.round(h * k)),
  };
}
