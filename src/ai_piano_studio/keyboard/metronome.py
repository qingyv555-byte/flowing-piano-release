"""Short monitor-only clicks; no project events, MIDI notes or export inputs."""
from array import array
from math import sin, pi, exp
from ai_piano_studio.audio.profile import ReleaseMode


class MonitorClick:
    def __init__(self, mixer):
        self.mixer = mixer
        self.index = 0
        self.samples = {
            accent: array('h', (int(10000 * sin(2*pi*(1600 if accent else 1000)*i/44100)
                                       * exp(-i/210)) for i in range(2205)))
            for accent in (False, True)
        }

    def tick(self, accent, volume):
        self.index += 1
        self.mixer.start_voice(f'monitor-click:{self.index % 4}', 84, max(1, round(volume*127)),
                              self.samples[bool(accent)], source_sample_rate=44100,
                              owner_tag='monitor-only', looping=False)

    def stop(self):
        for index in range(4):
            self.mixer.note_off(f'monitor-click:{index}', ReleaseMode.STOP)
