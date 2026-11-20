# local-llm-host

A self-hosted chat for local LLMs. Drop GGUF models in a folder, and chat with them from your PC, your phone or any
device on your Tailscale network. The models run on your own GPU through [llama.cpp](https://github.com/ggml-org/llama.cpp):
nothing is sent to a cloud service.

It started as the chat in [gen-center](https://github.com/jwlei/gen-center), and now runs on its own.

## What it can do

- **Any GGUF model.** Every model in the models folder appears in a picker. Put a vision projector (`mmproj`) beside
  a model and it can look at images you send.
- **Loads on demand, unloads when idle.** The first message starts llama.cpp with the chosen model. It stays loaded
  between messages for quick replies, then unloads after 10 minutes idle to give the GPU back. **Unload it now** in
  the menu frees it at once.
- **Streaming replies** with Markdown (code blocks, tables, lists, links) drawn safely, and the speed in tokens a second.
- **Conversations kept on the server**, so they are the same on every device. Start a new chat, clear one, or open
  an earlier one under **Sessions** to carry on.
- **Edit and resend** any of your messages, **regenerate** a reply, delete a message, or stop a reply mid-way and
  keep what it wrote.
- **Images**: attach up to 4 per message for a model that sees them. Photos are shrunk on the device before sending.
- **Context meter**: how much of the model's context the chat takes (the instruction, the conversation, room kept for
  the reply), measured by the model after each reply. Older messages are left out once a chat outgrows it.
- **From anywhere** over [Tailscale](https://tailscale.com): the app starts and reconnects Tailscale, and shows the
  address to open on your phone. A password protects every page.
- **Cleans up after itself**: llama-server runs on 127.0.0.1 only, and on Windows it ends with the app however the
  app ends, so a model is never left holding the GPU.
- **Windows shortcut** that starts it in the background, with no console window, and **Shut down** in the menu.
- Works on a phone: a full-screen layout that fits around the keyboard.

## Technologies

| Area | Used |
|---|---|
| Backend | Python 3.11+, Flask, streamed NDJSON replies |
| Inference | llama.cpp's `llama-server` (CUDA, Vulkan, Metal or CPU builds), GGUF models, OpenAI-compatible streaming API |
| Frontend | Plain JavaScript, HTML and CSS, no build step |
| Images | Pillow |
| Remote access | Tailscale, password sign-in with signed cookies |
| Windows | PowerShell shortcut script, a job object that ends llama-server with the app |
| Testing | `unittest` with a fake llama-server, GitHub Actions on Windows and Linux |

## Setup

1. **Python 3.11 or newer**, then:

   ```
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **llama.cpp.** Download a release from [github.com/ggml-org/llama.cpp/releases](https://github.com/ggml-org/llama.cpp/releases)
   for your GPU (CUDA for NVIDIA, Vulkan for most others, Metal on a Mac), unzip it, and set `LLAMA_SERVER` in `.env`
   to its `llama-server` program.

3. **Models.** Put GGUF files in `models/` (or set `MODELS_DIR` to a folder you already have):

   ```
   models/
     gemma-3-12b/                 a model that sees images
       gemma-3-12b-Q4_K_M.gguf
       mmproj-F16.gguf
       model.json                 optional settings for this model
     Qwen3-14B-Q4_K_M.gguf        a text-only model
   ```

   A model split into parts (`name-00001-of-00003.gguf`) is listed once.

4. **Other devices (optional).** Set `APP_PASSWORD` and `FLASK_SECRET_KEY` in `.env`, and install
   [Tailscale](https://tailscale.com/download) on this PC and your phone, signed in to the same account.

### Settings

In `.env` (all optional except `LLAMA_SERVER`):

| Setting | What it does | Default |
|---|---|---|
| `LLAMA_SERVER` | Path to llama.cpp's `llama-server` | none |
| `MODELS_DIR` | The models folder | `models` |
| `DATA_DIR` | Where chats, images and logs are kept | `data` |
| `APP_PASSWORD` | Needed to let other devices in | none: this PC only |
| `FLASK_SECRET_KEY` | Keeps sign-ins across restarts | random |
| `SYSTEM_PROMPT` | The instruction every chat starts with | a short helpful-assistant prompt |
| `CONTEXT` | Context size in tokens | `16384` |
| `REPLY_TOKENS` | Room kept for a reply | `4096` |
| `GPU_LAYERS` | Layers on the GPU: `all`, `auto` or a number | `all` |
| `THREADS` | CPU threads (0: llama.cpp chooses) | `0` |
| `CACHE_TYPE` | A quantised KV cache, such as `q8_0`, to save VRAM | none |
| `TEMPERATURE` | Sampling temperature | `0.8` |
| `WARM_SECONDS` | How long a model stays loaded after a reply | `600` |
| `START_TIMEOUT` | Seconds a model may take to load | `180` |
| `LLAMA_ARGS` | Extra arguments for llama-server | none |

A `model.json` in a model's folder overrides these for that model: `label`, `context`, `gpu_layers`, `cache_type`,
`threads`, `temperature` and `args` (a list of extra llama-server arguments). For example, to fit a 14B model with a
long context on a 12 GB card:

```json
{ "label": "Qwen3 14B", "cache_type": "q8_0" }
```

## Running it

```
python -m local_llm_host
```

opens it on this PC at http://127.0.0.1:8810. To let your other devices in:

```
python -m local_llm_host --host 0.0.0.0
```

This needs `APP_PASSWORD`. It also starts Tailscale and prints the addresses to open. The menu (**⋯**, top right)
shows them too, with copy buttons.

**On Windows**, make a shortcut that starts it in the background and opens it in your browser:

```
powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
```

With `APP_PASSWORD` set, the shortcut also lets in your other devices. Add `-Startup` to start it when you sign in
to Windows instead. Stop it with **Shut down** in the menu. Its output goes to `data/logs/app.log`, and llama-server's
to `data/logs/llama-server.log`.

## Tests

```
python -m unittest discover -s tests -t .
```

The tests run a small fake llama-server in Python, so they need no GPU, model or llama.cpp.

## License

MIT
