# For the spec queue: a browser cannot subscribe to an operator-scoped N.5 resource

**Where this belongs.** Appendix N.4, and the web-binding queue in
`SpatialDDS-spec`. Written here because the finding comes from the browser
rewiring and the spec repository is not this batch's. **Origin:** the web
bridge's browser lane, 2026-10-18, which is the first browser client to meet
the binding.

This one stops work rather than annotating it: the live half of the browser
rewiring has no conformant path, so the batch's part 1 cannot complete and
part 2's precondition — "with the browser off the dead lanes" — cannot be
met.

---

## The claim

N.4 requires a bearer credential in the `Authorization` header and forbids
one in a query parameter:

> A credential MUST NOT be accepted in a URL query parameter. Credentials
> belong in the `Authorization` header, for both `GET` and the WebSocket
> handshake; a query parameter writes the credential into proxy logs and
> `Referer` headers.

The reasoning is sound and the prohibition is right. But **the browser
WebSocket API cannot set any request header**, `Authorization` included —
that is a platform constraint, not an implementation gap. So for an
`operator-scoped` resource, N.5's subscription is reachable by every client
*except* the one the web binding most obviously exists for.

## Measured against the live bridge

```
N.3 GET  with Authorization header : 200
N.5 WS   with subprotocol only     : 401  <- a browser cannot do better
N.5 WS   with Authorization header : connected
```

The three rows are the whole finding. `fetch()` can set a header, so N.3 and
N.6 work from a browser. A WebSocket cannot, so N.5 does not. The gateway's
`_authorized` reads `Authorization` and nothing else, which is exactly what
N.4 says to do.

Consequence for this demo: the browser can fetch the venue and POST a `goto`,
and cannot watch anything move. The ducks and the robot are the demo.

## Three ways out, and the third is the cheapest

**1. Bless a header a browser can actually set.** The standard practice is the
subprotocol — `Sec-WebSocket-Protocol: bearer.<token>` — which is a header,
so it breaches neither N.4's letter nor its reasoning: it does not appear in a
URL, a proxy log line, or a `Referer`. It is the only credential channel the
WebSocket API exposes. N.4 would need a sentence permitting it and naming the
form, and the reference gateway would need to read it. The demo's client
already sends it, inert, so the line stops being inert when the mechanism
lands.

**2. A single-use ticket.** `GET` a short-lived ticket over HTTP with the
real credential, then present it in the subscription URL. This is what several
WebSocket deployments do, and it arguably sidesteps N.4 because a one-shot
ticket is not a credential. It costs a new endpoint, a lifetime, and a
revocation story, and it puts a secret-shaped string in a URL for the exact
duration the rule was written to prevent. Worth naming and not recommending.

**3. Question the access class, which may be the actual error.** N.4's own
guidance:

> An endpoint SHOULD classify by what a resource describes rather than by who
> asks: configured geometry — zones, crossing lines, frames, georeferences —
> is ordinarily public, and observations of people ordinarily are not.

The reference gateway's table reads:

| type | access |
| --- | --- |
| `spatial.events.SpatialZone` | `public-cacheable` |
| `spatial.core.GeoAnchor` | `public-cacheable` |
| `spatial.core.FrameTransform` | `public-cacheable` |
| **`spatial.owm.Entity`** | **`operator-scoped`** |

A venue's Open World Model is mostly *configured geometry* — a fountain, a
pond, declared bounds, a frame — which N.4 calls ordinarily public, and it
classes zones and anchors exactly that way. The entities that are not
configured are a robot and three rubber ducks, and neither is an observation
of a person. So `operator-scoped` for the whole type may be the
misclassification, and `public-cacheable` would make the browser lane work
with no credential, no new mechanism and no specification change.

Against that: `ModelLayer.FAST` entities are live observations of a *thing*,
and a venue may hold entities that are about people. A per-type access class
cannot express "this instance is configuration and that one is an
observation", which is the real tension — and it is the same shape as N.4's
own worked example, where a zone is public and a detection inside it is not.

## What this batch did with it

Stopped, and reported. `web/src/spatialdds_binding.ts` is written: N.3 state,
N.6 commands and the per-resource multiplexer the census called for, with the
socket's 401 documented at the line that causes it rather than worked around.
It is not wired into `app.ts`, because a browser that shows a static venue and
cannot watch it change is a worse demo than one that is honestly dark, and
that judgement is not the agent's to make unilaterally.

Nothing was done to `bridges/web_bridge/server.py`'s model readers either:
part 2 retires them once the browser is off them, and the browser cannot
come off them until this is settled.
