"""
modules/qt/scan_wia.py — Accès bas niveau au scanner via WIA (Windows Image Acquisition).

Module sans dépendance Qt : logique COM pure, appelée depuis un QThread
(voir modules/qt/scan_dialog_qt.py). Toute exception COM est convertie en
ScanError avant de sortir de ce module.

Approche inspirée du projet open source NAPS2 (WIA/TWAIN) : les capacités du
device (résolutions, modes couleur, zone) sont interrogées dynamiquement,
jamais supposées fixes, et un repli WIA 1.0 est tenté si WIA 2.0 échoue.
"""

import io


_LOG_FILENAME = "Log_scan.txt"
_LOG_MAX_SESSIONS = 30  # une "session" = un clic sur "Numériser" (bloc "Scan started")
_LOG_SESSION_MARKER = "— Scan started"


def get_scan_log_path() -> str:
    """Chemin complet de Log_scan.txt (%TEMP%\\MosaicViewTemp\\), qu'il existe
    déjà ou non — point de passage unique pour tout code (Qt ou non) qui a
    besoin de ce chemin, plutôt que de reconstruire
    os.path.join(get_mosaicview_temp_dir(), "Log_scan.txt") à chaque site."""
    from modules.qt.temp_files import get_mosaicview_temp_dir
    import os
    return os.path.join(get_mosaicview_temp_dir(), _LOG_FILENAME)


def scan_log_exists() -> bool:
    """True si Log_scan.txt existe (au moins un scan a eu lieu depuis la
    dernière purge/suppression manuelle) — utilisé pour griser les commandes
    "Ouvrir le journal de scan"/"Accéder au journal de scan" tant qu'il n'y a
    rien à ouvrir, plutôt que de les laisser cliquables sans effet visible."""
    import os
    return os.path.exists(get_scan_log_path())


def _mosaicview_version() -> str:
    """Version de l'application (même pattern que update_checker_qt.py :
    import local, jamais au niveau module)."""
    try:
        import MosaicView as _main
        return getattr(_main, "__version__", "?")
    except Exception:
        return "?"


def _environment_info() -> str:
    """Résumé de l'environnement système (Windows, Python, pywin32) — un
    utilisateur distant ne peut pas tester plusieurs versions corrigées de
    l'appli, donc tout ce qui pourrait expliquer un écart de comportement WIA
    doit être capturé dès le premier rapport plutôt que redemandé après coup."""
    import platform
    try:
        os_info = platform.platform()
    except Exception:
        os_info = "?"
    try:
        python_version = platform.python_version()
    except Exception:
        python_version = "?"
    try:
        import importlib.metadata
        pywin32_version = importlib.metadata.version("pywin32")
    except Exception:
        pywin32_version = "?"
    return f"OS: {os_info} | Python: {python_version} | pywin32: {pywin32_version}"


def _prune_scan_log(log_path: str) -> None:
    """Tronque Log_scan.txt pour ne garder que les _LOG_MAX_SESSIONS sessions
    de scan les plus récentes (une session = un clic sur "Numériser", repéré
    par _LOG_SESSION_MARKER dans un bloc "Scan started"). Appelée juste avant
    l'écriture d'un nouveau bloc "Scan started" — pas à chaque écriture, pour
    ne pas relire/réécrire tout le fichier à chaque ligne de détail.

    Best-effort, comme _log_scan_event() : toute erreur est avalée silencieusement,
    la purge n'est qu'un nettoyage de confort, jamais une condition du scan lui-même.
    """
    try:
        import os
        if not os.path.exists(log_path):
            return
        with open(log_path, encoding="utf-8") as f:
            content = f.read()

        # Découpe en blocs sur le séparateur de 60 "=" (garde le séparateur en
        # tête de chaque bloc suivant, pour reconstruire le fichier à l'identique).
        separator = "=" * 60
        blocks = content.split(separator)
        session_block_indices = [i for i, b in enumerate(blocks) if _LOG_SESSION_MARKER in b]
        if len(session_block_indices) <= _LOG_MAX_SESSIONS:
            return

        # Le préambule (avant le premier séparateur, ex. lignes "MosaicView vX.Y.Z"
        # orphelines) est jeté avec les vieux blocs qu'il précédait — il ne décrit
        # que la session qu'on est en train de supprimer, pas celles gardées.
        first_kept_index = session_block_indices[-_LOG_MAX_SESSIONS]
        kept = separator + separator.join(blocks[first_kept_index:])

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(kept)
    except Exception:
        pass


