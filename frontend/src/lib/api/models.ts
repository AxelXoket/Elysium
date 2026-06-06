import { request } from "./client";
import { ModelListSchema } from "../schemas/models";
import type { ModelList } from "../schemas/models";

export function listModels(refresh?: boolean): Promise<ModelList> {
  const query = refresh ? "?refresh=true" : "";
  return request(`/models/openrouter${query}`, ModelListSchema);
}
