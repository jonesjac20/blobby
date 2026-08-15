"""Bare WebSocket client for Phase 2 protocol checks. No rendering."""

from __future__ import annotations

import argparse
import asyncio
import json

from aiohttp import ClientSession, WSMsgType

from server.config import DEFAULT_COLOR, HOST, PORT


def _default_url() -> str:
    host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    return f"http://{host}:{PORT}/ws"


def _format_message(data: dict) -> str:
    kind = data.get("type")
    if kind == "state":
        parts = []
        for player in data.get("players", []):
            masses = ",".join(f"{piece['mass']:.0f}" for piece in player.get("pieces", []))
            parts.append(f"{player.get('name', '?')}[{masses}]")
        roster = " ".join(parts) if parts else "-"
        return f"state players={len(data.get('players', []))} {roster}"
    if kind == "food":
        return f"food n={len(data.get('food', []))} version={data.get('version')}"
    if kind == "welcome":
        return f"welcome id={data.get('id')}"
    if kind == "game_over":
        return (
            f"game_over peak_mass={data.get('peak_mass')} "
            f"survival_seconds={data.get('survival_seconds')}"
        )
    return json.dumps(data)


async def run(url: str, name: str, color: str, spectate: bool) -> None:
    async with ClientSession() as http:
        async with http.ws_connect(url, heartbeat=20.0) as ws:
            if not spectate:
                await ws.send_json({"type": "join", "name": name, "color": color})
            states = 0
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                        break
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    print(msg.data)
                    continue
                if not isinstance(data, dict):
                    print(msg.data)
                    continue
                print(_format_message(data))
                if spectate or data.get("type") != "state":
                    continue
                states += 1
                if states % 15 == 0:
                    await ws.send_json({"type": "input", "dx": 1.0, "dy": 0.0})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=_default_url())
    parser.add_argument("--name", default="probe")
    parser.add_argument("--color", default=DEFAULT_COLOR)
    parser.add_argument(
        "--spectate",
        action="store_true",
        help="Receive state and never send join.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, args.name, args.color, args.spectate))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
