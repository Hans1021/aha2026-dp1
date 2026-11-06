# use MicroPython GPIO/SPI and hexadecimal formatting.
import machine
import binascii


# run SPI echo, encryption, and decryption checks; no interrupts are used.
def main():
    # configure the FPGA configuration-done input; it is not polled.
    ice_done = machine.Pin(3, machine.Pin.IN)

    # configure the SPI bus and sideband controls on the RP2040.
    SCK = machine.Pin(6, machine.Pin.OUT)
    RST_N = machine.Pin(7, machine.Pin.OUT)
    MOSI = machine.Pin(8, machine.Pin.OUT)
    MISO = machine.Pin(9, machine.Pin.IN)
    NORM_CS_N = machine.Pin(10, machine.Pin.OUT)
    START = machine.Pin(12, machine.Pin.OUT)
    BUSY = machine.Pin(14, machine.Pin.IN)
    ENC_DEC = machine.Pin(13, machine.Pin.OUT)  # 0: encrypt; 1: decrypt

    # idle SCK low and release reset, chip-select, and start.
    SCK.value(0)
    RST_N.value(1)
    NORM_CS_N.value(1)
    START.value(0)

    # use SPI mode 0, eight-bit transfers, and MSB-first order at 50 kHz.
    spi = machine.SoftSPI(baudrate=50000, polarity=0, phase=0, bits=8, firstbit=machine.SPI.MSB, sck=SCK, mosi=MOSI, miso=MISO)

    # clock synchronous reset once, then clock once after release.
    RST_N.value(0)
    SCK.value(1)
    SCK.value(0)
    RST_N.value(1)
    SCK.value(1)
    SCK.value(0)

    # use the supplied input/output pair; no key transaction is present.
    plaintext = bytearray([0x59, 0xC3, 0x59, 0xC3])
    ciphertext = bytearray([0x9C, 0xD8, 0x43, 0x92])

    # load four plaintext bytes without starting computation.
    txdata = plaintext
    rxdata = bytearray(4)
    NORM_CS_N.value(0)
    spi.write(txdata)
    NORM_CS_N.value(1)

    # read the echo while shifting the same plaintext back in.
    NORM_CS_N.value(0)
    spi.write_readinto(txdata, rxdata)
    NORM_CS_N.value(1)

    # report a matching SPI echo.
    if txdata == rxdata:
        print("SPI functional test pass")
    # stop on an SPI mismatch.
    else:
        print("Error: SPI error")
        return

    # select encryption; the echo transfer has reloaded the plaintext.
    ENC_DEC.value(0)  # encrypt

    # hold START high for one complete SCK pulse.
    START.value(1)
    SCK.value(1)
    SCK.value(0)
    START.value(0)

    # check BUSY after the start pulse.
    if(BUSY.value() == 1):
        print("IP core successfully busy")
    # stop if the start pulse did not assert BUSY.
    else:
        print("Error: IP core did not go busy")
        return

    # send seven more SCK pulses; eight including start.
    for i in range(7):
        SCK.value(1)
        SCK.value(0)

    # check completion after all seven additional pulses.
    if(BUSY.value() == 0):
        print("IP core successfully finished")
    # stop if computation is still busy.
    else:
        print("Error: IP core did not finish")
        return

    # read ciphertext while transmitting plaintext on MOSI.
    NORM_CS_N.value(0)
    spi.write_readinto(txdata, rxdata)
    NORM_CS_N.value(1)

    # report an encryption mismatch and continue to decryption.
    if rxdata != ciphertext:
        print("Encryption failed, got", binascii.hexlify(rxdata), "expected", binascii.hexlify(ciphertext))
    # report the expected ciphertext.
    else:
        print("Encryption value correct:", binascii.hexlify(rxdata))

    # select decryption before resetting the core.
    ENC_DEC.value(1)  # decrypt

    # clock synchronous reset once, then clock once after release.
    RST_N.value(0)
    SCK.value(1)
    SCK.value(0)
    RST_N.value(1)
    SCK.value(1)
    SCK.value(0)

    # load the supplied ciphertext, independent of encryption readback.
    txdata = ciphertext
    rxdata = bytearray(4)
    NORM_CS_N.value(0)
    spi.write(txdata)
    NORM_CS_N.value(1)

    # start decryption with one SCK pulse; BUSY is not checked here.
    START.value(1)
    SCK.value(1)
    SCK.value(0)
    START.value(0)

    # send seven more SCK pulses; eight including start.
    for i in range(7):
        SCK.value(1)
        SCK.value(0)

    # check completion after all seven additional pulses.
    if(BUSY.value() == 0):
        print("IP core successfully finished")
    # stop if computation is still busy.
    else:
        print("Error: IP core did not finish")
        return

    # read plaintext while transmitting ciphertext on MOSI.
    NORM_CS_N.value(0)
    spi.write_readinto(txdata, rxdata)
    NORM_CS_N.value(1)

    # report a decryption mismatch.
    if rxdata != plaintext:
        print("Decryption failed, got", binascii.hexlify(rxdata), "expected", binascii.hexlify(plaintext))
    # report the expected plaintext.
    else:
        print("Decryption value correct:", binascii.hexlify(rxdata))


# execute the supplied hardware test.
main()
