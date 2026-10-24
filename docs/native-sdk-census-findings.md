# Findings from the native-SDK census: the prototype's DDS face

**Origin.** spatialdds-web's native-SDK census (`docs/NATIVE-SDK-CENSUS.md`
there, 2026-10-24) inventoried what `spatialdds_demo` and three other
codebases each implement of the same unwritten participant library, and then
built it once as `spatialdds.dds`. These are the defects it found here.

**Filing, not fixing.** This repository's adoption of the SDK is the follow-up
batch. Several of these close by construction there (the SDK's defaults are the
fixes), and the census's fit map lists every helper's SDK equivalent. Each item
says whether it was measured or read.

## 1. A crashed writer is a dispose (measured)

`typed_transport.make_writer` keeps DDS's default,
`autodispose_unregistered_instances = TRUE`. A participant killed with
`kill -9` therefore reaches readers as `NOT_ALIVE_DISPOSED`, a deliberate
removal, once its lease expires. §2.14: "Loss of liveliness, a lease expiry, or
a writer simply disappearing is not removal."

Measured with `ContentAnnouncePublisher` and `ContentInventory` unchanged, one
process each:

```
reader: 2.1 valid 16
reader: 12.2 invalid 32        # 32 = NOT_ALIVE_DISPOSED, ten seconds after kill -9
```

The same writer with `WriterDataLifecycle(autodispose=False)` gives `64`
(`NOT_ALIVE_NO_WRITERS`). spatialdds_ros made this change in 0.4.0.

## 2. The readers purge on no-writers (measured)

`ContentInventory.refresh` (`discovery_bus.py:230-232`) treats every invalid
sample as a removal. So even with finding 1 fixed, the crash above still
empties the inventory: the `64` arm's reader also showed
`inventory []`. `AnnounceSubscriber.poll` (`:160-163`) does the same for
services. §2.14: consumers "MUST NOT purge a durable keyed instance on
liveliness loss alone."

## 3. And they miss a removal that arrives on a valid sample (measured)

Under KEEP_LAST(1), a dispose that arrives before the reader has taken the
instance's last sample is reported as that valid sample's instance state, with
no separate invalid sample queued. `AnnounceSubscriber` and `ContentInventory`
read disposal only from invalid samples, so they report such a departure as
alive.

Measured against a participant built on the SDK, whose clean stop is a terminal
announce and then its dispose:

```
valid 32 -> demo's AnnounceSubscriber says alive
```

The SDK's reader hit the same thing while being built and reads instance state
on both kinds of sample now.

## 4. The Depart lane is latched here and volatile in the spec (measured)

`DEPART_PROFILE = "DISCOVERY_ANNOUNCE"` (`discovery_bus.py:40`) makes both the
writer and the reader TRANSIENT_LOCAL. §3.3's "QoS defaults for discovery
topics" make `depart` RELIABLE + VOLATILE + KEEP_LAST(1). A reader requesting
TRANSIENT_LOCAL does not match a writer offering VOLATILE, so this repository's
`Depart` reader never hears a conformant departure. Measured against the SDK's
departure: `depart reader matched writers before close: 0`. Combined with
finding 3, the demo saw neither door of a clean departure.

## 5. A removal with no terminal sample (read)

`pond_watch.run` disposes on exit, and `ModelPublisher.close` disposes every
entity, without the terminal sample §2.14 requires first. `ModelPublisher.retire`
does it right (RETIRED with a reason, a settle, then the dispose). The census's
fit check rebuilt `pond_watch` on the SDK in a scratch copy of this repository,
with `on_exit="withdraw"`, and this repository's own unchanged reader received
the terminal sample. Nothing was committed here.

## 6. A type with no profile assignment silently becomes EVENT_RT (read)

`topic_types.profile_for` returns `DEFAULT_PROFILE = "EVENT_RT"` for any type
it has no assignment for, and `PROFILE_FOR_TYPE` assigns `POSE_RT` to
`navsat_status` and `MAP_META` to `entity_binding`. §3.3.2 names no profile for
either. Each is a QoS claim nobody made, and since Deadline is request/offered,
a guessed profile can silently fail to match. The SDK refuses rather than
defaults, and lists the profiles that fit.

## 7. The domain defaults to 0 (read)

`dds_transport.require_dds_env` reads `SPATIALDDS_DDS_DOMAIN` with a default of
`"0"`. A deployment that forgot to set it joins domain 0, along with every
other one that forgot. The SDK refuses when neither `domain=` nor the variable
is given.

## 8. The JSON mapping's union dialect is not N.1 (measured)

`json_mapping` writes and reads unions as `{"discriminator": …, "<case>": …}`.
N.1 (Appendix N, the table of mappings) tags them on `"type"`. Measured: its
`from_json` accepts spatialdds' own `Announce` and `ContentAnnounce` builder
output byte-for-byte after `lift`, and refuses the `GeoAnchor` builder's with
`'type' is not one of its cases`. This repository's web surface is unaffected,
because the bridge projects through spatialdds' `webjson`. The MQTT bridge was
not checked. openvps vendors this mapper.
