# For the spec queue: a browser cannot subscribe to an operator-scoped N.5 resource

**Where this belongs.** Appendix N.4, and the web-binding queue in
`SpatialDDS-spec`. Written here because the finding comes from the browser
rewiring and the spec repository is not this batch's. **Origin:** the web
bridge's browser lane, 2026-10-18, which is the first browser client to meet
the binding.

**Status, 2026-10-18: ruled on, both ways.** This venue's model lanes are
reclassified `public-cacheable` and the browser is wired, which dissolves the
problem *here*; the gap stays filed as 1.9 material because reclassification
only helps where the data is genuinely public. Option 2 below is flagged as
the likely specification answer for lanes that must stay gated.

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

**And there is a second blocker, which the first measurement missed.** An
operator-scoped response carries `Cache-Control: no-store` and
`Vary: Authorization` and **no `Access-Control-Allow-Origin`** — correctly, by
the same decision that gated it, since a browser must not be invited to
attach a credential ambiently cross-origin. So a page on `:5173` could not
read `:8089` *even holding a valid token*. The table below was measured with
`aiohttp`, which does not enforce CORS, so it understated the problem: the
claim "fetch() can set a header, so N.3 and N.6 work from a browser" was
wrong for the cross-origin case, which is the only case a separate Vite dev
server has. Corrected here rather than quietly: operator-scoped is
unreachable from a browser on two counts, not one.

## Measured against the live bridge

```
N.3 GET  with Authorization header : 200
N.5 WS   with subprotocol only     : 401  <- a browser cannot do better
N.5 WS   with Authorization header : connected
```

Those rows isolate the handshake: `aiohttp` can set a header and connects, a
browser cannot and gets 401. The gateway's `_authorized` reads `Authorization`
and nothing else, which is exactly what N.4 says to do — there is no
implementation defect in it. Read them together with the CORS paragraph
above, which the rows do not show because the client measuring them does not
enforce CORS.

So for an operator-scoped lane the browser is refused twice: no credential on
the handshake, and no CORS on the HTTP responses it would have needed first.

## Three ways out, named rather than numbered

Named because this document has been cited by number in two places with two
different orders, and a cross-reference that depends on list position is a
footgun. The names are stable.

**(a) Subprotocol carriage — bless a header a browser can actually set.** The standard practice is the
subprotocol — `Sec-WebSocket-Protocol: bearer.<token>` — which is a header,
so it breaches neither N.4's letter nor its reasoning: it does not appear in a
URL, a proxy log line, or a `Referer`. It is the only credential channel the
WebSocket API exposes. N.4 would need a sentence permitting it and naming the
form, and the reference gateway would need to read it. The demo's client does
not send it: these lanes are public now, so there is nothing to carry, and a
client sending an inert credential at a surface that does not want one would
be cargo rather than preparation.

**(b) A single-use ticket.** `GET` a short-lived ticket over HTTP with the
real credential, then present it in the subscription URL. This is what several
WebSocket deployments do, and it arguably sidesteps N.4 because a one-shot
ticket is not a credential. It costs a new endpoint, a lifetime, and a
revocation story, and it puts a secret-shaped string in a URL for the exact
duration the rule was written to prevent. Worth naming and not recommending.

**(c) Reclassify — question the access class, which may be the actual error.** N.4's own
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

## What was done about it

**(c), for this venue.** `bridges/web_bridge/binding.py` carries an
`ACCESS` table that classifies the entity and pose lanes `public-cacheable`
and keeps the command lane `operator-scoped` — reading the venue and driving
it being different questions. N.4 instructs *the endpoint* to classify by
what a resource describes, so the override belongs there rather than in the
package's by-type defaults, and no release was needed.

Verified cross-origin with no credential, which is exactly what the page does:

```
N.3 state      : 200, 8 entities, CORS=*
N.5 live       : entity=27 events, pose=88 events (no credential)
N.6 no token   : 401 (must refuse)
N.6 with token : 202 accepted
```

**And the gap stays filed**, because that is a judgement about this venue's
data and not about the type. A deployment whose model carried observations of
people would get no such override and would meet both blockers unchanged. The
per-type-versus-per-instance tension is N.4's own — a zone is public and a
detection inside it is not — and a per-type access class cannot express it.

For the specification, the question is **(a), subprotocol carriage** — the
option flagged on review as the likely spec-level answer for data that
genuinely must stay operator-scoped. It is the established ecosystem
workaround, it is a header rather than a URL, and it breaches neither N.4's
letter nor its reasoning. Without it, "operator-scoped N.5 stream" and
"browser client" remain mutually exclusive, and **(b)** stays named and
argued against: a one-shot ticket still puts a secret-shaped string in a URL
for exactly the duration the rule exists to prevent.

This is 1.9 material rather than an erratum. N.4 is not self-contradictory —
it is browser-hostile, which is a different and more interesting problem, and
the spec agent characterizes it while this repository files it.
