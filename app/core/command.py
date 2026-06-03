from enum import IntEnum


class EScriptType(IntEnum):
    BAT        = 1
    PYTHON     = 2
    SHELL      = 3
    POWERSHELL = 4

class ECommandStatus(IntEnum):
    WAITING    = 1
    RUNNING    = 2
    COMPLETED  = 3
    TERMINATED = 4
    FAILED     = 5
