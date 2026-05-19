# src/services

In-process services (thin integration layer).

This folder contains **small, UI-free service wrappers** around external APIs that the app uses.
They are intentionally lightweight and can be imported by tools, storage ingestion, or runners without creating UI/runtime import cycles.

## Design rules
- **No Qt / UI imports**.
- Prefer **simple, synchronous** APIs.
- Keep secrets out of code: tokens are loaded from storage keychain helpers.
- Avoid importing `appcore.Runtime` here (prevents import cycles).

## What lives here
- `transcribe.py` — `TranscribeService` using OpenAI transcription (Whisper-like):
  - `transcribe_file(path, language=None)`
  - `transcribe_bytes(audio_bytes, filename=..., language=None)`
- `tts.py` — `TTSService` using OpenAI TTS:
  - `synthesize_to_file(text, file_path=None, voice=None, instructions=None)`
  - `synthesize_bytes(text, voice=None, instructions=None)`
  - writes default output under `<app_data_dir>/tts/`
- `confluence.py` — Confluence mechanics (single owner):
  - base URL normalization + inference
  - pageId extraction
  - secret naming + token retrieval (keychain)
  - page fetch + HTML→Markdown conversion

## How it connects to the app
### Bus layer
UI actions do not call these modules directly.
Instead, bus handlers call app services/tools, which may call these services.

### Transcription / TTS
- The UI/handlers typically talk to the audio buses:
  - `src/app_handlers/bus_transcribe.py`
  - `src/app_handlers/bus_tts.py`
- Those handlers create or reuse `TranscribeService` / `TTSService` and return results over the bus.

### Confluence
Confluence is designed as a “single owner” module so URL/token logic isn’t duplicated.
Consumers include:
- `src/tools/confluence.py` — `search_confluence` tool
- `src/storage/vectordb.py` — document ingestion (optionally downloads attachments)
- `src/app_services/settings_helpers.py` — token indexing + secret key naming

## Notes / gotchas
- `confluence.py` intentionally avoids importing `appcore.Runtime` to prevent import cycles
  (`Runtime` imports VectorDBManager).
- `tts.py` imports `get_app_data_dir` from storage to choose an output folder.

## Where to start
- Want to understand audio flows → start at `bus_transcribe.py` / `bus_tts.py`, then follow into `TranscribeService` / `TTSService`.
- Want to understand Confluence ingestion → start at `storage/vectordb.py`, then follow into `services/confluence.py`.