def _log_scan_event(text: str, with_version: bool = False) -> None:
    """Ajoute un bloc au log cumulatif des scans (%TEMP%\\MosaicViewTemp\\Log_scan.txt).

    Chaque ligne de détail (celles indentées par deux espaces, à l'intérieur
    d'un bloc) est préfixée par un horodatage complet YYYY-MM-DD HH:MM:SS.mmm —
    précision à la milliseconde pour repérer un blocage entre deux étapes
    consécutives d'une même opération, et date incluse pour permettre un tri
    chronologique de n'importe quelle ligne isolément, sans avoir à remonter
    au bloc englobant. Les lignes d'en-tête de bloc et les séparateurs
    ("="*60) ne sont pas préfixés, déjà horodatés explicitement par l'appelant.

    with_version=True préfixe le bloc par le numéro de version de MosaicView —
    utile pour savoir si un rapport utilisateur vient d'une version périmée,
    sans avoir à le redemander. Réservé aux en-têtes de bloc (début de scan,
    liste de devices), pas répété sur chaque ligne de détail du même bloc.

    Best-effort : une erreur d'écriture du log ne doit jamais faire échouer un
    scan par ailleurs réussi — voir usage dans scan_image()/list_scanner_devices().
    """
    try:
        from datetime import datetime
        from modules.qt.temp_files import get_mosaicview_temp_dir
        import os

        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        lines = text.split("\n")
        stamped_lines = [
            f"[{ts}] {line}" if line.startswith("  ") else line
            for line in lines
        ]
        text = "\n".join(stamped_lines)

        log_path = os.path.join(get_mosaicview_temp_dir(), _LOG_FILENAME)
        if with_version:
            text = f"MosaicView v{_mosaicview_version()}\n{text}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


# Noms de propriétés WIA (Automation Layer, wiaaut.dll).
# Accès par NOM et non par PropertyID numérique : sur certains drivers (constaté
# avec HP), l'Automation Layer n'accepte l'index par ID que pour la lecture via
# Properties.Item(position) (accès positionnel) — Properties(id_numérique) échoue
# systématiquement, en lecture des sous-attributs (SubType) comme en écriture
# directe de .Value.
WIA_IPS_XRES       = "Horizontal Resolution"
WIA_IPS_YRES       = "Vertical Resolution"
WIA_IPS_XPOS       = "Horizontal Start Position"
WIA_IPS_YPOS       = "Vertical Start Position"
WIA_IPS_XEXTENT    = "Horizontal Extent"
WIA_IPS_YEXTENT    = "Vertical Extent"
WIA_IPS_CUR_INTENT = "Current Intent"

WIA_ITEM_TYPE = "Item Type"
WIA_ITEM_TYPE_FLATBED = 0x2000
WIA_ITEM_TYPE_FEEDER  = 0x4000

# Intents de scan (WIA_IPS_CUR_INTENT)
WIA_INTENT_COLOR      = 0x00000001
WIA_INTENT_GRAYSCALE  = 0x00000002
WIA_INTENT_TEXT       = 0x00000004  # noir et blanc (line art)

FORMAT_ID_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

WIA_ERROR_PAPER_EMPTY = -2145320957  # 0x80210003 signé


class ScanError(Exception):
    """Erreur de scan WIA, avec une clé de traduction pour l'affichage utilisateur."""

    def __init__(self, message_key: str, detail: str = ""):
        self.message_key = message_key
        self.detail = detail
        super().__init__(f"{message_key}: {detail}" if detail else message_key)


def _dispatch_device_manager():
    """Crée l'objet COM WIA.DeviceManager (Automation Layer WIA, wiaaut.dll).
    Lève ScanError si WIA est indisponible."""
    import win32com.client.dynamic
    try:
        dm = win32com.client.dynamic.Dispatch("WIA.DeviceManager")
    except Exception as e:
        _log_scan_event(f"  WIA.DeviceManager: FAILED — {e}\n")
        raise ScanError("scan.error_wia_unavailable", str(e))
    return dm


def _read_wia_prop(props, name: str, default: str = "?") -> str:
    """Lit une propriété nommée de la collection Properties d'un DeviceInfo,
    avec repli si absente/illisible sur ce driver — jamais d'exception qui
    remonte, uniquement utilisé pour du diagnostic best-effort."""
    try:
        value = props(name).Value
        return str(value) if value not in (None, "") else default
    except Exception:
        return default


