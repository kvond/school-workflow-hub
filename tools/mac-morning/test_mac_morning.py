import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SOURCE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('build_app',SOURCE/'build_app.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class BuilderTest(unittest.TestCase):
    def test_profile_selection_uses_only_exact_supplied_school_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'Profile 77').mkdir()
            state=root/'Local State'
            state.write_text(json.dumps({'profile':{'info_cache':{'Profile 77':{'user_name':'teacher@school.example.test'}}}}))
            self.assertEqual(builder.checked_profile(state,'Profile 77','teacher@school.example.test'),root.resolve())
            for profile,account in [('Default','teacher@school.example.test'),('Profile 4','teacher@school.example.test'),('Profile 77','someone@other.example.test'),('Profile 77; touch bad','teacher@school.example.test')]:
                with self.assertRaises(ValueError):builder.checked_profile(state,profile,account)

    def test_no_overwrite_or_symlink_and_no_private_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);existing=root/'School Morning.app';existing.mkdir();(existing/'keep').write_text('preserve')
            with self.assertRaises(ValueError):builder.validate_output(existing)
            self.assertEqual((existing/'keep').read_text(),'preserve')
            link=root/'Link.app';link.symlink_to(root/'missing')
            with self.assertRaises(ValueError):builder.validate_output(link)
            with self.assertRaises(ValueError):builder.validate_output(root/'not-an-app')
        source=(SOURCE/'build_app.py').read_text()+(SOURCE/'launch.sh').read_text()
        # Match private-default shapes without recording any real local values.
        self.assertNotRegex(source,r'\bProfile [0-9]+\b')
        self.assertNotRegex(source,r'(?<![a-p])[a-p]{32}(?![a-p])')
        self.assertNotRegex(source,r'https?://')
        self.assertNotRegex(source,r'/Users/[^/\s\"\']+')

    def test_policy_escapes_local_paths_and_protects_exact_singleton_files(self):
        policy=builder.sandbox_policy('/tmp/fictional "school" data')
        self.assertIn('(deny signal)',policy);self.assertIn('(target same-sandbox)',policy)
        for name in ('SingletonLock','SingletonSocket','SingletonCookie'):
            self.assertIn('/'+name+'")',policy)
        self.assertIn('\\"school\\"',policy)
        with self.assertRaises(ValueError):builder.sandbox_policy('/tmp/bad\npath')

    def test_launcher_has_no_auth_automation_keepawake_or_grade_operation(self):
        source=(SOURCE/'launch.sh').read_text()+(SOURCE/'app-main.m').read_text()
        for forbidden in ('osascript','NSAppleScript','caffeinate','remote-debugging','killall','pkill','cookie','morning_run_now','morning_setting','morning_clear'):
            self.assertNotIn(forbidden,source)
        self.assertIn('--profile-directory=$profile',source)
        self.assertIn('morning-setup.html',source)
        self.assertIn('sandbox-exec',source)
        self.assertIn('com.openai.codex',source)

    def test_configuration_failure_records_only_private_safe_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);resources=root/'Resources';resources.mkdir()
            script=resources/'launch.sh';script.write_bytes((SOURCE/'launch.sh').read_bytes())
            (resources/'launch.conf').write_text('invalid\nProfile 999\n'+'a'*32+'\n/Applications/Google Chrome.app\n/synthetic/data\n')
            logs=root/'logs';environment=dict(os.environ,SCHOOL_MORNING_LOG_DIR=str(logs))
            result=subprocess.run(['/bin/sh',str(script)],env=environment,capture_output=True)
            self.assertEqual(result.returncode,64)
            status=logs/'last-launch.private.json'
            self.assertEqual(status.stat().st_mode&0o777,0o600)
            self.assertEqual(logs.stat().st_mode&0o777,0o700)
            self.assertEqual(json.loads(status.read_text()),{'schema':'school-morning-launch-status/v1','stage':'configuration','exit_code':64,'command_exit_code':None})
            self.assertFalse(list(logs.glob('*.tmp')))

