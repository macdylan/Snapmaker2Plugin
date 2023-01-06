import json
import socket
from typing import List, Dict

from UM.Logger import Logger
from UM.Platform import Platform
from UM.Signal import signalemitter, Signal
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.Application import Application

from .qt_comp import *
from .OutputDevice import SM2OutputDevice

#MACHINE_SERIES = "Snapmaker A"
DISCOVER_PORT = 20054
DISCOVER_INTERVAL = 6000  # 6 seconds


@signalemitter
class DiscoverSocket:
    dataReady = Signal()

    def __init__(self, address_entry: QNetworkAddressEntry) -> None:
        self._address_entry = address_entry
        self._broadcast_address = address_entry.broadcast()

        self._socket = None  # internal socket

        self._collect_timer = QTimer()
        self._collect_timer.setInterval(200)
        self._collect_timer.setSingleShot(True)
        self._collect_timer.timeout.connect(self.__collect)

    @property
    def address(self) -> QHostAddress:
        return self._address_entry.ip()

    def bind(self) -> bool:
        sock = QUdpSocket()

        bind_result = sock.bind(self._address_entry.ip(),
                                mode=QAbstractSocket.BindFlag.DontShareAddress
                                | QAbstractSocket.BindFlag.ReuseAddressHint)
        if not bind_result:
            return False

        if Platform.isWindows():
            # On Windows, QUdpSocket is unable to receive broadcast data, we use original socket instead
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                 socket.IPPROTO_UDP)
            sock.settimeout(0.2)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            self._socket = sock
        else:
            # On Unix, we use socket interface provided by Qt 6
            self._socket = sock
            sock.readyRead.connect(self.__read)

        return True

    def discover(self, message: bytes) -> None:
        if isinstance(self._socket, QUdpSocket):
            self._socket.writeDatagram(message, self._broadcast_address,
                                       DISCOVER_PORT)
        else:
            self._socket.sendto(
                message, (self._broadcast_address.toString(), DISCOVER_PORT))
            self._collect_timer.start()

    def abort(self) -> None:
        if not self._socket:
            return

        if isinstance(self._socket, QUdpSocket):
            self._socket.abort()
        else:
            self._socket.close()

        self._socket = None

    def __read(self) -> None:
        while self._socket.hasPendingDatagrams():
            data = self._socket.receiveDatagram()
            if data.isValid() and not data.senderAddress().isNull():
                try:
                    message = bytes(data.data()).decode("utf-8")
                    self.dataReady.emit(message)
                except UnicodeDecodeError as e:
                    Logger.error("error decoding data: %s", e)
                    pass

    def __collect(self) -> None:
        # the socket has abort and discover is cancelled
        if not self._socket:
            return

        if isinstance(self._socket, QUdpSocket):
            return

        while True:
            try:
                msg, _ = self._socket.recvfrom(128)
            except (TimeoutError, ConnectionError, socket.timeout) as e:
                Logger.error("error receiving data: %s", e)
                # normal timeout, or ConnectionError (including ConnectionAbortedError, ConnectionRefusedError,
                # ConnectionResetError) errors raise by the peer
                break

            try:
                message = msg.decode("utf-8")
                self.dataReady.emit(message)
            except UnicodeDecodeError as e:
                Logger.error("error decoding data: %s", e)
                pass


