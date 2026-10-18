/**
 * The 1.8 web binding, from the browser: N.3 for state, N.5 for live.
 *
 * This is the client half of `bridges/web_bridge/binding.py`, and it exists
 * because the model lanes moved to Appendix N while the AR demo's seven
 * request/response routes did not. So the app now talks to two surfaces, and
 * `spatialdds_bridge.ts` keeps the other one: localize, catalogue, frames and
 * health stay where they were, because N has no RPC shape to move them to.
 * `docs/bridge-1.8-census.md` finding 1 is why.
 *
 * Three things here are not what the old surface did, and each is the binding
 * being different rather than this module being clever.
 *
 * **State is a collection, not a snapshot document.** `GET /{scene}/world/
 * entity/v1` returns an array of entities. There is no `relationships` key
 * any more, because `spatial.owm/0.1` carries no relationship type --- see
 * the 1.9 candidate drafted at `docs/relationship-candidate-1.9.md`. The
 * hierarchy the old snapshot carried is simply gone, and the client renders
 * containment from geometry or not at all.
 *
 * **Live is one socket per resource.** N.5 has no pattern subscribe: a
 * subscription is reached at a resource's own URL, and each one replays that
 * resource's latched set and sends exactly one `end_of_replay` marker before
 * live events. The old `/ws` took `{subscribe, pattern: 'spatialdds/model/*'}`
 * on one connection, which has no equivalent. `subscribeModel` is the
 * multiplexer that closes that gap on this side: two sockets in, one typed
 * callback interface out, so the rest of the app does not have to learn N.5.
 *
 * **Reading needs no credential; commanding does.** The bridge classifies
 * this venue's model lanes `public-cacheable` --- N.4 asks an endpoint to
 * classify by what a resource *describes*, and these describe a fountain, a
 * pond, declared bounds, a frame, a simulated robot and three rubber ducks.
 * The command lane stays `operator-scoped`, because what it describes is an
 * instruction: reading the venue and driving it are different questions.
 *
 * That split is what makes this module possible at all. An operator-scoped
 * response carries `no-store`, `Vary: Authorization` and **no**
 * `Access-Control-Allow-Origin`, so a page on :5173 cannot read :8089 even
 * holding a valid token; and a browser cannot put a credential on a
 * WebSocket handshake, which N.4 requires and forbids the alternatives to.
 * Both are measured in `docs/browser-credential-finding.md`, which routes
 * them apart: the handshake (blocker A) is a specification question, the
 * CORS half (blocker B) is a gateway improvement needing no specification
 * change. They stay filed because reclassifying only helps where the data is
 * genuinely public.
 *
 * So state and live need nothing. `VITE_SPATIALDDS_BINDING_TOKEN` is needed
 * only to send a command, and without it Send Robot says so rather than
 * failing quietly.
 */
import type { ModelEntity } from './spatialdds_bridge';

const DEV_BINDING_URL = 'http://localhost:8089';

const VITE_ENV = (import.meta as ImportMeta & {
  env?: Record<string, string | undefined>;
}).env;

/**
 * Where the binding is. Its own variable, not a path under the bridge's.
 *
 * The two surfaces are two processes --- `server.py` on :8088 and
 * `binding.py` on :8089 --- so a client that derived one from the other would
 * be asserting a deployment shape neither process guarantees.
 */
function resolveBindingUrl(): string {
  const configured = VITE_ENV?.VITE_SPATIALDDS_BINDING_URL;
  if (configured) {
    return configured;
  }
  const loc = typeof window !== 'undefined' ? window.location : undefined;
  if (loc && loc.hostname &&
      !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(loc.hostname)) {
    // Deployed: same host, the binding's port. Stated rather than guessed at
    // a path, because the binding serves from the root of its own origin.
    return `${loc.protocol}//${loc.hostname}:8089`;
  }
  return DEV_BINDING_URL;
}

export const BINDING_URL = resolveBindingUrl();
export const BINDING_TOKEN = VITE_ENV?.VITE_SPATIALDDS_BINDING_TOKEN || '';

/** The scene the demo publishes under. One venue, one scene. */
export const SCENE = 'ut-austin-littlefield';

const ENTITY_PATH = `/${SCENE}/world/entity/v1`;
const POSE_PATH = `/${SCENE}/world/model_pose/v1`;
const COMMAND_PATH = `/${SCENE}/world/model_command/v1`;

function authHeaders(): Record<string, string> {
  return BINDING_TOKEN ? { Authorization: `Bearer ${BINDING_TOKEN}` } : {};
}

/** Whether a credential is configured --- needed only for commands. */
export function canCommand(): boolean {
  return Boolean(BINDING_TOKEN);
}

/**
 * The latched venue, as N.3 serves it.
 *
 * Returns an empty array rather than throwing, for the reason the old
 * snapshot did: "no model" and "no model layer" lead a client to the same
 * place, which is catalogue placement. A 401 lands here too, and that is
 * correct --- an app without a credential has no model, and saying so once in
 * the log beats a stack trace per reload.
 */
export async function bindingEntities(): Promise<ModelEntity[]> {
  try {
    const r = await fetch(`${BINDING_URL}${ENTITY_PATH}`, {
      headers: authHeaders()
    });
    if (!r.ok) {
      return [];
    }
    const doc = await r.json();
    return Array.isArray(doc) ? doc as ModelEntity[] : [];
  } catch {
    return [];
  }
}

