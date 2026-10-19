import _ctl
STA_IF, AP_IF = 0, 1


class WLAN:
    def __init__(self, i):
        self.i = i

    def active(self, v=None):
        return True

    def connect(self, ssid, pw):
        pass

    def disconnect(self):
        pass

    def isconnected(self):
        return _ctl.ctl().get('wifi', True)

    def status(self, k=None):
        return -58

    def ifconfig(self):
        return ('127.0.0.1', '', '', '')
