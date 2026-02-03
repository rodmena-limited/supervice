import argparse
import asyncio
import json
import struct
import sys
from typing import Any

HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024


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

    async def restart_process(self, name: str, force: bool = False) -> bool:
        try:
            response = await self.send_command("restart", name=name, force=force)
            print(response.get("message"))
            return response.get("status") == "ok"
        except Exception as e:
            print("Error: %s" % e)
            return False

    async def start_group(self, name: str) -> bool:
        try:
            response = await self.send_command("startgroup", name=name)
            print(response.get("message"))
            return response.get("status") == "ok"
        except Exception as e:
            print("Error: %s" % e)
            return False

    async def stop_group(self, name: str) -> bool:
        try:
            response = await self.send_command("stopgroup", name=name)
            print(response.get("message"))
            return response.get("status") == "ok"
        except Exception as e:
            print("Error: %s" % e)
            return False

    async def reload(self) -> bool:
        try:
            response = await self.send_command("reload")
            if response.get("status") == "ok":
                added = response.get("added", [])
                removed = response.get("removed", [])
                changed = response.get("changed", [])
                if added:
                    print("Added: %s" % ", ".join(added))
                if removed:
                    print("Removed: %s" % ", ".join(removed))
                if changed:
                    print("Changed (restart to apply): %s" % ", ".join(changed))
                if not added and not removed and not changed:
                    print("No changes detected")
                return True
            else:
                print("Error:", response.get("message"))
                return False
        except FileNotFoundError:
            print("Supervice is not running (socket not found)")
            return False
        except Exception as e:
            print("Error: %s" % e)
            return False


def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="supervicectl",
        description="Supervice process control client",
    )
    parser.add_argument(
        "-s",
        "--socket",
        default="/tmp/supervice.sock",
        help="Path to supervice Unix socket (default: /tmp/supervice.sock)",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show process status")

    start_p = subparsers.add_parser("start", help="Start a process")
    start_p.add_argument("name", help="Process name")

    stop_p = subparsers.add_parser("stop", help="Stop a process")
    stop_p.add_argument("name", help="Process name")

    restart_p = subparsers.add_parser("restart", help="Restart a process")
    restart_p.add_argument("name", help="Process name")
    restart_p.add_argument(
        "--force",
        action="store_true",
        help="Force restart with SIGKILL instead of graceful stop",
    )

    startgroup_p = subparsers.add_parser("startgroup", help="Start a process group")
    startgroup_p.add_argument("name", help="Group name")

    stopgroup_p = subparsers.add_parser("stopgroup", help="Stop a process group")
    stopgroup_p.add_argument("name", help="Group name")

    subparsers.add_parser("reload", help="Reload configuration")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = Controller(socket_path=args.socket)

    success = False
    if args.command == "status":
        success = asyncio.run(client.status())
    elif args.command == "start":
        success = asyncio.run(client.start_process(args.name))
    elif args.command == "stop":
        success = asyncio.run(client.stop_process(args.name))
    elif args.command == "restart":
        success = asyncio.run(client.restart_process(args.name, force=args.force))
    elif args.command == "startgroup":
        success = asyncio.run(client.start_group(args.name))
    elif args.command == "stopgroup":
        success = asyncio.run(client.stop_group(args.name))
    elif args.command == "reload":
        success = asyncio.run(client.reload())

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
