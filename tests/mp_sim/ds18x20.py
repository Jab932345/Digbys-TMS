import sim_ctl


class DS18X20:
    def __init__(self, ow):
        pass

    def scan(self):
        return [b'\x28rom'] if sim_ctl.ctl().get('probe', True) else []

    def convert_temp(self):
        if not sim_ctl.ctl().get('probe', True):
            raise OSError('no presence')

    def read_temp(self, rom):
        c = sim_ctl.ctl()
        if not c.get('probe', True) or c.get('temp') is None:
            raise Exception('CRC error')
        return float(c['temp'])
