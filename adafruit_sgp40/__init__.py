# SPDX-FileCopyrightText: Copyright (c) 2020 Bryan Siepert for Adafruit Industries
#
# SPDX-License-Identifier: MIT
"""
`adafruit_sgp41`
================================================================================

CircuitPython library for the Adafruit sgp41 Air Quality Sensor
Modified to work with SGP41 sensors

* Author(s): Bryan Siepert
             Keith Murray
             Thomas Ziemann
Implementation Notes
--------------------

**Hardware:**

* Adafruit sgp41 Air Quality Sensor Breakout - VOC Index <https://www.adafruit.com/product/4829>
* In order to use the `measure_raw` function, a temperature and humidity sensor which
  updates at at least 1Hz is needed (BME280, BME688, SHT31-D, SHT40, etc. For more, see:
  https://www.adafruit.com/category/66)

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://github.com/adafruit/circuitpython/releases

 * Adafruit's Bus Device library: https://github.com/adafruit/Adafruit_CircuitPython_BusDevice

"""
from time import sleep
from struct import unpack_from
from adafruit_bus_device import i2c_device

 try:
    from typing import List, Optional
    from circuitpython_typing import ReadableBuffer
    from busio import I2C
except ImportError:
    pass 
    
__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/veloyage/CircuitPython_SGP41.git"

                            
                                                                         

_WORD_LEN = 2

# no point in generating this each time
# Generated from temp 25c, humidity 50%
_READ_CMD = b"\x26\x19\x80\x00\xA2\x66\x66\x93"


