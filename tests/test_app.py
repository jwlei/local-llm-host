"""The app end to end, with tests/fake_llama_server.py in llama-server's place: nothing loads on the GPU."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from local_llm_host.app import create_app
from local_llm_host.config import Config
from tests.test_chats import data_url

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_llama_server.py")


def touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


class AppTest(unittest.TestCase):
    password = ""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = self.folder.name
        touch(root, "models", "seer", "seer.gguf")
        touch(root, "models", "seer", "mmproj.gguf")
        touch(root, "models", "text.gguf")
        self.record = os.path.join(root, "requests.jsonl")
        os.environ["FAKE_LLAMA_RECORD"] = self.record
        self.config = Config(llama_server=FAKE, models_dir=os.path.join(root, "models"), data_dir=os.path.join(root, "data"),
                             system_prompt="Be kind.", password=self.password, secret_key="test", start_timeout=30)
        self.shut = []
        self.app = create_app(self.config, shutdown=lambda: self.shut.append(True))
        self.runner = self.app.extensions["local_llm_host"]["runner"]
        self.client = self.app.test_client()
        self.quiet = redirect_stdout(StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.runner.unload()
        self.quiet.__exit__(None, None, None)
        os.environ.pop("FAKE_LLAMA_RECORD", None)
        self.folder.cleanup()

    def send(self, **body):
        response = self.client.post("/api/chat/send", json=body)
        if response.mimetype != "application/x-ndjson":
            return response, []
        return response, [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]

    def requests_made(self):
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class ChatTest(AppTest):
    def test_a_message_loads_the_model_streams_the_reply_and_keeps_both(self):
        _, events = self.send(text="hello world", model="text")
        self.assertEqual(events[0], {"status": "loading"})
        self.assertEqual(events[1], {"status": "writing"})
        self.assertEqual("".join(e.get("text", "") for e in events), "Echo: hello world")
        self.assertEqual(events[-1], {"done": True, "cut_off": False})
        state = self.client.get("/api/chat").get_json()
        self.assertEqual([(m["role"], m["content"]) for m in state["messages"]],
                         [("user", "hello world"), ("assistant", "Echo: hello world")])
        self.assertTrue(state["context"]["measured"])
        [request] = self.requests_made()
        self.assertEqual(request["body"]["messages"][0], {"role": "system", "content": "Be kind."})
        self.assertNotIn("--mmproj", request["argv"])

    def test_the_next_message_finds_the_model_still_loaded(self):
        self.send(text="one", model="text")
        _, events = self.send(text="two", model="text")
        self.assertEqual(events[0], {"status": "writing"})
        self.assertEqual(self.client.get("/api/status").get_json()["loaded"]["id"], "text")

    def test_unload_closes_the_model(self):
        self.send(text="one", model="text")
        self.assertTrue(self.client.post("/api/unload").get_json()["unloaded"])
        self.assertIsNone(self.client.get("/api/status").get_json()["loaded"])
        _, events = self.send(text="two", model="text")
        self.assertEqual(events[0], {"status": "loading"})

    def test_images_go_to_a_model_that_sees_them_with_its_projector(self):
        _, events = self.send(text="what is this", model="seer", images=[data_url()])
        self.assertEqual(events[-1]["done"], True)
        [request] = self.requests_made()
        self.assertIn("--mmproj", request["argv"])
        content = request["body"]["messages"][-1]["content"]
        self.assertEqual([part["type"] for part in content], ["image_url", "text"])
        [message] = [m for m in self.client.get("/api/chat").get_json()["messages"] if m["role"] == "user"]
        image = self.client.get(f"/api/chat/image/{message['images'][0]}")
        self.assertEqual(image.status_code, 200)
        image.close()

    def test_images_to_a_model_that_cant_see_are_refused(self):
        response, _ = self.send(text="look", model="text", images=[data_url()])
        self.assertEqual(response.status_code, 400)
        self.assertIn("can't see images", response.get_json()["error"])

    def test_an_unknown_model_is_refused(self):
        response, _ = self.send(text="hi", model="nope")
        self.assertEqual(response.status_code, 400)

    def test_regenerate_writes_the_last_reply_again(self):
        self.send(text="first", model="text")
        _, events = self.send(text="", model="text", **{"from": 1, "count": 2})
        self.assertEqual(events[-1]["done"], True)
        messages = self.client.get("/api/chat").get_json()["messages"]
        self.assertEqual([m["content"] for m in messages], ["first", "Echo: first"])
        self.assertEqual(len(self.requests_made()), 2)

    def test_edit_and_resend_replaces_the_message_and_what_followed(self):
        self.send(text="first", model="text")
        self.send(text="second", model="text")
        _, events = self.send(text="changed", model="text", **{"from": 2, "count": 4})
        self.assertEqual(events[-1]["done"], True)
        messages = self.client.get("/api/chat").get_json()["messages"]
        self.assertEqual([m["content"] for m in messages], ["first", "Echo: first", "changed", "Echo: changed"])

    def test_a_chat_changed_meanwhile_is_not_cut_in_the_wrong_place(self):
        self.send(text="first", model="text")
        response, _ = self.send(text="changed", model="text", **{"from": 0, "count": 5})
        self.assertEqual(response.status_code, 409)

    def test_a_reply_that_ran_out_of_room_is_marked_cut_off(self):
        _, events = self.send(text="CUT OFF please", model="text")
        self.assertEqual(events[-1], {"done": True, "cut_off": True})
        [_, reply] = self.client.get("/api/chat").get_json()["messages"]
        self.assertEqual(reply["ended"], "cut_off")

    def test_a_reply_only_in_chinese_is_an_error_and_closes_the_model(self):
        _, events = self.send(text="CHINESE ONLY", model="text")
        self.assertIn("only in Chinese", events[-1]["error"])
        self.assertIsNone(self.client.get("/api/status").get_json()["loaded"])

    def test_a_model_that_fails_to_load_says_why(self):
        self.config.extra_args = ["--fail"]
        _, events = self.send(text="hi", model="text")
        self.assertIn("failed to load model", events[-1]["error"])
        self.assertFalse(self.client.get("/api/chat").get_json()["busy"])

    def test_without_llama_server_set_it_says_how_to(self):
        self.config.llama_server = ""
        _, events = self.send(text="hi", model="text")
        self.assertIn("LLAMA_SERVER", events[-1]["error"])


class SessionTest(AppTest):
    def test_new_keeps_the_last_chat_under_sessions_and_open_returns_to_it(self):
        self.send(text="first chat", model="text")
        first = self.client.get("/api/chat").get_json()["id"]
        state = self.client.post("/api/chat/new").get_json()
        self.assertEqual(state["messages"], [])
        self.assertEqual([s["title"] for s in state["sessions"]], ["first chat"])
        state = self.client.post(f"/api/chat/open/{first}").get_json()
        self.assertEqual(state["id"], first)
        self.assertEqual(len(state["messages"]), 2)

    def test_clear_empties_only_the_open_chat(self):
        self.send(text="keep me", model="text")
        self.client.post("/api/chat/new")
        self.send(text="clear me", model="text")
        state = self.client.post("/api/chat/clear").get_json()
        self.assertEqual(state["messages"], [])
        self.assertEqual([s["title"] for s in state["sessions"]], ["keep me"])

    def test_delete_a_chat_and_a_message(self):
        self.send(text="one", model="text")
        state = self.client.post("/api/chat/message/delete", json={"index": 1, "count": 2}).get_json()
        self.assertEqual([m["content"] for m in state["messages"]], ["one"])
        state = self.client.post(f"/api/chat/delete/{state['id']}").get_json()
        self.assertEqual(state["messages"], [])
        self.assertEqual(state["sessions"], [])

    def test_shut_down(self):
        self.assertTrue(self.client.post("/api/shutdown").get_json()["ok"])


class PasswordTest(AppTest):
    password = "secret"

    def test_pages_and_the_api_need_a_sign_in(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/chat").status_code, 401)
        self.assertEqual(self.client.post("/login", data={"password": "wrong"}).status_code, 200)
        response = self.client.post("/login?next=/", data={"password": "secret"})
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(self.client.get("/api/chat").status_code, 200)

    def test_sign_in_only_sends_you_back_into_this_site(self):
        response = self.client.post("/login?next=//evil.example", data={"password": "secret"})
        self.assertEqual(response.headers["Location"], "/")

    def test_other_devices_are_let_in_with_a_password(self):
        response = self.client.get("/login", environ_base={"REMOTE_ADDR": "100.64.0.2"})
        self.assertEqual(response.status_code, 200)


class NoPasswordTest(AppTest):
    def test_without_a_password_only_this_pc_is_answered(self):
        self.assertEqual(self.client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code, 403)
        self.assertEqual(self.client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
