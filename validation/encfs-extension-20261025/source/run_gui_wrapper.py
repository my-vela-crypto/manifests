#!/usr/bin/env python3
"""Record the actual Python GUI runner return code from its direct parent."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path('/home/tourist/Desktop/openvela')
folder=root/'encfs-results/extension-20261025'
receipt=folder/'gui-wrapper-rc.json'
log=folder/'gui-wrapper.stdout.log'
results=root/'encfs-results/gui-extension-20261025-02'
if receipt.exists() or results.exists():
    raise SystemExit('Refusing to reuse wrapper receipt or result directory')
now=lambda:datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
command=[sys.executable,str(root/'.repo/manifests/tools/test_encfs_gui.py'),
         '--source-root',str(root),'--results-dir',str(results),
         '--build-dir',str(root/'cmake_out/vela_goldfish-arm64-v8a-ap-encfs-gui'),
         '--stress-seconds','60']
record={'schema':1,'started_at':now(),'command':command,'results_dir':str(results),
        'stdout_log':str(log),'return_code_source':'subprocess.Popen.wait in direct parent',
        'outer_tool_return_code':None,'program_return_code':None,'status':'running'}
try:
    with log.open('wb') as stream:
        child=subprocess.Popen(command,cwd=root,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        record['child_pid']=child.pid
        record['program_return_code']=child.wait()
        stream.flush(); os.fsync(stream.fileno())
    report=json.loads((results/'report.json').read_text())
    record['report_status']=report['status']
    record['status']='passed' if record['program_return_code']==0 and report['status']=='passed' else 'failed'
except BaseException as error:
    record.update(status='failed',error=f'{type(error).__name__}: {error}')
record['finished_at']=now()
with receipt.open('w') as stream:
    json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
print(json.dumps(record,ensure_ascii=False),flush=True)
raise SystemExit(0 if record['status']=='passed' else 1)
