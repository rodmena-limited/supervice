import argparse
import asyncio
import json
import struct
import sys
from typing import Any
HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024

def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)

class Controller:
    def __init__(self, socket_path: str = "/tmp/supervice.sock"):
        self.socket_path = socket_path

    async def _read_message(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a length-prefixed message from the stream."""
        header = await reader.readexactly(HEADER_SIZE)
        if not header:
            return None

        msg_length = struct.unpack(">I", header)[0]

        if msg_length > MAX_MESSAGE_SIZE:
            raise ValueError("Message too large: %d bytes" % msg_length)

        if msg_length == 0:
            return b""

        return await reader.readexactly(msg_length)

    async def _write_message(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        """Write a length-prefixed message to the stream."""
        header = struct.pack(">I", len(data))
        writer.write(header + data)
        await writer.drain()

    async def send_command(self, command: str, **kwargs: Any) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)

        try:
            request = {"command": command, **kwargs}
            await self._write_message(writer, json.dumps(request).encode("utf-8"))

            data = await self._read_message(reader)

            if data is None:
                return {"status": "error", "message": "Empty response"}

            return json.loads(data.decode("utf-8"))  # type: ignore
        finally:
            writer.close()
            await writer.wait_closed()

    async def status(self) -> bool:
        try:
            response = await self.send_command("status")
            if response.get("status") == "ok":
                processes = response.get("processes", [])
                has_health = any("healthy" in p for p in processes)
                has_uptime = any("uptime" in p for p in processes)

                header = f"{'NAME':<20} {'STATE':<10} {'PID':<10}"
                sep_len = 40
                if has_uptime:
                    header += f" {'UPTIME':<12}"
                    sep_len += 12
                if has_health:
                    header += f" {'HEALTH':<10}"
                    sep_len += 10
                print(header)
                print("-" * sep_len)

                for proc in processes:
                    pid = proc.get("pid") or "-"
                    line = f"{proc['name']:<20} {proc['state']:<10} {pid:<10}"
                    if has_uptime:
                        uptime = proc.get("uptime")
                        line += f" {_format_uptime(uptime):<12}"
                    if has_health:
                        health = proc.get("healthy")
                        if health is None:
                            health_str = "-"
                        elif health:
                            health_str = "OK"
                        else:
                            health_str = "FAIL"
                        line += f" {health_str:<10}"
                    print(line)
                return True
            else:
                print("Error:", response.get("message"))
                return False
        except FileNotFoundError:
            print("Supervice is not running (socket not found)")
            return False
        except Exception as e:
            print("Error connecting to supervice:", e)
            return False

    async def start_process(self, name: str) -> bool:
        try:
            response = await self.send_command("start", name=name)
            print(response.get("message"))
            return response.get("status") == "ok"
        except Exception as e:
            print("Error: %s" % e)
            return False

    async def stop_process(self, name: str) -> bool:
        try:
            response = await self.send_command("stop", name=name)
            print(response.get("message"))
            return response.get("status") == "ok"
        except Exception as e:
            print("Error: %s" % e)
            return False
