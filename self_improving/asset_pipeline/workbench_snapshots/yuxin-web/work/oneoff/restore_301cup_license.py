#!/usr/bin/env python3
'''One-off: restore 301_cup's audited license after the 08-10 re-import clobber.

The 2026-08-09 audit declared every ycb_axis_aligned model CC-BY-4.0 (YCB
dataset terms) with evidence URLs. 301_cup m0 was re-imported on 08-10 to fix
its collision mesh; upsert replaces the model entry wholesale, so the audited
license silently reverted to status=unknown -- the only unknown left in the
pool. Source coordinates (library/group/file) are unchanged, so the 08-09
determination still applies verbatim; this restores it from its sibling
302_can (same audit batch, same YCB source) with a note recording the event.
The structural fix (carry-forward in import_materialize) prevents recurrence.
'''
import json, sys
sys.path.insert(0, '1_asset_reuse')
from lib import ledger as L

cup_p = 'data/asset_library/301_cup/ledger.json'
can = json.load(open('data/asset_library/302_can/ledger.json'))
cup = json.load(open(cup_p))
tmpl = dict(can['models'][0]['source']['license'])
assert tmpl['status'] == 'declared' and tmpl['spdx'] == 'CC-BY-4.0', tmpl
tmpl['terms_note'] += ' [restored 2026-08-11 after 08-10 re-import clobbered the 08-09 audit; source coordinates unchanged]'
old = cup['models'][0]['source']['license']
print('before:', old)
cup['models'][0]['source']['license'] = tmpl
violations = L.validate_ledger(cup, check_files=False)
assert not violations, violations
L.write_ledger(cup_p, cup)
print('after :', json.load(open(cup_p))['models'][0]['source']['license']['status'], json.load(open(cup_p))['models'][0]['source']['license']['spdx'])
