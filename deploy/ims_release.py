#!/usr/bin/env python3
"""Root-only, forward-only release rehearsal/deployment for the verified IMS VPS.

Private environment, dumps, row digests and command output stay in /root.
Never restores a production database or reverses migrations automatically.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

APP = Path('/home/boforg/BoforgIMS')
PYTHON = '/home/boforg/ims/bin/python'
USER = 'boforg'
WEB = 'gunicorn-ims.service'
SOCKET = 'gunicorn-ims.socket'
UNITS = ('tasker-worker.service', 'tasker-scheduler.service')
SYSTEMCTL = '/usr/bin/systemctl'
RUNUSER = '/usr/sbin/runuser'
FLAG_VALUES = {
    'TASKER_DIRECTION_ENABLED': 'true',
    'TASKER_COORDINATION_ENABLED': 'true',
    'TASKER_REPORTS_ENABLED': 'true',
    'TASKER_OPERATOR_DISPATCH_ENABLED': 'true',
    'TASKER_STRATEGY_ENABLED': 'true',
    'TASKER_IMPROVEMENT_ENABLED': 'true',
    'TASKER_PILOT_ENABLED': 'true',
    'TASKER_PILOT_ACCESS_ENFORCED': 'false',
    'TASKER_ROLLOUT_WRITES_ENABLED': 'false',
    'TASKER_RECURRENCE_ENABLED': 'false',
    'TASKER_AI_WORKFLOW_ENABLED': 'false',
    'TASKER_REPORT_EMAIL_LIVE_ENABLED': 'false',
    'OPENAI_API_KEY': '',
}
INIT = "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); import django; django.setup(); "
META = INIT + """
import json
from django.conf import settings as s
from django.db import connection
from django.core.files.storage import storages
assert not s.DEBUG and connection.vendor=='postgresql'
db=connection.settings_dict
assert str(db['HOST']) in ('','localhost','127.0.0.1','::1','/var/run/postgresql','/run/postgresql')
assert type(storages['default']).__module__=='django.core.files.storage.filesystem'
print(json.dumps({'database':str(db['NAME']),'database_user':str(db['USER']),
 'port':str(db['PORT'] or '5432'),'media':str(s.MEDIA_ROOT),
 'private_media':str(s.PRIVATE_MEDIA_ROOT),'static':str(s.STATIC_ROOT)}))
"""
SNAPSHOT = INIT + """
import hashlib,json,sys
from django.db import connection
q=connection.ops.quote_name
prior=json.loads(open(sys.argv[1]).read()) if len(sys.argv)>1 else None
result={}
prefixes=('accounting_','inventory_','sales_','credit_control_','shop_','customers_','payments_')
class Sink:
 def __init__(self):self.h=hashlib.sha256()
 def write(self,data):self.h.update(data.encode() if isinstance(data,str) else data)
with connection.cursor() as cursor:
 tables=sorted(prior) if prior else sorted(t for t in connection.introspection.table_names(cursor) if t.startswith(prefixes))
 for table in tables:
  columns=prior[table]['columns'] if prior else [c.name for c in connection.introspection.get_table_description(cursor,table)]
  assert 'id' in columns
  cursor.execute('SELECT COUNT(*) FROM '+q(table));count=cursor.fetchone()[0]
  sink=Sink(); sql='COPY (SELECT '+','.join(map(q,columns))+' FROM '+q(table)+' ORDER BY '+q('id')+') TO STDOUT'
  cursor.copy_expert(sql,sink)
  result[table]={'columns':columns,'count':count,'sha256':sink.h.hexdigest()}
