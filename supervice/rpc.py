import asyncio
import errno
import json
import os
import stat
import struct
import time
from typing import Any

from supervice.logger import get_logger
from supervice.process import ProcessStartError

# Length-prefixed protocol constants
HEADER_SIZE = 4  # 4 bytes for message length (uint32, big-endian)
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB max message size

# States in which a process is considered "down" after a stop request.
_DOWN_STATES = ("STOPPED", "EXITED", "FATAL")

# Valid RPC commands
VALID_COMMANDS = frozenset(
    {
        "status",
        "start",
        "stop",
        "restart",
        "startgroup",
        "stopgroup",
        "reload",
    }
)


class RPCServer:
    def __init__(self, socket_path: str, supervisor: Any):
        self.socket_path = socket_path
        self.supervisor = supervisor
        self.logger = get_logger()
        self.server: asyncio.AbstractServer | None = None

    async def _probe_socket(self) -> str:
        """Classify an existing socket path: 'alive', 'stale', or 'unknown'.

        Only a definitively dead socket (nothing accepting connections) is
        'stale'. Anything that accepts a connection but does not speak our
        protocol — or cannot be judged — is 'unknown' and must not be removed:
        stealing a busy instance's socket silently splits the daemons.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path), timeout=2.0
            )
        except asyncio.TimeoutError:
            return "unknown"
        except OSError as e:
            if e.errno in (errno.ECONNREFUSED, errno.ENOENT, errno.ENOTSOCK):
                return "stale"
            return "unknown"
        try:
            request = json.dumps({"command": "status"}).encode("utf-8")
            await self._write_message(writer, request)
            data = await asyncio.wait_for(self._read_message(reader), timeout=2.0)
            return "alive" if data is not None else "unknown"
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return "unknown"
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def start(self) -> None:
        sock_dir = os.path.dirname(self.socket_path) or "."
        try:
            dir_mode = os.stat(sock_dir).st_mode
            if dir_mode & stat.S_IWOTH:
                self.logger.warning(
                    "Socket directory %s is world-writable; another local user could "
                    "pre-create %s to block startup or impersonate the daemon. "
                    "Configure 'socket' to a private directory.",
                    sock_dir,
                    self.socket_path,
                )
        except OSError:
            pass

        if os.path.exists(self.socket_path):
            probe = await self._probe_socket()
            if probe == "alive":
                msg = "Another supervice instance is already listening on %s" % self.socket_path
                self.logger.critical(msg)
                raise RuntimeError(msg)
            if probe == "unknown":
                msg = (
                    "Socket %s exists but could not be verified as stale; refusing to "
                    "replace it. Remove it manually if no supervice instance is running."
                    % self.socket_path
                )
                self.logger.critical(msg)
                raise RuntimeError(msg)
            try:
                os.unlink(self.socket_path)
            except OSError as e:
                msg = "Cannot remove stale socket %s: %s" % (self.socket_path, e)
                self.logger.critical(msg)
                raise RuntimeError(msg) from e

        # Security: Set umask to create socket with restrictive permissions atomically
        # This prevents the race condition where socket is world-readable briefly
        old_umask = os.umask(0o177)  # Results in mode 0o600
        try:
            self.server = await asyncio.start_unix_server(self.handle_client, self.socket_path)
        finally:
            os.umask(old_umask)

        self.logger.info("RPC Server listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)

    async def _read_message(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a length-prefixed message from the stream."""
        header = await reader.readexactly(HEADER_SIZE)

        msg_length = struct.unpack(">I", header)[0]

        if msg_length > MAX_MESSAGE_SIZE:
            msg = "Message too large: %d bytes (max %d)" % (msg_length, MAX_MESSAGE_SIZE)
            raise ValueError(msg)

        if msg_length == 0:
            return b""

        # Read the message body
        return await reader.readexactly(msg_length)

    async def _write_message(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        """Write a length-prefixed message to the stream."""
        header = struct.pack(">I", len(data))
        writer.write(header + data)
        await writer.drain()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await self._read_message(reader)
            if data is None:
                return

            # A zero-length body is a valid frame but not valid JSON; report it
            # clearly instead of surfacing a confusing JSONDecodeError.
            if not data:
                response = {
                    "status": "error",
                    "code": "EMPTY_REQUEST",
                    "message": "Empty request",
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            # Parse JSON with proper error handling
            try:
                request = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as e:
                self.logger.warning("Invalid JSON in RPC request: %s", e)
                response = {
                    "status": "error",
                    "code": "INVALID_JSON",
                    "message": "Invalid JSON: %s" % e,
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            # Validate request structure
            if not isinstance(request, dict):
                response = {
                    "status": "error",
                    "code": "INVALID_REQUEST",
                    "message": "Request must be a JSON object",
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            # Validate command
            command = request.get("command")
            if command not in VALID_COMMANDS:
                self.logger.warning("Unknown RPC command: %s", command)
                response = {
                    "status": "error",
                    "code": "UNKNOWN_COMMAND",
                    "message": "Unknown command: %s" % command,
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            response = await self.process_request(request)
            await self._write_message(writer, json.dumps(response).encode("utf-8"))

        except asyncio.IncompleteReadError:
            # Client disconnected mid-message
            self.logger.debug("Client disconnected during read")
        except Exception as e:
            self.logger.error("RPC Error: %s", e)
            try:
                error_response = {"status": "error", "code": "INTERNAL_ERROR", "message": str(e)}
                await self._write_message(writer, json.dumps(error_response).encode("utf-8"))
            except Exception:
                pass  # Client may have disconnected
        finally:
            writer.close()
            await writer.wait_closed()

    async def _start_one(self, name: str) -> dict[str, Any]:
        """Start one process and report the true outcome."""
        proc = self.supervisor.processes[name]
        if proc.state == "RUNNING":
            return {"status": "ok", "message": "%s is already running" % name}
        try:
            final_state = await proc.start_process()
        except ProcessStartError as e:
            return {"status": "error", "message": str(e)}
        if final_state == "RUNNING":
            return {"status": "ok", "message": "Started %s" % name}
        return {
            "status": "error",
            "message": "%s did not reach RUNNING (state: %s)" % (name, final_state),
        }

    async def _stop_one(self, name: str) -> dict[str, Any]:
        """Stop one process and report the true outcome."""
        proc = self.supervisor.processes[name]
        final_state = await proc.stop_process()
        if final_state in _DOWN_STATES:
            return {"status": "ok", "message": "Stopped %s" % name}
        return {
            "status": "error",
            "message": "%s did not stop (state: %s)" % (name, final_state),
        }

    async def process_request(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")

        if command == "status":
            now = time.time()
            processes = []
            for name, proc in self.supervisor.processes.items():
                is_alive = proc.process and proc.process.returncode is None
                proc_info: dict[str, Any] = {
                    "name": name,
                    "state": proc.state,
                    "pid": proc.process.pid if is_alive else None,
                }
                if proc.started_at is not None and is_alive:
                    proc_info["uptime"] = max(0, int(now - proc.started_at))
                if proc.is_healthy is not None:
                    proc_info["healthy"] = proc.is_healthy
                processes.append(proc_info)
            return {"status": "ok", "processes": processes}

        elif command == "stop":
            name = request.get("name")
            if name and name in self.supervisor.processes:
                return await self._stop_one(name)
            return {"status": "error", "message": "Process not found"}

        elif command == "start":
            name = request.get("name")
            if name and name in self.supervisor.processes:
                return await self._start_one(name)
            return {"status": "error", "message": "Process not found"}

        elif command == "restart":
            name = request.get("name")
            force = request.get("force", False)
            if name and name in self.supervisor.processes:
                proc = self.supervisor.processes[name]
                if force:
                    await proc.force_kill()
                else:
                    stop_result = await self._stop_one(name)
                    if stop_result["status"] != "ok":
                        return stop_result
                start_result = await self._start_one(name)
                if start_result["status"] == "ok":
                    return {"status": "ok", "message": "Restarted %s" % name}
                return start_result
            return {"status": "error", "message": "Process not found"}

        elif command == "stopgroup":
            group = request.get("name")
            if group and group in self.supervisor.groups:
                names = [
                    n for n in self.supervisor.groups[group] if n in self.supervisor.processes
                ]
                results = await asyncio.gather(
                    *(self._stop_one(n) for n in names), return_exceptions=True
                )
                failed = [
                    n
                    for n, r in zip(names, results, strict=True)
                    if isinstance(r, BaseException) or r.get("status") != "ok"
                ]
                if failed:
                    return {
                        "status": "error",
                        "message": "Stopped group %s, but failed for: %s"
                        % (group, ", ".join(failed)),
                    }
                return {"status": "ok", "message": "Stopped group %s" % group}
            return {"status": "error", "message": "Group not found"}

        elif command == "startgroup":
            group = request.get("name")
            if group and group in self.supervisor.groups:
                names = [
                    n for n in self.supervisor.groups[group] if n in self.supervisor.processes
                ]
                results = await asyncio.gather(
                    *(self._start_one(n) for n in names), return_exceptions=True
                )
                failed = [
                    n
                    for n, r in zip(names, results, strict=True)
                    if isinstance(r, BaseException) or r.get("status") != "ok"
                ]
                if failed:
                    return {
                        "status": "error",
                        "message": "Started group %s, but failed for: %s"
                        % (group, ", ".join(failed)),
                    }
                return {"status": "ok", "message": "Started group %s" % group}
            return {"status": "error", "message": "Group not found"}

        elif command == "reload":
            try:
                result = await self.supervisor.reload_config()
                return {"status": "ok", **result}
            except Exception as e:
                return {"status": "error", "message": "Reload failed: %s" % e}

        return {"status": "error", "message": "Unknown command"}
