import sim_ctl
STA_IF = 0
AP_IF = 1
CALLS = []


class WLAN:
    def __init__(self, i):
        self.i = i

    def active(self, v=None):
        return True

    def connect(self, ssid=None, pw=None):
        CALLS.append('creds' if ssid is not None else 'plain')

    def disconnect(self):
        pass

    def isconnected(self):
        return sim_ctl.ctl().get('wifi', True)

    def status(self, k=None):
        return -58

    def ifconfig(self):
        return ('127.0.0.1', '', '', '')
