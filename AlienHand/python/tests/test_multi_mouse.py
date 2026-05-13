from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alienhand_ai.multi_mouse import (
    CHANNEL_ALIENHAND,
    CHANNEL_BLOCKED,
    CHANNEL_LEGACY,
    CHANNEL_WINDOWS,
    MODE_BLOCKED,
    MODE_INDEPENDENT,
    MODE_INTEGRATED,
    PointerInputEvent,
    TargetIdentity,
    TargetPointerPolicy,
    TargetPolicyRule,
    TargetPolicyTable,
    VirtualPointerRouter,
    effective_target_mode,
    read_policy_table,
    run_multi_mouse_virtualization_proof,
    write_policy_table,
)


class MultiMouseRoutingTests(unittest.TestCase):
    def test_uncaptured_mouse_stays_on_windows_pointer(self):
        target = TargetIdentity("legacy-editor", "notepad.exe")
        router = VirtualPointerRouter(captured_device_ids={"mouse-b"})

        routed = router.route(PointerInputEvent("mouse-a", "move", 44, 55, target, 1))

        self.assertEqual(routed.channel, CHANNEL_WINDOWS)
        self.assertEqual(routed.pointer_id, "windows")
        self.assertTrue(routed.delivered)
        self.assertEqual(router.state_snapshot()["windows_pointer"]["x"], 44)

    def test_captured_mouse_modes_block_integrate_and_independently_dispatch(self):
        legacy_target = TargetIdentity("legacy-editor", "notepad.exe")
        alienhand_target = TargetIdentity(
            "alienhand-workbench",
            "AlienHand.exe",
            surface_id="chat",
            supports_independent_pointers=True,
        )
        blocked_target = TargetIdentity("secure-login", "credential-ui.exe")
        policies = TargetPolicyTable()
        policies.set_policy(legacy_target.target_id, TargetPointerPolicy(MODE_INTEGRATED))
        policies.set_policy(alienhand_target.target_id, TargetPointerPolicy(MODE_INDEPENDENT))
        policies.set_policy(blocked_target.target_id, TargetPointerPolicy(MODE_BLOCKED))
        router = VirtualPointerRouter(captured_device_ids={"mouse-b"}, policy_table=policies)

        blocked = router.route(PointerInputEvent("mouse-b", "move", 1, 2, blocked_target, 1))
        independent = router.route(PointerInputEvent("mouse-b", "left_down", 10, 20, alienhand_target, 2))
        integrated = router.route(PointerInputEvent("mouse-b", "left_up", 30, 40, legacy_target, 3))

        self.assertEqual(blocked.channel, CHANNEL_BLOCKED)
        self.assertFalse(blocked.delivered)
        self.assertEqual(independent.channel, CHANNEL_ALIENHAND)
        self.assertEqual(independent.pointer_id, "alienhand:mouse-b")
        self.assertEqual(integrated.channel, CHANNEL_LEGACY)
        self.assertEqual(integrated.details["source"], "anonymous_legacy_mouse_stream")
        self.assertEqual(router.state_snapshot()["virtual_pointers"]["mouse-b"]["x"], 30)

    def test_independent_mode_falls_back_for_legacy_targets(self):
        target = TargetIdentity("paint", "paint.exe", supports_independent_pointers=False)
        policy = TargetPointerPolicy(MODE_INDEPENDENT, legacy_fallback=MODE_INTEGRATED)

        mode, reason = effective_target_mode(policy, target)

        self.assertEqual(mode, MODE_INTEGRATED)
        self.assertEqual(reason, "target_not_independent_aware")

    def test_policy_rules_select_by_process_and_surface_specificity(self):
        table = TargetPolicyTable()
        table.add_rule(
            TargetPolicyRule(
                rule_id="alienhand-app",
                process_name="AlienHand.exe",
                policy=TargetPointerPolicy(MODE_INTEGRATED),
            )
        )
        table.add_rule(
            TargetPolicyRule(
                rule_id="alienhand-chat-surface",
                process_name="AlienHand.exe",
                surface_id="chat",
                policy=TargetPointerPolicy(MODE_INDEPENDENT),
            )
        )

        workbench = TargetIdentity(
            "alienhand-workbench",
            "alienhand.exe",
            surface_id="chat",
            supports_independent_pointers=True,
        )
        settings = TargetIdentity("alienhand-settings", "alienhand.exe", surface_id="settings")

        self.assertEqual(table.resolve(workbench).mode, MODE_INDEPENDENT)
        self.assertEqual(table.resolve(settings).mode, MODE_INTEGRATED)

    def test_policy_table_round_trips_to_json(self):
        table = TargetPolicyTable(default_captured_policy=TargetPointerPolicy(MODE_BLOCKED))
        table.set_policy("secure-login", TargetPointerPolicy(MODE_BLOCKED))
        table.add_rule(
            TargetPolicyRule(
                rule_id="legacy-editors",
                process_name="notepad.exe",
                window_title_contains="notes",
                policy=TargetPointerPolicy(MODE_INTEGRATED),
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            path = write_policy_table(Path(temp) / "pointer-policy.json", table)
            loaded = read_policy_table(path)

        self.assertEqual(loaded.resolve(TargetIdentity("secure-login", "credential-ui.exe")).mode, MODE_BLOCKED)
        self.assertEqual(loaded.resolve(TargetIdentity("note-window", "NOTEPAD.EXE", "Project notes")).mode, MODE_INTEGRATED)

    def test_virtualization_proof_runs_without_hardware(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_multi_mouse_virtualization_proof(Path(temp))

        self.assertTrue(result["hardware_free"])
        self.assertTrue(result["routing_modes_ok"])
        self.assertEqual(result["summary"]["channels"][CHANNEL_WINDOWS], 3)
        self.assertEqual(result["summary"]["channels"][CHANNEL_BLOCKED], 1)
        self.assertEqual(result["summary"]["channels"][CHANNEL_ALIENHAND], 3)
        self.assertEqual(result["summary"]["channels"][CHANNEL_LEGACY], 4)


if __name__ == "__main__":
    unittest.main()
