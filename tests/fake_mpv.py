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
    # ``playlist``: lo añadido con ``append`` tras ``current`` (entrada -> URL).
    state = {"sock": None, "entry": 0, "buf": b"", "observed": {}, "binds": {},
             "current": None, "playlist": [],
             # Propiedades que se pueden leer, salidas de audio y órdenes a filtros.
             "props": {"audio-device": "auto", "af": "", "volume": 100},
             "devices": [{"name": "auto", "description": "Autoselect device"},
                         {"name": "wasapi/{altavoces}", "description": "Altavoces (Realtek)"},
                         {"name": "wasapi/{cascos}", "description": "Auriculares (USB)"}],
             "af_commands": []}

    def send(obj):
        state["sock"].write(json.dumps(obj).encode() + b"\n")
        state["sock"].flush()

    def prop(name, value):
        if name in state["observed"]:
            send({"event": "property-change", "id": state["observed"][name], "name": name, "data": value})

    def start(entry, url):
        """Empieza una entrada, como mpv: start-file y, si abre, file-loaded."""
        state["current"] = entry
        send({"event": "start-file", "playlist_entry_id": entry})
        if "fail" in url:
            state["current"] = None
            send({"event": "end-file", "reason": "error", "playlist_entry_id": entry,
                  "file_error": "loading failed"})
            return False
        send({"event": "file-loaded"})
        prop("duration", 100.0)
        prop("time-pos", 0.5)
        return True

    def handle(msg):
        cmd = msg.get("command") or []
        if cmd[0] == "loadfile":
            # Como mpv >= 0.36: la respuesta trae la entrada, antes que start-file.
            if "crash" in cmd[1]:
                os._exit(3)
            state["entry"] += 1
            send({"error": "success", "request_id": msg.get("request_id"),
                  "data": {"playlist_entry_id": state["entry"]}})
        elif cmd[0] == "get_property":
            data = {"audio-device-list": state["devices"], "ytmn-af-commands": state["af_commands"],
                    "ytmn-pid": os.getpid()}.get(cmd[1], state["props"].get(cmd[1]))
            send({"error": "success", "request_id": msg.get("request_id"), "data": data})
        else:
            send({"error": "success", "request_id": msg.get("request_id")})
        if cmd[0] == "ytmn-test-drop":
            # Solo pruebas: corta la tubería sin terminar, como un fallo del IPC.
            sock, state["sock"] = state["sock"], None
            state["observed"] = {}
            sock.disconnectFromServer()
        elif cmd[0] == "observe_property":
            state["observed"][cmd[2]] = cmd[1]
            if cmd[2] == "audio-device-list":
                prop("audio-device-list", state["devices"])     # como mpv: el valor de entrada
        elif cmd[0] == "ytmn-test-devices":
            # Solo pruebas: se conecta o desconecta una salida de audio.
            state["devices"] = json.loads(cmd[1])
            prop("audio-device-list", state["devices"])
        elif cmd[0] == "af-command":
            state["af_commands"].append(cmd[1:])
        elif cmd[0] == "set_property" and cmd[1] != "pause":
            state["props"][cmd[1]] = cmd[2]
        elif cmd[0] == "keybind":
            state["binds"][cmd[1]] = cmd[2]
        elif cmd[0] == "keypress":
            bound = state["binds"].get(cmd[1], "")
            if bound.startswith("script-message "):
                send({"event": "client-message", "args": bound.split()[1:]})
            elif bound == "cycle pause":
                prop("pause", True)
        elif cmd[0] == "loadfile" and len(cmd) > 2 and cmd[2] == "append" and state["current"] is not None:
            # Encadenada: espera a que acabe la que suena.
            state["playlist"].append((state["entry"], cmd[1]))
        elif cmd[0] == "loadfile":
            url = cmd[1]
            entry = state["entry"]
            state["playlist"] = []
            if not start(entry, url):
                return
            if "auto" in url:
                prop("time-pos", 99.8)
                state["current"] = None
                send({"event": "end-file", "reason": "eof", "playlist_entry_id": entry})
        elif cmd[0] == "playlist-clear":
            state["playlist"] = []
        elif cmd[0] == "ytmn-test-eof":
            # Solo pruebas: la que suena llega al final y, como mpv, sigue con
            # lo encadenado (end-file, luego start-file de la siguiente).
            ended, state["current"] = state["current"], None
            prop("time-pos", 99.8)
            send({"event": "end-file", "reason": "eof", "playlist_entry_id": ended})
            if state["playlist"]:
                start(*state["playlist"].pop(0))
        elif cmd[0] == "seek":
            prop("time-pos", float(cmd[1]))
        elif cmd[0] == "set_property" and cmd[1] == "pause":
            prop("pause", bool(cmd[2]))
        elif cmd[0] == "stop":
            state["playlist"], state["current"] = [], None
            send({"event": "end-file", "reason": "stop", "playlist_entry_id": state["entry"]})
        elif cmd[0] == "quit":
            app.quit()

    def on_ready():
        if state["sock"] is None:
            return
        state["buf"] += bytes(state["sock"].readAll())
        while b"\n" in state["buf"] and state["sock"] is not None:
            line, state["buf"] = state["buf"].split(b"\n", 1)
            if line.strip():
                handle(json.loads(line))

    def on_connection():
        state["sock"] = server.nextPendingConnection()
        state["buf"] = b""
        state["sock"].readyRead.connect(on_ready)
        on_ready()

    server.newConnection.connect(on_connection)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
