---
name: nano-banana-pro
description: Generate or edit images via OpenRouter Nano Banana 2.
homepage: https://openrouter.ai/
metadata:
  {
    "openclaw":
      {
        "emoji": "🍌",
        "requires": { "bins": ["python"], "env": ["OPENROUTER_API_KEY"] },
        "primaryEnv": "OPENROUTER_API_KEY"
      }
  }
---

# Nano Banana 2 via OpenRouter

Use the bundled script to generate or edit images.

Agent startup checklist (run this before generation/editing every time)

1. Check whether `OPENROUTER_API_KEY` is already present in environment variables.
2. If not present, check whether `{baseDir}/.env` exists.
3. If `{baseDir}/.env` does not exist, copy `{baseDir}/.env.example` to `{baseDir}/.env`.
4. Remind the user to fill `OPENROUTER_API_KEY` in `{baseDir}/.env`.
5. Provide the exact file path `{baseDir}/.env` to the user so they can edit it.

Generate

```bash
python {baseDir}/scripts/generate_image.py --prompt "your image description" --filename "output.png" --resolution 1K
```

Edit (single image)

```bash
python {baseDir}/scripts/generate_image.py --prompt "edit instructions" --filename "output.png" -i "/path/in.png" --resolution 2K
```

Multi-image composition (up to 14 images)

```bash
python {baseDir}/scripts/generate_image.py --prompt "combine these into one scene" --filename "output.png" -i img1.png -i img2.png -i img3.png
```

API key

- `OPENROUTER_API_KEY` env var
- `.env` file at `{baseDir}/.env` (auto-loaded by the script)
- Or set `skills."nano-banana-pro".apiKey` / `skills."nano-banana-pro".env.OPENROUTER_API_KEY` in `~/.openclaw/openclaw.json`

Notes

- Model default: `google/gemini-3.1-flash-image-preview` (Nano Banana 2).
- If no key is found and `{baseDir}/.env.example` exists, the script auto-creates `{baseDir}/.env` and asks the user to fill it.
- Resolutions: `0.5K`, `1K` (default), `2K`, `4K`.
- Use timestamps in filenames: `yyyy-mm-dd-hh-mm-ss-name.png`.
- The script prints a `MEDIA:` line for OpenClaw to auto-attach on supported chat providers.
- Do not read the image back; report the saved path only.
