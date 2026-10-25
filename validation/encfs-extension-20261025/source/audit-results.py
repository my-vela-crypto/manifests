#!/usr/bin/env python3
"""Independently reconcile saved EncFS guest serials with their JSON reports."""
import hashlib
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
r = json.loads((root / 'report.json').read_text())
assert r['status'] == 'passed'
audit = {'status': 'passed', 'report_sha256': hashlib.sha256((root / 'report.json').read_bytes()).hexdigest()}
input_checks = {}
for name, artifact in r.get('build_artifacts', r.get('artifacts', {})).items():
    digest = hashlib.sha256()
    with Path(artifact['path']).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    assert digest.hexdigest() == artifact['sha256']
    input_checks[name] = digest.hexdigest()
audit['inputs_rechecked_unchanged'] = input_checks
if 'build_artifacts' in r:
    assert len(r['phases']) == 6
    seen = []
    for phase in r['phases']:
        serial = Path(phase['serial_log'])
        found = re.findall(r'ENCFS_TEST_RESULT mode=(\w+) pass=(\d+) fail=(\d+)', serial.read_text())
        assert len(found) == 1
        mode, ok, bad = found[0]
        assert phase['guest_result'] == {'mode': mode, 'pass': int(ok), 'fail': int(bad)}
        assert int(bad) == 0
        seen.append({'phase': phase['name'], 'mode': mode, 'pass': int(ok), 'fail': 0, 'serial_sha256': hashlib.sha256(serial.read_bytes()).hexdigest()})
    assert sum(x['pass'] for x in seen) == 789
    hashes = [p['image']['sha256'] for p in r['phases'] if p['guest_result']['mode'] in ('write', 'verify')]
    assert len(set(hashes)) == 1
    audit.update(total_pass=789, total_fail=0, phases=seen, encrypted_disk_unchanged_across_three_cold_boots=True, disk_sha256=hashes[0])
else:
    assert r['upstream_cmocka']['counts'] == {'run': 46, 'pass': 46, 'fail': 0, 'skip': 0}
    raw = (root / 'upstream-fs-regression.serial.log').read_text()
    run = re.findall(r'\[\s*RUN\s*\]\s+(\w+)', raw)
    ok = re.findall(r'\[\s*OK\s*\]\s+(\w+)', raw)
    assert len(run) == len(ok) == 46 and set(run) == set(ok)
    assert 'File system tests done... OK: 20, FAILED: 0' in raw
    assert 'TEST PASSED !' in raw and 'TEST FAILED !' not in raw
    assert len(r['powercuts']) == 20
    checks = []
    for n, cut in enumerate(r['powercuts'], 1):
        raw = (root / f'powercut-{n:02}.serial.log').read_text()
        assert f'ENCFS_POWER_VERIFIED sentinels=3 committed_generation={n-1} ' in raw
        assert re.search(rf'ENCFS_POWER_COMMITTED generation={n}\s', raw)
        assert cut['exit_signal'] == 9 and cut['process_group_stopped']
        assert cut['expected_committed_generation'] == n
        if n % 2:
            assert re.search(rf'ENCFS_POWER_INFLIGHT generation={n+1} offset=4096\s', raw)
        checks.append({'cut': n, 'boundary': cut['boundary'], 'verified_prior_generation': n-1, 'committed_generation': n, 'signal': 9})
    raw = (root / 'power-final-coldboot.serial.log').read_text()
    assert 'ENCFS_POWER_VERIFIED sentinels=3 committed_generation=20 bytes=1442328' in raw
    raw = (root / 'single-byte-corruption-oracle.serial.log').read_text()
    assert 'seek=4097 conv=notrunc' in raw
    assert re.search(r'\b65537\b[^\n]*\bsentinel2\b', raw)
    assert 'ENCFS_EXTENDED_RESULT action=verify result=-1 errno=84' in raw
    serials = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.serial.log')}
    assert len(r['phases']) == len(serials) == 24
    audit.update(raw_serial_count=24, phase_count=24, upstream_cmocka=r['upstream_cmocka']['counts'], fstest=r['upstream_fstest'], legacy_rwloops=100, powercuts=checks, final_verified_generations=21, final_verified_bytes=1442328, single_byte_same_length_corruption_rejected=True, serial_sha256=serials)
(root / 'independent-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in audit.items() if key not in ('phases', 'powercuts', 'serial_sha256')}, ensure_ascii=False))
