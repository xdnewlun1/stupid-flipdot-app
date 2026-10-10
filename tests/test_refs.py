"""refs.py: renames follow through and deletes clean up. Run: python3 -m unittest tests.test_refs"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apps  # noqa: E402,F401  (fills the app registry refs.py reads param types from)
import refs  # noqa: E402


def sample():
    return {
        "playlists": {
            "Day": {"items": [
                {"app": "clock", "params": {}},
                {"app": "layers", "params": {"composition": "Mix"}},
                {"app": "scene", "params": {"scene": "cat-1"}},
                {"app": "layers", "params": {"layers": [{"app": "clock", "params": {}},
                                                        {"app": "slideshow", "params": {"gallery": "g1"}}]}},
            ]},
            "Night": {"items": [{"app": "blank", "params": {"color": "black"}}]},
        },
        "compositions": {
            "Mix": {"layers": [{"app": "life", "params": {}}, {"app": "drawing", "params": {"drawing": "d1"}}]},
            "Other": {"layers": [{"app": "scene", "params": {"scene": "cat-1"}}]},
        },
        "schedule": {"enabled": True, "default": "Day",
                     "rules": [{"days": [0, 1, 2, 3, 4], "start": "22:00", "end": "07:00", "playlist": "Night"}]},
        "rules": [
            {"id": "r1", "name": "Bedtime", "enabled": True, "action": {"type": "playlist", "playlist": "Night"}},
            {"id": "r2", "name": "Party", "enabled": True, "action": {"type": "composition", "composition": "Mix"}},
            {"id": "r3", "name": "Bell", "enabled": True, "action": {"type": "notify", "scene": "cat-1"}},
            {"id": "r4", "name": "Mix app", "enabled": True,
             "action": {"type": "app", "app": "layers", "params": {"composition": "Mix"}}},
        ],
    }


class Refs(unittest.TestCase):
    def test_find(self):
        d = sample()
        self.assertEqual(refs.find(d, "playlist", "Night"),
                         ["Schedule rule 1 (Mon, Tue, Wed, Thu, Fri 22:00-07:00)", 'Automation "Bedtime"'])
        self.assertEqual(refs.find(d, "composition", "Mix"),
                         ['Playlist "Day", item 2', 'Automation "Party"', 'Automation "Mix app"'])
        self.assertIn('Playlist "Day", item 4', refs.find(d, "gallery", "g1"))  # nested layer
        self.assertEqual(refs.find(d, "drawing", "d1"), ['Layer set "Mix", layer 2'])
        self.assertEqual(refs.find(d, "playlist", "Nope"), [])

    def test_rename_playlist(self):
        d = sample()
        n, changed = refs.rename(d, "playlist", "Day", "Daytime")
        self.assertEqual(n, 1)
        self.assertEqual(changed, {"schedule"})
        self.assertEqual(d["schedule"]["default"], "Daytime")
        n, changed = refs.rename(d, "playlist", "Night", "Late")
        self.assertEqual((n, changed), (2, {"schedule", "rules"}))
        self.assertEqual(d["schedule"]["rules"][0]["playlist"], "Late")
        self.assertEqual(d["rules"][0]["action"]["playlist"], "Late")

    def test_rename_composition(self):
        d = sample()
        n, changed = refs.rename(d, "composition", "Mix", "Party mix")
        self.assertEqual(n, 3)
        self.assertEqual(changed, {"playlists", "rules"})
        self.assertEqual(d["playlists"]["Day"]["items"][1]["params"]["composition"], "Party mix")
        self.assertEqual(d["rules"][1]["action"]["composition"], "Party mix")
        self.assertEqual(d["rules"][3]["action"]["params"]["composition"], "Party mix")
        self.assertEqual(refs.find(d, "composition", "Mix"), [])

    def test_remove_scene(self):
        d = sample()
        done, changed = refs.remove(d, "scene", "cat-1")
        self.assertEqual(changed, {"playlists", "compositions", "rules"})
        self.assertEqual([i["app"] for i in d["playlists"]["Day"]["items"]], ["clock", "layers", "layers"])
        self.assertEqual(d["compositions"]["Other"]["layers"], [])
        self.assertFalse(d["rules"][2]["enabled"])
        self.assertTrue(d["rules"][0]["enabled"])
        self.assertEqual(len(done), 3)

    def test_remove_nested_gallery_keeps_item(self):
        d = sample()
        done, _ = refs.remove(d, "gallery", "g1")
        item = d["playlists"]["Day"]["items"][3]
        self.assertEqual([l["app"] for l in item["params"]["layers"]], ["clock"])
        self.assertEqual(done, ['Playlist "Day", item 4: layer removed'])

    def test_remove_playlist(self):
        d = sample()
        done, changed = refs.remove(d, "playlist", "Day")
        self.assertEqual(changed, {"schedule"})
        self.assertEqual(d["schedule"]["default"], "")
        done, changed = refs.remove(d, "playlist", "Night")
        self.assertEqual(d["schedule"]["rules"][0]["playlist"], "")
        self.assertFalse(d["rules"][0]["enabled"])


if __name__ == "__main__":
    unittest.main()
