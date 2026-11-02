#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import queue
import threading
import time

import pigpio


def tao_goi(lenh, du_lieu=b""):
    goi = bytes([0xAA, lenh, len(du_lieu)]) + du_lieu
    return goi + bytes([sum(goi) & 0xFF])


def doc_ban_phim(hang_doi):
    try:
        while True:
            lua_chon = input().strip().lower()
            hang_doi.put(lua_chon)
            if lua_chon == "q":
                return
    except EOFError:
        hang_doi.put("q")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tx-pin", type=int, choices=[6, 16], default=6,
        help="GPIO n?i RX c?a module: m?c d?nh GPIO6"
    )
    args = parser.parse_args()
    tx = args.tx_pin

    pi = pigpio.pi("localhost", 8888)

    if not pi.connected:
        print("Chua k?t n?i pigpiod. Ch?y:")
        print("sudo /usr/local/bin/pigpiod -l")
        return

    # Ch? c?u hình chân TX, không di?u khi?n chân TX c?a module.
    if pi.wave_tx_busy():
        print("B? phát pigpio dang du?c chuong trình khác s? d?ng.")
        pi.stop()
        return

    che_do_cu = pi.get_mode(tx)
    if che_do_cu not in (pigpio.INPUT, pigpio.OUTPUT):
        print(f"GPIO{tx} dang dùng ch?c nang ngo?i vi khác.")
        pi.stop()
        return

    pi.write(tx, 1)  # UART ngh? ? m?c HIGH.
    pi.set_mode(tx, pigpio.OUTPUT)

    def gui(lenh, du_lieu=b""):
        if pi.wave_tx_busy():
            raise RuntimeError("B? phát pigpio dang b?n.")

        goi = tao_goi(lenh, du_lieu)
        pi.wave_add_new()
        pi.wave_add_serial(
            tx, 9600, goi,
            bb_bits=8,
            bb_stop=2,  # M?t stop bit.
        )

        wave_id = pi.wave_create()
        if wave_id < 0:
            raise RuntimeError("Không t?o du?c tín hi?u UART.")

        try:
            pi.wave_send_once(wave_id)
            han = time.monotonic() + 1

            while pi.wave_tx_busy():
                if time.monotonic() > han:
                    raise TimeoutError("G?i UART quá th?i gian.")
                time.sleep(0.001)
        finally:
            if pi.wave_tx_busy() and pi.wave_tx_at() == wave_id:
                pi.wave_tx_stop()
            pi.wave_delete(wave_id)

        time.sleep(0.05)

    def phat_bai(so_bai):
        gui(0x07, so_bai.to_bytes(2, "big"))

    try:
        time.sleep(0.5)
        gui(0x04)                # D?ng.
        gui(0x0B, b"\x02")       # Ch?n b? nh? trong.
        time.sleep(0.3)
        gui(0x13, bytes([30]))   # Âm lu?ng t?i da.

        print(f"Sound Player CN210 — TX: GPIO{tx}")
        print("C? ba m?c d?u có âm lu?ng MAX 30/30.")
        print("1: Pip ch?m")
        print("2: Pip nhanh")
        print("3: Pip nguy hi?m")
        print("0: T?t loa")
        print("q: Thoát")
        print("Nh?p l?a ch?n r?i nh?n Enter:")

        hang_doi = queue.Queue()
        threading.Thread(
            target=doc_ban_phim,
            args=(hang_doi,),
            daemon=True,
        ).start()

        bai_hien_tai = None
        lan_phat_tiep = None

        while True:
            cho = (
                None if lan_phat_tiep is None
                else max(0, lan_phat_tiep - time.monotonic())
            )

            try:
                lua_chon = hang_doi.get(timeout=cho)
            except queue.Empty:
                phat_bai(bai_hien_tai)
                lan_phat_tiep = time.monotonic() + 4.2
                continue

            if lua_chon == "q":
                break

            if lua_chon not in ("0", "1", "2", "3"):
                print("Ch? nh?p 1, 2, 3, 0 ho?c q.")
                continue

            gui(0x04)
            bai_hien_tai = None
            lan_phat_tiep = None

            if lua_chon == "0":
                print("Ðã t?t loa.")
                continue

            bai_hien_tai = int(lua_chon)
            gui(0x13, bytes([30]))
            phat_bai(bai_hien_tai)
            lan_phat_tiep = time.monotonic() + 4.2

            print(f"Ðã g?i l?nh m?c {lua_chon} — MAX 30/30.")

    except KeyboardInterrupt:
        print("\nÐã d?ng.")
    except Exception as loi:
        print(f"L?i: {loi}")
    finally:
        try:
            gui(0x04)
        except Exception as loi:
            print(f"Không g?i du?c l?nh d?ng: {loi}")
        finally:
            try:
                pi.write(tx, 1)
                pi.set_mode(tx, che_do_cu)
            finally:
                pi.stop()


if __name__ == "__main__":
    main()