def list_scanner_devices() -> list[dict]:
    """
    Énumère les scanners WIA disponibles.

    Retourne une liste de dicts {"id": device_id, "name": nom_affichable,
    "manufacturer": nom_fabricant}. Liste vide si aucun scanner détecté (pas
    une erreur en soi).
    """
    device_manager = _dispatch_device_manager()

    native_devices = []   # Type == 1 (StiDeviceTypeScanner), pilote natif du fabricant
    other_devices = []    # tout autre Type (ex. 65535 = doublon ESCL généré par Windows)
    all_seen = []  # tous les devices WIA vus, pour le log, quel que soit leur sort final
    try:
        device_infos = device_manager.DeviceInfos
        for i in range(1, device_infos.Count + 1):
            info = device_infos.Item(i)
            wia_type = getattr(info, "Type", 1)
            props = info.Properties
            name = _read_wia_prop(props, "Name", "Scanner inconnu")
            manufacturer = _read_wia_prop(props, "Manufacturer", "Fabricant inconnu")
            port = _read_wia_prop(props, "Port")
            driver_version = _read_wia_prop(props, "Driver Version")
            wia_version = _read_wia_prop(props, "WIA Version")
            device_id = info.DeviceID
            entry = {"id": device_id, "name": name, "manufacturer": manufacturer}

            # Type 1 = StiDeviceTypeScanner (WIA_DEVICE_TYPE), le pilote natif du
            # fabricant — préféré par défaut. Tout autre Type (ex. 65535, doublon
            # ESCL générique généré par Windows lui-même pour le même scanner
            # physique quand il supporte ce protocole récent, indépendamment du
            # pilote natif — voir skill scan, section ESCL) n'est retenu qu'en
            # secours, si AUCUN device natif n'a été trouvé du tout (un scanner
            # sans pilote fabricant installé pourrait n'exister QUE sous cette
            # forme). Jamais les deux mélangés dans la liste finale, pour éviter
            # d'afficher un doublon visible du même scanner physique.
            if wia_type == 1:
                native_devices.append(entry)
            else:
                other_devices.append(entry)

            all_seen.append(
                f"  - [Type={wia_type}] "
                f"{manufacturer} / {name} | Port={port} | Driver={driver_version} | WIA={wia_version} | id={device_id}"
            )
    except Exception as e:
        raise ScanError("scan.error_enumeration_failed", str(e))

    if native_devices:
        devices = native_devices
        selection_note = f"{len(native_devices)} native kept, {len(other_devices)} other ignored"
    elif other_devices:
        devices = other_devices
        selection_note = f"0 native, falling back to {len(other_devices)} non-native device(s)"
    else:
        devices = []
        selection_note = "no device of any type"

    if all_seen:
        from datetime import datetime
        _log_scan_event(
            f"\n{'=' * 60}\n"
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — Devices detected ({selection_note}):\n"
            f"  {_environment_info()}\n"
            f"{chr(10).join(all_seen)}\n",
            with_version=True,
        )

    return devices


def find_escl_fallback(failed_device_id: str) -> dict | None:
    """Cherche un device WIA non-natif (ex. repli ESCL généré par Windows,
    voir skill scan section "Repli ESCL") pour la même machine physique que
    failed_device_id, à proposer après l'échec d'un scan avec le pilote natif.

    Ré-énumère tous les devices WIA (comme list_scanner_devices(), mais sans
    en jeter aucun) et retourne le premier device non-natif trouvé, différent
    de failed_device_id. None si aucun candidat (aucun device non-natif, ou
    l'échec provient déjà d'un device non-natif). Best-effort : ne lève jamais,
    une erreur d'énumération ici ne doit pas empêcher l'affichage de l'erreur
    de scan d'origine."""
    try:
        device_manager = _dispatch_device_manager()
        device_infos = device_manager.DeviceInfos
        for i in range(1, device_infos.Count + 1):
            info = device_infos.Item(i)
            device_id = info.DeviceID
            wia_type = getattr(info, "Type", 1)
            if device_id == failed_device_id:
                continue
            if wia_type != 1:
                props = info.Properties
                name = _read_wia_prop(props, "Name", "Scanner inconnu")
                manufacturer = _read_wia_prop(props, "Manufacturer", "Fabricant inconnu")
                _log_scan_event(f"  ESCL fallback candidate found: {manufacturer} / {name} | id={device_id}\n")
                return {"id": device_id, "name": name, "manufacturer": manufacturer}
        _log_scan_event("  ESCL fallback candidate: none found\n")
        return None
    except Exception as e:
        _log_scan_event(f"  ESCL fallback candidate: lookup FAILED — {e}\n")
        return None


