# ═══════════════════════════════════════════════════════════════════════════════
# Single instance — canal local (named pipe Windows) entre les lancements
#
# Principe : la première instance écoute sur un QLocalServer au nom fixe.
# Tout lancement ultérieur détecte le serveur, lui transmet son éventuel
# chemin de fichier (association Windows) puis se termine immédiatement.
#
# Handshake : le serveur se présente par une bannière fixe dès la connexion ;
# le client ne transmet son chemin (et ne quitte) qu'après l'avoir reçue.
# Sans bannière, celui qui écoute n'est pas MosaicView (nom de pipe squatté
# ou collision avec une autre appli) : le lancement démarre alors normalement
# au lieu de mourir. La bannière est publique — elle identifie, elle
# n'authentifie pas (impossible entre processus d'un même compte Windows).
# ═══════════════════════════════════════════════════════════════════════════════
import os
import time

from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Suffixe utilisateur : deux sessions Windows différentes sur la même machine
# gardent chacune leur instance.
_SERVER_NAME = f"MosaicView-SingleInstance-{os.environ.get('USERNAME', 'default')}"

# Taille maximale d'un message entrant. Le protocole ne transporte qu'un
# chemin de fichier ; le plus long chemin Windows possible (\\?\, 32767
# caractères UTF-16) tient dans ~96 Ko en UTF-8. Au-delà, le client est
# anormal (ou malveillant) : on coupe sans traiter, pour éviter qu'un
# processus local ne fasse gonfler la mémoire indéfiniment.
_MAX_MESSAGE_BYTES = 128 * 1024

# Bannière du handshake (voir en-tête du fichier).
_BANNER = b"MOSAICVIEW1\n"
# Délai maximal d'attente de la bannière côté client. Volontairement large :
# si le thread principal de l'instance existante est occupé, la bannière peut
# tarder — conclure trop vite à un imposteur lancerait une seconde instance
# à tort. Ce délai n'est payé en entier que si le pipe est squatté par un
# serveur muet (cas rarissime) ; le vrai serveur répond en quelques ms.
_HANDSHAKE_TIMEOUT_MS = 3000

_server = None  # référence gardée pour toute la vie du process


def try_forward_to_running_instance(path):
    """Tente de transmettre `path` (chaîne, éventuellement vide) à une
    instance MosaicView déjà ouverte.

    Retourne True si une instance existait (message transmis) : l'appelant
    doit alors quitter immédiatement. False sinon (on est la 1ère instance)."""
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if not sock.waitForConnected(500):
        return False
    # Handshake : on n'envoie rien tant que le serveur ne s'est pas présenté.
    # Bannière absente/incorrecte → ce n'est pas MosaicView : on raccroche et
    # on retourne False, l'appelant démarre normalement (le listen() échouera
    # ensuite → mode dégradé sans single instance, mais l'appli fonctionne).
    deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_MS / 1000.0
    greeting = bytearray()
    while len(greeting) < len(_BANNER):
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0 or not sock.waitForReadyRead(remaining_ms):
            break
        greeting.extend(bytes(sock.readAll()))
    if not bytes(greeting).startswith(_BANNER):
        sock.abort()
        return False
    # Autorise l'instance existante à passer au premier plan (Windows
    # restreint le vol de focus entre process ; nous avons le focus car
    # nous venons d'être lancés par l'utilisateur, on le délègue).
    # Délégation ciblée sur le PID du processus serveur du pipe quand il est
    # identifiable ; repli ASFW_ANY (-1, tout processus) sinon, pour ne pas
    # casser la remontée au premier plan.
    try:
        import ctypes
        target_pid = -1  # ASFW_ANY
        handle = int(sock.socketDescriptor())
        if handle > 0:
            pid = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetNamedPipeServerProcessId(
                ctypes.c_void_p(handle), ctypes.byref(pid))
            if ok and pid.value:
                target_pid = pid.value
        ctypes.windll.user32.AllowSetForegroundWindow(target_pid)
    except Exception:
        pass
    sock.write((path or "").encode("utf-8"))
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(1000)
    return True


def start_single_instance_server(on_path_received):
    """Démarre le serveur local de la 1ère instance.

    `on_path_received(path: str)` est appelé dans le thread Qt principal à
    chaque lancement redirigé ; `path` est le chemin transmis ('' si le
    lancement était sans fichier)."""
    global _server
    # Nettoie un éventuel canal orphelin laissé par un crash
    QLocalServer.removeServer(_SERVER_NAME)
    _server = QLocalServer()
    if not _server.listen(_SERVER_NAME):
        # Dégradé : pas de single instance, l'appli fonctionne normalement
        _server = None
        return

    def _on_new_connection():
        sock = _server.nextPendingConnection()
        if sock is None:
            return
        # Handshake : se présente immédiatement — le client n'envoie son
        # chemin qu'après avoir reçu cette bannière.
        sock.write(_BANNER)
        sock.flush()
        buf = bytearray()

        def _read():
            buf.extend(bytes(sock.readAll()))
            if len(buf) > _MAX_MESSAGE_BYTES:
                # Message anormalement gros : on abandonne la connexion sans
                # jamais appeler le callback (d'où la déconnexion de _done
                # AVANT abort(), qui peut émettre disconnected).
                sock.disconnected.disconnect(_done)
                sock.abort()
                sock.deleteLater()

        def _done():
            sock.deleteLater()
            on_path_received(bytes(buf).decode("utf-8", "replace").strip())

        sock.readyRead.connect(_read)
        sock.disconnected.connect(_done)
        _read()  # données éventuellement déjà arrivées avant la connexion des signaux

    _server.newConnection.connect(_on_new_connection)
