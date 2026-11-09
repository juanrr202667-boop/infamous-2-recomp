"""Synthetic checks for incremental observations of the menu transition."""

import unittest

from scripts.menu_witness import MenuWitness


INTRO_OPEN = (
    "[fs] open '/dev_bdvd/PS3_GAME/USRDIR/movies/cutscene/"
    "v_00_intro_montage_g.bik' -> fd 9"
)


class MenuWitnessTests(unittest.TestCase):
    def test_state2_write_records_evidence_without_verifying_gameplay(self) -> None:
        line = "[ww] 0x00ABFE50 <- 0x2 (w4) guest-fn=0x000C0F20 lr=0x000C0E00 tid=5"
        witness = MenuWitness()
        witness.feed(line, 411.25)
        report = witness.as_dict()

        self.assertTrue(report["state2_write_observed"])
        self.assertEqual(report["state2_write_elapsed"], 411.25)
        self.assertEqual(report["last_state"], 2)
        self.assertFalse(report["gameplay_verified"])
        self.assertEqual(report["evidence"], [{
            "event": "state2_write", "elapsed_seconds": 411.25, "line": line,
        }])

    def test_absent_milestones_have_no_elapsed_time(self) -> None:
        report = MenuWitness().as_dict()
        for name in ("intro_opened", "intro_eof_observed", "intro_teardown_observed",
                     "state2_write_observed", "gameplay_verified"):
            self.assertIn(name, report)
            self.assertFalse(report[name], name)
        for name in ("intro_opened_elapsed", "intro_eof_elapsed",
                     "intro_teardown_elapsed", "state2_write_elapsed",
                     "last_state", "last_intro_frame", "last_rsx_frame"):
            self.assertIn(name, report)
            self.assertIsNone(report[name], name)
        self.assertEqual(report["rsx_capture_count"], 0)

    def test_intro_open_requires_a_successful_open_of_the_exact_movie(self) -> None:
        witness = MenuWitness()
        for line in (
            "[fs] open 'v_00_intro_montage_g.bik' FAILED error=0x80010006",
            "[fs] open 'v_00_intro_montage_e.bik' -> fd 9",
            "[fs] open 'v_00_intro_montage_g.bik.bak' -> fd 9",
            "[fs] close 'v_00_intro_montage_g.bik' -> fd 9",
        ):
            witness.feed(line, 9.0)
        self.assertFalse(witness.as_dict()["intro_opened"])
        witness.feed(INTRO_OPEN, 259.0)
        report = witness.as_dict()
        self.assertTrue(report["intro_opened"])
        self.assertEqual(report["intro_opened_elapsed"], 259.0)
        self.assertFalse(report["intro_eof_observed"])
        self.assertFalse(report["gameplay_verified"])

    def test_splash_generation_and_port_close_do_not_prove_intro_eof(self) -> None:
        witness = MenuWitness()
        witness.feed("[i2-bink-buffer] gen=1 id=6 h_C=00000CC9", 10.0)
        witness.feed("[i2-bink-sem] gen=1 id=6 final=1", 11.0)
        witness.feed("[cellAudio] PortClose1", 12.0)
        report = witness.as_dict()
        self.assertFalse(report["intro_eof_observed"])
        self.assertFalse(report["intro_teardown_observed"])
        self.assertIsNone(report["intro_eof_elapsed"])
        self.assertIsNone(report["last_intro_frame"])
        self.assertFalse(report["gameplay_verified"])

    def test_partial_intro_progress_is_hexadecimal_and_not_eof(self) -> None:
        witness = MenuWitness()
        witness.feed(INTRO_OPEN, 259.0)
        witness.feed("[i2-bink-buffer] gen=2 id=6 h_C=000009C0", 420.0)
        report = witness.as_dict()
        self.assertEqual(report["last_intro_frame"], 2496)
        self.assertFalse(report["intro_eof_observed"])
        self.assertIsNone(report["intro_eof_elapsed"])
        self.assertFalse(report["state2_write_observed"])

    def test_intro_eof_does_not_require_state2_or_an_open_line(self) -> None:
        witness = MenuWitness()
        witness.feed("[i2-bink-buffer] h_C=00000CC9 id=6 gen=2", 469.0)
        report = witness.as_dict()
        self.assertTrue(report["intro_eof_observed"])
        self.assertEqual(report["intro_eof_elapsed"], 469.0)
        self.assertEqual(report["last_intro_frame"], 3273)
        self.assertFalse(report["state2_write_observed"])
        self.assertIsNone(report["state2_write_elapsed"])
        self.assertFalse(report["gameplay_verified"])

    def test_intro_eof_threshold_is_frame_3273(self) -> None:
        witness = MenuWitness()
        witness.feed("[i2-bink-buffer] gen=2 id=6 h_C=00000CC8", 10.0)
        self.assertEqual(witness.as_dict()["last_intro_frame"], 3272)
        self.assertFalse(witness.as_dict()["intro_eof_observed"])
        witness.feed("[i2-bink-buffer] gen=2 id=6 h_C=00000CC9", 11.0)
        self.assertTrue(witness.as_dict()["intro_eof_observed"])
        self.assertEqual(witness.as_dict()["intro_eof_elapsed"], 11.0)

    def test_teardown_and_eof_remain_distinct_with_reordered_lines(self) -> None:
        witness = MenuWitness()
        witness.feed("[i2-bink-sem] final=1 gen=2 id=6", 470.0)
        before_eof = witness.as_dict()
        self.assertTrue(before_eof["intro_teardown_observed"])
        self.assertEqual(before_eof["intro_teardown_elapsed"], 470.0)
        self.assertFalse(before_eof["intro_eof_observed"])
        witness.feed("[i2-bink-buffer] id=6 gen=2 h_C=0x00000CCA", 471.0)
        witness.feed(INTRO_OPEN, 472.0)
        report = witness.as_dict()
        self.assertTrue(report["intro_opened"])
        self.assertTrue(report["intro_eof_observed"])
        self.assertEqual(report["intro_eof_elapsed"], 471.0)
        self.assertEqual(report["last_intro_frame"], 3274)
        self.assertFalse(report["gameplay_verified"])

    def test_buffer_requires_exact_generation_id_and_valid_hex_frame(self) -> None:
        for line in (
            "[i2-bink-buffer] gen=12 id=6 h_C=00000CC9",
            "[i2-bink-buffer] gen=2 id=16 h_C=00000CC9",
            "[i2-bink-buffer] gen=2 id=6 h_C=00000CC9suffix",
            "[i2-bink-buffer] gen=2 id=6 h_C=not_hex",
            "[i2-bink-buffer] gen=2 id=6 h_C=00000CC9 valid_mask=FFE",
            "[i2-bink-buffer] gen=2 id=6 other_h_C=00000CC9",
            "[i2-bink-buffer] gen=2 h_C=00000CC9",
        ):
            with self.subTest(line=line):
                witness = MenuWitness()
                witness.feed(line, 1.0)
                self.assertFalse(witness.as_dict()["intro_eof_observed"])
                self.assertIsNone(witness.as_dict()["last_intro_frame"])

    def test_interleaved_markers_preserve_fields_and_multiple_events(self) -> None:
        witness = MenuWitness()
        line = (
            "[i2-bink-buffer] h_C=00000CC9 [audio] blocks=12 id=6 gen=2 "
            "[RSXCAP] frame 102 -> capture "
            "[ww] 0x00abfe50 <- 0x00000002 (w4) guest-fn=0x000C0F20 "
            "[i2-bink-sem] final=1 [dma-range] size=32 gen=2 id=6"
        )
        witness.feed(line, 500.5)
        report = witness.as_dict()
        self.assertTrue(report["intro_eof_observed"])
        self.assertTrue(report["intro_teardown_observed"])
        self.assertTrue(report["state2_write_observed"])
        self.assertEqual(report["last_rsx_frame"], 102)
        self.assertEqual(report["rsx_capture_count"], 1)
        self.assertFalse(report["gameplay_verified"])
        self.assertTrue(all(item["line"] == line for item in report["evidence"]))

    def test_separate_bink_messages_cannot_supply_each_others_fields(self) -> None:
        witness = MenuWitness()
        witness.feed(
            "[i2-bink-buffer] gen=1 id=6 h_C=00000CC9 "
            "[i2-bink-buffer] gen=2 id=6 h_C=000009C0 "
            "[i2-bink-sem] gen=1 id=6 final=1", 10.0)
        report = witness.as_dict()
        self.assertFalse(report["intro_eof_observed"])
        self.assertFalse(report["intro_teardown_observed"])
        self.assertEqual(report["last_intro_frame"], 2496)

    def test_capture_count_counts_messages_and_keeps_latest_frame(self) -> None:
        witness = MenuWitness()
        witness.feed("[RSXCAP] frame 31 -> a [RSXCAP] frame 41 -> b", 1.0)
        witness.feed("[RSXCAP] frame 11 -> c", 2.0)
        report = witness.as_dict()
        self.assertEqual(report["rsx_capture_count"], 3)
        self.assertEqual(report["last_rsx_frame"], 11)
        self.assertFalse(report["gameplay_verified"])

    def test_state_write_requires_exact_address_value_and_word_width(self) -> None:
        for line in (
            "[ww] 0x00ABFE54 <- 0x2 (w4)",
            "[ww] 0x00ABFE500 <- 0x2 (w4)",
            "[ww] 0x00ABFE50 <- 0x2 (w1)",
            "[ww] 0x00ABFE50 <- 0x2 (w8)",
            "[ww] 0x00ABFE50 <- 0x2 (w04)",
            "[ww] 0x00ABFE50 <- 0x02000000 (w4)",
            "[ww] 0x00ABFE50 <- 0x12 (w4)",
            "[ww] 0x00ABFE50 <- 0x2suffix (w4)",
            "[ww] 0x00ABFE50 <- 2 (w4)",
        ):
            with self.subTest(line=line):
                witness = MenuWitness()
                witness.feed(line, 1.0)
                report = witness.as_dict()
                self.assertFalse(report["state2_write_observed"])
                self.assertIsNone(report["state2_write_elapsed"])

    def test_milestones_persist_while_latest_values_keep_changing(self) -> None:
        witness = MenuWitness()
        witness.feed("[i2-bink-buffer] gen=2 id=6 h_C=00000CC9", 10.0)
        witness.feed("[ww] 0x00ABFE50 <- 0x2 (w4)", 11.0)
        witness.feed("[i2-bink-buffer] gen=2 id=6 h_C=00000001", 12.0)
        witness.feed("[ww] 0x00ABFE50 <- 0x0 (w4)", 13.0)
        witness.feed("[ww] 0x00ABFE54 <- 0x7 (w4)", 14.0)
        report = witness.as_dict()
        self.assertEqual(report["last_state"], 0)
        self.assertEqual(report["last_intro_frame"], 1)
        self.assertTrue(report["state2_write_observed"])
        self.assertEqual(report["state2_write_elapsed"], 11.0)
        self.assertTrue(report["intro_eof_observed"])
        self.assertEqual(report["intro_eof_elapsed"], 10.0)
        self.assertEqual(report["state_write_observation_kind"],
                         "write_watch_report_before_store")
        self.assertFalse(report["gameplay_verified"])

    def test_evidence_retains_latest_eight_without_resetting_first_elapsed(self) -> None:
        witness = MenuWitness()
        for number in range(20):
            witness.feed(f"[ww] 0x00ABFE50 <- 0x2 (w4) tid={number}\n", float(number))
        report = witness.as_dict()
        self.assertEqual(len(report["evidence"]), 8)
        self.assertEqual(report["evidence"][0], {
            "event": "state2_write", "elapsed_seconds": 12.0,
            "line": "[ww] 0x00ABFE50 <- 0x2 (w4) tid=12",
        })
        self.assertEqual(report["evidence"][-1]["elapsed_seconds"], 19.0)
        self.assertEqual(report["state2_write_elapsed"], 0.0)

    def test_report_is_a_snapshot_that_cannot_rewrite_retained_evidence(self) -> None:
        witness = MenuWitness()
        line = "[ww] 0x00ABFE50 <- 0x2 (w4) tid=5"
        witness.feed(line, 1.0)
        snapshot = witness.as_dict()
        snapshot["evidence"][0]["line"] = "forged"
        snapshot["evidence"].clear()
        self.assertEqual(witness.as_dict()["evidence"][0]["line"], line)

    def test_conflicting_bink_fields_do_not_establish_a_milestone(self) -> None:
        witness = MenuWitness()
        witness.feed(
            "[i2-bink-buffer] gen=1 [audio] gen=2 id=6 h_C=00000CC9", 1.0)
        witness.feed("[i2-bink-sem] gen=2 final=0 [audio] final=1", 2.0)
        report = witness.as_dict()
        self.assertFalse(report["intro_eof_observed"])
        self.assertFalse(report["intro_teardown_observed"])


if __name__ == "__main__":
    unittest.main()
