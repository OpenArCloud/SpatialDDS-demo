"""List the venue's Open World Model entities, from a served recording.

The migration's demo-able artifact: six lines of the `spatialdds` client
library against the web binding, reading the entities this demo publishes on
`spatial.owm/0.1` with no DDS, no CycloneDDS, and nothing from this
repository imported.

    export SPATIALDDS_ENDPOINT=http://127.0.0.1:8831
    export SPATIALDDS_TOKEN=<the gateway's bearer token>
    python3 examples/list_owm_entities.py

Serve the recording first:

    python3 tools/run_gateway.py \\
        --mcap ../SpatialDDS-demo/recordings/owm-littlefield.mcap --port 8831

The entity surface is operator-scoped, which is why there is a token: an OWM
entity is site data, not public map data, and the binding's access classes say
so. The gateway prints the export lines in its banner.

`serves("entity")` rather than `search()[0]`: the endpoint may carry more than
one service, and indexing reads whichever one sorted first.
"""
import os, sys, spatialdds as sd
ep = sd.Endpoint(os.environ.get("SPATIALDDS_ENDPOINT") or "http://127.0.0.1:8831",
                 token=os.environ.get("SPATIALDDS_TOKEN"))
hit = next((h for h in ep.search() if h.serves("entity")), None) or sys.exit(
    "  no entity topic here; serve recordings/owm-littlefield.mcap")
rows = hit.topic("entity").latched()
for e in sorted(rows, key=lambda r: (r["layer"], r["entity_id"])):
    where = (f'({e["pose"]["t"][0]:7.2f},{e["pose"]["t"][1]:7.2f})'
             if e["has_pose"] else "     unplaced     ")
    label = next((p["value"] for p in e["properties"]
                  if p["key"] == "demo.label"), "")
    print(f'{e["entity_id"]:28} {e["layer"]:7} {e["basis"]:8} {where}  '
          f'{e["state"]:7} {label}')
f = rows[0]["frame_ref"]
print(f'\n{len(rows)} entities in {hit.service_id}, frame {f["fqn"]} '
      f'({f["coord_convention"]}, {f["meters_per_unit"]:g} m/unit, '
      f'{f["scale_status"]})')
