import os
import tempfile
import threading
import unittest

import requests

from app import run_pipeline
from core.errors import AccessDeniedError, CancelledError, FfmpegFailedError, ValidationFailedError
from core.ffmpeg_pipeline import PipelineConfig, download_audio, download_video
from core.models import Capture
from core.mux import mux_streams, validate_output
from core.stream_classifier import build_plan
from core.tools import find_tools, probe_json
from tests.support import HAVE_FFMPEG, MockCdn, make_hls

HDRS = {"referer": "https://player.vimeo.com/", "origin": "https://player.vimeo.com", "user-agent": "TestUA/1.0",
        "cookie": "sid=SHOULD_NEVER_BE_SENT"}
quiet = lambda *a, **k: None


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class PipelineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plain = make_hls(__import__("pathlib").Path(cls.tmp.name) / "plain").parent
        cls.ranged = make_hls(__import__("pathlib").Path(cls.tmp.name) / "ranged", single_file=True).parent
        cls.tools = find_tools()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cfg(self, **kw):
        return PipelineConfig(self.tools, HDRS, protocol_whitelist="http,https,tls,tcp", backoff=0.1, **kw)

    def plan(self, cdn, quality="best"):
        url = cdn.url("master.m3u8")
        text = requests.get(url).text
        return build_plan([Capture(1, url, text)], quality, fetch=lambda u: requests.get(u).text)

    def run_full(self, root, audio_mode="copy", quality="best"):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(root) as cdn:
            plan = self.plan(cdn, quality)
            cfg = self.cfg(audio_mode=audio_mode)
            final = run_pipeline(plan, cfg, out, "lesson")
            return cdn, plan, cfg, final, out

    def test_full_pipeline_copy_audio(self):
        cdn, plan, cfg, final, out = self.run_full(self.plain)
        info = validate_output(cfg, final, plan.duration)
        self.assertEqual((info["video"], info["audio"]), ("h264", "aac"))   # original AAC preserved
        self.assertEqual(info["height"], 360)
        self.assertAlmostEqual(info["duration"], 8, delta=1)
        self.assertEqual(os.listdir(out), ["lesson.mkv"])                     # temp files cleaned up
        self.assertTrue(plan.audio_url and "audio" in plan.audio_url)

    def test_forwarded_headers_reach_playlists_and_segments_but_never_cookies(self):
        cdn, *_ = self.run_full(self.plain)
        # requests made by ffmpeg only (the test's own plan-building requests use python-requests)
        media = [h for p, h in cdn.headers_seen if "python-requests" not in h.get("user-agent", "")]
        self.assertTrue(media)
        for h in media:
            self.assertEqual(h.get("referer"), "https://player.vimeo.com/")
            self.assertEqual(h.get("origin"), "https://player.vimeo.com")
            self.assertEqual(h.get("user-agent"), "TestUA/1.0")
        self.assertFalse(any("cookie" in h for _, h in cdn.headers_seen))

    def test_no_master_captured_video_and_audio_identified_by_ffprobe(self):
        from app import make_probe
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            audio_url, video_url = cdn.url("audio/index.m3u8"), cdn.url("v360/index.m3u8")
            # audio playlist seen FIRST -- the old "first .m3u8 wins" logic would have downloaded audio as video
            caps = [Capture(1, audio_url, requests.get(audio_url).text), Capture(2, video_url, requests.get(video_url).text)]
            cfg = self.cfg()
            plan = build_plan(caps, headers=HDRS, fetch=lambda u: requests.get(u).text, probe=make_probe(self.tools, HDRS, cfg))
            self.assertEqual((plan.video_url, plan.audio_url, plan.source), (video_url, audio_url, "ffprobe"))
            final = run_pipeline(plan, cfg, out, "lesson")
            info = validate_output(cfg, final, plan.duration)
            self.assertEqual((info["video"], info["audio"], info["height"]), ("h264", "aac", 360))

    def test_flac_is_a_transcode_and_default_is_not(self):
        _, plan, cfg, final, _ = self.run_full(self.plain, audio_mode="flac")
        self.assertEqual(validate_output(cfg, final, plan.duration)["audio"], "flac")

    def test_byte_range_single_file_stream(self):
        _, plan, cfg, final, _ = self.run_full(self.ranged)
        self.assertEqual(validate_output(cfg, final, plan.duration)["video"], "h264")

    def test_quality_fallback_picks_lower(self):
        _, plan, _, final, _ = self.run_full(self.plain, quality="1080")
        self.assertEqual(plan.height, 360)
        self.assertTrue(plan.notes)

    def test_expired_link_is_reported_not_retried_forever(self):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            plan = self.plan(cdn)
            cdn.deny_media_after = 0
            with self.assertRaises(AccessDeniedError) as ctx:
                download_video(self.cfg(), plan, os.path.join(out, "v.mkv"))
            self.assertIn("403", str(ctx.exception))
            self.assertNotIn("127.0.0.1:", str(ctx.exception).replace("http", ""))  # no URLs in the message

    def test_transient_server_errors_recover(self):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            plan = self.plan(cdn)
            cdn.fail_first["seg_001.m4s"] = 2
            download_video(self.cfg(retries=3), plan, os.path.join(out, "v.mkv"))
            self.assertAlmostEqual(float(probe_json(self.tools, os.path.join(out, "v.mkv"))["format"]["duration"]), 8, delta=1)

    def test_cancel_stops_ffmpeg_and_leaves_no_final_file(self):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            plan = self.plan(cdn)
            cdn.delay = 0.6
            ev = threading.Event()
            threading.Timer(1.0, ev.set).start()
            with self.assertRaises(CancelledError):
                download_video(self.cfg(cancel=ev), plan, os.path.join(out, "v.mkv"), on_progress=quiet)
            self.assertFalse(os.path.exists(os.path.join(out, "v.mkv")))

    def test_http_blocked_by_default_protocol_whitelist(self):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            plan = self.plan(cdn)
            strict = PipelineConfig(self.tools, HDRS)   # default: https only
            with self.assertRaises(FfmpegFailedError):
                download_video(strict, plan, os.path.join(out, "v.mkv"))

    def test_validation_rejects_wrong_duration(self):
        _, plan, cfg, final, _ = self.run_full(self.plain)
        with self.assertRaises(ValidationFailedError):
            validate_output(cfg, final, plan.duration * 3)

    def test_existing_valid_file_is_not_redownloaded(self):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        with MockCdn(self.plain) as cdn:
            plan = self.plan(cdn)
            cfg = self.cfg()
            run_pipeline(plan, cfg, out, "lesson")
            before = cdn.media_requests_ok()
            run_pipeline(plan, cfg, out, "lesson")
            self.assertEqual(cdn.media_requests_ok(), before)


if __name__ == "__main__":
    unittest.main()
