import concurrent.futures
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine

class Boundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.src=self.root/'in.lua';self.out=self.root/'out.lua'
        self.src.write_text('return 1')

    def test_timeout_leaves_no_output(self):
        with patch.object(engine.subprocess,'run',side_effect=subprocess.TimeoutExpired('worker',1)):
            with self.assertRaisesRegex(ValueError,'BUILD_TIMEOUT'):engine.protect(self.src,self.out)
        self.assertFalse(self.out.exists())

    def test_worker_failure_leaves_no_output(self):
        with patch.object(engine.subprocess,'run',return_value=subprocess.CompletedProcess([],2)):
            with self.assertRaisesRegex(ValueError,'BUILD_FAILED'):engine.protect(self.src,self.out)
        self.assertFalse(self.out.exists())

    def test_empty_and_oversize(self):
        for data in (b'',b'x'*33):
            self.src.write_bytes(data)
            with patch.object(engine,'LIMIT',32):
                with self.assertRaisesRegex(ValueError,'SIZE_LIMIT'):engine.protect(self.src,self.out)

    def test_invalid_options(self):
        for kw in ({'seed':0},{'seed':True},{'seed':2**32},{'timeout':True},{'timeout':0},{'timeout':721},{'profile':'missing'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):engine.protect(self.src,self.out,**kw)

    def test_invalid_utf8(self):
        self.src.write_bytes(b'local x="\xff"')
        with self.assertRaises(ValueError):engine.protect(self.src,self.out)
        self.assertFalse(self.out.exists())

    def test_environment_introspection_rejected(self):
        self.src.write_text('local f=function() return x end;setfenv(f,{x=42});return f()')
        with self.assertRaises(ValueError):engine.protect(self.src,self.out)
        self.assertFalse(self.out.exists())

    def test_introspection_words_in_strings_are_allowed(self):
        self.src.write_text('-- getfenv\nreturn "setfenv debug"')
        engine.protect(self.src,self.out)
        self.assertTrue(self.out.is_file())

    def test_existing_output_unchanged(self):
        self.out.write_bytes(b'keep me')
        with self.assertRaises(ValueError):engine.protect(self.src,self.out)
        self.assertEqual(self.out.read_bytes(),b'keep me')

    def test_dangling_symlink(self):
        dest=self.root/'elsewhere.lua'
        try:self.out.symlink_to(dest)
        except OSError:self.skipTest('OS cannot create symlink')
        with self.assertRaisesRegex(ValueError,'OUTPUT_SYMLINK'):engine.protect(self.src,self.out)
        self.assertFalse(dest.exists())

    def test_atomic_collision(self):
        def write(data):
            try:engine.publish(self.out,data);return True
            except FileExistsError:return False
        data=[bytes([i])*20000 for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(write,data))
        self.assertEqual(sum(results),1)
        self.assertIn(self.out.read_bytes(),data)
        self.assertEqual(list(self.root.glob('.ryonex-*')),[])

    def test_build_does_not_execute_source(self):
        sentinel=self.root/'must-not-exist'
        path=sentinel.as_posix().replace('"','\\"')
        self.src.write_text('local f=io.open("'+path+'","w");f:write("bad");f:close();while true do end')
        engine.protect(self.src,self.out)
        self.assertFalse(sentinel.exists())

    def test_vendor_tamper_fails_closed(self):
        with patch.object(engine,'verify_vendor',side_effect=ValueError('VENDOR_INTEGRITY_ERROR')):
            with self.assertRaisesRegex(ValueError,'VENDOR_INTEGRITY'):engine.protect(self.src,self.out)
        self.assertFalse(self.out.exists())

if __name__=='__main__':unittest.main(verbosity=2)