def _log_item_properties(item, label: str) -> None:
    """Journalise Name/Value de toutes les propriétés WIA de l'item de scan —
    systématiquement, succès ou échec (voulu ainsi : un utilisateur distant ne
    peut pas tester plusieurs versions corrigées de l'appli, donc le seul
    rapport reçu doit permettre de comparer un scan qui marche à un qui ne
    marche pas, pas seulement de diagnostiquer un échec). Remplace l'ancienne
    fonction de diagnostic _dump_properties (print(), retirée) : mêmes données,
    mais écrites dans Log_scan.txt et appelées à chaque scan plutôt que
    ponctuellement à la main pendant le développement."""
    try:
        props = item.Properties
        count = props.Count
        lines = []
        for i in range(1, count + 1):
            try:
                p = props.Item(i)
                lines.append(f"    {p.Name} = {p.Value!r}")
            except Exception as e:
                lines.append(f"    <property #{i}: unreadable — {e}>")
        _log_scan_event(f"  {label} ({count}):\n" + "\n".join(lines) + "\n")
    except Exception as e:
        _log_scan_event(f"  {label}: FAILED to enumerate — {e}\n")


def _connect_device(device_manager, device_id: str):
    """Retrouve le DeviceInfo correspondant à device_id et retourne le Device connecté.

    Dans l'Automation Layer WIA (wiaaut.dll), c'est DeviceInfo.Connect() — sans
    argument — qui retourne l'objet Device connecté, pas une méthode Connect(id)
    ou CreateDevice(flags, id) sur DeviceManager lui-même (DeviceManager n'expose
    que DeviceInfos, confirmé via GetIDsOfNames lors du diagnostic)."""
    _log_scan_event(f"  Connection attempt: device_id={device_id}\n")

    try:
        device_infos = device_manager.DeviceInfos
        target_info = None
        for i in range(1, device_infos.Count + 1):
            info = device_infos.Item(i)
            if info.DeviceID == device_id:
                target_info = info
                break
    except Exception as e:
        _log_scan_event(f"  Connection attempt: FAILED (DeviceInfos enumeration) — {e}\n")
        raise ScanError("scan.error_device_unreachable", str(e))

    if target_info is None:
        _log_scan_event("  Connection attempt: FAILED — device_id not found in DeviceInfos\n")
        raise ScanError("scan.error_device_unreachable", "device_id introuvable dans DeviceInfos")

    # Type relu depuis WIA au moment de la connexion (pas déduit de la forme du
    # device_id, non fiable d'un fabricant à l'autre) — Type=1 (StiDeviceTypeScanner)
    # = pilote natif, tout autre Type = device non-natif (ex. repli ESCL généré
    # par Windows, voir skill scan section "Repli ESCL"). Sert à savoir, dans
    # chaque bloc de log ultérieur (capacités, scan), quel chemin a réellement
    # été emprunté sans avoir à interpréter le format du device_id à l'œil.
    connected_type = getattr(target_info, "Type", 1)
    driver_kind = "native driver" if connected_type == 1 else f"non-native (Type={connected_type}, e.g. ESCL fallback)"
    _log_scan_event(f"  Connection attempt: via {driver_kind}\n")

    try:
        device = target_info.Connect()
    except Exception as e:
        _log_scan_event(f"  Connection attempt: FAILED (DeviceInfo.Connect) — {e}\n")
        raise ScanError("scan.error_device_unreachable", str(e))

    _log_scan_event("  Connection attempt: OK\n")
    return device


def _find_scan_item(device):
    """Retourne l'item WIA à plat (Flatbed) du device, ou son premier item si non identifiable."""
    try:
        items = device.Items
        for i in range(1, items.Count + 1):
            item = items.Item(i)
            try:
                item_type = item.Properties(WIA_ITEM_TYPE).Value
                if item_type & WIA_ITEM_TYPE_FLATBED:
                    _log_scan_event("  Scan item: OK (Flatbed)\n")
                    return item
            except Exception:
                continue
        # Aucun item explicitement "Flatbed" trouvé — retombe sur le premier item image
        if items.Count >= 1:
            _log_scan_event("  Scan item: OK (fallback, first item)\n")
            return items.Item(1)
    except Exception as e:
        _log_scan_event(f"  Scan item: FAILED — {e}\n")
        raise ScanError("scan.error_no_scan_item", str(e))
    _log_scan_event("  Scan item: FAILED — no item on device\n")
    raise ScanError("scan.error_no_scan_item", "")


