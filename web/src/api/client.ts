/**
 * The typed API client. Types come from schema.d.ts, generated from openapi.json (`npm run gen:api`).
 * Every request carries the signed-in key; a 401 signs out (see app/auth.tsx).
 */
import createClient from "openapi-fetch";
import type { paths } from "./schema";

let apiKey: string | null = null;
let onUnauthorized = () => {};

export function setApiKey(key: string | null) {
  apiKey = key;
}

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

export const api = createClient<paths>();

api.use({
  onRequest({ request }) {
    if (apiKey) request.headers.set("X-API-Key", apiKey);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && apiKey) onUnauthorized();
    return response;
  },
});

/** The API's error envelope (`{"error": {code, message, details}}`) as an exception. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
  }
}

function toError(response: Response, body: unknown): ApiError {
  const error = (body as { error?: { code?: string; message?: string; details?: Record<string, unknown> | null } })
    ?.error;
  if (error?.code) return new ApiError(response.status, error.code, error.message ?? error.code, error.details ?? null);
  return new ApiError(response.status, "http_error", `${response.status} ${response.statusText || "Request failed"}`);
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** `{data: T}` envelope -> T, or throws `ApiError`. */
export async function unwrap<T>(request: Promise<Result<{ data: T }>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok && data !== undefined) return data.data;
  throw toError(response, error);
}

/** For 204 and binary responses: the body as is, or throws `ApiError`. */
export async function ok<T>(request: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  throw toError(response, error);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
