import threading
import socket
import requests
from io import StringIO
from PyQt5.QtCore import QTimer

from cura.CuraApplication import CuraApplication
from cura.PrinterOutput.NetworkedPrinterOutputDevice import NetworkedPrinterOutputDevice, AuthState
from cura.PrinterOutput.PrinterOutputDevice import ConnectionState, ConnectionType

from UM.Signal import Signal
from UM.Logger import Logger
from UM.Message import Message
from UM.FileHandler.WriteFileJob import WriteFileJob
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin

from .SM2GCodeWriter import SM2GCodeWriter


class SM2OutputDeviceManager(OutputDevicePlugin):

    discoveredDevicesChanged = Signal()

    def __init__(self):
        super().__init__()
        self._discovered_devices = [] # List[ip:bytes, id:bytes]
        self.discoveredDevicesChanged.connect(self.addOutputDevice)

        self._update_thread = None
        self._check_update = False

        self._app = CuraApplication.getInstance()
        self._preferences = self._app.getPreferences()

        self._app.globalContainerStackChanged.connect(self.start)
        self._app.applicationShuttingDown.connect(self.stop)

    def start(self):
        self._check_update = True
        if not self._update_thread or not self._update_thread.is_alive():
            self._update_thread = threading.Thread(target=self._updateThread, daemon=True)
            self._update_thread.start()

    def stop(self):
        self._check_update = False

    def _updateThread(self):
        while self._check_update:
            self._addRemoveDevice(self._discover())

    def _discover(self, msg=b"discover", port=20054, timeout=5):
        devices = []
        cs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        cs.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        cs.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        cs.settimeout(timeout)

        try:
            cs.sendto(msg, ("<broadcast>", port))

            while True:
                resp, (ip, _) = cs.recvfrom(512)
                if b"model:" in resp and b"status:" in resp:
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
            id, model, status = self._parse(resp)
            if id:
                device = self.getOutputDeviceManager().getOutputDevice(id.decode())
                if not device:
                    properties = {b"model": model, b"status": status}
                    device = SM2OutputDevice(id.decode(), ip, properties)
                    self.getOutputDeviceManager().addOutputDevice(device)

    def _parse(self, resp:bytes):
        """
        Snapmaker-DUMMY@127.0.0.1|model:Snapmaker 2 Model A350|status:IDLE
        """
        p_model = resp.find(b"|model:")
        p_status = resp.find(b"|status:")
        if p_model and p_status:
            id = resp[:p_model]
            model = resp[p_model+7:p_status]
            status = resp[p_status+8:]
            return id, model, status
        return None, None, None


class SM2OutputDevice(NetworkedPrinterOutputDevice):

    def __init__(self, device_id, address, properties={}):
        super().__init__(
            device_id=device_id,
            address=address,
            properties=properties,
            connection_type=ConnectionType.NetworkConnection)

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

    def _setInterface(self):
        self.setPriority(2)
        self.setName("Snapmaker 2.0 Printing")
        self.setShortDescription("Send to {}".format(self._address))
        self.setDescription("Send to {}".format(self._id))
        self.setConnectionText("Connected to {}".format(self._id))

    def _onConnectionStateChanged(self):
        if self.connectionState == ConnectionState.Busy:
            Message(title="Unable to upload", text="{} is busy".format(self._id)).show()

    def _setAuthState(self, state):
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

    def connect(self) -> str:
        super().connect()
        try:
            conn = requests.post("http://" + self._address + self._api_prefix + "/connect",
                                data={"token": self._auth_token})
            Logger.log("d", "/connect: %d from %s", conn.status_code, self._address)
            if conn.status_code == 200:
                return conn.json().get("token")
            elif conn.status_code == 403 and self._auth_token:
                # expired
                self._auth_token = ""
                return self.connect()
            else:
                Message(text="Please check the touchscreen and try again.", lifetime=10, dismissable=True).show()
                return self._auth_token

        except requests.exceptions.ConnectionError as e:
            Message(title="Error", text=str(e), dismissable=True).show()
            return self._auth_token

    def check_status(self):
        try:
            conn = requests.get("http://" + self._address + self._api_prefix + "/status", params={"token": self._auth_token})
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
        Logger.log("d", "Token: %s", self._auth_token)
        if not self._auth_token:
            return

        self.check_status()

        if self.connectionState != ConnectionState.Connected:
            return

        if self.authenticationState != AuthState.Authenticated:
            return

        self._progress.show()

        name = CuraApplication.getInstance().getPrintInformation().jobName.strip()
        if name is "":
            name = "untitled_print"
        file_name = "%s.gcode" % name

        parts = [
            self._createFormPart("name=token", self._auth_token.encode()),
            self._createFormPart("name=file; filename=\"{}\"".format(file_name), self._gcode_stream.getvalue().encode())
        ]
        self._gcode_stream = StringIO()
        self.postFormWithParts("/upload", parts,
                        on_finished=self._onUploadCompleted,
                        on_progress=self._onUploadProgress)

    def _onUploadCompleted(self, reply):
        self._progress.hide()
        if not reply.error():
            Message(
                title="Sent to {}".format(self._id),
                text="Start print on the touchscreen.",
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
            title = "Sending Print Job",
            text = "Uploading print job to printer:",
            progress = -1,
            lifetime = 0,
            dismissable = False,
            use_inactivity_timer = False
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
            title = "Screen authorization needed",
            text = "Please tap Yes on Snapmaker touchscreen to continue.",
            lifetime = 0,
            dismissable = True,
            use_inactivity_timer = False
        )
        self._device = device
        self.setProgress(-1)
        # self.addAction("", "Continue", "", "")
        # self.actionTriggered.connect(self._onCheck)
        self._gTimer = QTimer()
        self._gTimer.setInterval(1.5 * 1000)
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
