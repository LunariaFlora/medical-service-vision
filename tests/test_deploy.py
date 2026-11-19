"""Deployment regressions: reconnect is not a bed transition, no silent new mission."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in ('firmware', 'host'):
    if str(_ROOT / _folder) not in sys.path:
        sys.path.insert(0, str(_ROOT / _folder))
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
from mission import Mission, LocalInterfaces
import result_transport


class Stop:
    def is_set(self): return False
    def wait(self, seconds): return False


class DeploymentChecks(unittest.TestCase):
    def test_config_validation_and_relative_output(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'config.json'
            value = {'port':'COM9', 'preset_qr':'13', 'preview':False, 'output_dir':'data'}
            file.write_text(json.dumps(value), encoding='utf-8')
            self.assertEqual(app.load_config(file)['output_dir'], str(Path(directory)/'data'))
            value['preset_qr']='99'
            file.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError): app.load_config(file)

    def test_exclusive_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            with app.single_instance(directory):
                with self.assertRaises(OSError):
                    with app.single_instance(directory): pass
            with app.single_instance(directory): pass

    def test_reconnect_keeps_candidates_and_clears_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            mission = Mission(LocalInterfaces(directory), '13')
            mission.observe([{'type':'CODE128','payload':'123'}], mission.capture_token(), now=0)
            mission.gap_ready=True
            mission.empty_since=0
            mission.pending={'999': {'hits':3,'first_s':0,'last_s':1}}
            args = SimpleNamespace(out=directory,frames=2,reconnect=True,mission_controller=mission)
            def first(*_):
                args.board_result={'boot':'a','seq':100}
                yield 0,0,None
                raise OSError('unplugged')
            def second(*_):
                args.board_result={'boot':'b','seq':0}
                yield 0,0,None
            with patch.object(result_transport, '_session', side_effect=[first(),second()]):
                got=list(result_transport.frames(args, Stop()))
            self.assertEqual([r[0] for r in got],[0,1])
            self.assertGreaterEqual(got[1][1],got[0][1])
            self.assertEqual(mission.results['1'],'123')
            self.assertEqual(mission.all_candidates['123']['hits'],1)
            self.assertFalse(mission.gap_ready)
            self.assertEqual(mission.pending,{})
            health=json.loads((Path(directory)/'connection.json').read_text())
            self.assertEqual(health['boot'],'b')

    def test_diagnostic_mode_still_raises(self):
        def fail(*_):
            raise ValueError('CRC mismatch')
            yield
        with patch.object(result_transport,'_session',side_effect=fail):
            with self.assertRaises(ValueError):
                list(result_transport.frames(SimpleNamespace(frames=1)))

    def test_stop_during_reconnect(self):
        class Closing(Stop):
            def wait(self,seconds): return True
        with tempfile.TemporaryDirectory() as directory:
            def fail(*_):
                raise OSError('missing device')
                yield
            with patch.object(result_transport,'_session',side_effect=fail):
                self.assertEqual(list(result_transport.frames(
                    SimpleNamespace(out=directory,frames=0,reconnect=True),Closing())),[])


if __name__ == '__main__':
    unittest.main()
