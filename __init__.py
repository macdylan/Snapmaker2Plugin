from . import SM2OutputDeviceManager
from . import SM2GCodeWriter

def getMetaData():
    return {
        "mesh_writer": {
            "output": [
                {
                    "extension": "gcode",
                    "description": "Snapmaker G-code file",
                    "mime_type": "text/x-gcode",
                    "mode": SM2GCodeWriter.SM2GCodeWriter.OutputMode.TextMode
                }
            ]
        }
    }

def register(app):
    return {
        "output_device": SM2OutputDeviceManager.SM2OutputDeviceManager(),
        "mesh_writer": SM2GCodeWriter.SM2GCodeWriter()
    }
