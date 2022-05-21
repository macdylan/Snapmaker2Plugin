import threading
import socket
import requests
import time
import json
from io import StringIO
try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtNetwork import QNetworkReply
    QNetworkReplyNetworkErrors = QNetworkReply.NetworkError
except ImportError:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtNetwork import QNetworkReply
    QNetworkReplyNetworkErrors = QNetworkReply.NetworkError

from cura.CuraApplication import CuraApplication
from cura.PrinterOutput.NetworkedPrinterOutputDevice import NetworkedPrinterOutputDevice, AuthState
from cura.PrinterOutput.PrinterOutputDevice import ConnectionState, ConnectionType

from UM.Signal import Signal
from UM.Logger import Logger
from UM.Message import Message
from UM.FileHandler.WriteFileJob import WriteFileJob
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.Application import Application

from .SM2GCodeWriter import SM2GCodeWriter


class SM2OutputDeviceManager(OutputDevicePlugin):
    """
    tokens:
    {
        "My3DP@Snapmaker 2 Model A350": "token1",
        "MyCNC@Snapmaker 2 Model A250": "token2",
    }
    """
    PREFERENCE_KEY_TOKEN = "Snapmaker2PluginSettings/tokens"

    discoveredDevicesChanged = Signal()

    def __init__(self):
        super().__init__()
        self._discovering = threading.Event()
        self._discover_thread = None
        self._discovered_devices = []  # List[ip:bytes, id:bytes]
        self.discoveredDevicesChanged.connect(self.addOutputDevice)

        app = Application.getInstance()
        self._preferences = app.getPreferences()
        self._preferences.addPreference(self.PREFERENCE_KEY_TOKEN, "{}")
        try:
            self._tokens = json.loads(
                self._preferences.getValue(self.PREFERENCE_KEY_TOKEN)
            )
        except ValueError:
            self._tokens = {}
        if not isinstance(self._tokens, dict):
            self._tokens = {}
        Logger.log("d", "Load tokens: {}".format(self._tokens))

        app.initializationFinished.connect(self.start)
        app.applicationShuttingDown.connect(self.stop)

    def start(self):
        if self._discover_thread is None or not self._discover_thread.is_alive():
            self._discover_thread = threading.Thread(target=self._discoverThread, daemon=True)
            self._discover_thread.start()

    def stop(self):
        self._discovering.set()
        if self._discover_thread and self._discover_thread.is_alive():
            self._discover_thread.join(timeout=1)
        self._saveTokens()

    def _saveTokens(self):
        devices = self.getOutputDeviceManager().getOutputDevices()
        for d in devices:
            if hasattr(d, "getToken") and hasattr(d, "getModel") and d.getToken():
                name = self._tokensKeyName(d.getName(), d.getModel())
                self._tokens[name] = d.getToken()
        if self._preferences and len(self._tokens.keys()):
            self._preferences.setValue(self.PREFERENCE_KEY_TOKEN, json.dumps(self._tokens))
            Logger.log("d", "%d tokens saved." % len(self._tokens.keys()))

    def startDiscovery(self):
        Logger.log("i", "Discover start")
        if self._preferences:
            self._preferences.resetPreference(self.PREFERENCE_KEY_TOKEN)
        self._addRemoveDevice(self._discover(timeout=3))
        Logger.log("i", "Discover finished, found %d devices.", len(self._discovered_devices))

    def _discoverThread(self):
        while not self._discovering.is_set():
            self._addRemoveDevice(self._discover())
            self._saveTokens()  # TODO
            self._discovering.wait(4.0)

    def _discover(self, msg=b"discover", port=20054, timeout=2):
        devices = []
        cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        cs.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        cs.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        cs.settimeout(timeout)

        try:
            cs.sendto(msg, ("<broadcast>", port))

            while True:
                resp, (ip, _) = cs.recvfrom(512)
                if b"|model:" in resp and b"|status:" in resp:
                    Logger.log("d", "Found device [%s] %s", ip, resp)
                    devices.append((ip, resp))
                else:
                    Logger.log("w", "Unknown device %s from %s", resp, ip)
        except (socket.timeout, OSError):
            return devices

    def _addRemoveDevice(self, devices: list):
        self._discovered_devices = devices
        self.discoveredDevicesChanged.emit()

    def addOutputDevice(self):
        for ip, resp in self._discovered_devices:
            id, name, model = self._parse(resp.decode())
            if not id:
                continue
            device = self.getOutputDeviceManager().getOutputDevice(id)
            if not device:
                device = SM2OutputDevice(ip, id, name, model)
                key = self._tokensKeyName(name, model)
                if key in self._tokens:
                    device.setToken(self._tokens[key])
                self.getOutputDeviceManager().addOutputDevice(device)

    def _tokensKeyName(self, name, model) -> str:
        return "{}@{}".format(name, model)

    def _parse(self, resp):
        """
        Snapmaker-DUMMY@127.0.0.1|model:Snapmaker 2 Model A350|status:IDLE
        """
        p_model = resp.find("|model:")
        p_status = resp.find("|status:")
        if p_model and p_status:
            id = resp[:p_model]
            name = id[:id.rfind("@")]
            model = resp[p_model + 7:p_status]
            # status = resp[p_status + 8:]
            return id, name, model
        return None, None, None


