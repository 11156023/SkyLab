# AI Navigation Demo

This standalone demo shows how an AI-native navigation layer can guide a frontend.
The UI mimics the `frontend` dashboard shape: left sidebar, central page
preview, and a right-side AI chatbox.

Flow:

```text
User text
  -> Static frontend
  -> FastAPI /api/navigation/resolve
  -> vLLM chat completions, or keyword fallback
  -> Frontend shows navigation buttons or workflow buttons
```

## Purpose

This project is intentionally small. It does not implement real business features.
It demonstrates the idea that pages can be described as capabilities, then an AI
assistant can select the most relevant frontend destination from those capabilities.
If the user goal requires multiple pages, the backend returns a workflow.

## Run

```bash
cd ai-navigation-demo
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8010
```

Open:

```text
http://localhost:8010
```

## vLLM connection

The app expects an OpenAI-compatible vLLM endpoint.

The demo has its own local `.env` file:

```text
ai-navigation-demo/.env
```

Example:

```env
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_API_KEY=EMPTY
VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

If `VLLM_MODEL` is empty, or vLLM fails, the backend uses keyword fallback.

## Demo pages

The page catalog is in `app.py`.

Each page has:

- `key`
- `title`
- `summary`
- `keywords`
- `roles`
- `group`
- `stage`
- `page_type`
- `actions`

That is a lightweight version of a Page Capability Schema.

## UI behavior

- Left sidebar matches the broad `frontend` navigation groups.
- Center panel renders a rough mock shape for the selected page.
- Right AI panel is only a chatbox plus guide output.
- A single-page result renders a guide button.
- A multi-page result renders workflow step buttons.
- Clicking any guide/workflow button switches the central page preview.