print(json.dumps(result,sort_keys=True))
"""
PLAN = INIT + """
import json
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
e=MigrationExecutor(connection);e.loader.check_consistent_history(connection)
assert not e.loader.detect_conflicts()
plan=e.migration_plan(e.loader.graph.leaf_nodes())
assert not any(backwards for migration,backwards in plan)
print(json.dumps([[m.app_label,m.name] for m,b in plan]))
"""
EXPECTED = {
 'accounting': ['0012_source_status_timestamp'],
 'shop': ['0003_source_status_timestamp'],
 'inventory': ['0019_replenishmentplan_replenishmentevent_and_more'],
 'sales': ['0011_invoice_custom_fields_invoice_reference_and_more'],
 'credit_control': ['0003_sourceactionevent_collectiontask_promise_to_pay_and_more'],
 'operations': ['0001_operational_responsibility','0002_responsibility_priority','0003_responsibility_audit_snapshot'],
 'tasker': ['0021_governed_ai_workflows','0022_seed_governed_ai_workflows',
  '0023_strategy_board_scenarios','0024_businesssynccursor_businessrecordlink_and_more',
  '0025_replenishment_adapter','0026_business_outcome_evidence','0027_operational_audit_alignment',
  '0028_source_completion_evidence_alignment','0029_continuous_improvement',
  '0030_improvement_email_policy','0031_pilot_protocol_and_release_evidence','0032_pilot_measurement_guardrails'],
}


class Release:
    def __init__(self, backup):
        self.backup = Path(backup)
        self.state = json.loads((self.backup/'state.json').read_text())
        self.env = dict(v.decode().split('=',1) for v in (self.backup/'environment.bin').read_bytes().split(b'\0') if v)
        self.seq = 0

    def save(self):
        (self.backup/'state.json').write_text(json.dumps(self.state,indent=2))

    def run(self, args, *, env=None, cwd=None, user=None, name='command', capture=False, ok=(0,)):
        self.seq += 1
        if user:
            args = [RUNUSER,'-u',user,'--',*map(str,args)]
        result = subprocess.run(list(map(str,args)),cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        path = self.backup/f'{self.seq:03d}-{name}.log'
        path.write_bytes(result.stdout+b'\nSTDERR\n'+result.stderr)
        if result.returncode not in ok:
            raise RuntimeError(f'{name} failed (exit {result.returncode}); private log: {path}')
        return result.stdout.decode() if capture else None

    def git(self, *args, **kw):
        return self.run(['/usr/bin/git','-C',APP,*args],user=USER,**kw)

    def app(self, *args, stage=False, final=False, **kw):
        env = dict(self.env)
        if stage:
            env.update({'DJANGO_SETTINGS_MODULE':'ims_rehearsal_settings','OPENAI_API_KEY':'',
                        'TASKER_AI_WORKFLOW_ENABLED':'false','TASKER_REPORT_EMAIL_LIVE_ENABLED':'false'})
        if final:
            env.update(FLAG_VALUES)
        return self.run([PYTHON,*args],env=env,cwd=self.state['stage'] if stage else APP,user=USER,**kw)

    def json_app(self, code, *args, **kw):
        return json.loads(self.app('-c',code,*args,capture=True,**kw))

    def db(self, tool, *args, **kw):
        return self.run([f'/usr/bin/{tool}','--host=/var/run/postgresql',f"--port={self.state['meta']['port']}",*args],user='postgres',**kw)

    def dump(self, name):
        target=self.backup/name
        # Root opens the destination; postgres never traverses /root.
        with target.open('wb') as output, (self.backup/(name+'.stderr')).open('wb') as errors:
            subprocess.run([RUNUSER,'-u','postgres','--','/usr/bin/pg_dump',
                '--host=/var/run/postgresql',f"--port={self.state['meta']['port']}",
                '--format=custom','--no-owner','--no-acl',
                f"--dbname={self.state['meta']['database']}"],stdout=output,stderr=errors,check=True)
        assert target.stat().st_size>0
        self.run(['/usr/bin/pg_restore','--list',target],name='backup-index')
        return target

    def restore(self, dump):
        with dump.open('rb') as source, (self.backup/'restore-private.log').open('wb') as output:
            subprocess.run([RUNUSER,'-u','postgres','--','/usr/bin/pg_restore',
                '--host=/var/run/postgresql',f"--port={self.state['meta']['port']}",
                '--exit-on-error','--no-owner','--no-acl',
                f"--role={self.state['meta']['database_user']}",
                f"--dbname={self.state['stage_db']}"],stdin=source,stdout=output,stderr=output,check=True)

    def snapshot(self, name, *, prior=None, **kw):
        value=self.json_app(SNAPSHOT,*([prior] if prior else []),**kw)
        path=self.backup/name;path.write_text(json.dumps(value,sort_keys=True))
        return value

    def integrity(self, name, **kw):
        text=self.app('manage.py','check_ims_integrity',capture=True,name=name,**kw)
        value=json.loads(text)
        (self.backup/(name+'.json')).write_text(json.dumps(value,sort_keys=True))
        return {k:v for k,v in value.items() if k!='note'}

    def check_plan(self, **kw):
        plan=self.json_app(PLAN,**kw)
        expected={(app,n) for app,names in EXPECTED.items() for n in names}
        if set(map(tuple,plan)) != expected:
            raise RuntimeError('Pending migration plan differs from the reviewed 20-migration release.')
        (self.backup/'migration-plan.json').write_text(json.dumps(plan,indent=2))
        print('Reviewed forward migrations:',len(plan),flush=True)
        return plan

    def rehearse(self):
        stage=Path(self.state['stage'])
        if stage.exists():
            raise RuntimeError('Rehearsal path already exists; preserve it and use a fresh preparation.')
        self.git('fetch','origin','review/ims-reliability',name='fetch')
        self.git('merge-base','--is-ancestor',self.state['old'],self.state['release'],name='ancestry')
        if self.git('status','--porcelain',capture=True,name='clean-check').strip():
            raise RuntimeError('Production checkout has local changes.')
        self.git('worktree','add','--detach',stage,self.state['release'],name='staging-worktree')
        # A private config copy is loaded only by Django; no values are printed.
        if (APP/'.env').is_file():
            shutil.copy2(APP/'.env',stage/'.env');os.chown(stage/'.env',pwd.getpwnam(USER).pw_uid,pwd.getpwnam(USER).pw_gid)
            (stage/'.env').chmod(0o600)
        setting="from config.settings import *\nDATABASES['default']['NAME']="+repr(self.state['stage_db'])+"\nDATABASES['default']['CONN_MAX_AGE']=0\nCACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}\nEMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'\nOPENAI_API_KEY=''\nTASKER_AI_OPENAI_API_KEY=''\n"
        for key in FLAG_VALUES:
            if key.startswith('TASKER_'):setting+=key+'=False\n'
        (stage/'ims_rehearsal_settings.py').write_text(setting)
        (stage/'ims_rehearsal_settings.py').chmod(0o644)
        self.run([PYTHON,'-m','pip','check'],name='dependencies')
        before=self.snapshot('rehearsal-before.json')
        findings=self.integrity('rehearsal-integrity-before')
        dump=self.dump('rehearsal.dump')
        self.db('createdb',f"--owner={self.state['meta']['database_user']}",self.state['stage_db'],name='isolated-database')
        self.restore(dump)
        # The live snapshot can change before pg_dump. Compare restored records
        # to a snapshot of the same restored database, then preserve it through migration.
        restored=self.snapshot('restored-before.json',stage=True)
        restored_findings=self.integrity('restored-integrity-before',stage=True)
        self.check_plan(stage=True)
        self.app('manage.py','migrate','--noinput',stage=True,name='rehearsal-migrate')
        self.app('manage.py','check','--deploy',stage=True,name='rehearsal-check')
        self.app('manage.py','makemigrations','--check','--dry-run',stage=True,name='rehearsal-drift')
        self.app('manage.py','setup_tasker_roles',stage=True,name='rehearsal-roles')
        # Old-column snapshots must be accessible to the app user without exposing /root.
        prior=stage/'ims_rehearsal_snapshot.json';prior.write_text(json.dumps(restored));prior.chmod(0o600)
        os.chown(prior,pwd.getpwnam(USER).pw_uid,pwd.getpwnam(USER).pw_gid)
        after=self.snapshot('restored-after.json',prior=prior,stage=True)
        assert after==restored,'Canonical records changed in the migration rehearsal'
        assert self.integrity('restored-integrity-after',stage=True)==restored_findings,'IMS findings changed in rehearsal'
        self.app('manage.py','collectstatic','--noinput',stage=True,name='rehearsal-static')
        self.state['rehearsed']=True;self.state['rehearsed_plan']=json.loads((self.backup/'migration-plan.json').read_text());self.save()
        print('Production-backup migration rehearsal passed; canonical row digests and IMS findings preserved.',flush=True)
        print('Private backup/rehearsal:',self.backup,flush=True)

    def install_units(self):
        config=Path('/etc/boforg-ims');config.mkdir(mode=0o700,exist_ok=True)
        def env_file(path, values):
            lines=[]
            for key,value in sorted(values.items()):
                if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key):raise RuntimeError('Invalid environment name')
                lines.append(key+'="'+value.replace('\\','\\\\').replace('"','\\"')+'"')
            path.write_text('\n'.join(lines)+'\n');path.chmod(0o600)
        env_file(config/'release.env',FLAG_VALUES)
        env_file(config/'tasker-runtime.env',{**self.env,**FLAG_VALUES,'PYTHONUNBUFFERED':'1'})
        drop=Path('/etc/systemd/system/gunicorn-ims.service.d');drop.mkdir(exist_ok=True)
        (drop/'90-ims-release.conf').write_text('[Service]\nEnvironmentFile=/etc/boforg-ims/release.env\n')
        for role,extra in [('worker','--worker-id ims-tasker-worker --limit 25 --poll-seconds 2 --lease-seconds 600'),('scheduler','--scheduler-id ims-tasker-scheduler --limit 100 --poll-seconds 30')]:
            path=Path('/etc/systemd/system')/f'tasker-{role}.service'
            if path.exists():shutil.copy2(path,self.backup/path.name)
            path.write_text(f'''[Unit]
Description=Boforg IMS Tasker {role}
After=network.target postgresql.service

[Service]
Type=simple
User=boforg
Group=boforg
WorkingDirectory=/home/boforg/BoforgIMS
EnvironmentFile=/etc/boforg-ims/tasker-runtime.env
ExecStart={PYTHON} manage.py tasker_{role} --loop {extra}
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
''')
        self.run([SYSTEMCTL,'daemon-reload'],name='reload-units')

    def apply(self):
        assert self.state.get('rehearsed') and not self.state.get('applied')
        assert self.git('rev-parse','HEAD',capture=True).strip()==self.state['old']
        assert not self.git('status','--porcelain',capture=True).strip()
        # Refuse an unrelated current process or configuration change.
        pid=int(subprocess.check_output([SYSTEMCTL,'show',WEB,'-p','MainPID','--value']))
        assert Path(f'/proc/{pid}/environ').read_bytes()==(self.backup/'environment.bin').read_bytes()
        if (APP/'.env').exists():assert hashlib.sha256((APP/'.env').read_bytes()).hexdigest()==self.state['env_sha']
        for unit in UNITS:
            self.run([SYSTEMCTL,'stop',unit],name='pause-background',ok=(0,5))
        self.run([SYSTEMCTL,'stop',SOCKET],name='pause-socket')
        self.run([SYSTEMCTL,'stop',WEB],name='pause-web')
        self.state['maintenance']=True;self.save()
        try:
            assert subprocess.check_output([SYSTEMCTL,'is-active',WEB],text=True).strip()=='inactive'
        except subprocess.CalledProcessError as exc:
            assert exc.output.strip()=='inactive'
        final=self.dump('final-production.dump')
        self.snapshot('final-before.json')
        findings=self.integrity('final-integrity-before')
        for key in ('media','private_media'):
            path=Path(self.state['meta'][key])
            if path.is_dir():
                assert path.resolve().is_relative_to(APP) and path.resolve()!=APP
                self.run(['/usr/bin/tar','-C',APP,'-czf',self.backup/(key+'.tar.gz'),str(path.relative_to(APP))],name='backup-'+key)
        self.run(['/usr/bin/tar','-C',APP,'--exclude=.git','--exclude=media','--exclude=private_media','--exclude=staticfiles','-czf',self.backup/'old-application.tar.gz','.'],name='backup-code-config')
        self.git('merge','--ff-only',self.state['release'],name='release-checkout')
        self.check_plan()
        assert json.loads((self.backup/'migration-plan.json').read_text())==self.state['rehearsed_plan']
        self.app('manage.py','migrate','--noinput',name='production-migrate')
        self.app('manage.py','check','--deploy',name='production-check')
        self.app('manage.py','setup_tasker_roles',name='production-roles')
        prior=APP/'ims_release_snapshot.json';prior.write_text((self.backup/'final-before.json').read_text());prior.chmod(0o600)
        os.chown(prior,pwd.getpwnam(USER).pw_uid,pwd.getpwnam(USER).pw_gid)
        try:
            assert self.snapshot('final-after.json',prior=prior)==json.loads((self.backup/'final-before.json').read_text()),'Production canonical records changed'
        finally:
            prior.unlink()
        assert self.integrity('final-integrity-after')==findings,'Production IMS findings changed'
        self.app('manage.py','collectstatic','--noinput',name='production-static')
        static=Path(self.state['meta']['static'])
        assert static==APP/'staticfiles' and static.resolve()==static
        paths=[static,*static.rglob('*')]
        assert not any(p.is_symlink() for p in paths)
        for p in paths:p.chmod(0o755 if p.is_dir() else 0o644)
        self.install_units()
        self.run(['/usr/sbin/nginx','-t'],name='nginx-check')
        self.run([SYSTEMCTL,'start',SOCKET],name='start-socket')
        self.run([SYSTEMCTL,'start',WEB],name='start-web')
        self.state['maintenance']=False;self.state['applied']=True;self.save()
        print('Application migrated and web restarted. Private backup:',self.backup,flush=True)


def prepare(sha):
    assert re.fullmatch('[0-9a-f]{40}',sha)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path('/root')/f'ims-release-{stamp}'
    backup.mkdir(mode=0o700)
    pid=int(subprocess.check_output([SYSTEMCTL,'show',WEB,'-p','MainPID','--value']))
    assert pid>0
    (backup/'environment.bin').write_bytes(Path(f'/proc/{pid}/environ').read_bytes())
    assert subprocess.check_output([SYSTEMCTL,'show',WEB,'-p','User','--value'],text=True).strip()==USER
    assert subprocess.check_output([SYSTEMCTL,'show',WEB,'-p','WorkingDirectory','--value'],text=True).strip()==str(APP)
    old=subprocess.check_output([RUNUSER,'-u',USER,'--','/usr/bin/git','-C',APP,'rev-parse','HEAD'],text=True).strip()
    state={'release':sha,'old':old,'stage':f'/home/boforg/ims-rehearsal-{stamp}',
           'stage_db':'ims_rehearsal_'+stamp.lower(),'maintenance':False,'applied':False,
           'env_sha':hashlib.sha256((APP/'.env').read_bytes()).hexdigest() if (APP/'.env').exists() else None}
    (backup/'state.json').write_text(json.dumps(state))
    (Path('/root')/'ims-release-last.path').write_text(str(backup)+'\n')
    release=Release(backup);release.state['meta']=release.json_app(META,name='runtime-metadata');release.save()
    for unit in (WEB,SOCKET):release.run([SYSTEMCTL,'cat',unit],name=unit)
    print(backup,flush=True)
    release.rehearse()


if __name__=='__main__':
    os.umask(0o077)
    assert os.geteuid()==0,'Run as root'
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','apply'])
    parser.add_argument('value')
    args=parser.parse_args()
    try:
        if args.action=='prepare':prepare(args.value)
        else:Release(args.value).apply()
    except Exception as error:
        # Exceptions and command output can include private configuration.
        last=Path('/root/ims-release-last.path')
        location=Path(last.read_text().strip()) if last.exists() else Path('/root')
        import traceback
        (location/'failure-private.log').write_text(traceback.format_exc())
        print('STOP: release step failed. Private diagnostics:',location/'failure-private.log',file=sys.stderr)
        print('Check maintenance state before reopening. No database restore or migration reversal was attempted.',file=sys.stderr)
        sys.exit(1)
