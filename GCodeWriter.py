import base64
from io import StringIO
from typing import cast

from UM.Mesh.MeshWriter import MeshWriter
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry

from cura.Snapshot import Snapshot
from cura.CuraApplication import CuraApplication
from cura.Settings.ExtruderManager import ExtruderManager
from cura.Utils.Threading import call_on_qt_thread
try:
    from PyQt6.QtCore import QBuffer
    from PyQt6.QtGui import QImage
    QBufferOpenMode = QBuffer.OpenModeFlag.ReadWrite
except ImportError:
    from PyQt5.QtCore import QBuffer
    from PyQt5.QtGui import QImage
    QBufferOpenMode = QBuffer.ReadWrite

from UM.i18n import i18nCatalog

catalog = i18nCatalog("cura")


class ModError(Exception):
    pass


class SM2GCodeWriter(MeshWriter):
    PROCESSED_IDENTITY = ";Processed by Snapmaker2Plugin (https://github.com/macdylan/Snapmaker2Plugin)"

    @call_on_qt_thread
    def write(self,
              stream,
              nodes,
              mode=MeshWriter.OutputMode.TextMode) -> bool:
        if mode != MeshWriter.OutputMode.TextMode:
            Logger.error("SM2GCodeWriter does not support non-text mode.")
            self.setInformation(
                catalog.i18nc(
                    "@error:not supported",
                    "SM2GCodeWriter does not support non-text mode."))
            return False

        gcode = StringIO()
        writer = cast(
            MeshWriter,
            PluginRegistry.getInstance().getPluginObject("GCodeWriter"))
        success = writer.write(gcode, None)

        if not success:
            self.setInformation(writer.getInformation())
            return False

        gcode.seek(0)
        try:
            result = self.mod(gcode)
            stream.write(result.getvalue())
            Logger.info("SM2GCodeWriter done")
            return True
        except ModError as e:
            self.setInformation(str(e))
            Logger.error(e)
            return False

    def mod(self, data: StringIO) -> StringIO:
        i = 0
        for x in data:
            if i > 100:
                break
            if x.find(self.PROCESSED_IDENTITY) != -1:
                return data
            i += 1

        data.seek(0)
        gcodes = data.readlines()

        p = StringIO()
        p.write(self.PROCESSED_IDENTITY + "\n")
        p.write(";Header Start\n")
        p.write(gcodes[0])  # FLAVOR
        p.write(gcodes[1])  # TIME
        p.write(gcodes[2])  # Filament used
        p.write(gcodes[3])  # Layer height
        p.write(";header_type: 3dp\n")

        ss = self._createSnapshot()
        if ss:
            p.write(";thumbnail: data:image/png;base64,")
            p.write(self._encodeSnapshot(ss))
            p.write("\n")

        app = CuraApplication.getInstance()
        print_time = int(app.getPrintInformation().currentPrintTime
                         ) * 1.07  # Times empirical parameter: 1.07
        print_speed = float(self._getValue("speed_infill"))
        print_temp = float(self._getValue("material_print_temperature"))
        bed_temp = float(self._getValue("material_bed_temperature")) or 0.0

        if not print_speed or not print_temp:
            raise ModError(
                "Unable to slice with the current settings: speed_infill or material_print_temperature"
            )

        p.write(";file_total_lines: %d\n" % len(gcodes))
        p.write(";estimated_time(s): %.0f\n" % print_time)
        p.write(";nozzle_temperature(°C): %.0f\n" % print_temp)
        p.write(";build_plate_temperature(°C): %.0f\n" % bed_temp)
        p.write(";work_speed(mm/minute): %.0f\n" % (print_speed * 60.0))
        p.write(gcodes[7].replace("MAXX:", "max_x(mm): "))  # max_x
        p.write(gcodes[8].replace("MAXY:", "max_y(mm): "))  # max_y
        p.write(gcodes[9].replace("MAXZ:", "max_z(mm): "))  # max_z
        p.write(gcodes[4].replace("MINX:", "min_x(mm): "))  # min_x
        p.write(gcodes[5].replace("MINY:", "min_y(mm): "))  # min_y
        p.write(gcodes[6].replace("MINZ:", "min_z(mm): "))  # min_z
        p.write(";Header End\n")

        p.write("".join(gcodes[10:]))
        return p

    def _createSnapshot(self) -> QImage:
        Logger.debug("Creating thumbnail image...")
        try:
            return Snapshot.snapshot(width=240, height=160)
        except Exception:
            Logger.logException("w", "Failed to create snapshot image")
            return None

    def _encodeSnapshot(self, snapshot: QImage) -> str:
        Logger.debug("Encoding thumbnail image...")
        try:
            thumbnail_buffer = QBuffer()
            thumbnail_buffer.open(QBufferOpenMode)
            thumbnail_image = snapshot
            thumbnail_image.save(thumbnail_buffer, "PNG")
            base64_bytes = base64.b64encode(thumbnail_buffer.data())
            base64_message = base64_bytes.decode('ascii')
            thumbnail_buffer.close()
            return base64_message
        except Exception:
            Logger.logException("w", "Failed to encode snapshot image")

    def _getValue(self, key) -> str:
        stack = ExtruderManager.getInstance().getActiveExtruderStack()
        if not stack:
            return ""

        GetType = stack.getProperty(key, "type")
        GetVal = stack.getProperty(key, "value")

        if str(GetType) == "float":
            GelValStr = "{:.4f}".format(GetVal).rstrip("0").rstrip(".")
        else:
            if str(GetType) == "enum":
                get_option = str(GetVal)
                GetOption = stack.getProperty(key, "options")
                GetOptionDetail = GetOption[get_option]
                GelValStr = GetOptionDetail
            else:
                GelValStr = str(GetVal)

        return GelValStr
