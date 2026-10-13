import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
        other=subprocess.Popen(['/bin/sleep','15'])
        try:
            start=time.monotonic();result=subprocess.run([str(self.binary),'/bin/sleep','15'])
            self.assertEqual(result.returncode,66);self.assertLess(time.monotonic()-start,7)
            self.assertIsNone(other.poll())
        finally:other.terminate();other.wait()

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

if __name__=='__main__':unittest.main()
