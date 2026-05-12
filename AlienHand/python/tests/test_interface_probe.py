from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.interface_probe import probe_godot_interfaces, summarize_interface_map


class InterfaceProbeTests(unittest.TestCase):
    def test_probes_godot_docs_shortcuts_project_and_alienhand_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fake_godot_tree(root)

            interface_map = probe_godot_interfaces(root)
            entries = {entry.id: entry for entry in interface_map.entries}

            self.assertIn("godot.api.EditorInterface.play_main_scene", entries)
            self.assertIn("godot.api.EditorInterface.scene_changed", entries)
            self.assertIn("godot.ui.shortcut.editor/run_project", entries)
            self.assertIn("godot.ai.alienhand_agent.record_frame", entries)
            self.assertIn("godot.project.demo.project.godot.input.player.fire", entries)
            self.assertIn("godot.project.demo.project.godot.autoload.gamestate", entries)

            play_entry = entries["godot.api.EditorInterface.play_main_scene"]
            self.assertIn("control", play_entry.metadata["intent_tags"])
            self.assertIn("lifecycle", play_entry.metadata["intent_tags"])

            equivalence_pairs = {(item.left_id, item.right_id) for item in interface_map.equivalences}
            self.assertIn(
                ("godot.ui.shortcut.editor/run_project", "godot.api.EditorInterface.play_main_scene"),
                equivalence_pairs,
            )

            routes = {item.route for item in interface_map.guidance}
            self.assertIn("api_and_ui", routes)
            self.assertIn("ai_bridge", routes)
            self.assertIn("project_input", routes)

            summary = summarize_interface_map(interface_map)
            self.assertGreaterEqual(summary["entries"], 6)
            self.assertEqual(summary["by_surface"]["ai_bridge"], 3)

    def test_writes_json_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "godot"
            root.mkdir()
            self._write_fake_godot_tree(root)

            interface_map = probe_godot_interfaces(root)
            output = interface_map.write_json(Path(temp) / "interface-map.json")
            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(data["entries"])
        self.assertTrue(data["guidance"])

    @staticmethod
    def _write_fake_godot_tree(root: Path) -> None:
        docs = root / "doc" / "classes"
        docs.mkdir(parents=True)
        (docs / "EditorInterface.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8" ?>
<class name="EditorInterface">
  <methods>
    <method name="play_main_scene">
      <return type="void" />
      <description>Runs the main scene.</description>
    </method>
    <method name="save_scene">
      <return type="Error" />
      <param index="0" name="scene" type="Node" />
      <description>Saves a scene.</description>
    </method>
  </methods>
  <signals>
    <signal name="scene_changed">
      <param index="0" name="scene_root" type="Node" />
      <description>Emitted when the edited scene changes.</description>
    </signal>
  </signals>
</class>
""",
            encoding="utf-8",
        )
        editor = root / "editor"
        editor.mkdir()
        (editor / "editor_node.cpp").write_text(
            'ED_SHORTCUT("editor/run_project", TTRC("Run Project"), Key::F5);\n'
            'if (ED_IS_SHORTCUT("editor/save_scene", p_event)) {}\n',
            encoding="utf-8",
        )
        module = root / "modules" / "alienhand_agent"
        module.mkdir(parents=True)
        (module / "alienhand_agent.h").write_text(
            """class AlienHandAgent {
public:
    Error start_recording(const String &p_path);
    Error record_frame(const Dictionary &p_state);
};
""",
            encoding="utf-8",
        )
        project = root / "demo" / "project.godot"
        project.parent.mkdir()
        project.write_text(
            """[autoload]
GameState="*res://game_state.gd"

[input]
player_fire={
"deadzone": 0.5,
"events": []
}
""",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
