import type { AgentWeaveSseEvent } from "./api-client";

export interface SubscribeRunEventsOptions {
  baseUrl: string;
  token: string;
  eventsUrl: string;
  signal?: AbortSignal;
  onEvent: (event: AgentWeaveSseEvent) => void;
}

export async function subscribeRunEvents({
  baseUrl,
  token,
  eventsUrl,
  signal,
  onEvent,
}: SubscribeRunEventsOptions): Promise<void> {
  const response = await fetch(`${baseUrl}${eventsUrl}`, {
    headers: {
      Accept: "text/event-stream",
      Authorization: `Bearer ${token}`,
    },
    signal,
  });

  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(`AgentWeave SSE ${response.status}: ${text}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed) {
        onEvent(parsed);
      }
    }
  }
}

function parseSseFrame(frame: string): AgentWeaveSseEvent | null {
  const dataLines = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trim());
  if (dataLines.length === 0) {
    return null;
  }
  return JSON.parse(dataLines.join("\n")) as AgentWeaveSseEvent;
}
