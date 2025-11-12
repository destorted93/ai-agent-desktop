# TTS Module

Lightweight text-to-speech wrapper for the OpenAI Python SDK. This is a module (not a service) intended to be imported and used by tools under `tools/` or other parts of the agent.

## Features

- Simple API to synthesize speech to a file or return raw bytes
- Configurable model, voice, input text, and optional instructions
- Sensible defaults with environment variable overrides
- Streaming-to-file support to avoid high memory usage

## Installation

The root project already depends on `openai>=1.40.0` via `agent-main/requirements.txt`. Ensure your OpenAI API key is available:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

## Usage

```python
from tts import TTSClient, TTSConfig

# Optional configuration (env defaults shown below)
config = TTSConfig(
    model="gpt-4o-mini-tts",      # or env TTS_MODEL
    voice="coral",                # or env TTS_VOICE
    instructions="Cheerful tone.",# or env TTS_INSTRUCTIONS
    audio_format="mp3",           # or env TTS_AUDIO_FORMAT
)

client = TTSClient(config=config)

# 1) Stream directly to a file (recommended)
output_path = client.synthesize_to_file(
    input_text="Today is a wonderful day to build something people love!",
    file_path="chat_history/speech.mp3",  # any target path
)
print("Saved:", output_path)

# 2) Or get bytes in memory
audio_bytes = client.synthesize_bytes(
    input_text="Welcome to the agent.",
    model="gpt-4o-mini-tts",  # can override per call
    voice="coral",
)
with open("chat_history/welcome.mp3", "wb") as f:
    f.write(audio_bytes)
```

## Environment Variables

- `TTS_MODEL` — default model (default: `gpt-4o-mini-tts`)
- `TTS_VOICE` — default voice (default: `coral`)
- `TTS_INSTRUCTIONS` — optional default instructions
- `TTS_AUDIO_FORMAT` — output format, `mp3` or `wav` (default: `mp3`)
- `TTS_OUTPUT_DIR` — default folder for generated audio when no `file_path` is provided (default: `tts/generated`)

## Notes

- The `synthesize_to_file` method uses OpenAI's streaming response API to write audio directly to disk.
- If `file_path` is omitted, audio is saved by default under `tts/generated/` with a timestamped filename like `speech_YYYYMMDD-HHMMSS.mp3`.
- If you pass a directory to `file_path`, the client writes a timestamped filename inside that directory.
- The `instructions` parameter is included only if provided to maintain compatibility with SDKs that may not accept it.

## Integrating with Tools

Tools can import and reuse this module to generate speech. Example sketch for a tool class (add under `tools/` later):

```python
from tts import TTSClient, TTSConfig

class TextToSpeechTool:
    schema = {
        "name": "text_to_speech",
        "description": "Generate speech audio from text and save to a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "file_path": {"type": "string"},
                "model": {"type": "string"},
                "voice": {"type": "string"},
                "instructions": {"type": "string"},
                "audio_format": {"type": "string", "enum": ["mp3", "wav"]},
            },
            "required": ["text", "file_path"],
        },
    }

    def __init__(self):
        self.tts = TTSClient(TTSConfig())

    def run(self, text, file_path=None, model=None, voice=None, instructions=None, audio_format=None):
        path = self.tts.synthesize_to_file(
            input_text=text,
            file_path=file_path,  # If None, defaults to tts/generated
            model=model,
            voice=voice,
            instructions=instructions,
            audio_format=audio_format,
        )
        return {"ok": True, "file_path": path}
```

This lets the agent call the tool with custom model/voice/instructions per request while still benefiting from module defaults.
