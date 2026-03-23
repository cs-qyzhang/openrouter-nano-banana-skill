#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Generate or edit images using OpenRouter + Nano Banana 2.

Usage:
    python generate_image.py --prompt "your image description" --filename "output.png" [--resolution 1K|2K|4K] [--api-key KEY]

Multi-image editing (up to 14 images):
    python generate_image.py --prompt "combine these images" --filename "output.png" -i img1.png -i img2.png -i img3.png
"""

import argparse
import base64
import shutil
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"
SUPPORTED_INPUT_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
API_KEY_ENV_NAMES = ("OPENROUTER_API_KEY", "GEMINI_API_KEY")


def get_api_key(provided_key: str | None) -> str | None:
    """Get API key from argument first, then environment variables."""
    if provided_key:
        return provided_key
    for env_name in API_KEY_ENV_NAMES:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_dotenv_candidates() -> list[Path]:
    project_root = get_project_root()
    cwd = Path.cwd().resolve()
    candidates = [cwd / ".env"]
    if cwd != project_root:
        candidates.append(project_root / ".env")
    return candidates


def parse_dotenv_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].strip()
    if "=" not in line:
        return None

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None

    if len(value) >= 2 and (
        (value[0] == '"' and value[-1] == '"')
        or (value[0] == "'" and value[-1] == "'")
    ):
        value = value[1:-1]
    return key, value


def load_dotenv_file(dotenv_path: Path) -> bool:
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return False
    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_dotenv_line(raw_line)
            if not parsed:
                continue
            key, value = parsed
            # Keep explicit shell env higher priority than .env values.
            os.environ.setdefault(key, value)
        return True
    except Exception as e:
        print(f"Warning: Failed to read .env file '{dotenv_path}': {e}", file=sys.stderr)
        return False


def load_dotenv_if_present() -> list[Path]:
    loaded_paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in get_dotenv_candidates():
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if load_dotenv_file(resolved):
            loaded_paths.append(resolved)
    return loaded_paths


def ensure_env_file_from_example() -> Path | None:
    project_root = get_project_root()
    env_path = project_root / ".env"
    example_path = project_root / ".env.example"
    if env_path.exists() or not example_path.exists():
        return None
    shutil.copyfile(example_path, env_path)
    return env_path.resolve()


def detect_mime_type(path: Path, data: bytes) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed in SUPPORTED_INPUT_MIME_TYPES:
        return guessed

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"

    return "application/octet-stream"


def get_image_dimensions(path: Path, data: bytes, mime_type: str) -> tuple[int, int] | None:
    """Return (width, height) when detectable from image headers."""
    try:
        if mime_type == "image/png" and len(data) >= 24:
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return (width, height)

        if mime_type == "image/gif" and len(data) >= 10:
            width = int.from_bytes(data[6:8], "little")
            height = int.from_bytes(data[8:10], "little")
            return (width, height)

        if mime_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
            i = 2
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            }
            while i < len(data):
                while i < len(data) and data[i] == 0xFF:
                    i += 1
                if i >= len(data):
                    break
                marker = data[i]
                i += 1

                if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                    continue
                if marker == 0xDA:
                    break
                if i + 2 > len(data):
                    break

                segment_length = int.from_bytes(data[i:i + 2], "big")
                if segment_length < 2 or i + segment_length > len(data):
                    break
                if marker in sof_markers and segment_length >= 7:
                    height = int.from_bytes(data[i + 3:i + 5], "big")
                    width = int.from_bytes(data[i + 5:i + 7], "big")
                    return (width, height)
                i += segment_length
    except Exception:
        return None

    return None


def image_path_to_data_url(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    mime_type = detect_mime_type(path, data)
    if mime_type not in SUPPORTED_INPUT_MIME_TYPES:
        raise ValueError(
            f"Unsupported image type for '{path}'. "
            f"Supported types: {', '.join(sorted(SUPPORTED_INPUT_MIME_TYPES))}"
        )

    dims = get_image_dimensions(path, data, mime_type)
    max_dim = max(dims) if dims else 0
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", max_dim


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    header, _, payload = data_url.partition(",")
    if not payload:
        raise ValueError("Malformed data URL: missing payload.")

    mime_type = "application/octet-stream"
    if ";" in header:
        mime_type = header[5:].split(";")[0] or mime_type
    elif len(header) > 5:
        mime_type = header[5:] or mime_type

    if ";base64" in header:
        image_bytes = base64.b64decode(payload)
    else:
        image_bytes = urllib.parse.unquote_to_bytes(payload)
    return mime_type, image_bytes


def read_image_reference(image_ref: str) -> tuple[str, bytes]:
    if image_ref.startswith("data:"):
        return decode_data_url(image_ref)
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        req = urllib.request.Request(image_ref, method="GET")
        with urllib.request.urlopen(req, timeout=300) as response:
            mime_type = response.headers.get_content_type() or "application/octet-stream"
            return mime_type, response.read()
    raise ValueError("Unsupported image reference in response (expected data: or https:// URL).")


def make_openrouter_request(api_key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        OPENROUTER_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        message = body
        try:
            err = json.loads(body)
            message = err.get("error", {}).get("message", body)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {message}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


def extract_output_text(message: dict) -> list[str]:
    texts: list[str] = []
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        texts.append(content.strip())
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    return texts


def extract_output_images(message: dict) -> list[str]:
    image_urls: list[str] = []

    images = message.get("images")
    if isinstance(images, list):
        for image in images:
            if not isinstance(image, dict):
                continue
            image_url_obj = image.get("image_url") or image.get("imageUrl")
            if isinstance(image_url_obj, dict):
                url = image_url_obj.get("url")
                if isinstance(url, str) and url:
                    image_urls.append(url)

    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "image_url":
                continue
            image_url_obj = part.get("image_url") or part.get("imageUrl")
            if isinstance(image_url_obj, dict):
                url = image_url_obj.get("url")
                if isinstance(url, str) and url:
                    image_urls.append(url)

    return image_urls


def main():
    parser = argparse.ArgumentParser(
        description="Generate or edit images using OpenRouter (Nano Banana 2)"
    )
    parser.add_argument(
        "--prompt", "-p",
        required=True,
        help="Image description/prompt"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="~/generated-image",
        help="Output directory (default: ~/generated-image)"
    )
    parser.add_argument(
        "--filename", "-f",
        required=True,
        help="Output filename (e.g., sunset-mountains.png)"
    )
    parser.add_argument(
        "--input-image", "-i",
        action="append",
        dest="input_images",
        metavar="IMAGE",
        help="Input image path(s) for editing/composition. Can be specified multiple times (up to 14 images)."
    )
    parser.add_argument(
        "--resolution", "-r",
        choices=["1K", "2K", "4K"],
        default="1K",
        help="Output resolution: 1K (default), 2K, or 4K"
    )
    parser.add_argument(
        "--aspect-ratio", "-a",
        choices=["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1"],
        default=None,
        help="Aspect ratio (1:1 default if not specified). Extended ratios (1:4, 4:1, 1:8, 8:1) only supported by google/gemini-3.1-flash-image-preview"
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model ID (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--api-key", "-k",
        help="OpenRouter API key (overrides OPENROUTER_API_KEY env var)"
    )

    args = parser.parse_args()

    loaded_dotenv_paths = load_dotenv_if_present()

    # Get API key
    api_key = get_api_key(args.api_key)
    if not api_key:
        created_env_path = ensure_env_file_from_example()
        env_candidates = [str(path) for path in get_dotenv_candidates()]
        print("Error: No API key provided.", file=sys.stderr)
        if loaded_dotenv_paths:
            print(
                "Loaded .env file(s) but no API key was found in OPENROUTER_API_KEY or GEMINI_API_KEY:",
                file=sys.stderr,
            )
            for path in loaded_dotenv_paths:
                print(f"  - {path}", file=sys.stderr)
        else:
            print("No .env file was loaded. Checked:", file=sys.stderr)
            for path in env_candidates:
                print(f"  - {path}", file=sys.stderr)
        if created_env_path:
            print(f"Created .env from .env.example: {created_env_path}", file=sys.stderr)
            print("Please fill OPENROUTER_API_KEY in that .env file and retry.", file=sys.stderr)
        else:
            print("Please either:", file=sys.stderr)
            print("  1. Provide --api-key argument", file=sys.stderr)
            print("  2. Set OPENROUTER_API_KEY environment variable", file=sys.stderr)
            print("  3. Put OPENROUTER_API_KEY in a .env file", file=sys.stderr)
            print("  4. (Compatibility) Set GEMINI_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    # Set up output path
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / args.filename

    # Load input images if provided (up to 14 for Nano Banana 2)
    input_data_urls: list[str] = []
    output_resolution = args.resolution
    if args.input_images:
        if len(args.input_images) > 14:
            print(f"Error: Too many input images ({len(args.input_images)}). Maximum is 14.", file=sys.stderr)
            sys.exit(1)

        max_input_dim = 0
        for img_path in args.input_images:
            try:
                path = Path(img_path)
                if not path.exists() or not path.is_file():
                    raise ValueError("file not found")

                data_url, detected_max_dim = image_path_to_data_url(path)
                input_data_urls.append(data_url)
                if detected_max_dim > 0:
                    max_input_dim = max(max_input_dim, detected_max_dim)
                print(f"Loaded input image: {path}")
            except Exception as e:
                print(f"Error loading input image '{img_path}': {e}", file=sys.stderr)
                sys.exit(1)

        # Auto-detect resolution from largest input if not explicitly set
        if args.resolution == "1K" and max_input_dim > 0:  # Default value
            if max_input_dim >= 3000:
                output_resolution = "4K"
            elif max_input_dim >= 1500:
                output_resolution = "2K"
            else:
                output_resolution = "1K"
            print(f"Auto-detected resolution: {output_resolution} (from max input dimension {max_input_dim})")

    # Build OpenRouter request payload
    if input_data_urls:
        user_content = [{"type": "text", "text": args.prompt}]
        for data_url in input_data_urls:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )
        img_count = len(input_data_urls)
        print(f"Processing {img_count} image{'s' if img_count > 1 else ''} with resolution {output_resolution}...")
    else:
        user_content = args.prompt
        print(f"Generating image with resolution {output_resolution}...")

    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "modalities": ["image", "text"],
        "stream": False,
    }

    # Build image_config if any image options are specified
    image_config = {}
    if output_resolution in ("1K", "2K", "4K"):
        image_config["image_size"] = output_resolution
    if args.aspect_ratio:
        image_config["aspect_ratio"] = args.aspect_ratio
    if image_config:
        payload["image_config"] = image_config

    try:
        response = make_openrouter_request(api_key=api_key, payload=payload)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("No choices returned by OpenRouter.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError("Malformed OpenRouter response: choice is not an object.")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Malformed OpenRouter response: missing assistant message.")

        for text in extract_output_text(message):
            print(f"Model response: {text}")

        image_urls = extract_output_images(message)
        if not image_urls:
            print("Error: No image was generated in the response.", file=sys.stderr)
            sys.exit(1)
        if len(image_urls) > 1:
            print(f"Received {len(image_urls)} images. Saving the first image to: {output_path}")

        mime_type, image_data = read_image_reference(image_urls[0])
        output_path.write_bytes(image_data)

        full_path = output_path.resolve()
        print(f"\nImage saved: {full_path} ({mime_type})")
        # OpenClaw parses MEDIA tokens and will attach the file on supported providers.
        print(f"MEDIA: {full_path}")

    except Exception as e:
        print(f"Error generating image: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