@unittest.skipUnless(sys.platform=='darwin','Native Mac launcher checks')
class NativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.root=Path(cls.temporary.name)
        cls.binary=cls.root/'bounded-handoff'
        subprocess.run(['/usr/bin/xcrun','clang','-Wall','-Wextra',str(SOURCE/'bounded-handoff.c'),'-o',str(cls.binary)],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def test_child_exit_and_timeout_do_not_signal_unrelated_process(self):
        self.assertEqual(subprocess.run([str(self.binary),'/bin/sh','-c','exit 0']).returncode,0)
        self.assertEqual(subprocess.run([str(self.binary),'/bin/sh','-c','exit 7']).returncode,7)
        other=subprocess.Popen(['/bin/sleep','45'])
        try:
            start=time.monotonic();result=subprocess.run([str(self.binary),'/bin/sleep','45'])
            self.assertEqual(result.returncode,66);self.assertGreaterEqual(time.monotonic()-start,30);self.assertLess(time.monotonic()-start,35)
            self.assertIsNone(other.poll())
        finally:other.terminate();other.wait()

    def test_stderr_is_private_worker_opt_in_and_preserves_actual_child_result(self):
        command=['/bin/sh','-c','printf synthetic-diagnostic >&2; exit 7']
        quiet=subprocess.run([str(self.binary),*command],capture_output=True)
        captured=subprocess.run([str(self.binary),'--capture-stderr',*command],capture_output=True)
        self.assertEqual(quiet.returncode,7);self.assertEqual(quiet.stderr,b'')
        self.assertEqual(captured.returncode,7);self.assertEqual(captured.stderr,b'synthetic-diagnostic')

    def test_sandbox_preserves_outside_process_and_singleton_symlinks(self):
        paths=self.root/'synthetic-data';paths.mkdir()
        for name in ('SingletonLock','SingletonSocket','SingletonCookie'):
            (paths/name).symlink_to('/tmp/synthetic-target')
        policy=self.root/'handoff.sb';policy.write_text(builder.sandbox_policy(paths))
        other=subprocess.Popen(['/bin/sleep','15'])
        try:
            command='kill -KILL "$1"; rm -f "$2/SingletonLock" "$2/SingletonSocket" "$2/SingletonCookie"'
            result=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),'/bin/sh','-c',command,'synthetic',str(other.pid),str(paths)])
            self.assertNotEqual(result.returncode,0);self.assertIsNone(other.poll())
            for name in ('SingletonLock','SingletonSocket','SingletonCookie'):
                self.assertTrue((paths/name).is_symlink())
            read=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),'/usr/bin/readlink',str(paths/'SingletonLock')])
            self.assertEqual(read.returncode,0)
        finally:other.terminate();other.wait()

    def test_sandbox_allows_unix_socket_roundtrip_with_literal_symlink_and_readlink_target(self):
        # Short paths respect macOS's sockaddr_un length limit. The builder
        # still receives a /tmp alias so canonical policy matching is tested.
        with tempfile.TemporaryDirectory(prefix='hub-socket-',dir='/tmp') as temp:
            root=Path(temp).resolve();data=root/'data';data.mkdir();sockets=root/'sockets';sockets.mkdir()
            policy=root/'handoff.sb';policy.write_text(builder.sandbox_policy(data))
            for mode in ('literal','symlink','readlink_target'):
                with self.subTest(mode=mode):
                    link=data/'SingletonSocket';target=link if mode=='literal' else sockets/'SingletonSocket'
                    observed=[]
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                        server.bind(str(target));server.listen(1);server.settimeout(3)
                        if mode!='literal':link.symlink_to(target)
                        def receive():
                            try:
                                connection,_=server.accept()
                                with connection:
                                    connection.settimeout(3)
                                    observed.append(connection.recv(32));connection.sendall(b'ACK')
                            except Exception as error:observed.append(type(error).__name__)
                        worker=threading.Thread(target=receive);worker.start()
                        destination=target if mode=='readlink_target' else link
                        command='import socket,sys; c=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); c.connect(sys.argv[1]); c.sendall(b"START"); assert c.recv(32)==b"ACK"; c.close()'
                        result=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),sys.executable,'-c',command,str(destination)],capture_output=True,timeout=7)
                        worker.join(4)
                        self.assertEqual(result.returncode,0)
                        self.assertEqual(observed,[b'START'])
                        denied=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),'/bin/rm','-f',str(link)],capture_output=True,timeout=7)
                        self.assertNotEqual(denied.returncode,0)
                        self.assertTrue(link.exists())
                    link.unlink()
                    if target!=link:target.unlink()

    def test_sandbox_denies_data_writes_to_guarded_regular_singleton_files(self):
        with tempfile.TemporaryDirectory(prefix='hub-write-',dir='/tmp') as temp:
            root=Path(temp);policy=root/'handoff.sb';policy.write_text(builder.sandbox_policy(root))
            for name in ('SingletonLock','SingletonCookie'):
                protected=root/name;protected.write_text('preserve')
                result=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),'/bin/sh','-c','printf changed > "$1"','synthetic',str(protected)],capture_output=True,timeout=7)
                self.assertNotEqual(result.returncode,0);self.assertEqual(protected.read_text(),'preserve')

    def test_sandbox_guards_remain_after_chrome_singleton_timeout(self):
        with tempfile.TemporaryDirectory(prefix='hub-late-guard-',dir='/tmp') as temp:
            root=Path(temp);policy=root/'handoff.sb';policy.write_text(builder.sandbox_policy(root))
            for name in ('SingletonLock','SingletonSocket','SingletonCookie'):(root/name).symlink_to('/tmp/synthetic-preserved')
            outside=subprocess.Popen(['/bin/sleep','40'])
            try:
                command='/bin/sleep 21; kill -KILL "$1"; /bin/rm -f "$2/SingletonLock" "$2/SingletonSocket" "$2/SingletonCookie"'
                result=subprocess.run([str(self.binary),'/usr/bin/sandbox-exec','-f',str(policy),'/bin/sh','-c',command,'synthetic',str(outside.pid),str(root)],capture_output=True,timeout=35)
                self.assertNotEqual(result.returncode,0);self.assertIsNone(outside.poll())
                for name in ('SingletonLock','SingletonSocket','SingletonCookie'):self.assertTrue((root/name).is_symlink())
            finally:outside.terminate();outside.wait()

if __name__=='__main__':unittest.main()
