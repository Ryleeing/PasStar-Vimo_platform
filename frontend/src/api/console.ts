// The agent console streams server-sent events from a POST, which EventSource cannot do: read the body as a stream.
import { API_BASE, ApiError, apiState } from './client';

export interface Behind {
  identity: { agent_id: string | null; buyer_id?: string; sensitivity?: string; note?: string };
  refused?: { code: string; message: string; reported_to_valio: boolean };
  steps?: { node: number; name: string; summary: string }[];
  options?: { shown: number; left_out: number };
  seal?: { signed_by: string; algorithm: string; key_id: string; signature_valid: boolean; expired: boolean; quote_valid_until: string; protocol_id: string };
  gate?: { status: string; summary?: string; reasons?: string[] };
  mandate?: string;
  audit: { seq: number; event: string; summary: string; hash: string }[];
}

export type ConsoleEvent =
  | { type: 'connected'; server: string; tools: string[] }
  | { type: 'thinking' }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown>; rest?: boolean }
  | { type: 'tool_result'; id: string; name: string; ok: boolean; ms: number; result: Record<string, unknown>; behind: Behind }
  | { type: 'assistant'; text: string }
  | { type: 'attack'; attack: string; title: string }
  | { type: 'error'; message: string }
  | { type: 'done'; history?: unknown[]; state?: Record<string, unknown> };

export async function streamConsole(path: '/v1/console/turn' | '/v1/console/attacks', body: unknown,
                                    onEvent: (e: ConsoleEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(API_BASE + path, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', 'X-Demo-User': apiState.demoUser, ...(apiState.session ? { 'X-Demo-Session': apiState.session } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err?.error?.code ?? 'ERROR', err?.error?.message ?? `Console failed (${res.status})`, err?.error?.details ?? {});
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      if (chunk.startsWith('data: ')) onEvent(JSON.parse(chunk.slice(6)) as ConsoleEvent);
    }
  }
}
