import time
import json
from typing import List
from io import StringIO

from cura.CuraApplication import CuraApplication
from cura.PrinterOutput.NetworkedPrinterOutputDevice import NetworkedPrinterOutputDevice, AuthState
from cura.PrinterOutput.PrinterOutputDevice import ConnectionState

from UM.Logger import Logger
from UM.Message import Message
from UM.FileHandler.WriteFileJob import WriteFileJob

from .qt_comp import *
from .GCodeWriter import SM2GCodeWriter


class SM2OutputDevice(NetworkedPrinterOutputDevice):

    def __init__(self, device_id, address, token, properties={}, **kwargs):
        assert "@" in device_id
        super().__init__(device_id, address, properties, **kwargs)

        self._name, self._model = device_id.rsplit("@", 1)
        self._token = token

        self._filename = ""
        self._api_prefix = ":8080/api/v1"
        self._gcode_stream = StringIO()

        self.setPriority(2)
        self.setShortDescription("Send to {}".format(self._address))  # button
        self.setDescription("Send to {}".format(self._id))  # pop menu
        self.setConnectionText("Connected to {}".format(self._id))

        self.authenticationStateChanged.connect(
            self._onAuthenticationStateChanged)
        self.connectionStateChanged.connect(self._onConnectionStateChanged)
        self.writeFinished.connect(self._byebye)

        self._progress = PrintJobUploadProgressMessage(self)
        self._need_auth = PrintJobNeedAuthMessage(self)

    def getToken(self) -> str:
        return self._token

    def setToken(self, token: str):
        self._token = token

    def getModel(self) -> str:
        return self._model

    def setDeviceStatus(self, status: str):
        Logger.debug("%s setDeviceStatus: %s, last state: %s", self.getId(),
                     status, self.connectionState)
        if status == "IDLE":
            if self.connectionState != ConnectionState.Connected:
                self.setConnectionState(ConnectionState.Connected)
        elif status in ("RUNNING", "PAUSED", "STOPPED"):
            if self.connectionState != ConnectionState.Busy:
                self.setConnectionState(ConnectionState.Busy)

    def _onConnectionStateChanged(self, id):
        Logger.debug("onConnectionStateChanged: id: %s, state: %s", id,
                     self.connectionState)
        if id != self.getId():
            return

        if (self.connectionState == ConnectionState.Connected
                and self.authenticationState == AuthState.Authenticated):
            if self._sending_gcode and not self._progress.visible:
                self._progress.show()
                self._upload()

    def _onAuthenticationStateChanged(self):
        if self.authenticationState == AuthState.Authenticated:
            self._need_auth.hide()
        elif self.authenticationState == AuthState.AuthenticationRequested:
            self._need_auth.show()
        elif self.authenticationState == AuthState.AuthenticationDenied:
            self._token = ""
            self._sending_gcode = False
            self._need_auth.hide()

    def requestWrite(self,
                     nodes,
                     file_name=None,
                     limit_mimetypes=False,
                     file_handler=None,
                     filter_by_machine=False,
                     **kwargs) -> None:
        if self.connectionState == ConnectionState.Busy:
            Message(title="Unable to upload",
                    text="{} is busy.".format(self.getId())).show()
            return

        if self._progress.visible or self._need_auth.visible:
            Logger.info("Still working in progress.")
            return

        # reset
        self._sending_gcode = True
        self.setConnectionState(ConnectionState.Closed)
        self.setAuthenticationState(AuthState.NotAuthenticated)

        self.writeStarted.emit(self)
        self._gcode_stream = StringIO()
        job = WriteFileJob(SM2GCodeWriter(), self._gcode_stream, nodes,
                           SM2GCodeWriter.OutputMode.TextMode)
        job.finished.connect(self._onWriteJobFinished)

        message = Message(title="Preparing for upload",
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
            self._createFormPart('name=token', self._token.encode()),
            self._createFormPart('name=_', "{}".format(time.time()).encode())
        ]

    def _hello(self) -> None:
        self.postFormWithParts("/connect", self._queryParams(),
                               self._onRequestFinished)

    def _byebye(self):
        if self._token:
            self.postFormWithParts(
                "/disconnect", self._queryParams(),
                lambda r: self.setConnectionState(ConnectionState.Closed))

    def checkStatus(self):
        url = "/status?token={}&_={}".format(self._token, time.time())
        self.get(url, self._onRequestFinished)

    def _upload(self):
        Logger.debug("Start upload to {}".format(self._name))
        if not self._token:
            return

        print_info = CuraApplication.getInstance().getPrintInformation()
        job_name = print_info.jobName.strip()
        print_time = print_info.currentPrintTime
        material_name = "-".join(print_info.materialNames)

        self._filename = "{}_{}_{}.gcode".format(
            job_name, material_name,
            "{}h{}m{}s".format(print_time.days * 24 + print_time.hours,
                               print_time.minutes, print_time.seconds))

        parts = self._queryParams()
        parts.append(
            self._createFormPart(
                'name=file; filename="{}"'.format(self._filename),
                self._gcode_stream.getvalue().encode()))
        self._gcode_stream.close()
        self.postFormWithParts("/upload",
                               parts,
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
                QNetworkReplyNetworkErrors.
                AuthenticationRequiredError  # 204 is No Content, not an error
        ):
            Logger.warning("Error %d from %s", reply.error(), http_url)
            self.setConnectionState(ConnectionState.Closed)
            Message(title="Error",
                    text=reply.errorString(),
                    lifetime=0,
                    dismissable=True).show()
            return

        http_code = reply.attribute(
            QNetworkRequestAttributes.HttpStatusCodeAttribute)
        Logger.info("Request: %s - %d", http_url, http_code)
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
                    self.setAuthenticationState(
                        AuthState.AuthenticationRequested)
                else:
                    self.setAuthenticationState(AuthState.NotAuthenticated)

        elif http_method == QNetworkAccessManagerOperations.PostOperation:
            if self._api_prefix + "/connect" in http_url:
                if http_code == 200:
                    resp = self._jsonReply(reply)
                    token = resp.get("token")
                    if self._token != token:
                        self._token = token
                    self.checkStatus()  # check status and upload

                elif http_code == 403 and self._token:
                    # expired
                    self._token = ""
                    self.connect()
                else:
                    self.setConnectionState(ConnectionState.Closed)
                    Message(
                        title="Error",
                        text=
                        "Please check the touchscreen and try again (Err: {})."
                        .format(http_code),
                        lifetime=10,
                        dismissable=True).show()

            # elif self._api_prefix + "/disconnect" in http_url:
            #     self.setConnectionState(ConnectionState.Closed)

            elif self._api_prefix + "/upload" in http_url:
                self._progress.hide()
                self.writeFinished.emit()
                self._sending_gcode = False

                Message(title="Sent to {}".format(self.getId()),
                        text="Start print on the touchscreen: {}".format(
                            self._filename),
                        lifetime=60).show()

    def _jsonReply(self, reply: QNetworkReply):
        try:
            return json.loads(bytes(reply.readAll()).decode("utf-8"))
        except json.decoder.JSONDecodeError:
            Logger.warning("Received invalid JSON from snapmaker.")
            return {}


class PrintJobUploadProgressMessage(Message):

    def __init__(self, device: SM2OutputDevice):
        super().__init__(title="Sending to {}".format(device.getId()),
                         progress=-1,
                         lifetime=0,
                         dismissable=False,
                         use_inactivity_timer=False)
        self._device = device
        self._gTimer = QTimer()
        self._gTimer.setInterval(3 * 1000)
        self._gTimer.timeout.connect(lambda: self._heartbeat())
        self.inactivityTimerStart.connect(self._startTimer)
        self.inactivityTimerStop.connect(self._stopTimer)

    def show(self):
        self.setProgress(0)
        super().show()

    def update(self, percentage: int):
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

    def __init__(self, device: SM2OutputDevice):
        super().__init__(
            title="Screen authorization needed",
            text="Please tap Yes on Snapmaker touchscreen to continue.",
            lifetime=0,
            dismissable=True,
            use_inactivity_timer=False)
        self._device = device
        self.setProgress(-1)
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

    def _onCheck(self, *args, **kwargs):
        self._device.checkStatus()
