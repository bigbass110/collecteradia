import io

import numpy as np
import soundfile as sf

from collecte import audio as A


def signal(silence_avant=1.0, parole=2.0, silence_apres=1.0):
    t = np.linspace(0, parole, int(parole * A.FREQ), endpoint=False)
    voix = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    bruit = lambda s: (0.001 * np.random.default_rng(0).standard_normal(int(s * A.FREQ))).astype("float32")
    return np.concatenate([bruit(silence_avant), voix, bruit(silence_apres)])


def test_rognage_garde_la_parole_et_une_marge():
    rogne, debut, fin = A.rogner(signal())
    assert abs(len(rogne) / A.FREQ - (2.0 + 2 * A.MARGE_S)) < 0.1
    assert not debut and not fin


def test_detection_debut_coupe():
    _, debut, _ = A.rogner(signal(silence_avant=0.0))
    assert debut


def test_controles_bloquent_silence_et_duree():
    _, _, bloc = A.controler(np.zeros(A.FREQ * 3, dtype="float32"))
    assert any("inaudible" in b for b in bloc)
    _, _, bloc = A.controler(signal(0, 25, 0), "phrase")
    assert any("trop long" in b for b in bloc)
    _, _, bloc = A.controler(signal(0, 25, 0), "compte_rendu")
    assert bloc == []


def test_conversion_48k_stereo_vers_16k_mono():
    x = signal()
    x48 = np.repeat(x, 3)
    tampon = io.BytesIO()
    sf.write(tampon, np.stack([x48, x48], axis=1), 48_000, format="WAV")
    y = A.convertir_16k(tampon.getvalue())
    assert y.ndim == 1 and abs(len(y) - len(x)) < 100