/**
 * Send a command over N.6.
 *
 * The body is a complete `spatial.owm.ModelCommand`, because the binding
 * validates it against the published schema before any backend sees it --- so
 * the guarded members have to be present and `stamp.sec` has to be a decimal
 * *string*, which is N.1's rule for every int64 on this wire. The old bridge
 * accepted a partial command and filled the rest in server-side; the binding
 * will not, and that is the better bargain: a malformed command is a 400 with
 * a problem document rather than a server guessing.
 *
 * Returns the outcome. N.6 makes a decline a first-class result, so a `409`
 * is not an exception here --- it is an answer, with a reason, and the caller
 * is told which it got.
 */
export async function bindingCommand(
  verb: string,
  subjectId: string,
  pose?: { t: number[]; q: number[] },
  reason = ''
): Promise<{ accepted: boolean; status: number; detail: string }> {
  const now = Date.now() / 1000;
  const body = {
    command_id: `cmd:web:${verb}-${Math.floor(now * 1000)}`,
    verb,
    subject_id: subjectId,
    reason,
    requester_id: 'web:cesium',
    has_pose: Boolean(pose),
    pose: pose || { t: [0, 0, 0], q: [0, 0, 0, 1] },
    has_extent: false,
    extent: { min_xyz: [0, 0, 0], max_xyz: [0, 0, 0] },
    // N.1: an int64 is a decimal string. `nanosec` is a uint32 and stays a
    // number, which is the asymmetry the mapping actually specifies.
    stamp: { sec: String(Math.floor(now)), nanosec: 0 }
  };
  try {
    const r = await fetch(`${BINDING_URL}${COMMAND_PATH}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });
    const doc = await r.json().catch(() => ({}));
    return {
      accepted: r.status === 202,
      status: r.status,
      detail: String(doc.reason || doc.detail || doc.title || r.statusText)
    };
  } catch (e) {
    return { accepted: false, status: 0, detail: String(e) };
  }
}

export type ModelHandlers = {
  /** A whole entity: new, or changed in something other than its pose. */
  onEntity(entity: ModelEntity): void;
  /** The fast lane: a pose for something already on screen. */
  onPose(entityId: string, pose: { t: number[]; q: number[] }): void;
  /** The entity is gone. N.5.2 removal, after its terminal sample. */
  onRemoved(entityId: string): void;
  /** One lane finished replaying its latched set, with the count it claimed. */
  onReplayComplete(lane: string, replayed: number): void;
  /** Anything a human should see in the log. */
  onStatus(line: string): void;
};

/**
 * Subscribe to the model over N.5: one socket per resource, one interface out.
 *
 * **This is the multiplexer the bridge census called for.** The binding
 * models a subscription per resource and the app wants one model, so the
 * translation lives here and nowhere else: the entity lane and the pose lane
 * each get their own socket, their own replay and their own marker, and the
 * callbacks above are what the app sees.
 *
 * Reconnection is per socket and deliberately simple --- a fixed delay, no
 * backoff ladder. A lane that drops on this demo has lost its bridge, and a
 * client that retried cleverly would mostly be hiding that.
 *
 * Returns a function that closes both sockets and stops reconnecting.
 */
export function subscribeModel(handlers: ModelHandlers): () => void {
  const sockets: WebSocket[] = [];
  let stopped = false;

  const open = (path: string, lane: string) => {
    if (stopped) {
      return;
    }
    const wsBase = BINDING_URL.replace(/^http/, 'ws');
    // No credential here, and none is possible: a browser cannot set a
    // header on a WebSocket handshake, and N.4 requires the credential in a
    // header and forbids the alternatives. These lanes are reachable only
    // because the bridge classifies them public --- see
    // docs/browser-credential-finding.md, blocker A, for the measurement and
    // the specification question it leaves open for lanes that must stay
    // gated.
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${wsBase}${path}`);
    } catch (e) {
      handlers.onStatus(`model:${lane} socket failed (${String(e)})`);
      return;
    }
    sockets.push(ws);

    ws.onopen = () => handlers.onStatus(`model:${lane} subscribed over N.5`);
    ws.onmessage = (event) => {
      let msg: {
        event?: string; topic?: string; key?: string;
        data?: Record<string, unknown>; replayed?: number;
      };
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return;
      }
      if (msg.event === 'end_of_replay') {
        handlers.onReplayComplete(lane, Number(msg.replayed || 0));
        return;
      }
      if (msg.event === 'dispose') {
        if (msg.key) {
          handlers.onRemoved(msg.key);
        }
        return;
      }
      if (msg.event === 'liveliness' || !msg.data) {
        return;
      }
      if (lane === 'pose') {
        const p = msg.data as unknown as
          { entity_id?: string; pose?: { t: number[]; q: number[] } };
        if (p.entity_id && p.pose) {
          handlers.onPose(p.entity_id, p.pose);
        }
        return;
      }
      handlers.onEntity(msg.data as unknown as ModelEntity);
    };
    ws.onclose = () => {
      if (!stopped) {
        handlers.onStatus(`model:${lane} closed; reconnecting`);
        window.setTimeout(() => open(path, lane), 2000);
      }
    };
  };

  open(ENTITY_PATH, 'entity');
  open(POSE_PATH, 'pose');

  return () => {
    stopped = true;
    for (const ws of sockets) {
      try {
        ws.close();
      } catch {
        // closing an already-closed socket is not an error worth reporting
      }
    }
  };
}