# SubType WIA (type de contrainte de valeur exposée par IProperty.SubType) —
# énumération WiaSubType officielle (Wiaaut.vbs, doc Microsoft), à ne pas
# deviner : constantes précédentes (WIA_PROP_LIST=3, WIA_PROP_RANGE=2) FAUSSES
# et INVERSÉES par rapport aux vraies valeurs ci-dessous — bug resté silencieux
# tout du long car chaque site d'usage a un fallback qui masque un mismatch
# (_closest_allowed_value renvoie la valeur demandée telle quelle, get_device_
# capabilities retombe sur sa liste fixe). Découvert en creusant pourquoi le
# HP ENVY 4520 ne semblait exposer ni liste ni plage de résolutions — en
# réalité les comparaisons ne pouvaient jamais correspondre à la vraie valeur.
WIA_PROP_UNSPECIFIED = 0  # ni liste, ni plage, ni flag (valeur fixe unique)
WIA_PROP_RANGE       = 1  # plage continue (SubTypeMin/SubTypeMax/SubTypeStep)
WIA_PROP_LIST        = 2  # valeurs discrètes énumérées (SubTypeValues)
WIA_PROP_FLAG        = 3  # ensemble de valeurs combinables (bitmask)


def _closest_allowed_value(prop, value):
    """Ajuste `value` à la valeur la plus proche réellement acceptée par le
    driver pour cette propriété (liste discrète ou plage), sinon la renvoie
    telle quelle si la contrainte n'est pas exposée ou pas exploitable."""
    try:
        sub_type = prop.SubType
        if sub_type == WIA_PROP_LIST:
            allowed = list(prop.SubTypeValues)
            if allowed:
                return min(allowed, key=lambda v: abs(v - value))
        elif sub_type == WIA_PROP_RANGE:
            lo, hi = prop.SubTypeMin, prop.SubTypeMax
            return max(lo, min(hi, value))
    except Exception:
        pass
    return value


def _set_property(item, prop_name, value):
    """Fixe une propriété WIA (accès par NOM, voir commentaire sur WIA_IPS_XRES
    etc.) si elle existe sur l'item ; ignore silencieusement sinon (toutes les
    propriétés ne sont pas supportées par tous les drivers). Ajuste d'abord la
    valeur demandée à la valeur autorisée la plus proche — beaucoup de drivers
    (dont HP) rejettent une valeur hors de leur liste/plage discrète au lieu de
    l'arrondir eux-mêmes. Chaque tentative (ajustée ou non, réussie ou non) est
    loguée pour comparer la valeur demandée à la valeur réellement appliquée
    (ex. DPI demandé vs DPI réel accepté par le driver).

    Retourne la valeur réellement appliquée (relue après écriture), ou None en
    cas d'échec — pour que l'appelant (scan_image) puisse reporter le DPI réel
    dans son résumé final plutôt que de se fier à la valeur demandée."""
    try:
        prop = item.Properties(prop_name)
        adjusted = _closest_allowed_value(prop, value)
        prop.Value = adjusted
        actual = prop.Value
        if adjusted != value:
            _log_scan_event(
                f"  Set {prop_name}: requested={value}, adjusted-to-supported={adjusted}, actual={actual}\n"
            )
        else:
            _log_scan_event(f"  Set {prop_name}: requested={value}, actual={actual}\n")
        return actual
    except Exception as e:
        _log_scan_event(f"  Set {prop_name}: FAILED (requested={value}) — {e}\n")
        return None


def get_device_capabilities(device_id: str) -> dict:
    """
    Interroge dynamiquement les capacités du device : résolutions DPI disponibles,
    modes couleur supportés, zone de scan maximale (en pixels, à la résolution
    courante du device).

    Retourne un dict :
        {
            "resolutions": [75, 150, 300, 600, ...],  # valeurs DPI supportées
            "color_modes": ["color", "grayscale", "bw"],  # sous-ensemble supporté
            "max_width":  int,   # WIA_IPS_XEXTENT max, en pixels
            "max_height": int,   # WIA_IPS_YEXTENT max, en pixels
        }
    Lève ScanError si le device est inaccessible.
    """
    from datetime import datetime
    started_at = datetime.now()

    _log_scan_event(
        f"\n{'=' * 60}\n"
        f"{started_at.strftime('%Y-%m-%d %H:%M:%S')} — Capabilities query started\n"
        f"Device: {device_id}\n",
        with_version=True,
    )

    try:
        device_manager = _dispatch_device_manager()
        device = _connect_device(device_manager, device_id)
        item = _find_scan_item(device)
    except ScanError as e:
        duration = (datetime.now() - started_at).total_seconds()
        _log_scan_event(f"Capabilities query: FAILED after {duration:.1f}s — {e.message_key} ({e.detail})\n")
        raise

    return _read_device_capabilities(item, started_at)


