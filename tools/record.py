"""Record the Phase 1 verification scenarios and serve them to the browser viewer.

    python -m tools.record            # regenerate client/recordings/
    python -m tools.record --serve    # regenerate, then open the viewer

The generated recordings are build artifacts and are gitignored. Serving is a
plain stdlib static server; in Phase 2 aiohttp takes over this job.
"""

import argparse
import functools
import http.server
import json
import socketserver
import webbrowser
from pathlib import Path

from server.config import TICK_RATE, WORLD_HEIGHT, WORLD_WIDTH
from tools.scenarios import SCENARIOS, Scenario

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "client" / "recordings"
VIEWER_PATH = "/client/viewer.html"


def record(scenario: Scenario) -> dict:
    recorder = scenario.build()
    return {
        "id": scenario.id,
        "title": scenario.title,
        "checklist": scenario.checklist,
        "expect": scenario.expect,
        "view": list(scenario.view),
        "speed": scenario.speed,
        "world": {"width": WORLD_WIDTH, "height": WORLD_HEIGHT},
        "tickRate": TICK_RATE,
        "frames": recorder.frames,
    }


def write_all() -> list[tuple[str, int, int]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    written = []

    for scenario in SCENARIOS:
        recording = record(scenario)
        path = OUTPUT_DIR / f"{scenario.id}.json"
        payload = json.dumps(recording, separators=(",", ":"))
        path.write_text(payload, encoding="utf-8")

        frames = recording["frames"]
        index.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "checklist": scenario.checklist,
                "expect": scenario.expect,
                "speed": scenario.speed,
                "tags": scenario.tags,
                "duration": frames[-1]["t"] if frames else 0.0,
                "frameCount": len(frames),
            }
        )
        written.append((scenario.id, len(frames), len(payload)))

    (OUTPUT_DIR / "index.json").write_text(
        json.dumps({"scenarios": index}, indent=2), encoding="utf-8"
    )
    return written


def serve(port: int) -> None:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(PROJECT_ROOT)
    )
    # Without this the port lingers in TIME_WAIT and a quick re-run fails.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}{VIEWER_PATH}"
        print(f"\nserving {PROJECT_ROOT} at {url}")
        print("Ctrl+C to stop")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve", action="store_true", help="serve the viewer and open a browser"
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    written = write_all()
    total = sum(size for _, _, size in written)
    for scenario_id, frames, size in written:
        print(f"  {scenario_id:<24} {frames:>5} frames  {size / 1024:>8.1f} KB")
    print(f"\n{len(written)} recordings, {total / 1024:.0f} KB total -> {OUTPUT_DIR}")

    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
