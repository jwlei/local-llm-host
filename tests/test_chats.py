import base64
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from PIL import Image

from local_llm_host.chats import CHARS_PER_TOKEN, ChatStore, Context, ForeignScript, read_image, title


def data_url(size=(40, 30), kind="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, kind)
    return f"data:image/{kind.lower()};base64," + base64.b64encode(buffer.getvalue()).decode()


class ForeignScriptTest(unittest.TestCase):
    def test_chinese_is_taken_out_of_a_reply_to_a_conversation_without_any(self):
        script = ForeignScript(["Hello there"])
        self.assertEqual([script.clean(p) for p in ["你好\n", "Hi ", "世界there"]], ["", "Hi ", "there"])
        self.assertTrue(script.dropped)

    def test_a_conversation_in_chinese_keeps_it(self):
        script = ForeignScript(["你好"])
        self.assertEqual(script.clean("世界"), "世界")
        self.assertFalse(script.dropped)


class ContextTest(unittest.TestCase):
    def test_older_messages_are_left_out_once_the_conversation_outgrows_the_context(self):
        context = Context(window=2048, reply_tokens=512, instruction="Be brief.")
        room = context.history_chars
        messages = [{"role": "user", "content": "a" * (room // 2 + 1)} for _ in range(3)]
        self.assertEqual(context.kept(messages), 1)
        sent = context.sent(messages, store=None)
        self.assertEqual(sent[0], {"role": "system", "content": "Be brief."})
        self.assertEqual(len(sent), 2)
        usage = context.usage({"messages": messages, "usage": None})
        self.assertEqual(usage["left_out"], 2)
        self.assertFalse(usage["measured"])

    def test_the_models_own_count_is_used_after_its_reply(self):
        context = Context(window=16384, reply_tokens=4096, instruction="x")
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        usage = context.usage({"messages": messages, "usage": {"tokens": 500, "messages": 2, "kept": 2}})
        self.assertTrue(usage["measured"])
        self.assertEqual(usage["conversation"], 500 - context.estimate([{"content": "x"}]))
        self.assertEqual(usage["free"], 16384 - usage["instruction"] - usage["conversation"] - 4096)

    def test_an_image_counts_as_much_text_as_it_costs(self):
        message = {"content": "abc", "images": ["a.jpg"]}
        self.assertEqual(Context.size(message), 3 + 280 * CHARS_PER_TOKEN)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ChatStore(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_images_no_chat_names_are_deleted_when_saved(self):
        kept = self.store.keep_image(b"jpeg")
        dropped = self.store.keep_image(b"jpeg")
        chats = self.store.load()
        self.store.session(chats)["messages"].append({"role": "user", "content": "", "images": [kept]})
        self.store.save(chats)
        self.assertIsNotNone(self.store.image_path(kept))
        self.assertIsNone(self.store.image_path(dropped))

    def test_an_unreadable_file_is_kept_aside_not_written_over(self):
        with open(self.store.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        with redirect_stdout(StringIO()) as said:
            chats = self.store.load()
        self.assertIn("moved to", said.getvalue())
        self.assertEqual(len(chats["sessions"]), 1)
        self.assertTrue(any(name.startswith("chats.json.unreadable-") for name in os.listdir(self.folder.name)))

    def test_image_names_must_be_the_stores_own(self):
        self.assertIsNone(self.store.image_path("../chats.json"))


class ImageTest(unittest.TestCase):
    def test_an_image_is_kept_as_jpeg_no_larger_than_the_edge(self):
        data = read_image(data_url(size=(3000, 1000)))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1536, 512))

    def test_what_isnt_an_image_is_refused(self):
        with self.assertRaises(ValueError):
            read_image("data:image/png;base64," + base64.b64encode(b"not an image").decode())
        with self.assertRaises(ValueError):
            read_image("https://example.com/a.png")


class TitleTest(unittest.TestCase):
    def test_a_title_is_the_first_message_cut_short(self):
        self.assertEqual(title([{"role": "user", "content": "  hello\n there "}]), "hello there")
        self.assertEqual(len(title([{"role": "user", "content": "x" * 100}])), 60)
        self.assertEqual(title([{"role": "user", "content": "", "images": ["a.jpg"]}]), "Image")


if __name__ == "__main__":
    unittest.main()
