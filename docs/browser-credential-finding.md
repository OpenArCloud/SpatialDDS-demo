# A browser cannot reach an operator-scoped resource: two blockers, two queues

*(Filed under `browser-credential-finding.md`. The title said "for the spec
queue" and "N.5" until 2026-10-18, when the second blocker turned out to be
an HTTP-side CORS problem the implementation can fix without the
specification. The filename is kept because the client module and the bridge
census both cite it.)*

**Where this belongs.** Appendix N.4, and the web-binding queue in
`SpatialDDS-spec`. Written here because the finding comes from the browser
rewiring and the spec repository is not this batch's. **Origin:** the web
bridge's browser lane, 2026-10-18, which is the first browser client to meet
the binding.

**Status, 2026-10-18 (second revision): blocker B is fixed in the gateway;
blocker A is unchanged and still the specification's.**
`spatialdds[server]` 0.5.3 answers the preflight on every path and echoes the
asking origin on an authorized operator-scoped response, so a cross-origin
page can now read a gated resource and POST an N.6 command. Verified from
PyPI; the demo pins 0.5.3. What remains is A — a browser cannot put a
credential on a WebSocket handshake at all — plus the specification note that
nothing required any of B in the first place. The rest of this filing is kept
as written, because the reasoning is what made the fix findable and the
measurements are what made it provable.

**Status, 2026-10-18 (first revision): ruled on, both ways — and reading is
fixed here while commanding is not.** This venue's model lanes are reclassified
`public-cacheable` and the browser reads them with no credential, which
dissolves the *downstream* problem here; the gap stays filed as 1.9 material
because reclassification only helps where the data is genuinely public, and
option **(a)** is flagged as the likely specification answer for lanes that
must stay gated.

The upstream half is **not** dissolved, and running the browser is what showed
it: a command always carries a credential, so it always trips blocker B's
unanswered preflight, and **N.6 is unreachable from a browser for every access
class** — reclassifying cannot help, because the credential is the trigger.
See "B is wider than operator-scoped" below. That makes B's gateway fix a
prerequisite for the browser's command lane rather than a tidy-up — and it
was met in `spatialdds-web` 0.5.3 rather than asked of the specification. See
the second revision above.

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

**And there is a second blocker, which the first measurement missed — but it
is a different kind of problem, and the distinction is the point of this
section.** Operator-scoped is unreachable from a browser on two counts:

| | blocker | kind |
| --- | --- | --- |
| **A** | a browser cannot put a credential on a WebSocket handshake, and N.4 requires a header and forbids the alternatives | **specification-level** |
| **B** | operator-scoped HTTP responses are unreachable cross-origin: the CORS preflight is unanswered and the response carries no `Access-Control-Allow-Origin` | **implementation-remediable, no spec change** |

Blocker **B** measured on the live bridge:

```
OPTIONS /…/world/model_command/v1
  Origin: http://localhost:5173
  Access-Control-Request-Method: GET
  Access-Control-Request-Headers: authorization
→ HTTP/1.1 405 Method Not Allowed
  Allow: GET,HEAD,POST
```

An `Authorization` header is not CORS-safelisted, so a cross-origin `fetch`
carrying one *preflights* — and the gateway routes no `OPTIONS`, so the
preflight 405s and the GET is never attempted. Beyond that, the
operator-scoped branch of `_cache_headers` sets `no-store` and
`Vary: Authorization` and no `Access-Control-Allow-Origin` at all.

So the earlier claim here — "fetch() can set a header, so N.3 and N.6 work
from a browser" — was wrong for the cross-origin case, which is the only case
a separate Vite dev server has. It was measured with `aiohttp`, which does not
enforce CORS. Corrected rather than quietly dropped.

### B is wider than "operator-scoped", and that was measured the hard way

**Amended 2026-10-18, from the browser actually running.** The paragraph above
describes B as operator-scoped responses being unreachable, which is true and
is not the whole shape. The preflight is forced by the **request** carrying
`Authorization`, not by the resource's access class — so B blocks *any*
credentialed cross-origin request to this gateway, whatever the resource is
classified as. Two consequences, both measured against binding 0.5.2 with a
page on another origin:

**1. N.6 is unreachable from a browser for every access class.** A command
always carries a credential, so it always preflights:

```
OPTIONS /…/world/model_command/v1
  Access-Control-Request-Method: POST
  Access-Control-Request-Headers: authorization,content-type
→ HTTP/1.1 405 Method Not Allowed
  Allow: GET,HEAD,POST
```

Nothing on the page can work around that — it is the browser enforcing CORS
correctly against a gateway that does not answer. So "a tab POSTs a `goto`"
did not work cross-origin, and `web/tests/robot-goto.spec.ts` probes this
preflight and skips with the reason rather than failing — which is how it
started running again by itself when 0.5.3 began answering, with no edit to
the spec. That was the shape's whole purpose, and it is the part of this
filing that paid for itself. The demo's own
`tools/post_goto.py` is unaffected: it is not a browser and enforces no CORS,
which is exactly why this went unnoticed.

**2. A client must not volunteer a credential to a public lane.** The
converse, and the sharper lesson. `GET /{scene}/world/entity/v1` is
`public-cacheable` and answers `200` with `Access-Control-Allow-Origin: *` to
a plain cross-origin GET. Send a *valid* `Authorization` header with it and
the page gets nothing at all — not a 401, but a failed preflight before any
request is made:

```
GET     entity/v1, no credential    → 200, Access-Control-Allow-Origin: *
OPTIONS entity/v1, authorization    → 405 Allow: GET,HEAD,POST
GET     entity/v1, with credential  → 200  (from curl; a browser never gets here)
```

The client sent that header until 2026-10-18, on the reasoning that a
credential is harmless where it is not required. It is not harmless: it is the
difference between a venue that loads and a venue that is silently empty. It
was invisible because the acceptance run had no token configured, so the
header was never added — **a credential path that is only exercised when a
credential exists is untested in exactly the configuration that has one.**
`spatialdds_binding.ts` now carries the token on the command lane alone.

**B needs no specification change and should not be asked of one.** A gateway
may answer the preflight, permit the `Authorization` request header, and echo
the requesting `Origin` on an authorized operator-scoped response with
`Vary: Origin`. That weakens no gate: the credential is still required, and
the response stays `no-store`. Echoing the origin rather than sending `*` is
the conservative form and keeps the header out of any shared-cache
interaction — which matters least here, since the resource is uncacheable
anyway, and is still the right default. **This half belongs on the
`spatialdds-web` queue as a candidate gateway improvement, not on the spec
queue.**

Everything below about credential carriage is blocker **A**, which is the
specification question.

## Measured against the live bridge

```
N.3 GET  with Authorization header : 200
N.5 WS   with subprotocol only     : 401  <- a browser cannot do better
N.5 WS   with Authorization header : connected
```

Those rows isolate blocker **A**, the handshake: `aiohttp` can set a header
and connects, a browser cannot and gets 401. The gateway's `_authorized`
reads `Authorization` and nothing else, which is exactly what N.4 says to do
— there is no implementation defect in it, which is why this half is a
specification question and not a bug report.

The rows do not show blocker **B**, because the client measuring them does
not enforce CORS. Both are needed for the whole picture: a browser is refused
at the preflight before it reaches the GET, and refused at the handshake if it
got that far.

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

### A third thing to route, and it is neither A nor B

**Added 2026-10-18 on review.** B's *fix* is an implementation's, and that
stays ruled: a gateway answers `OPTIONS`, allows the header, echoes the
`Origin`, and no specification sentence is needed to permit any of it. But the
fact that **every** reference gateway could omit all of it and still conform
is itself a specification gap, and it is a different one from A.

Appendix N's flagship consumer is a browser. A binding written for browsers
that says nothing about preflight behaviour leaves its most obvious client
unable to use its command surface at all, while the endpoint passes every
conformance check --- which is what happened here, against a gateway with no
defect in it. So the general lesson is **not** "permit this" (nothing forbids
it) but "**require it to be stated**": a web binding whose transport is
`fetch` and `WebSocket` from a page needs its CORS behaviour specified, most
naturally as an implementation-requirements note rather than new protocol.

Four candidate sentences' worth of substance, for the spec agent to
characterize rather than for this repository to draft:

- an endpoint MUST answer `OPTIONS` for every resource it serves, and MUST
  include `Authorization` and `Content-Type` among the allowed request
  headers wherever it accepts them;
- an authorized response MUST carry `Access-Control-Allow-Origin`, echoing
  the requesting `Origin` with `Vary: Origin` when the response is not
  cacheable --- otherwise a credential that N.4 requires buys a response the
  client cannot read;
- an **N.8 problem document** MUST be readable by the client it is addressed
  to, which on this wire means it carries the same reach as a success. This is
  the sharpest of the three and it was learned the hard way: `spatialdds-web`
  0.5.3 gave the preflight and the successful GET their origin and left every
  problem document without one, so a cross-origin `401`, `409` or `400`
  rejected at the `fetch` and the client received **neither status nor body**.
  N.8 exists to make a refusal a *stated reason* rather than a number, and a
  reason the client cannot read gives up precisely that — **N.8 defeated by
  transport**, with nothing in the specification to notice it. Fixed in 0.5.4;
  recorded there as finding 34b;
- and a client SHOULD NOT send a credential to a resource whose access class
  does not require one, because on this wire a volunteered credential changes
  a readable response into an unreadable one.

The last is a client requirement and the only one of the four this
repository has already paid for: see consequence 2 above. The problem-document
row is the one the *gateway* paid for, twice — once by omitting it and once by
fixing it in the wrong shape first.

**And a note on why this kind of gap lasts, which the fix does not retire.**
Same-origin requests are not preflighted, so a dev server that proxies the API
hides the whole problem. This demo does exactly that —
`web/vite.config.ts` proxies the scene prefix to the binding with `ws: true`.

**Its status changed on 0.5.3 and the proxy stays.** It was load-bearing:
without it, tap-to-send could not work in development at all. Now it is a
convenience — one origin, no preflight round trip, no token baked into a dev
bundle — and the thing it used to work around is fixed upstream. Keeping it is
right for development and the distinction is worth writing down, because the
proxy is also the single most likely reason a missing preflight survives
contact with a working browser client for months: **the configuration
everybody develops in is the one configuration where it does not matter.**
This gap was found by a page talking *directly* to another port, which is the
configuration a deployment has and a dev server usually does not.

**Routing, so no part lands in the wrong queue.** Three destinations, not
two — the third was added on review and is the one easiest to lose, because it
looks like B.

**A** is the spec agent's to characterize: N.4's credential carriage, with
subprotocol carriage the likely answer.

**B** was a candidate improvement for the `spatialdds-web` gateway — answer
the preflight, allow the `Authorization` header, echo the `Origin` with
`Vary: Origin`. Asking the specification for it would have been asking the
wrong body for a thing the implementation could simply do, and the
implementation did it: 0.5.3, finding 34 there. **Closed.**

**The third** is the spec agent's too: that a browser-facing binding must
*state* its preflight behaviour, as an implementation-requirements note. It is
separate from B because it explains why nothing obliged anyone to do B — a
gateway could omit all of it and still conform. **B is the fix; the third is
why the fix was optional.**

**Dispatched, 2026-10-18.** This was a block staged for
`spatialdds-web`'s queue, because B belonged to that repository and the batch
that found it was this one's. It was never queued: it was implemented instead,
as 0.5.3, and is recorded there as finding 34 with its own gate
(`gates/cors_preflight.py`, 16 checks). Kept below as the ask that was made,
so the fix can be read against it:

```markdown
**Gateway candidate: answer the CORS preflight.** `gw_http` routes no
`OPTIONS`, so every cross-origin request carrying `Authorization` is refused
at the preflight with `405 Allow: GET,HEAD,POST` and never reaches the
handler. Measured against 0.5.2 from a page on another origin
(`SpatialDDS-demo`, `docs/browser-credential-finding.md`): N.6 commands are
unreachable from a browser for **every** access class, because a command
always carries a credential; and a `public-cacheable` GET that would answer
`200` with `Access-Control-Allow-Origin: *` fails outright if the client
volunteers a credential it does not need. Fix: route `OPTIONS`, allow the
`Authorization` and `Content-Type` request headers, and echo the requesting
`Origin` with `Vary: Origin` on an authorized response — the credential stays
required and the response stays `no-store`, so no gate is weakened.
```