def _read_device_capabilities(item, started_at) -> dict:
    from datetime import datetime
    caps = {
        "resolutions": [],
        "color_modes": [],
        "max_width": 0,
        "max_height": 0,
    }

    # SubTypeValues/SubTypeMin/SubTypeMax sont des PROPRIÉTÉS (pas des méthodes)
    # sur cette Automation Layer — voir _closest_allowed_value(). La résolution
    # WIA peut être exposée par le driver de deux façons différentes (doc
    # Microsoft, WIA_IPS_XRES) : WIA_PROP_LIST (liste discrète, SubTypeValues)
    # OU WIA_PROP_RANGE (plage continue, SubTypeMin/SubTypeMax) — un driver qui
    # choisit RANGE plutôt que LIST n'a jamais de SubTypeValues à lire, ce que
    # la première version de cette fonction ne testait pas du tout (bug réel :
    # sur le HP ENVY 4520, le driver expose la résolution en RANGE, donc
    # l'ancien code retombait systématiquement sur le repli fixe, quelle que
    # soit la vraie plage supportée par l'optique du capteur).
    _log_scan_event(f"  Requesting: {WIA_IPS_XRES!r} supported values\n")
    values = None
    try:
        xres_prop = item.Properties(WIA_IPS_XRES)
        sub_type = xres_prop.SubType
        if sub_type == WIA_PROP_LIST:
            values = list(xres_prop.SubTypeValues)
            _log_scan_event(f"  Response: SubType=LIST, values={values}\n")
        elif sub_type == WIA_PROP_RANGE:
            lo, hi, step = xres_prop.SubTypeMin, xres_prop.SubTypeMax, getattr(xres_prop, "SubTypeStep", 0)
            _log_scan_event(f"  Response: SubType=RANGE, min={lo}, max={hi}, step={step}\n")
            # Génère une liste de valeurs usuelles dans la plage annoncée par
            # le driver, plutôt que d'exposer un slider continu à l'utilisateur
            # (cohérent avec le combo actuel) — les paliers DPI classiques du
            # scan filtrés à ceux réellement compris dans [lo, hi], plus hi
            # lui-même s'il ne coïncide pas déjà avec un palier (capture le
            # vrai maximum optique du capteur, ex. 1200, 2400, 4800...).
            common_steps = [75, 100, 150, 200, 300, 600, 1200, 2400, 4800, 9600]
            values = [v for v in common_steps if lo <= v <= hi]
            if hi not in values:
                values.append(int(hi))
        else:
            _log_scan_event(f"  Response: SubType={sub_type!r} (neither LIST nor RANGE)\n")
    except Exception as e:
        _log_scan_event(f"  Response: FAILED to read — {e}\n")
        values = None

    if values:
        try:
            caps["resolutions"] = sorted(int(v) for v in values)
        except Exception:
            caps["resolutions"] = []

    if not caps["resolutions"]:
        # Repli raisonnable si le driver n'expose ni liste ni plage exploitable
        caps["resolutions"] = [75, 100, 150, 200, 300, 600]
        _log_scan_event(f"  Resolutions: driver exposed neither a usable list nor range, using fallback {caps['resolutions']}\n")
    else:
        _log_scan_event(f"  Resolutions: {caps['resolutions']}\n")

    _log_scan_event(f"  Requesting: {WIA_IPS_CUR_INTENT!r} supported values\n")
    try:
        intent_prop = item.Properties(WIA_IPS_CUR_INTENT)
        intent_values = list(intent_prop.SubTypeValues) if intent_prop.SubType == WIA_PROP_LIST else None
    except Exception as e:
        _log_scan_event(f"  Response: FAILED to read — {e}\n")
        intent_values = None

    supported_intents = set(int(v) for v in intent_values) if intent_values else {
        WIA_INTENT_COLOR, WIA_INTENT_GRAYSCALE, WIA_INTENT_TEXT
    }
    if intent_values:
        _log_scan_event(f"  Response: raw intent flags {sorted(supported_intents)}\n")
    else:
        _log_scan_event(f"  Response: driver did not expose a list, assuming all modes supported\n")
    if WIA_INTENT_COLOR in supported_intents:
        caps["color_modes"].append("color")
    if WIA_INTENT_GRAYSCALE in supported_intents:
        caps["color_modes"].append("grayscale")
    if WIA_INTENT_TEXT in supported_intents:
        caps["color_modes"].append("bw")
    if not caps["color_modes"]:
        caps["color_modes"] = ["color", "grayscale", "bw"]

    _log_scan_event(f"  Requesting: {WIA_IPS_XEXTENT!r} / {WIA_IPS_YEXTENT!r} (max scan area)\n")
    try:
        caps["max_width"] = int(item.Properties(WIA_IPS_XEXTENT).Value)
        caps["max_height"] = int(item.Properties(WIA_IPS_YEXTENT).Value)
        _log_scan_event(f"  Response: {caps['max_width']}x{caps['max_height']}px\n")
    except Exception as e:
        caps["max_width"] = 0
        caps["max_height"] = 0
        _log_scan_event(f"  Response: FAILED to read — {e}\n")

    duration = (datetime.now() - started_at).total_seconds()
    _log_scan_event(
        f"Capabilities query: OK after {duration:.1f}s — "
        f"resolutions={caps['resolutions']}, color_modes={caps['color_modes']}, "
        f"max_size={caps['max_width']}x{caps['max_height']}px\n"
    )

    return caps


