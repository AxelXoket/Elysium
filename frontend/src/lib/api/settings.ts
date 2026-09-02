import { request } from "./client";
import {
  SettingsSchema,
  ProxyHealthSchema,
  OkResponseSchema,
  ApiKeySaveResponseSchema,
  StopSequencesResponseSchema,
  AutoLockResponseSchema,
  ImageOutputResponseSchema,
  ModelSelectionResponseSchema,
} from "../schemas/settings";
import type {
  Settings,
  ProxyHealth,
  OkResponse,
  ApiKeySaveResponse,
  StopSequencesResponse,
  AutoLockResponse,
  ImageOutputResponse,
  ModelSelectionResponse,
} from "../schemas/settings";

export function getSettings(): Promise<Settings> {
  return request("/settings", SettingsSchema);
}

export function setApiKey(apiKey: string): Promise<ApiKeySaveResponse> {
  return request("/settings/api-key", ApiKeySaveResponseSchema, {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function deleteApiKey(): Promise<OkResponse> {
  return request("/settings/api-key", OkResponseSchema, {
    method: "DELETE",
  });
}

export function setProxy(
  proxyUrl: string,
  proxyRequired: boolean,
  proxyAlias: string | null,
): Promise<OkResponse> {
  return request("/settings/proxy", OkResponseSchema, {
    method: "POST",
    body: JSON.stringify({
      proxy_url: proxyUrl,
      proxy_required: proxyRequired,
      proxy_alias: proxyAlias,
    }),
  });
}

/**
 * Arm/disarm the kill-switch alone.
 *
 * The proxy URL field is write-only (cleared after every save, never shown),
 * so routing this boolean through setProxy() meant retyping the whole URL from
 * memory to change it - which is why flipping the switch used to write nothing.
 */
/** Persist the stop sequences in the vault. The server clamps and dedupes. */
export function setStopSequences(
  stopSequences: string[],
): Promise<StopSequencesResponse> {
  return request("/settings/stop-sequences", StopSequencesResponseSchema, {
    method: "POST",
    body: JSON.stringify({ stop_sequences: stopSequences }),
  });
}

/**
 * Allow (or stop allowing) a model to answer with a generated picture.
 *
 * Vault-stored, not a browser preference: it changes the outgoing request. The
 * server does no capability check on write - whether the model selected right
 * now can draw is decided per request from the cached catalogue.
 */
export function setImageOutput(
  enabled: boolean,
): Promise<ImageOutputResponse> {
  return request("/settings/image-output", ImageOutputResponseSchema, {
    method: "POST",
    body: JSON.stringify({ image_output_enabled: enabled }),
  });
}

/**
 * Lock the vault after this many minutes of doing nothing. 0 disables it.
 *
 * Vault-stored, not a browser preference, for the same reason the passphrase
 * is: browser storage is readable without it, and a protection setting
 * somebody else can read and change is not a protection setting.
 */
export function setAutoLock(minutes: number): Promise<AutoLockResponse> {
  return request("/settings/auto-lock", AutoLockResponseSchema, {
    method: "POST",
    body: JSON.stringify({ auto_lock_minutes: minutes }),
  });
}

/**
 * Hide the window from screen capture. Stored in the VAULT, like the auto-lock
 * delay and for the same reason: a protection setting somebody can read and
 * change without the passphrase is not one.
 *
 * It is a defence layer, not a guarantee - it stops the ordinary capture and
 * screen-share APIs on Windows, and it is not applied at all while the vault
 * is locked, because a locked screen has nothing on it to protect.
 */
export function setScreenPrivacy(enabled: boolean): Promise<OkResponse> {
  return request("/settings/screen-privacy", OkResponseSchema, {
    method: "POST",
    body: JSON.stringify({ screen_privacy_enabled: enabled }),
  });
}

/**
 * Persist which model is selected, in the VAULT rather than the browser.
 *
 * An OpenRouter model id ("anthropic/claude-3.5-sonnet") is a NAME a person
 * reads on screen, the exact shape S-09b (and the privacy rule) bans from
 * localStorage. It used to be one of three keys in uiStore's
 * `elysium-ui-state` blob; it lives here now, next to the API key and the
 * stop sequences. See uiStore.ts's version-3 migrate, which cleans the
 * plaintext copy out of any install that already has one.
 */
export function setSelectedModel(
  modelId: string | null,
): Promise<ModelSelectionResponse> {
  return request("/settings/model-selection", ModelSelectionResponseSchema, {
    method: "POST",
    body: JSON.stringify({ selected_model_id: modelId }),
  });
}

export function setProxyRequired(proxyRequired: boolean): Promise<OkResponse> {
  return request("/settings/proxy/required", OkResponseSchema, {
    method: "POST",
    body: JSON.stringify({ proxy_required: proxyRequired }),
  });
}

/** Rename the configured proxy on its own (no URL rewrite). */
export function setProxyAlias(
  proxyAlias: string | null,
): Promise<OkResponse> {
  return request("/settings/proxy/alias", OkResponseSchema, {
    method: "POST",
    body: JSON.stringify({ proxy_alias: proxyAlias }),
  });
}

export function deleteProxy(): Promise<OkResponse> {
  return request("/settings/proxy", OkResponseSchema, {
    method: "DELETE",
  });
}

export function getProxyHealth(): Promise<ProxyHealth> {
  return request("/settings/proxy/health", ProxyHealthSchema);
}
