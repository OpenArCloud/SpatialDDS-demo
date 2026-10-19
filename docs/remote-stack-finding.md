# Running the demo against a stack that is not on this machine

**Filed 2026-10-19, from the first session driving the AR demo against the 1.8
OpenVPS deployed on EC2 (`openvps-fountain-3`, `g4dn.2xlarge`). Nothing here
is a defect in the deployment; all four are things that only appear once the
stack is remote, and three of them produced a wrong conclusion before they
produced an error.**

The page ran locally (`npm run dev` on :5173) against the instance's bridge
and binding over two `AWS-StartPortForwardingSession` tunnels on :8088 and
:8089. That is the configuration this records.

## 1. The tunnel dies two different ways, and only one of them is fixed by traffic

Both were hit in one afternoon:

- **Idle timeout.** `Your session timed out due to inactivity and has been
  terminated.` A periodic request prevents this.
- **Broken pipe.** `Cannot perform start session: write tcp ... broken pipe.`
  The session is gone, and no amount of traffic brings it back.

The first was met with a keepalive loop, which was the right fix for the
failure in hand and no help at all for the second: when the pipe broke, the
keepalive went on cheerfully polling two dead tunnels and reporting nothing.
What is needed is a supervisor that **probes for reachability** -- a 200 from
`/health` and from the entity lane -- and restarts the forward when the probe
fails, rather than one that assumes liveness because it is still sending.

One detail worth keeping: killing the `aws` wrapper does **not** take the
`session-manager-plugin` child with it, and the orphan keeps the local port
bound, so the replacement cannot bind and the restart silently fails. A
restart has to clear the plugin by pid first. In a foreground session Ctrl-C
is clean; backgrounded, it is not.

## 2. A dead tunnel looked exactly like a working demo

This is the part that cost real time. When the tunnels dropped, the page did
not report an error: `bridgeHealth()` failed, the badge fell back to the mock,
and `mockLocalize()` returned a canned pose logged as `localize:success`. So
the demo looked like it was working and was quietly answering from a local
fixture -- a failure mode that reads as a success with wrong numbers, which is
the worst shape available.

Two things made it indistinguishable rather than merely quiet, and both are
fixed:

- the badge's failure state and its *pre-probe* state were the same string,
  "Mock Mode", in the same failure colour, shipped that way in `index.html`.
  A tunnel that had been dead for an hour and a page that had been open for
  200 ms looked identical. The badge now opens on "Checking bridge..." in grey
  and says "No Bridge" when the probe answers no.
- the mock's pose was ~148 m from the venue, which is near enough to the real
  answer to pass a casual glance and far enough to be wrong. It now stands at
  the demo's own start position, which at least makes "did it localize?" a
  question about the log rather than about the map.

The general lesson is the one already in the ledger about verifying as the
audience does: every `curl` against the tunnels passed, in the same minutes
the page was in mock mode, because the page and the probe were asking
different questions.

## 3. Two specs structurally cannot run against a remote stack

`model-stack.helpers.ts`'s `container()` shells out to
`docker ps --format '{{.Names}}' | grep dds_bridge` **on the machine running
the test**. With the stack on EC2 it returns null, so `robot-goto.spec.ts`
skips with "no robot publishing" -- a reason that is true of this machine and
false of the system under test. `restoreVenue` is in the same position,
because it quiesces the mover by `docker exec`.

So the one spec that drives tap-to-goto end to end cannot cover the
deployment, and says so in words that suggest the robot is absent rather than
unreachable. The skip is honest about *that* it skipped and misleading about
*why*. Nothing is wrong with the helper for a local stack; what is missing is
a way to reach a remote container, and until there is one the goto path on a
deployment has to be driven by hand.

Recorded rather than fixed because the fix is a decision about how these specs
address a stack -- an env naming a host, an SSH or SSM shim, or splitting the
container-dependent assertions out -- and that is worth choosing deliberately.

## 4. `run_bridge_server_docker.sh` has no host-networking flag

The deployed demo needs the container on the host network, because the
OpenVPS service and the demo's bridge have to find each other over unicast
DDS on the same box. The launcher offers no way to ask for that, so getting it
onto the instance took an instance-local `sed` of the script -- an edit that
lives nowhere, is invisible to the next person, and vanishes on a re-clone.

It wants a real flag, in the same shape as the ones beside it
(`SPATIALDDS_MOCK_VPS`, `SPATIALDDS_DDS_CONFIG`): an env that adds
`--network host` and refuses to combine with the port mappings it makes
meaningless. Not done here because it is a change to how the demo starts and
could not be tested against the deployment in the same session it was found.
