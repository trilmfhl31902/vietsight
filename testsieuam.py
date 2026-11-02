import glob
import time

try:
    import lgpio
except ImportError:
    raise SystemExit("Cai thu vien: python3 -m pip install lgpio")

# Cam bien OhStem noi vao CN212:
# Chan 1: GND
# Chan 2: VCC 3.3V
# Chan 3: ECHO -> GPIO11
# Chan 4: TRIG -> GPIO8
ECHO = 11
TRIG = 8

NGUONG_CM = 100
TIMEOUT = 0.03

chip = None
claimed = []


def tim_gpio():
    labels = {
        "pinctrl-bcm2711",
        "pinctrl-bcm2835",
        "pinctrl-rp1",
    }

    for path in sorted(glob.glob("/dev/gpiochip*")):
        handle = None

        try:
            number = int(path.split("gpiochip")[-1])
            handle = lgpio.gpiochip_open(number)
            status, lines, name, label = lgpio.gpio_get_chip_info(handle)
        except Exception:
            if handle is not None:
                lgpio.gpiochip_close(handle)
            continue

        if status >= 0 and label in labels and lines > max(TRIG, ECHO):
            return handle

        lgpio.gpiochip_close(handle)

    raise RuntimeError("Khong tim thay bo dieu khien GPIO Raspberry Pi.")


def cho_echo(muc):
    deadline = time.monotonic() + TIMEOUT

    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Qua thoi gian cho ECHO ve muc {muc}")

        if lgpio.gpio_read(chip, ECHO) == muc:
            return time.monotonic()


def do_khoang_cach():
    # Cho tin hieu ECHO ket thuc truoc khi do tiep.
    cho_echo(0)

    lgpio.gpio_write(chip, TRIG, 0)
    time.sleep(0.00001)

    # Phat xung kich hoat it nhat 10 micro giay.
    lgpio.gpio_write(chip, TRIG, 1)
    try:
        deadline = time.monotonic() + 0.00001
        while time.monotonic() < deadline:
            pass
    finally:
        lgpio.gpio_write(chip, TRIG, 0)

    bat_dau = cho_echo(1)
    ket_thuc = cho_echo(0)

    khoang_cach = (ket_thuc - bat_dau) * 34300 / 2

    if not 3 <= khoang_cach <= 400:
        raise ValueError("Khoang cach ngoai dai do 3-400 cm")

    return khoang_cach


try:
    chip = tim_gpio()

    # Kiem tra ca hai chan truoc khi cau hinh.
    for pin in (ECHO, TRIG):
        status, offset, flags, name, consumer = lgpio.gpio_get_line_info(
            chip, pin
        )
        if status < 0:
            raise RuntimeError(f"Khong doc duoc trang thai GPIO{pin}")
        if flags & 1:
            raise RuntimeError(
                f"GPIO{pin} dang bi chiem boi: {consumer or 'driver/chuong trinh khac'}"
            )

    lgpio.gpio_claim_input(chip, ECHO)
    claimed.append(ECHO)

    lgpio.gpio_claim_output(chip, TRIG, 0)
    claimed.append(TRIG)

    time.sleep(0.2)

    print("CAM BIEN SIEU AM OHSTEM - CN212")
    print("ECHO = GPIO11 | TRIG = GPIO8")
    print("Canh bao khi khoang cach < 100 cm")
    print("Nhan Ctrl+C de dung.\n", flush=True)

    while True:
        try:
            cm = do_khoang_cach()
            gio = time.strftime("%H:%M:%S")

            if cm < NGUONG_CM:
                trang_thai = "CANH BAO: VAT GAN < 100 cm"
            else:
                trang_thai = "VAT CACH >= 100 cm"

            print(f"[{gio}] {cm:.1f} cm | {trang_thai}", flush=True)

        except (TimeoutError, ValueError) as error:
            print(f"KHONG DO DUOC | {error}", flush=True)

        time.sleep(0.25)

except KeyboardInterrupt:
    print("\nDa dung.")

except Exception as error:
    print(f"Loi GPIO: {error}")
    print("Neu GPIO busy: kiem tra SPI va chuong trinh dang dung GPIO8/11.")
    print("Tat SPI: sudo raspi-config -> Interface Options -> SPI -> No")
    print("Sau do khoi dong lai Raspberry Pi.")

finally:
    if chip is not None:
        if TRIG in claimed:
            try:
                lgpio.gpio_write(chip, TRIG, 0)
            except Exception:
                pass

        for pin in claimed:
            try:
                lgpio.gpio_free(chip, pin)
            except Exception:
                pass

        lgpio.gpiochip_close(chip)