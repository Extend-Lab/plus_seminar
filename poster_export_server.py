#!/usr/bin/env python3
import os
import subprocess
import tempfile
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
CSS_A4_WIDTH = 794
CAPTURE_WIDTH = 794
CAPTURE_HEIGHT = 1123
DEFAULT_EXPORT_WIDTH = 2480
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def find_chrome():
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("Google Chrome was not found in /Applications.")


def run_checked(command, timeout=45):
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(details or "export command failed")


def capture_png_with_chrome(command, output_path, timeout=45):
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + timeout
    last_size = -1
    stable_since = None

    while time.time() < deadline:
        if output_path.exists() and output_path.stat().st_size > 0:
            size = output_path.stat().st_size
            if size == last_size:
                if stable_since and time.time() - stable_since > 0.75:
                    break
                if stable_since is None:
                    stable_since = time.time()
            else:
                last_size = size
                stable_since = time.time()

        if process.poll() is not None:
            break

        time.sleep(0.2)
    else:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        if not output_path.exists():
            details = stderr.strip() or stdout.strip()
            raise TimeoutError(details or "Chrome screenshot timed out")
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
    else:
        stdout, stderr = process.communicate()
        if process.returncode != 0 and not output_path.exists():
            details = stderr.strip() or stdout.strip()
            raise RuntimeError(details or "Chrome screenshot failed")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Chrome did not create a screenshot")


class PosterExportHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/poster-export":
            self.export_poster(parsed.query)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def export_poster(self, query):
        try:
            params = parse_qs(query)
            image_format = params.get("format", ["png"])[0].lower()
            export_width = int(params.get("width", [str(DEFAULT_EXPORT_WIDTH)])[0])

            if image_format not in {"png", "jpeg"}:
                raise ValueError("format must be png or jpeg")
            if export_width < 800 or export_width > 5000:
                raise ValueError("width must be between 800 and 5000")

            export_height = round(export_width * 297 / 210)
            scale = export_width / CSS_A4_WIDTH
            chrome = find_chrome()

            with tempfile.TemporaryDirectory(prefix="plus-poster-export-") as temp_dir:
                temp_path = Path(temp_dir)
                screenshot_path = temp_path / "poster-full.png"
                png_path = temp_path / "poster.png"
                profile_path = temp_path / "chrome-profile"
                target_url = f"http://127.0.0.1:{self.server.server_port}/poster.html?export=1"
                crop_x = 0
                crop_y = 0

                capture_png_with_chrome([
                    chrome,
                    "--headless",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={profile_path}",
                    f"--window-size={CAPTURE_WIDTH},{CAPTURE_HEIGHT}",
                    f"--force-device-scale-factor={scale:.8f}",
                    f"--screenshot={screenshot_path}",
                    target_url,
                ], screenshot_path)

                run_checked([
                    "sips",
                    "-c",
                    str(export_height),
                    str(export_width),
                    "--cropOffset",
                    str(crop_y),
                    str(crop_x),
                    str(screenshot_path),
                    "--out",
                    str(png_path),
                ])

                output_path = png_path
                content_type = "image/png"
                extension = "png"

                if image_format == "jpeg":
                    jpg_path = temp_path / "poster.jpg"
                    run_checked([
                        "sips",
                        "-s",
                        "format",
                        "jpeg",
                        "-s",
                        "formatOptions",
                        "96",
                        str(png_path),
                        "--out",
                        str(jpg_path),
                    ])
                    output_path = jpg_path
                    content_type = "image/jpeg"
                    extension = "jpg"

                data = output_path.read_bytes()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="plus-seminar-2026-poster-{export_width}px.{extension}"',
            )
            self.end_headers()
            self.wfile.write(data)
            print(f"Exported {image_format.upper()} {export_width}x{export_height}")
        except Exception as error:
            message = str(error)
            data = message.encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"Export failed: {message}")


def main():
    os.chdir(ROOT)
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), PosterExportHandler)
    print(f"Serving PLUS poster at http://127.0.0.1:{port}/poster.html")
    print("Use Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
