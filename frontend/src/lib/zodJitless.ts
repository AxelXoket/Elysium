/**
 * Tell Zod the truth about this app's Content-Security-Policy.
 *
 * Zod 4 decides whether to compile a fast object parser by probing
 * `new Function("")` inside a try/catch (node_modules/zod/v4/core/util.cjs, the
 * `allowsEval` cached getter). Under our `script-src 'self'` the call throws,
 * Zod swallows it and falls back to the slow path - correct behaviour, but
 * Chromium still reports the blocked call as an ENFORCED
 * `securitypolicyviolation`. The result is a red CSP error in the WebView2
 * console on every single launch, for a probe whose answer is already known.
 *
 * Zod's own comment at that line says to set `jitless` to skip it. Setting it
 * costs nothing: the JIT is unavailable under the policy either way, so this
 * only removes a false alarm - and it makes dev behave like the packaged build
 * instead of quietly taking a different parsing path.
 *
 * Do NOT "fix" the violation by adding 'unsafe-eval' to the policy instead.
 *
 * This is a side-effect module and it MUST be the first import in main.tsx:
 * `allowsEval` is lazily cached on first read, and our schema modules read it
 * while creating their schemas at module scope. Imported second, the probe has
 * already fired.
 */
import { config as zodConfig } from "zod/v4/core";

zodConfig({ jitless: true });
