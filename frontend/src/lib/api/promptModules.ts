/**
 * Client for /prompt-modules.
 *
 * Reads go through `request` like every other route. The writes that can turn
 * a module on (PATCH with `enabled`, and the preset's "turn on what was on in
 * SillyTavern") go through `moduleWrite` below instead, for one reason: when
 * the server refuses them with `prompt_modules_over_budget`, the refusal
 * carries the module names and their token cost, and the shared response
 * handler keeps only the `detail` code. The page has to say WHICH modules do
 * not fit, so this path keeps the whole body.
 *
 * The import sends the file's bytes as the request body with its name in a
 * header, the same raw-body shape as the character import. No multipart form:
 * the server reads the body in memory, and a multipart upload is what makes a
 * server spill a large part to a temp file.
 */
import { API_BASE } from "./base";
import {
  isApiError,
  notifyVaultLocked,
  rawRequest,
  request,
  type ApiError,
} from "./client";
import { launchTokenHeader } from "./launchToken";
import {
  EnablePresetDefaultsResponseSchema,
  PromptModuleImportResponseSchema,
  PromptModuleListSchema,
  PromptModuleSettingsSchema,
  PromptModuleWriteSchema,
  PromptModulesOverBudgetSchema,
  type EnablePresetDefaultsResponse,
  type PromptModuleImportResponse,
  type PromptModuleList,
  type PromptModuleSettings,
  type PromptModulesOverBudget,
} from "../schemas/promptModules";
import { z, type ZodType } from "zod/v4";

import { queryClient } from "@/lib/query/queryClient";

const PREFIX = "/prompt-modules";

/** Header that carries the file name on import. URI-encoded, because a
 *  header value is Latin-1 and file names are not. The same name as
 *  `filename_header` in shared/prompt_module_limits.json, which the backend
 *  pins its own copy against; PromptModulesSection.test.tsx pins this one. */
export const PROMPT_MODULE_FILENAME_HEADER = "X-Prompt-Module-Filename";

/** An ApiError that also says which modules did not fit. */
export type OverBudgetError = ApiError & {
  detail: "prompt_modules_over_budget";
  overBudget: PromptModulesOverBudget;
};

export function isOverBudgetError(err: unknown): err is OverBudgetError {
  return (
    isApiError(err) &&
    err.detail === "prompt_modules_over_budget" &&
    "overBudget" in err
  );
}

export function listPromptModules(): Promise<PromptModuleList> {
  return request(PREFIX, PromptModuleListSchema);
}

/** The list query's key. The settings page reads it through React Query;
 *  the chat's over-budget refusal reads the same cache to name modules. */
export const PROMPT_MODULE_LIST_KEY = ["promptModules", "list"] as const;

// ── The send-time refusal (H4 at request time) ─────────────────────────────
//
// When a message is refused because the enabled modules no longer fit (the
// window shrank, the model changed, a big card), the completion route answers
// with the same JSON body the toggle refusal has: the code, and each module
// with its id and token cost. This reads that body and names each module the
// way the settings page names it, from the module list, so the chat can say
// which modules and what they cost.

/** The refusal body as the completion route sends it. A name in the body is
 *  used only when the list does not know the id. */
const OverBudgetBodySchema = z.object({
  detail: z.literal("prompt_modules_over_budget"),
  modules: z.array(
    z.object({
      id: z.number().nullable().default(null),
      name: z.string().optional(),
      tokens: z.number(),
    }),
  ),
  total_tokens: z.number().nullable().optional(),
  room_tokens: z.number().nullable().optional(),
});

/**
 * The refusal in the response body `json`, with module names, or null when
 * the body is not a readable refusal (the caller then shows the catalogued
 * sentence, which is still true, just less specific).
 *
 * Names come from the module list: the cached copy if it is fresh, else one
 * fetch. A module the list does not know (deleted meanwhile, or the list
 * could not be read) keeps the body's name, else is named by its number
 * rather than dropped, so the count of modules in the sentence is always
 * the server's.
 */
export async function overBudgetFromBody(
  json: unknown,
): Promise<PromptModulesOverBudget | null> {
  const result = OverBudgetBodySchema.safeParse(json);
  if (!result.success) return null;
  const parsed = result.data;
  const names = new Map<number, string>();
  try {
    const list = await queryClient.fetchQuery({
      queryKey: PROMPT_MODULE_LIST_KEY,
      queryFn: listPromptModules,
      staleTime: 30_000,
    });
    for (const row of list.modules) names.set(row.id, row.name);
  } catch {
    // Named from the body, or by number, below.
  }
  return {
    detail: "prompt_modules_over_budget",
    modules: parsed.modules.map((m) => ({
      id: m.id,
      name:
        (m.id != null && names.get(m.id)) || m.name || `Module ${m.id ?? "?"}`,
      tokens: m.tokens,
    })),
    total_tokens: parsed.total_tokens ?? undefined,
    room_tokens: parsed.room_tokens ?? undefined,
  };
}

