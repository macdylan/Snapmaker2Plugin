import ipaddress
import time
import json
from io import StringIO
from typing import List
try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtNetwork import (
        QUdpSocket,
        QHostAddress,
        QHttpPart,
        QNetworkRequest,
        QNetworkAccessManager,
        QNetworkReply,
        QNetworkInterface,
        QAbstractSocket
    )
    QNetworkAccessManagerOperations = QNetworkAccessManager.Operation
    QNetworkRequestAttributes = QNetworkRequest.Attribute
    QNetworkReplyNetworkErrors = QNetworkReply.NetworkError
    QHostAddressBroadcast = QHostAddress.SpecialAddress.Broadcast
    QIPv4Protocol = QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
except ImportError:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtNetwork import (
        QUdpSocket,
        QHostAddress,
        QHttpPart,
        QNetworkRequest,
        QNetworkAccessManager,
        QNetworkReply,
        QNetworkInterface,
        QAbstractSocket
    )
    QNetworkAccessManagerOperations = QNetworkAccessManager
    QNetworkRequestAttributes = QNetworkRequest
    QNetworkReplyNetworkErrors = QNetworkReply
    QHostAddressBroadcast = QHostAddress.SpecialAddress.Broadcast
    QIPv4Protocol = QAbstractSocket.NetworkLayerProtocol.IPv4Protocol

from cura.CuraApplication import CuraApplication
from cura.PrinterOutput.NetworkedPrinterOutputDevice import NetworkedPrinterOutputDevice, AuthState
from cura.PrinterOutput.PrinterOutputDevice import ConnectionState

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

        self._discover_sockets = []
        for interface in QNetworkInterface.allInterfaces():
            for addr in interface.addressEntries():
                bcastAddress = addr.broadcast()
                ipAddress = addr.ip()
                if not ipAddress.isLoopback() and ipAddress.protocol() == QIPv4Protocol and bcastAddress != ipAddress:
                    Logger.log("i", "Discovering printers on network interface: {}".format(ipAddress.toString()))
                    socket = QUdpSocket()
                    socket.bind(ipAddress)
                    socket.readyRead.connect(lambda: self._udpProcessor(socket))
                    self._discover_sockets.append(socket)

        self._discover_timer = QTimer()
        self._discover_timer.setInterval(6000)
        self._discover_timer.timeout.connect(self._onDiscovering)

        self._discovered_devices = set()  # set{ip:bytes, id:bytes}
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

        app.globalContainerStackChanged.connect(self.start)
        app.applicationShuttingDown.connect(self.stop)

    def start(self):
        if self._discover_timer and not self._discover_timer.isActive():
            self._onDiscovering()
            self._discover_timer.start()
            Logger.log("i", "Snapmaker2Plugin started.")

    def stop(self):
        for socket in self._discover_sockets:
            socket.abort()
        if self._discover_timer and self._discover_timer.isActive():
            self._discover_timer.stop()
        self._saveTokens()
        Logger.log("i", "Snapmaker2Plugin stopped.")

    def _saveTokens(self):
        updated = False
        devices = self.getOutputDeviceManager().getOutputDevices()
        for d in devices:
            if hasattr(d, "getToken") and hasattr(d, "getModel") and d.getToken():
                name = self._tokensKeyName(d.getName(), d.getModel())
                if name not in self._tokens or self._tokens[name] != d.getToken():
                    self._tokens[name] = d.getToken()
                    updated = True
        if updated and self._preferences:
            try:
                self._preferences.setValue(self.PREFERENCE_KEY_TOKEN, json.dumps(self._tokens))
                Logger.log("d", "%d tokens saved." % len(self._tokens.keys()))
            except ValueError:
                self._tokens = {}

    def startDiscovery(self):
        Logger.log("i", "Discovering ...")
        self._onDiscovering()

    def _udpProcessor(self, socket):
        devices = set()
        while socket.hasPendingDatagrams():
            data = socket.receiveDatagram()
            if data.isValid() and not data.senderAddress().isNull():
                ip = data.senderAddress().toString()
                try:
                    msg = bytes(data.data()).decode("utf-8")
                    if "|model:" in msg and "|status:" in msg:
                        devices.add((ip, msg))
                    else:
                        Logger.log("w", "Unknown device %s from %s", msg, ip)
                except UnicodeDecodeError:
                    pass
        if len(devices) and self._discovered_devices != devices:
            Logger.log("i", "Discover finished, found %d devices.", len(devices))
            self._discovered_devices = devices
            self.discoveredDevicesChanged.emit()

    def _onDiscovering(self, *args, **kwargs):
        for socket in self._discover_sockets:
            socket.writeDatagram(b"discover", QHostAddressBroadcast, 20054)
        self._saveTokens()  # TODO

    def addOutputDevice(self):
        for ip, resp in self._discovered_devices:
            Logger.log("d", "Found device [%s] %s", ip, resp)
            id, name, model, status = self._parse(resp)
            if not id:
                continue
            device = self.getOutputDeviceManager().getOutputDevice(id)
            if not device:
                device = SM2OutputDevice(ip, id, name, model)
                self.getOutputDeviceManager().addOutputDevice(device)
            key = self._tokensKeyName(name, model)
            if key in self._tokens:
                device.setToken(self._tokens[key])
            device.setDeviceStatus(status)

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
            status = resp[p_status + 8:]
            return id, name, model, status
        return None, None, None, None