class SM2OutputDevicePlugin(OutputDevicePlugin):
    PREFERENCE_KEY_TOKEN = "Snapmaker2PluginSettings/tokens"

    def __init__(self) -> None:
        super().__init__()

        self._discover_timer = QTimer()
        self._discover_timer.setInterval(DISCOVER_INTERVAL)
        self._discover_timer.setSingleShot(False)
        self._discover_timer.timeout.connect(self.__discover)

        self._discover_sockets = []  # type: List[QUdpSocket]
        self._tokens = {}  # type: Dict[str, str]

        Application.getInstance().globalContainerStackChanged.connect(
            self._onGlobalContainerStackChanged)
        Application.getInstance().applicationShuttingDown.connect(self.stop)

    def _loadTokens(self) -> None:
        preferences = Application.getInstance().getPreferences()
        preferences.addPreference(self.PREFERENCE_KEY_TOKEN, "{}")

        try:
            self._tokens = json.loads(
                preferences.getValue(self.PREFERENCE_KEY_TOKEN))
        except ValueError:
            pass

        if not isinstance(self._tokens, dict):
            self._tokens = {}

        Logger.debug("(%d) tokens loaded.", len(self._tokens.keys()))

    def _saveTokens(self) -> None:
        updated = False
        devices = self.getOutputDeviceManager().getOutputDevices()

        for d in devices:
            if isinstance(d, SM2OutputDevice) and d.getToken():
                ex_token = self._tokens.get(d.getId(), "")
                if not ex_token or ex_token != d.getToken():
                    self._tokens[d.getId()] = d.getToken()
                    updated = True

        if updated:
            try:
                Application.getInstance().getPreferences().setValue(
                    self.PREFERENCE_KEY_TOKEN, json.dumps(self._tokens))
                Logger.debug("(%d) tokens saved.", len(self._tokens.keys()))
            except ValueError:
                self._tokens = {}

    def _deviceId(self, name, model) -> str:
        return "{}@{}".format(name, model)

    def __prepare(self) -> None:
        self._discover_sockets = []
        for interface in QNetworkInterface.allInterfaces():
            for address_entry in interface.addressEntries():
                address = address_entry.ip()
                if address.isLoopback():
                    continue
                if address.protocol() != QIPv4Protocol:
                    continue

                sock = DiscoverSocket(address_entry)
                if sock.bind():
                    Logger.info(
                        "Discovering printers on network interface: %s",
                        address.toString())
                    sock.dataReady.connect(self.__onData)
                    self._discover_sockets.append(sock)

    def __discover(self) -> None:
        if not self._discover_sockets:
            self.__prepare()

        for sock in self._discover_sockets:
            Logger.debug("Discovering networked printer... (interface: %s)",
                         sock.address.toString())
            sock.discover(b"discover")

        self._saveTokens()  # TODO

        # TODO: remove output devices that not reply message for a period of time

    def __onData(self, msg: str) -> None:
        """Parse message.

        msg: Snapmaker-DUMMY@127.0.0.1|model:Snapmaker 2 Model A350|status:IDLE
        """
        Logger.debug("got msg: %s", msg)
        parts = msg.split("|")
        if len(parts) < 1 or "@" not in parts[0]:
            # invalid message
            return

        name, address = parts[0].rsplit("@", 1)

        properties = {}
        for part in parts[1:]:
            if ":" not in part:
                continue

            key, value = part.split(":")
            properties[key] = value

        model = properties.get("model", "")
        Logger.debug("machine model is %s", model)
        if not model.startswith("Snapmaker 2"):
            return

        device_id = self._deviceId(name, model)

        device = self.getOutputDeviceManager().getOutputDevice(device_id)
        if not device:
            token = self._tokens.get(device_id, "")
            Logger.info("Discovered Snapmaker printer: %s@%s (token: '%s')",
                        name, address, token)
            device = SM2OutputDevice(device_id, address, token, properties)
            self.getOutputDeviceManager().addOutputDevice(device)

    def start(self) -> None:
        if self._isSM2Container() and not self._discover_timer.isActive():
            self._loadTokens()
            self._discover_timer.start()
            Logger.info("Snapmaker discovering started.")

    def stop(self) -> None:
        if self._discover_timer.isActive():
            self._discover_timer.stop()

        for sock in self._discover_sockets:
            sock.abort()

        # clear all discover sockets
        self._discover_sockets.clear()

        self._saveTokens()

        Logger.info("Snapmaker discovering stopped.")

    def startDiscovery(self) -> None:
        self.__discover()

    def _onGlobalContainerStackChanged(self) -> None:
        if self._isSM2Container():
            self.start()
        else:
            self.stop()

    def _isSM2Container(self) -> bool:
        stack = Application.getInstance().getGlobalContainerStack()
        if not stack:
            return False
        machine_name = stack.getProperty("machine_name", "value")
        Logger.debug('machine name: %s', machine_name)
        return machine_name.startswith("Snapmaker A")