_INTENT_BY_COLOR_MODE = {
    "color": WIA_INTENT_COLOR,
    "grayscale": WIA_INTENT_GRAYSCALE,
    "bw": WIA_INTENT_TEXT,
}


def _configure_item(item, settings: dict) -> int:
    """Applique dpi/color_mode/zone sur l'item WIA avant transfert.

    Retourne le DPI horizontal réellement appliqué (relu après écriture, voir
    _set_property) — peut différer de settings["dpi"] si le driver l'a ajusté
    à sa propre plage/liste supportée. Repli sur la valeur demandée si la
    relecture a échoué (property non supportée par ce driver), pour que le
    résumé final de scan_image() affiche toujours un DPI plausible."""
    dpi = settings.get("dpi", 300)
    color_mode = settings.get("color_mode", "color")
    intent = _INTENT_BY_COLOR_MODE.get(color_mode, WIA_INTENT_COLOR)

    actual_dpi = _set_property(item, WIA_IPS_XRES, dpi)
    _set_property(item, WIA_IPS_YRES, dpi)
    _set_property(item, WIA_IPS_CUR_INTENT, intent)

    x_pos = settings.get("x_pos")
    y_pos = settings.get("y_pos")
    width = settings.get("width")
    height = settings.get("height")
    if x_pos is not None:
        _set_property(item, WIA_IPS_XPOS, x_pos)
    if y_pos is not None:
        _set_property(item, WIA_IPS_YPOS, y_pos)
    if width is not None:
        _set_property(item, WIA_IPS_XEXTENT, width)
    if height is not None:
        _set_property(item, WIA_IPS_YEXTENT, height)

    return actual_dpi if actual_dpi is not None else dpi


def _transfer(item) -> bytes:
    """Déclenche le transfert de l'image scannée en mode programmé (sans UI native)
    et retourne les bytes de l'image (BMP)."""
    _log_scan_event("  Transfer: starting item.Transfer()...\n")
    try:
        image = item.Transfer(FORMAT_ID_BMP)
    except Exception as e:
        hresult = getattr(e, "hresult", None) or (e.args[0] if e.args else None)
        _log_scan_event(f"  Transfer: FAILED — hresult={hresult}, raw error: {e}\n")
        if hresult == WIA_ERROR_PAPER_EMPTY:
            raise ScanError("scan.error_no_paper", str(e))
        raise ScanError("scan.error_transfer_failed", str(e))

    try:
        raw = image.FileData.BinaryData
    except Exception as e:
        _log_scan_event(f"  Transfer: FAILED (reading FileData.BinaryData) — {e}\n")
        raise ScanError("scan.error_transfer_failed", str(e))

    _log_scan_event(f"  Transfer: OK ({len(raw)} raw bytes)\n")
    return bytes(raw)


