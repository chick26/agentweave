import { AgentWeaveClient } from "./api-client";
import { subscribeRunEvents } from "./sse-client";

const baseUrl = import.meta.env.VITE_AGENTWEAVE_API_BASE;
const token = import.meta.env.VITE_AGENTWEAVE_TOKEN;

const client = new AgentWeaveClient(baseUrl, token);

export async function askAgentWeave(message: string): Promise<string> {
  const session = await client.createSession();
  console.log("welcome:", session.message);

  const run = await client.createRun(session.session_id, message);
  let answer = "";

  await subscribeRunEvents({
    baseUrl,
    token,
    eventsUrl: run.events_url,
    onEvent: (event) => {
      if (event.type === "runtime_event") {
        console.log("trace:", event.payload.kind, event.payload.payload);
      }
      if (event.type === "result_created") {
        console.log("result:", event.result_id, event.sample_rows);
      }
      if (event.type === "model_delta") {
        console.log("model delta:", event.payload.kind, event.payload.delta);
        if (event.payload.kind === "orchestration_model") {
          answer += event.payload.delta;
        }
      }
      if (event.type === "run_complete") {
        // Final answer is authoritative; use it to recover from missed SSE chunks.
        answer = event.answer;
      }
      if (event.type === "run_error") {
        throw new Error(event.message);
      }
    },
  });

  return answer;
}
