from .OutputDevicePlugin import SM2OutputDevicePlugin
from .GCodeWriter import SM2GCodeWriter


def getMetaData():
    return {
        "mesh_writer": {
            "output": [{
                "extension": "gcode",
                "description": "Snapmaker 2 G-code file",
                "mime_type": "text/x-gcode",
                "mode": SM2GCodeWriter.OutputMode.TextMode
            }]
        }
    }


def register(app):
    return {
        "output_device": SM2OutputDevicePlugin(),
        "mesh_writer": SM2GCodeWriter()
    }