class SGP41:
    """
    Class to use the sgp41 Air Quality Sensor Breakout

    :param int address: The I2C address of the device. Defaults to :const:`0x59`


    **Quickstart: Importing and using the SGP41 temperature sensor**

        Here is one way of importing the `SGP41` class so you can use it with the name ``sgp``.
        First you will need to import the libraries to use the sensor

        .. code-block:: python

            import board
            import adafruit_sgp41
            # If you have a temperature sensor, like the bme280, import that here as well
            # import adafruit_bme280

        Once this is done you can define your `board.I2C` object and define your sensor object

        .. code-block:: python

            i2c = board.I2C()  # uses board.SCL and board.SDA
            sgp = adafruit_sgp41.sgp41(i2c)
            # And if you have a temp/humidity sensor, define the sensor here as well
            # bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c)

        Now you have access to the raw gas value using the :attr:`raw` attribute.
        And with a temperature and humidity value, you can access the class function
        :meth:`measure_raw` for a humidity compensated raw reading

        .. code-block:: python

            raw_gas_value = sgp.raw
            # Lets quickly grab the humidity and temperature
            # temperature = bme280.temperature
            # humidity = bme280.relative_humidity
            # compensated_raw_gas = sgp.measure_raw(temperature=temperature,
            # relative_humidity=humidity)
            # temperature = temperature, relative_humidity = humidity)



    .. note::
        The operational range of temperatures for the sgp41 is -10 to 50 degrees Celsius
        and the operational range of relative humidity for the sgp41 is 0 to 90 %
        (assuming that humidity is non-condensing).

        Humidity compensation is further optimized for a subset of the temperature
        and relative humidity readings. See Figure 3 of the Sensirion datasheet for
        the sgp41. At 25 degrees Celsius, the optimal range for relative humidity is 8% to 90%.
        At 50% relative humidity, the optimal range for temperature is -7 to 42 degrees Celsius.

        Prolonged exposures outside of these ranges may reduce sensor performance, and
        the sensor must not be exposed towards condensing conditions at any time.

        For more information see:
        https://www.sensirion.com/fileadmin/user_upload/customers/sensirion/Dokumente/9_Gas_Sensors/Datasheets/Sensirion_Gas_Sensors_Datasheet_sgp41.pdf
        and
        https://learn.adafruit.com/adafruit-sgp41

    """

    def __init__(self, i2c: I2C, address: int = 0x59) -> None:
        self.i2c_device = i2c_device.I2CDevice(i2c, address)
        self._command_buffer = bytearray(2)
        self._measure_command = _READ_CMD
        self._voc_algorithm = None

        self.initialize()

    def initialize(self) -> None:
        """Reset the sensor to it's initial unconfigured state and configure it with sensible
        defaults so it can be used"""
        # check serial number
        self._command_buffer[0] = 0x36
        self._command_buffer[1] = 0x82
        serialnumber = self._read_word_from_command(readlen=3, delay_ms=1)

        if serialnumber[0] != 0x0000:
            raise RuntimeError("Serial number does not match")

        # Check feature set
        self._command_buffer[0] = 0x20
        self._command_buffer[1] = 0x2F
        featureset = self._read_word_from_command()

        if featureset[0] != 0x0240: # as reported by SGP41, undocumented
            raise RuntimeError(f"Feature set does not match: {featureset[0]:#x}")

        # Self Test
        self._command_buffer[0] = 0x28
        self._command_buffer[1] = 0x0E
        self_test = self._read_word_from_command(delay_ms=500)
        if self_test[0] & 0b01 == 1:
            raise RuntimeError("VOC self test failed")
        if self_test[0] & 0b10 == 1:
            raise RuntimeError("NOX self test failed")
        self._reset()

    def _reset(self) -> None:
        # This is a general call Reset. Several sensors may see this and it doesn't appear to
        # ACK before resetting
        self._command_buffer[0] = 0x00
        self._command_buffer[1] = 0x06
        try:
            self._read_word_from_command(delay_ms=50)
        except (OSError, RuntimeError):
            # Got expected OSError from reset
            # or RuntimeError on some Blinka setups
            pass
        sleep(1)

    @staticmethod
    def _celsius_to_ticks(temperature: float) -> List[int]:
        """
        Converts Temperature in Celsius to 'ticks' which are an input parameter
        the sgp41 can use

        Temperature to Ticks : From sgp41 Datasheet Table 10
        temp (C)    | Hex Code (Check Sum/CRC Hex Code)
            25      | 0x6666   (CRC 0x93)
            -45     | 0x0000   (CRC 0x81)
            130     | 0xFFFF   (CRC 0xAC)

        """
        temp_ticks = int(((temperature + 45) * 65535) / 175) & 0xFFFF
        least_sig_temp_ticks = temp_ticks & 0xFF
        most_sig_temp_ticks = (temp_ticks >> 8) & 0xFF

        return [most_sig_temp_ticks, least_sig_temp_ticks]

    @staticmethod
    def _relative_humidity_to_ticks(humidity: float) -> List[int]:
        """
        Converts Relative Humidity in % to 'ticks' which are  an input parameter
        the sgp41 can use

        Relative Humidity to Ticks : From sgp41 Datasheet Table 10
        Humidity (%) | Hex Code (Check Sum/CRC Hex Code)
            50       | 0x8000   (CRC 0xA2)
            0        | 0x0000   (CRC 0x81)
            100      | 0xFFFF   (CRC 0xAC)

        """
        humidity_ticks = int((humidity * 65535) / 100 + 0.5) & 0xFFFF
        least_sig_rhumidity_ticks = humidity_ticks & 0xFF
        most_sig_rhumidity_ticks = (humidity_ticks >> 8) & 0xFF

        return [most_sig_rhumidity_ticks, least_sig_rhumidity_ticks]

    @property
    def raw_VOC(self):
        """The raw VOC gas value"""
        # recycle a single buffer
        self._command_buffer = self._measure_command
        read_value = self._read_word_from_command(delay_ms=500)
        self._command_buffer = bytearray(2)
        return read_value[0]

    @property
    def raw_NOX(self):
        """The raw NOx gas value"""
        # recycle a single buffer
        self._command_buffer = self._measure_command
        read_value = self._read_word_from_command(readlen=2, delay_ms=50)
        self._command_buffer = bytearray(2)
        return read_value[1]

    def conditioning(self):
        """
        Conditioning command which prepares the NOx pixel.
        Should be run after startup for 10s.
        Command returns VOC raw value, but not NOX.
        After 10s, the normal measure command should be run.
        """
        # recycle a single buffer
        self._command_buffer = b"\x26\x12\x80\x00\xA2\x66\x66\x93"
        read_value = self._read_word_from_command(delay_ms=50)
        self._command_buffer = bytearray(2)
        return read_value[0]

    def compensate(self, temperature=25, relative_humidity=50):
        """
        Compensates for humidity and temperature, which helps
        address fluctuations in raw gas values due to changing humidity.
        :param float temperature: The temperature in degrees Celsius, defaults
                                     to :const:`25`
        :param float relative_humidity: The relative humidity in percentage, defaults
                                     to :const:`50`

        Adjusts the raw gas values for the current temperature (c) and humidity (%)
        """
        # recycle a single buffer
        _compensated_read_cmd = [0x26, 0x19]
        humidity_ticks = self._relative_humidity_to_ticks(relative_humidity)
        humidity_ticks.append(self._generate_crc(humidity_ticks))
        temp_ticks = self._celsius_to_ticks(temperature)
        temp_ticks.append(self._generate_crc(temp_ticks))
        _cmd = _compensated_read_cmd + humidity_ticks + temp_ticks
        self._measure_command = bytearray(_cmd)
        #return self.raw_VOC

                      
    def measure_index(self, raw, temperature=25, relative_humidity=50):
             
        """Measure VOC index after humidity compensation
        :param float temperature: The temperature in degrees Celsius, defaults to :const:`25`
        :param float relative_humidity: The relative humidity in percentage, defaults to :const:`50`
        :note  VOC index can indicate the quality of the air directly.
        The larger the value, the worse the air quality.
        :note 0-100, no need to ventilate, purify
        :note 100-200, no need to ventilate, purify
        :note 200-400, ventilate, purify
        :note 00-500, ventilate, purify intensely
        :return int The VOC index measured, ranged from 0 to 500
        """
        # import/setup algorithm only on use of index
        # pylint: disable=import-outside-toplevel
        from adafruit_sgp41.voc_algorithm import (
            VOCAlgorithm,
        )

        if self._voc_algorithm is None:
            self._voc_algorithm = VOCAlgorithm()
            self._voc_algorithm.vocalgorithm_init()

        # self.compensate(temperature, relative_humidity)
        # raw = self.raw_VOC
        if raw < 0:
            return -1
        voc_index = self._voc_algorithm.vocalgorithm_process(raw)
        return voc_index

    def _read_word_from_command(
        self,
        delay_ms: int = 10,
        readlen: Optional[int] = 1,
    ) -> Optional[List[int]]:
        """_read_word_from_command - send a given command code and read the result back

        Args:
            delay_ms (int, optional): The delay between write and read, in milliseconds.
                Defaults to 10ms
            readlen (int, optional): The number of bytes to read. Defaults to 1.
        """
        # TODO: Take 2-byte command as int (0x280E, 0x0006) and packinto command buffer

        with self.i2c_device as i2c:
            i2c.write(self._command_buffer)

        sleep(round(delay_ms * 0.001, 3))

        if readlen is None:
            return None
        readdata_buffer = []

        # The number of bytes to read back, based on the number of words to read
        replylen = readlen * (_WORD_LEN + 1)
        # recycle buffer for read/write w/length
        replybuffer = bytearray(replylen)

        with self.i2c_device as i2c:
            i2c.readinto(replybuffer, end=replylen)

        for i in range(0, replylen, 3):
            if not self._check_crc8(replybuffer[i : i + 2], replybuffer[i + 2]):
                raise RuntimeError("CRC check failed while reading data")
            readdata_buffer.append(unpack_from(">H", replybuffer[i : i + 2])[0])

        return readdata_buffer

    def _check_crc8(self, crc_buffer: ReadableBuffer, crc_value: int) -> bool:
        """
        Checks that the 8 bit CRC Checksum value from the sensor matches the
        received data
        """
        return crc_value == self._generate_crc(crc_buffer)

    @staticmethod
    def _generate_crc(crc_buffer: ReadableBuffer) -> int:
        """
        Generates an 8 bit CRC Checksum from the input buffer.

        This checksum algorithm is outlined in Table 7 of the sgp41 datasheet.

        Checksums are only generated for 2-byte data packets. Command codes already
        contain 3 bits of CRC and therefore do not need an added checksum.
        """
        crc = 0xFF
        for byte in crc_buffer:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (
                        crc << 1
                    ) ^ 0x31  # 0x31 is the Seed for sgp41's CRC polynomial
                else:
                    crc = crc << 1
        return crc & 0xFF  # Returns only bottom 8 bits