class SM2OutputDevice(NetworkedPrinterOutputDevice):

    def __init__(self, address, device_id, name, model, **kwargs):
        super().__init__(
            device_id=device_id,
            address=address,
            properties={},
            **kwargs)

        self._model = model
        self._name = name
        self._api_prefix = ":8080/api/v1"
        self._auth_token = ""
        self._filename = ""
        self._gcode_stream = StringIO()

        self._setInterface()

        self.authenticationStateChanged.connect(self._onAuthenticationStateChanged)
        self.connectionStateChanged.connect(self._onConnectionStateChanged)
        self.writeFinished.connect(self._byebye)

        self._progress = PrintJobUploadProgressMessage(self)
        self._need_auth = PrintJobNeedAuthMessage(self)

    def getToken(self) -> str:
        return self._auth_token

    def setToken(self, token: str):
        Logger.log("d", "%s setToken: %s", self.getId(), token)
        self._auth_token = token

    def getModel(self) -> str:
        return self._model

    def setDeviceStatus(self, status: str):
        Logger.log("d", "%s setDeviceStatus: %s, last state: %s", self.getId(), status, self.connectionState)
        if status == "IDLE":
            if self.connectionState != ConnectionState.Connected:
                self.setConnectionState(ConnectionState.Connected)
        elif status in ("RUNNING", "PAUSED", "STOPPED"):
            if self.connectionState != ConnectionState.Busy:
                self.setConnectionState(ConnectionState.Busy)

    def _onConnectionStateChanged(self, id):
        Logger.log("d", "onConnectionStateChanged: id: %s, state: %s", id, self.connectionState)
        if id != self.getId():
            return

        if (
            self.connectionState == ConnectionState.Connected and
            self.authenticationState == AuthState.Authenticated
        ):
            if self._sending_gcode and not self._progress.visible:
                self._progress.show()
                self._upload()

    def _onAuthenticationStateChanged(self):
        if self.authenticationState == AuthState.Authenticated:
            self._need_auth.hide()
        elif self.authenticationState == AuthState.AuthenticationRequested:
            self._need_auth.show()
        elif self.authenticationState == AuthState.AuthenticationDenied:
            self._auth_token = ""
            self._sending_gcode = False
            self._need_auth.hide()

    def _setInterface(self):
        self.setPriority(2)
        self.setShortDescription("Send to {}".format(self._address))  # button
        self.setDescription("Send to {}".format(self._id))  # pop menu
        self.setConnectionText("Connected to {}".format(self._id))

    def requestWrite(self, nodes,
                     file_name=None, limit_mimetypes=False, file_handler=None, filter_by_machine=False, **kwargs) -> None:
        if self.connectionState == ConnectionState.Busy:
            Message(title="Unable to upload", text="{} is busy.".format(self.getId())).show()
            return

        if self._progress.visible or self._need_auth.visible:
            Logger.log("i", "Still working in progress.")
            return

        # reset
        self._sending_gcode = True
        self.setConnectionState(ConnectionState.Closed)
        self.setAuthenticationState(AuthState.NotAuthenticated)

        self.writeStarted.emit(self)
        self._gcode_stream = StringIO()
        job = WriteFileJob(SM2GCodeWriter(), self._gcode_stream, nodes, SM2GCodeWriter.OutputMode.TextMode)
        job.finished.connect(self._onWriteJobFinished)

        message = Message(
            title="Preparing for upload",
            progress=-1,
            lifetime=0,
            dismissable=False,
            use_inactivity_timer=False)
        message.show()

        job.setMessage(message)
        job.start()

    def _onWriteJobFinished(self, job):
        self._hello()

    def _queryParams(self) -> List[QHttpPart]:
        return [
            self._createFormPart('name=token', self._auth_token.encode()),
            self._createFormPart('name=_', "{}".format(time.time()).encode())
        ]

    def _hello(self) -> None:
        # if self._auth_token:
        #     # Closed to prevent set Connected in NetworkedPrinterOutputDevice._handleOnFinished
        #     self.setConnectionState(ConnectionState.Closed)
        self.postFormWithParts("/connect", self._queryParams(), self._onRequestFinished)

    def _byebye(self):
        if self._auth_token:
            self.postFormWithParts("/disconnect", self._queryParams(),
                                   lambda r: self.setConnectionState(ConnectionState.Closed))

    def checkStatus(self):
        url = "/status?token={}&_={}".format(self._auth_token, time.time())
        self.get(url, self._onRequestFinished)

    def _upload(self):
        Logger.log("d", "Start upload to {} with token {}".format(self._name, self._auth_token))
        if not self._auth_token:
            return

        print_info = CuraApplication.getInstance().getPrintInformation()
        job_name = print_info.jobName.strip()
        print_time = print_info.currentPrintTime
        material_name = "-".join(print_info.materialNames)

        self._filename = "{}_{}_{}.gcode".format(
            job_name,
            material_name,
            "{}h{}m{}s".format(
                print_time.days * 24 + print_time.hours,
                print_time.minutes,
                print_time.seconds)
        )

        parts = self._queryParams()
        parts.append(
            self._createFormPart(
                'name=file; filename="{}"'.format(self._filename),
                self._gcode_stream.getvalue().encode()
            )
        )
        self._gcode_stream.close()
        self.postFormWithParts("/upload", parts,
                               on_finished=self._onRequestFinished,
                               on_progress=self._onUploadProgress)

    def _onUploadProgress(self, bytes_sent: int, bytes_total: int):
        if bytes_total > 0:
            perc = (bytes_sent / bytes_total) if bytes_total else 0
            self._progress.setProgress(perc * 100)
            self.writeProgress.emit()

    def _onRequestFinished(self, reply: QNetworkReply) -> None:
        http_url = reply.url().toString()

        if reply.error() not in (
            QNetworkReplyNetworkErrors.NoError,
            QNetworkReplyNetworkErrors.AuthenticationRequiredError  # 204 is No Content, not and error
        ):
            Logger.log("w", "Error %d from %s", reply.error(), http_url)
            self.setConnectionState(ConnectionState.Closed)
            Message(
                title="Error",
                text=reply.errorString(),
                lifetime=0,
                dismissable=True
            ).show()
            return

        http_code = reply.attribute(QNetworkRequestAttributes.HttpStatusCodeAttribute)
        Logger.log("i", "Request: %s - %d", http_url, http_code)
        if not http_code:
            return

        http_method = reply.operation()
        if http_method == QNetworkAccessManagerOperations.GetOperation:
            if self._api_prefix + "/status" in http_url:
                if http_code == 200:
                    self.setAuthenticationState(AuthState.Authenticated)
                    resp = self._jsonReply(reply)
                    device_status = resp.get("status", "UNKNOWN")
                    self.setDeviceStatus(device_status)
                elif http_code == 401:
                    self.setAuthenticationState(AuthState.AuthenticationDenied)
                elif http_code == 204:
                    self.setAuthenticationState(AuthState.AuthenticationRequested)
                else:
                    self.setAuthenticationState(AuthState.NotAuthenticated)

        elif http_method == QNetworkAccessManagerOperations.PostOperation:
            if self._api_prefix + "/connect" in http_url:
                if http_code == 200:
                    resp = self._jsonReply(reply)
                    token = resp.get("token")
                    if self._auth_token != token:
                        Logger.log("i", "New token: %s", token)
                        self._auth_token = token
                    self.checkStatus()  # check status and upload

                elif http_code == 403 and self._auth_token:
                    # expired
                    self._auth_token = ""
                    self.connect()
                else:
                    self.setConnectionState(ConnectionState.Closed)
                    Message(
                        title="Error",
                        text="Please check the touchscreen and try again (Err: {}).".format(http_code),
                        lifetime=10,
                        dismissable=True
                    ).show()

            # elif self._api_prefix + "/disconnect" in http_url:
            #     self.setConnectionState(ConnectionState.Closed)

            elif self._api_prefix + "/upload" in http_url:
                self._progress.hide()
                self.writeFinished.emit()
                self._sending_gcode = False

                Message(
                    title="Sent to {}".format(self.getId()),
                    text="Start print on the touchscreen: {}".format(self._filename),
                    lifetime=60
                ).show()

    def _jsonReply(self, reply: QNetworkReply):
        try:
            return json.loads(bytes(reply.readAll()).decode("utf-8"))
        except json.decoder.JSONDecodeError:
            Logger.log("w", "Received invalid JSON from snapmaker.")
            return {}


class PrintJobUploadProgressMessage(Message):
    def __init__(self, device):
        super().__init__(
            title="Sending to {}".format(device.getId()),
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
        self._device.checkStatus()

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
        self._device.checkStatus()
        # self.hide()