def scan_image(device_id: str, settings: dict) -> bytes:
    """
    Scanne une image avec le device et les paramètres donnés.

    settings attend : {"dpi": int, "color_mode": "color"|"grayscale"|"bw",
                        "x_pos": int|None, "y_pos": int|None,
                        "width": int|None, "height": int|None}
    (zone omise ou None = pleine page selon la valeur courante du device).

    Retourne les bytes de l'image scannée, convertie en JPEG (les scanners WIA
    renvoient généralement du BMP non compressé — conversion nécessaire pour
    rester cohérent avec les formats gérés par create_entry côté mosaïque).

    Lève ScanError en cas d'échec (device absent, driver indisponible, feuille
    absente sur un scanner à plat, etc.).
    """
    from datetime import datetime
    started_at = datetime.now()

    device_manager = _dispatch_device_manager()

    # Nom/marque du device utilisé — capturés séparément de _connect_device()
    # (qui ne retourne que l'objet Device connecté) pour figurer dans le log
    # même en cas d'échec de connexion. Repli explicite si indisponible : voir
    # list_scanner_devices() pour la même logique de repli sur l'énumération.
    device_name = "Scanner inconnu"
    device_manufacturer = "Fabricant inconnu"
    try:
        for i in range(1, device_manager.DeviceInfos.Count + 1):
            info = device_manager.DeviceInfos.Item(i)
            if info.DeviceID == device_id:
                device_name = _read_wia_prop(info.Properties, "Name", device_name)
                device_manufacturer = _read_wia_prop(info.Properties, "Manufacturer", device_manufacturer)
                break
    except Exception:
        pass

    try:
        from modules.qt.temp_files import get_mosaicview_temp_dir
        import os
        _prune_scan_log(os.path.join(get_mosaicview_temp_dir(), _LOG_FILENAME))
    except Exception:
        pass

    _log_scan_event(
        f"\n{'=' * 60}\n"
        f"{started_at.strftime('%Y-%m-%d %H:%M:%S')} — Scan started\n"
        f"  {_environment_info()}\n"
        f"Device: {device_manufacturer} / {device_name} ({device_id})\n"
        f"Requested settings: {settings}\n",
        with_version=True,
    )

    try:
        device = _connect_device(device_manager, device_id)
        item = _find_scan_item(device)
        _log_item_properties(item, "Scan item properties (before configuration)")

        actual_dpi = _configure_item(item, settings)
        _log_item_properties(item, "Scan item properties (after configuration, before transfer)")

        try:
            raw_bytes = _transfer(item)
        except ScanError as first_error:
            # Repli WIA 1.0 : certains drivers renvoient E_INVALIDARG en WIA 2.0
            # sur des propriétés qu'ils ne supportent en réalité qu'en WIA 1.0.
            # On retente une seule fois avec un minimum de propriétés fixées.
            try:
                device_manager_retry = _dispatch_device_manager()
                device_retry = _connect_device(device_manager_retry, device_id)
                item_retry = _find_scan_item(device_retry)
                _set_property(item_retry, WIA_IPS_XRES, settings.get("dpi", 300))
                _set_property(item_retry, WIA_IPS_YRES, settings.get("dpi", 300))
                raw_bytes = _transfer(item_retry)
                _log_scan_event("WIA 2.0 transfer failed, WIA 1.0-style fallback succeeded.\n")
            except Exception:
                raise first_error

        from PIL import Image as PILImage
        try:
            img = PILImage.open(io.BytesIO(raw_bytes))
            img.load()
            out = io.BytesIO()
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=95, dpi=(settings.get("dpi", 300), settings.get("dpi", 300)))
        except Exception as e:
            raise ScanError("scan.error_invalid_image", str(e))

    except ScanError as e:
        duration = (datetime.now() - started_at).total_seconds()
        _log_scan_event(
            f"FAILED after {duration:.1f}s — {e.message_key}\n"
            f"Detail: {e.detail}\n"
        )
        raise
    except Exception as e:
        duration = (datetime.now() - started_at).total_seconds()
        _log_scan_event(
            f"FAILED after {duration:.1f}s — unexpected {type(e).__name__}\n"
            f"Detail: {e}\n"
        )
        raise
    duration = (datetime.now() - started_at).total_seconds()
    result_bytes = out.getvalue()
    _log_scan_event(
        f"OK after {duration:.1f}s — {actual_dpi} DPI, image {img.size[0]}x{img.size[1]}px, "
        f"mode={img.mode}, {len(result_bytes)} bytes JPEG\n"
    )
    return result_bytes
