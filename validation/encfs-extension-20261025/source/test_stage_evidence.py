#!/usr/bin/env python3
"""Exercise staging safety in a disposable synthetic workspace only."""
from pathlib import Path
import importlib.util
import json
import tempfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('stage_evidence',HERE/'stage_evidence.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
checks=[]
def rejected(name, action):
    try:action()
    except module.EvidenceError:checks.append({'check':name,'status':'passed'}); return
    raise AssertionError('unexpectedly accepted: '+name)

with tempfile.TemporaryDirectory(prefix='encfs-staging-tests-') as value:
    root=Path(value)
    normal=root/'plain.json'; normal.write_text('{"fixture":true}\n')
    plan=module.Plan(root)
    rejected('path traversal',lambda:plan.add(normal,'../escape.json','reports'))
    rejected('wrong declared fingerprint',lambda:plan.add(normal,'ok.json','reports','0'*64))
    (root/'keys').mkdir(); secret=root/'keys'/'master-00000000.bin'; secret.write_bytes(b'disposable-test-fixture')
    rejected('temporary master directory',lambda:plan.add(secret,'master.log','reports'))
    image=root/'disk.img'; image.write_bytes(b'not-a-real-disk')
    rejected('binary disk suffix',lambda:plan.add(image,'disk.log','reports'))
    avd=root/'avd'/'unit.vvd'; avd.mkdir(parents=True); private=avd/'metadata.json'; private.write_text('{}')
    rejected('runtime avd subtree',lambda:plan.add(private,'metadata.json','reports'))
    link=root/'symlink.json'; link.symlink_to(normal)
    rejected('symbolic-link input',lambda:plan.add(link,'link.json','reports'))
    run=root/'encfs-results'/'active'; run.mkdir(parents=True)
    (run/'report.json').write_text('{"status":"running"}')
    rejected('incomplete run',lambda:module.Plan(root).run('active'))
    good=module.Plan(root); good.add(normal,'reports/plain.json','reports')
    dest=root/'new-evidence'; module.publish(good,dest)
    require=[dest/'reports/plain.json',dest/'index.json',dest/'SHA256SUMS']
    assert all(path.is_file() for path in require)
    assert json.loads((dest/'index.json').read_text())['scope'].startswith('Only listed')
    checks.append({'check':'fresh synthetic publish and checksum verification','status':'passed'})
    before={str(path.relative_to(dest)):path.read_bytes() for path in dest.rglob('*') if path.is_file()}
    rejected('preexisting destination',lambda:module.publish(good,dest))
    assert before=={str(path.relative_to(dest)):path.read_bytes() for path in dest.rglob('*') if path.is_file()}
    changed=module.Plan(root); changed.add(normal,'reports/plain.json','reports'); normal.write_text('{"changed":true}\n')
    refused=root/'refused'; rejected('input changed after preflight',lambda:module.publish(changed,refused))
    assert not refused.exists() and not list(root.glob('.refused.*'))
    checks.append({'check':'failed preflight leaves no archive or staging tree','status':'passed'})

out={'status':'passed','scope':'synthetic /tmp workspace only; no real validation archive or report modified','checks':checks}
(HERE/'stager-tests.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False))