class SM2OutputDevice(NetworkedPrinterOutputDevice):

    def __init__(self, address, device_id, name, model):
        super().__init__(
            device_id=device_id,
            address=address,
            properties={},
            connection_type=ConnectionType.NetworkConnection)

        self._model = model
        self._name = name
        self._api_prefix = ":8080/api/v1"
        self._auth_token = ""
        self._gcode_stream = StringIO()
        self._writing = False

        self._setInterface()

        self._authentication_state = AuthState.NotAuthenticated
        self.authenticationStateChanged.connect(self._onAuthenticationStateChanged)

        self.connectionStateChanged.connect(self._onConnectionStateChanged)

        self._progress = PrintJobUploadProgressMessage(self)
        self._need_auth = PrintJobNeedAuthMessage(self)

    def getToken(self) -> str:
        return self._auth_token

    def setToken(self, token: str):
        Logger.log("d", "setToken: %s", token)
        self._auth_token = token

    def getModel(self) -> str:
        return self._model

    def _setInterface(self):
        self.setPriority(2)
        self.setShortDescription("Send to {}".format(self._address))  # button
        self.setDescription("Send to {}".format(self._id))  # pop menu
        self.setConnectionText("Connected to {}".format(self._id))

    def _onConnectionStateChanged(self):
        if self.connectionState == ConnectionState.Busy:
            Message(title="Unable to upload", text="{} is busy".format(self._id)).show()

    def _setAuthState(self, state: "AuthState"):
        self._authentication_state = state
        self.authenticationStateChanged.emit()

    def _onAuthenticationStateChanged(self):
        if self.authenticationState == AuthState.Authenticated:
            self._need_auth.hide()
        elif self.authenticationState == AuthState.AuthenticationRequested:
            self._need_auth.show()
        elif self.authenticationState == AuthState.AuthenticationDenied:
            self._auth_token = ""
            self._need_auth.hide()

    def requestWrite(self, nodes, file_name=None, limit_mimetypes=False, file_handler=None, filter_by_machine=False, **kwargs) -> None:
        if self._progress.visible or self._need_auth.visible or self._writing:
            return

        self.writeStarted.emit(self)
        self._writing = True

        self._gcode_stream = StringIO()
        job = WriteFileJob(SM2GCodeWriter(), self._gcode_stream, nodes, SM2GCodeWriter.OutputMode.TextMode)
        job.finished.connect(self._onWriteJobFinished)

        message = Message(title="Preparing for upload", progress=-1, lifetime=0, dismissable=False, use_inactivity_timer=False)
        message.show()

        job.setMessage(message)
        job.start()

    def _onWriteJobFinished(self, job):
        self._writing = False
        self._auth_token = self.connect()
        self._startUpload()

    def _queryParams(self):
        return {
            "token": self._auth_token,
            "_": time.time(),
        }

    def connect(self) -> str:
        super().connect()
        try:
            conn = requests.post("http://" + self._address + self._api_prefix + "/connect", data=self._queryParams())
            Logger.log("d", "/connect: %d from %s", conn.status_code, self._address)
            if conn.status_code == 200:
                return conn.json().get("token")
            elif conn.status_code == 403 and self._auth_token:
                # expired
                self._auth_token = ""
                return self.connect()
            else:
                Message(text="Please check the touchscreen and try again (Err: %d)." % conn.status_code, lifetime=10, dismissable=True).show()
                return self._auth_token

        except requests.exceptions.ConnectionError as e:
            Message(title="Error", text=str(e), dismissable=True).show()
            return self._auth_token

    def disconnect(self):
        requests.post("http://" + self._address + self._api_prefix + "/disconnect", data=self._queryParams())
        self.setConnectionState(ConnectionState.Closed)
        Logger.log("d", "/disconnect")

    def check_status(self):
        try:
            conn = requests.get("http://" + self._address + self._api_prefix + "/status", params=self._queryParams())
            Logger.log("d", "/status: %d from %s", conn.status_code, self._address)
            if conn.status_code == 200:

                status = conn.json().get("status", "UNKNOWN")
                Logger.log("d", "Printer status is %s" % status)
                if status == "IDLE":
                    self.setConnectionState(ConnectionState.Connected)
                elif status in ("RUNNING", "PAUSED", "STOPPED"):
                    self.setConnectionState(ConnectionState.Busy)
                else:
                    self.setConnectionState(ConnectionState.Error)

                self._setAuthState(AuthState.Authenticated)

            if conn.status_code == 401:
                self._setAuthState(AuthState.AuthenticationDenied)

            if conn.status_code == 204:
                self._setAuthState(AuthState.AuthenticationRequested)

        except:
            self._setAuthState(AuthState.NotAuthenticated)

    def _startUpload(self):
        Logger.log("d", "{} token is {}".format(self._name, self._auth_token))
        if not self._auth_token:
            return

        self.check_status()

        if self.connectionState != ConnectionState.Connected:
            return

        if self.authenticationState != AuthState.Authenticated:
            return

        self._progress.show()

        print_info = CuraApplication.getInstance().getPrintInformation()
        job_name = print_info.jobName.strip()
        print_time = print_info.currentPrintTime
        material_name = "-".join(print_info.materialNames)

        file_name = "{}_{}_{}.gcode".format(
            job_name,
            material_name,
            "{}h{}m{}s".format(
                print_time.days * 24 + print_time.hours,
                print_time.minutes,
                print_time.seconds)
        )

        parts = [
            self._createFormPart("name=token", self._auth_token.encode()),
            self._createFormPart("name=file; filename=\"{}\"".format(file_name), self._gcode_stream.getvalue().encode())
        ]
        self._gcode_stream.close()
        self.postFormWithParts("/upload", parts,
                               on_finished=lambda reply: self._onUploadCompleted(file_name, reply),
                               on_progress=self._onUploadProgress)

    def _onUploadCompleted(self, filename, reply):
        self._progress.hide()

        if self.connectionState == ConnectionState.Connected:
            self.disconnect()

        if reply.error() == QNetworkReplyNetworkErrors.NoError:
            Message(
                title="Sent to {}".format(self._id),
                text="Start print on the touchscreen: {}".format(filename),
                lifetime=0).show()
            self.writeFinished.emit()
        else:
            Message(title="Error", text=reply.errorString(), lifetime=0, dismissable=True).show()
            self.writeError.emit()

    def _onUploadProgress(self, bytes_sent: int, bytes_total: int):
        if bytes_total > 0:
            perc = (bytes_sent / bytes_total) if bytes_total else 0
            self._progress.setProgress(perc * 100)
            self.writeProgress.emit()

    def checkAndStartUpload(self):
        self._startUpload()


