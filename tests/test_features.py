import unittest

import numpy as np

from audio.mixer import Mixer
from features.chat_manager import ChatManager


class FeatureTests(unittest.TestCase):
    def test_mixer_applies_gain_per_sender(self):
        mixer = Mixer()
        frame = np.zeros(320, dtype=np.int16)
        frame[:4] = [1000, -1000, 2000, -2000]
        mixer.set_gain(7, 0.5)
        mixer.add(7, frame.tobytes())
        mixed = mixer.mix()
        out = np.frombuffer(mixed, dtype=np.int16)
        expected = np.zeros(320, dtype=np.int16)
        expected[:4] = [500, -500, 1000, -1000]
        self.assertTrue(np.array_equal(out, expected))

    def test_chat_manager_formats_reply_text(self):
        text = ChatManager._format_reply_text("Alice", "hello")
        self.assertIn("回复 Alice", text)
        self.assertIn("hello", text)


if __name__ == "__main__":
    unittest.main()
