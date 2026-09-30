"""Contador de lotes rodando por conversa: o /api/activity acende a bolinha e a interface avisa quando zera."""
import threading
import time

from app import lotes


def test_pendentes_acende_enquanto_roda_e_apaga_ao_terminar():
    solta = threading.Event()
    lotes._disparar(lambda conv: solta.wait(5), 991)
    lotes._disparar(lambda conv: 1 / 0, 991)  # o que estoura também sai da conta
    assert 991 in lotes.pendentes()
    solta.set()
    for _ in range(50):
        if 991 not in lotes.pendentes():
            break
        time.sleep(0.05)
    assert 991 not in lotes.pendentes()
