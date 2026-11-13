"""mpv simulado para las pruebas: habla el mismo IPC JSON por una tubería con nombre."""

import json
import os
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer


def main() -> int:
    pipe = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--input-ipc-server="))
    assert "--ytdl=no" in sys.argv and "--no-video" in sys.argv
    app = QCoreApplication(sys.argv)
    server = QLocalServer()
    QLocalServer.removeServer(pipe)
    if not server.listen(pipe):
        return 5
    state = {"sock": None, "entry": 0, "buf": b"", "observed": {}, "binds": {}}

    def send(obj):
        state["sock"].write(json.dumps(obj).encode() + b"\n")
        state["sock"].flush()

    def prop(name, value):
        if name in state["observed"]:
            send({"event": "property-change", "id": state["observed"][name], "name": name, "data": value})

    def handle(msg):
        cmd = msg.get("command") or []
        send({"error": "success", "request_id": msg.get("request_id")})
        if cmd[0] == "observe_property":
            state["observed"][cmd[2]] = cmd[1]
        elif cmd[0] == "keybind":
            state["binds"][cmd[1]] = cmd[2]
        elif cmd[0] == "keypress":
            bound = state["binds"].get(cmd[1], "")
            if bound.startswith("script-message "):
                send({"event": "client-message", "args": bound.split()[1:]})
            elif bound == "cycle pause":
                prop("pause", True)
        elif cmd[0] == "loadfile":
            url = cmd[1]
            if "crash" in url:
                os._exit(3)
            state["entry"] += 1
            entry = state["entry"]
            send({"event": "start-file", "playlist_entry_id": entry})
            if "fail" in url:
                send({"event": "end-file", "reason": "error", "playlist_entry_id": entry,
                      "file_error": "loading failed"})
                return
            send({"event": "file-loaded"})
            prop("duration", 100.0)
            prop("time-pos", 0.5)
            if "auto" in url:
                prop("time-pos", 99.8)
                send({"event": "end-file", "reason": "eof", "playlist_entry_id": entry})
        elif cmd[0] == "seek":
            prop("time-pos", float(cmd[1]))
        elif cmd[0] == "set_property" and cmd[1] == "pause":
            prop("pause", bool(cmd[2]))
        elif cmd[0] == "stop":
            send({"event": "end-file", "reason": "stop", "playlist_entry_id": state["entry"]})
        elif cmd[0] == "quit":
            app.quit()

    def on_ready():
        state["buf"] += bytes(state["sock"].readAll())
        while b"\n" in state["buf"]:
            line, state["buf"] = state["buf"].split(b"\n", 1)
            if line.strip():
                handle(json.loads(line))

    def on_connection():
        state["sock"] = server.nextPendingConnection()
        state["sock"].readyRead.connect(on_ready)
        on_ready()

    server.newConnection.connect(on_connection)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
