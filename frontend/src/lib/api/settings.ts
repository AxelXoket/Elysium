import { request } from "./client";
import {
  SettingsSchema,
  ProxyHealthSchema,
  OkResponseSchema,
  ApiKeySaveResponseSchema,
  StopSequencesResponseSchema,
} from "../schemas/settings";
import type {
  Settings,
  ProxyHealth,
  OkResponse,
  ApiKeySaveResponse,
  StopSequencesResponse,
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
