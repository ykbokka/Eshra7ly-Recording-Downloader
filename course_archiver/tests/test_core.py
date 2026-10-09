import unittest

from core import hls_parser as hls
from core.errors import ProtectedStreamError, StreamDetectionError
from core.filenames import sanitize_filename
from core.models import Capture, safe_headers
from core.redact import redact_command, redact_text, redact_url
from core.stream_classifier import build_plan

BASE = "https://vod.example.com/x/master/playlist.m3u8?exp=1&hmac=SECRETVAL"
MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="../audio/media.m3u8?hmac=SECRETVAL"
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=2560x1440,CODECS="avc1.640032,mp4a.40.2",AUDIO="aud"
../v1440/media.m3u8?hmac=SECRETVAL
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="avc1.64002a,mp4a.40.2",AUDIO="aud"
../v1080/media.m3u8?hmac=SECRETVAL
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",AUDIO="aud"
../v720/media.m3u8?hmac=SECRETVAL
"""
MEDIA = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\na.m4s\n#EXTINF:4,\nb.m4s\n#EXT-X-ENDLIST\n'


class ParserTests(unittest.TestCase):
    def test_variants_and_audio(self):
        m = hls.parse_master(MASTER, BASE)
        self.assertEqual([v.height for v in m.variants], [1440, 1080, 720])
        v, note = hls.choose_variant(m, "1080")
        self.assertEqual((v.height, note), (1080, ""))
        self.assertEqual(hls.choose_audio(m, v).url, "https://vod.example.com/x/audio/media.m3u8?hmac=SECRETVAL")

    def test_quality_fallbacks(self):
        m = hls.parse_master(MASTER, BASE)
        self.assertEqual(hls.choose_variant(m, "best")[0].height, 1440)
        v, note = hls.choose_variant(m, "900")
        self.assertEqual(v.height, 720)
        self.assertIn("900p", note)
        self.assertEqual(hls.choose_variant(m, "240p")[0].height, 720)
        with self.assertRaises(StreamDetectionError):
            hls.choose_variant(m, "ultra")

    def test_media_and_protection(self):
        md = hls.parse_media(MEDIA, "https://c.example.com/m.m3u8")
        self.assertEqual((md.segment_count, md.duration, md.is_vod, md.has_init), (2, 10.0, True, True))
        enc = MEDIA.replace("#EXT-X-MAP", '#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXT-X-MAP')
        self.assertTrue(hls.parse_media(enc, "https://c.example.com/m.m3u8").protection)

    def test_not_a_playlist(self):
        with self.assertRaises(StreamDetectionError):
            hls.parse_master("<html>", BASE)


class ClassifierTests(unittest.TestCase):
    def caps(self):
        return [
            Capture(1, BASE, MASTER),
            Capture(2, "https://vod.example.com/x/v1080/media.m3u8?hmac=SECRETVAL", MEDIA),
            Capture(3, "https://vod.example.com/x/audio/media.m3u8?hmac=SECRETVAL", MEDIA),
        ]

    def test_master_decides_video_and_audio_not_first_url(self):
        # the audio playlist was captured *before* the video one: order must not matter
        caps = [self.caps()[0], self.caps()[2], self.caps()[1]]
        plan = build_plan(caps, "1080", fetch=lambda u: MEDIA)
        self.assertIn("/v1080/", plan.video_url)
        self.assertIn("/audio/", plan.audio_url)
        self.assertEqual((plan.label, plan.duration, plan.source), ("1080p", 10.0, "master"))

    def test_missing_media_capture_is_fetched(self):
        fetched = []
        plan = build_plan([self.caps()[0]], "720", fetch=lambda u: fetched.append(u) or MEDIA)
        self.assertEqual(len(fetched), 2)
        self.assertEqual(plan.height, 720)

    def test_newest_master_wins(self):
        other = MASTER.replace("v1080", "w1080")
        caps = self.caps() + [Capture(9, "https://vod.example.com/y/playlist.m3u8", other)]
        plan = build_plan(caps, "1080", fetch=lambda u: MEDIA)
        self.assertIn("/w1080/", plan.video_url)

    def test_protected_master_refused(self):
        enc = MASTER + '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="skd://x"\n'
        with self.assertRaises(ProtectedStreamError):
            build_plan([Capture(1, BASE, enc)], fetch=lambda u: MEDIA)

    def test_protected_media_refused(self):
        enc = MEDIA.replace("#EXT-X-MAP", '#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXT-X-MAP')
        with self.assertRaises(ProtectedStreamError):
            build_plan([Capture(1, BASE, MASTER)], fetch=lambda u: enc)

    def test_no_master_uses_probe(self):
        v = Capture(1, "https://c.example.com/a/media.m3u8", MEDIA)
        a = Capture(2, "https://c.example.com/b/media.m3u8", MEDIA)
        info = {v.url: {"streams": [{"codec_type": "video", "height": 1080, "width": 1920, "codec_name": "h264"}]},
                a.url: {"streams": [{"codec_type": "audio"}]}}
        plan = build_plan([a, v], probe=lambda u: info[u], fetch=lambda u: MEDIA)
        self.assertEqual((plan.video_url, plan.audio_url, plan.source), (v.url, a.url, "ffprobe"))

    def test_nothing_captured(self):
        with self.assertRaises(StreamDetectionError):
            build_plan([], fetch=lambda u: MEDIA)


class SafetyTests(unittest.TestCase):
    def test_headers_whitelist_drops_cookies(self):
        self.assertEqual(safe_headers({"Cookie": "a", "Authorization": "b", "Referer": "r", "User-Agent": "u"}),
                         {"referer": "r", "user-agent": "u"})

    def test_redaction(self):
        u = "https://c.example.com/exp=1~hmac=SECRETVAL/p.m3u8?hdnts=SECRETVAL"
        self.assertNotIn("SECRETVAL", redact_url(u))
        self.assertNotIn("SECRETVAL", redact_text(f"fail {u}\nCookie: SECRETVAL"))
        cmd = redact_command(["ffmpeg", "-headers", "Cookie: SECRETVAL\r\n", "-i", u])
        self.assertNotIn("SECRETVAL", cmd)

    def test_filenames(self):
        self.assertEqual(sanitize_filename('Lesson 1: Intro?'), "Lesson 1 - Intro")
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("درس: مقدمة"), "درس - مقدمة")


if __name__ == "__main__":
    unittest.main()