class PrintJobUploadProgressMessage(Message):
    def __init__(self, device):
        super().__init__(
            title="Sending to {}".format(device.getId()),
            text="Uploading print job to printer:",
            progress=-1,
            lifetime=0,
            dismissable=False,
            use_inactivity_timer=False
        )
        self._device = device
        self._gTimer = QTimer()
        self._gTimer.setInterval(3 * 1000)
        self._gTimer.timeout.connect(lambda: self._heartbeat())
        self.inactivityTimerStart.connect(self._startTimer)
        self.inactivityTimerStop.connect(self._stopTimer)

    def show(self):
        self.setProgress(0)
        super().show()

    def update(self, percentage: int) -> None:
        if not self._visible:
            super().show()
        self.setProgress(percentage)

    def _heartbeat(self):
        self._device.check_status()

    def _startTimer(self):
        if self._gTimer and not self._gTimer.isActive():
            self._gTimer.start()

    def _stopTimer(self):
        if self._gTimer and self._gTimer.isActive():
            self._gTimer.stop()


class PrintJobNeedAuthMessage(Message):
    def __init__(self, device) -> None:
        super().__init__(
            title="Screen authorization needed",
            text="Please tap Yes on Snapmaker touchscreen to continue.",
            lifetime=0,
            dismissable=True,
            use_inactivity_timer=False
        )
        self._device = device
        self.setProgress(-1)
        # self.addAction("", "Continue", "", "")
        # self.actionTriggered.connect(self._onCheck)
        self._gTimer = QTimer()
        self._gTimer.setInterval(1500)
        self._gTimer.timeout.connect(lambda: self._onCheck(None, None))
        self.inactivityTimerStart.connect(self._startTimer)
        self.inactivityTimerStop.connect(self._stopTimer)

    def _startTimer(self):
        if self._gTimer and not self._gTimer.isActive():
            self._gTimer.start()

    def _stopTimer(self):
        if self._gTimer and self._gTimer.isActive():
            self._gTimer.stop()

    def _onCheck(self, messageId, actionId):
        self._device.checkAndStartUpload()
        # self.hide()
