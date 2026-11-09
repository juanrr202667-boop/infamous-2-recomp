"""Regression checks for the unattended boot report."""

from pathlib import Path
import tempfile
import unittest

from scripts import probe_boot
from scripts.probe_boot import summarize_log


class BootReportTests(unittest.TestCase):
    def test_pipeline_failure_keeps_draw_evidence_and_error_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            failure = "[FP] PSO FAIL (fp=0x0F2DC5C1, 0x80070057)"
            validation = "[D3D12-IQ][sev=1 id=690] Root Signature doesn't match Pixel Shader: Shader sampler descriptor range (BaseShaderRegister=4, NumDescriptors=1, RegisterSpace=0) is not fully bound in root signature"
            log.write_text(
                "[D3D12] draw_arrays #19 prim=8 first=768 count=176\n"
                + failure + "\n" + validation + "\n", encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["draws_observed"], 20)
            self.assertEqual(report["issues"].get("graphics_pipeline_failure"), 1)
            self.assertEqual(report["issues"].get("graphics_validation_error"), 1)
            self.assertEqual(report.get("first_graphics_failures"), [failure, validation])
            self.assertIn(validation, report["events"])
            self.assertEqual(probe_boot.classify_outcome(report, None, True),
                             "guest_graphics_failed")

    def test_validation_warning_does_not_mark_graphics_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[D3D12-IQ][sev=2 id=679] Pixel Shader expects RTV slot 1, but none is bound.\n"
                "[D3D12] draw_indexed(prim=5, first=0, count=6)\n",
                encoding="utf-8")
            report = summarize_log(log)
            self.assertFalse(report["issues"].get("graphics_validation_error"))
            self.assertEqual(probe_boot.classify_outcome(report, None, True),
                             "guest_graphics_observed")

    def test_indexed_only_d3d12_boot_keeps_graphics_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[D3D12] draw_indexed(prim=5, first=0, count=6)\n" * 2,
                encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["draws_observed"], 2)
            self.assertFalse(report["draws_total_known"])
            self.assertEqual(probe_boot.classify_outcome(report, None, True),
                             "guest_graphics_observed")

    def test_d3d12_draw_log_reports_lower_bound_instead_of_false_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[D3D12] Initialization complete (1280x720, 2 buffers, pipeline=ready)\n"
                "[D3D12] draw_arrays #0 prim=8 first=0 count=256\n"
                "[D3D12] draw_arrays #19 prim=8 first=768 count=176\n",
                encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["draws_observed"], 20)
            self.assertFalse(report.get("draws_total_known", True))
            self.assertTrue(report["stages"].get("d3d12_backend_ready", False))
            self.assertFalse(report["stages"]["headless_backend_ready"])

    def test_incomplete_policy_run_keeps_return_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            returned = "[pm-end] lane 0 ctx=00001234 halted=0 status=0x1 pc=0x00A60 lr=0x02BE0 r3=0x45 depth=0 recent: 02CB0\n"
            log.write_text(returned * 2, encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["issues"].get("spu_policy_unexpected_return"), 2)
            self.assertEqual(report.get("first_incomplete_spu_run"), {
                "lane": "0", "halted": "0", "status": "0x1",
                "pc": "0x00A60", "lr": "0x02BE0"})
            self.assertEqual(report["events"], [returned.strip()])

    def test_polling_and_null_callbacks_keep_first_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            hot = "[HOTREAD] spinning on 0x00A25A18 (=0x00000003) guest cia=0x00000000 lr=0x001DB7DC\n"
            null = "[null-call] guest called a NULL function pointer (near lr=0x00000000, tid=3) r3=00000002\n"
            log.write_text(hot * 2 + null, encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["issues"].get("guest_hot_read"), 2)
            self.assertEqual(report["issues"].get("guest_null_call"), 1)
            self.assertEqual(report.get("busy_guest_reads"), [{
                "address": "0x00A25A18", "value": "0x00000003", "lr": "0x001DB7DC"}])
            self.assertEqual(report.get("first_null_calls"), [null.strip()])

    def test_spurs_job_misses_list_unique_raw_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[spurs-job] dispatch MISS fp=0x054414E2CCD626FF size=1040 job=0x00B15580 (#1)\n" * 2
                + "[spurs-job] dispatch MISS fp=0x64D9D14299A2B7AB size=272 job=0x00B154E0 (#3)\n",
                encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report["issues"].get("spu_job_dispatch_miss"), 3)
            self.assertEqual(report.get("missing_jobs"), [
                "fp=0x054414E2CCD626FF size=1040", "fp=0x64D9D14299A2B7AB size=272"])

    def test_runtime_failure_keeps_progress_and_identifies_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[ppu] run: code 0x00010230\n"
                "[i2-lfqueue] push q=0x47D7FD00 ticket=0 bytes=32\n"
                "[cellSpurs] EventFlagWait WAKE tid=1 got=0x0001\n"
                "[ppu] unresolved indirect call -> 0x000B42C8 (tid=1)\n"
                "[ppu] FATAL: stuck calling 0x000B42C8 (2000 times) -- aborting run\n",
                encoding="utf-8")
            report = summarize_log(log)
            self.assertEqual(report.get("unresolved_call_targets"), ["0x000B42C8"])
            self.assertEqual(report["issues"]["fatal_runtime"], 1)
            self.assertEqual(report["activity"]["lfqueue_pushes_logged"], 1)
            self.assertEqual(report["activity"]["spurs_event_wakes_logged"], 1)
            self.assertEqual(probe_boot.classify_outcome(report, 3, False), "native_runtime_failed")

    def test_spu_flags_override_inherited_experiment_settings(self) -> None:
        inherited = {"SPURS_PM_FRESH": "0", "SPU_LS_WATCH": "0x210", "PATH": "tools"}
        default = probe_boot.configure_spu_environment(inherited)
        self.assertNotIn("SPURS_PM_FRESH", default)
        self.assertNotIn("SPU_LS_WATCH", default)
        self.assertEqual(default["PATH"], "tools")
        self.assertEqual(inherited["SPU_LS_WATCH"], "0x210")
        explicit = probe_boot.configure_spu_environment(
            inherited, fresh_spu=True, spu_ls_watch="0x300")
        self.assertEqual(explicit["SPURS_PM_FRESH"], "1")
        self.assertEqual(explicit["SPU_LS_WATCH"], "0x300")

    def test_unresolved_imports_record_unique_nids_and_first_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[hle] unresolved NID 0x8A85674D\n" * 2
                + "[hle] unresolved NID 0x0123ABCD\n",
                encoding="utf-8",
            )
            report = summarize_log(log)
            self.assertEqual(report["issues"]["unresolved_import"], 3)
            self.assertEqual(report["unresolved_nids"], ["0x8A85674D", "0x0123ABCD"])
            self.assertEqual(len(report["events"]), 2)

    def test_idle_presents_do_not_count_as_guest_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[ppu] loaded 2 PT_LOAD segments, entry OPD 0x0094DD38\n"
                "[ppu] run: code 0x00010230\n"
                "[cellGcmSys] Ini[another thread]t(cmdSize=0x10000)\n"
                "[cellGcmSys] SetFlipMode(mode=2)\n"
                "[rsx] headless diagnostic backend init OK -- no window\n"
                "[cellGame] title id from PARAM.SFO ('disc/PARAM.SFO'): 'BCUS98125'\n"
                "[headless] present #10\n"
                "[cellSpurs] wid=0 PM NOT LIFTED (fp=0xABCD size=11904)\n"
                "SPU interleave [sys_fs] open FAILED: missing/ne.bv\n"
                "[headless] draw #2\n"
                "[headless] guest frame #1\n",
                encoding="utf-8",
            )
            report = summarize_log(log)
            self.assertEqual(report["draws_observed"], 2)
            self.assertEqual(report["backend_presents_observed"], 10)
            self.assertEqual(report["guest_frames_observed"], 1)
            self.assertEqual(report["issues"]["spu_workload_not_lifted"], 1)
            self.assertEqual(report["issues"]["file_open_failed"], 1)
            self.assertEqual(report["first_failed_file_opens"],
                             ["[sys_fs] open FAILED: missing/ne.bv"])
            self.assertTrue(report["stages"]["headless_backend_ready"])
            self.assertTrue(report["stages"]["guest_entry_reached"])
            self.assertTrue(report["stages"]["gcm_initialized"])
            self.assertEqual(report["title_id"], "BCUS98125")

    def test_spu_wait_and_dma_halt_are_reported_once_in_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[spu-dma] 256 identical transfers -- halting the SPU\n" * 3
                + "[cellSpurs] EventFlagWait BLOCKED tid=1\n"
                + "[spu_workload] task sleeping 2s in WAIT_SIGNAL\n",
                encoding="utf-8",
            )
            report = summarize_log(log)
            self.assertEqual(report["issues"]["spu_repeated_dma_halt"], 3)
            self.assertEqual(report["issues"]["spurs_event_flag_wait"], 1)
            self.assertEqual(report["issues"]["spu_wait_signal"], 1)
            self.assertEqual(len(report["events"]), 3)

    def test_malformed_dma_records_first_effective_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "boot.log"
            log.write_text(
                "[mfc] REJECTED malformed transfer: img=1 pc=0x02CD8 cmd=0x20 "
                "lsa=0x00000 ea=0x0000B080 size=16\n"
                "[mfc] REJECTED malformed transfer: img=1 pc=0x02CD8 cmd=0x20 "
                "lsa=0x00000 ea=0x00000200 size=16\n",
                encoding="utf-8",
            )
            report = summarize_log(log)
            self.assertEqual(report["issues"]["spu_malformed_dma"], 2)
            self.assertEqual(report["first_malformed_dma"]["ea"], "0x0000B080")
            self.assertEqual(report["first_malformed_dma"]["pc"], "0x02CD8")
            self.assertEqual(len(report["events"]), 1)


if __name__ == "__main__":
    unittest.main()
