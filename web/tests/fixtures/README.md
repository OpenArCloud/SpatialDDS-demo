# Captured bridge responses

Real JSON from the demo, not hand-written approximations — so a parser test
fails when the wire shape moves, which is the whole point of having them.

`localize_response.json` is `spatial::argeo::VpsResponse` as
`/v1/localize` returns it. Refresh from the repo root with:

```bash
python3 -c "
import sys, json; sys.path.insert(0,'.')
from spatialdds_demo.json_mapping import from_json, to_json
from spatialdds_idl.spatial.argeo import VpsResponse
from spatialdds_test import VPSServiceV15, SpatialDDSLogger
wire = to_json(from_json(VpsResponse,
    VPSServiceV15(SpatialDDSLogger()).create_localize_response_template()))
wire.update(query_id='fixture-query-0001', confidence=0.87, rmse_m=0.42)
json.dump(wire, open('web/tests/fixtures/localize_response.json','w'),
          indent=2, sort_keys=True)
"
```

`confidence` and `rmse_m` are pinned to fixed values after capture: the
responder randomises them, and a fixture that changes every time it is
refreshed tells you nothing.