/** `err` carrying the named refusal, so the error path can say more than
 *  the catalogued sentence. */
export function withOverBudget(
  err: ApiError,
  overBudget: PromptModulesOverBudget,
): OverBudgetError {
  return { ...err, detail: "prompt_modules_over_budget", overBudget };
}

/** The chat's sentence for a named refusal: which modules, what each costs,
 *  and the two things that fix it. */
export function overBudgetSentence(overBudget: PromptModulesOverBudget): string {
  const list = overBudget.modules
    .map((m) => `${m.name} (about ${m.tokens} tokens)`)
    .join(", ");
  return `These prompt modules do not fit this chat's context window: ${list}. Turn a module off, or raise the context window.`;
}

export function importPromptModuleFile(
  file: File,
): Promise<PromptModuleImportResponse> {
  return rawRequest(`${PREFIX}/import`, PromptModuleImportResponseSchema, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      [PROMPT_MODULE_FILENAME_HEADER]: encodeURIComponent(file.name),
    },
    body: file,
  });
}

export interface PromptModulePatch {
  enabled?: boolean;
  /** The 0-based position to move to; the server renumbers the rest. */
  sort_order?: number;
  name?: string;
}

/**
 * The window a toggle is checked against (H4 at toggle time). The server
 * cannot know it: the context window, the reply reserve and the model live
 * in the client's generation settings and travel with every completion, so
 * they travel with a toggle too. `chat_id` is the open chat, whose card,
 * persona, Limits and notebook are the fixed cost; null uses the server's
 * 3,000 character reference card.
 */
export interface PromptModuleBudgetInputs {
  chat_id: number | null;
  context_budget_tokens: number | null;
  max_tokens: number | null;
  model_id: string | null;
}

export function patchPromptModule(
  id: number,
  patch: PromptModulePatch,
  budget?: PromptModuleBudgetInputs,
): Promise<unknown> {
  // The window only matters when a module is being switched on; a rename or
  // a move carries none, so the server has nothing to validate there.
  const body = patch.enabled === true && budget ? { ...patch, ...budget } : patch;
  return moduleWrite(`${PREFIX}/${id}`, PromptModuleWriteSchema, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function enablePresetDefaults(
  importId: number,
  budget?: PromptModuleBudgetInputs,
): Promise<EnablePresetDefaultsResponse> {
  return moduleWrite(
    `${PREFIX}/enable-preset-defaults`,
    EnablePresetDefaultsResponseSchema,
    {
      method: "POST",
      body: JSON.stringify({ import_id: importId, ...budget }),
    },
  );
}

export function deletePromptModule(id: number): Promise<unknown> {
  return request(`${PREFIX}/${id}`, PromptModuleWriteSchema, {
    method: "DELETE",
  });
}

export function getPromptModuleSettings(): Promise<PromptModuleSettings> {
  return request(`${PREFIX}/settings`, PromptModuleSettingsSchema);
}

/** Both settings, always together: the server replaces them as a pair and
 *  refuses a body with only one. */
export function putPromptModuleSettings(
  next: PromptModuleSettings,
): Promise<PromptModuleSettings> {
  return request(`${PREFIX}/settings`, PromptModuleSettingsSchema, {
    method: "PUT",
    body: JSON.stringify(next),
  });
}

/**
 * `request`, keeping the over-budget body.
 *
 * Every other outcome is shaped exactly like the shared client's: a network
 * failure is `network_error`, a 423 fires the vault-locked signal, a refusal
 * is an ApiError with the server's code, and a body that does not match the
 * schema is `invalid_response_shape` with the real status.
 */
async function moduleWrite<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...launchTokenHeader(),
        ...init.headers,
      },
    });
  } catch {
    const err: ApiError = {
      status: 0,
      detail: "network_error",
      message: "Could not reach the server. Please check your connection.",
    };
    throw err;
  }
  const json: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 423) notifyVaultLocked();
    const over = PromptModulesOverBudgetSchema.safeParse(json);
    if (over.success) {
      const err: OverBudgetError = {
        status: res.status,
        detail: "prompt_modules_over_budget",
        message: `Request failed: ${res.status}`,
        overBudget: over.data,
      };
      throw err;
    }
    const detail = (json as { detail?: unknown })?.detail;
    const err: ApiError = {
      status: res.status,
      detail: typeof detail === "string" ? detail : "unknown_error",
      message: `Request failed: ${res.status}`,
    };
    throw err;
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    const err: ApiError = {
      status: res.status,
      detail: "invalid_response_shape",
      message: "Unexpected response format from server.",
    };
    throw err;
  }
  return parsed.data;
}
