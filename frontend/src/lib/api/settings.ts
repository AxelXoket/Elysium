import { request } from "./client";
import {
  SettingsSchema,
  ProxyHealthSchema,
  OkResponseSchema,
  ApiKeySaveResponseSchema,
} from "../schemas/settings";
import type {
  Settings,
  ProxyHealth,
  OkResponse,
  ApiKeySaveResponse,
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

export function deleteProxy(): Promise<OkResponse> {
  return request("/settings/proxy", OkResponseSchema, {
    method: "DELETE",
  });
}

export function getProxyHealth(): Promise<ProxyHealth> {
  return request("/settings/proxy/health", ProxyHealthSchema);
}
