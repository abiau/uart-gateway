"""使用可觀測 Driver 替身驗證開啟順序與故障，不存取 COM3。"""

from dataclasses import replace
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from uart_gateway.core.models import AdapterInfo, SerialSettings
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.serial_backend import AdapterSelector, PySerialBackend, SerialBackendError, port_key


class DriverTimeout(OSError):
    pass


class Driver(FakeSerial):
    def __init__(self, **kwargs):
        super().__init__(echo=False)
        self.kwargs = kwargs
        self.dtr, self.rts, self.port = True, True, kwargs["port"]
        self.open_snapshot = None
        self.close_error = False

    def open(self):
        self.open_snapshot = (self.port, self.dtr, self.rts)
        super().open()

    def close(self):
        if self.close_error:
            raise OSError("測試關閉失敗")
        super().close()


class SerialBackendTests(unittest.TestCase):
    def setUp(self):
        self.handles = []
        self.backends = []
        self.devices = [AdapterInfo("COM3", 0x0403, 0x6001, "adapter-1", "usb-1")]
        self.factory_error = None
        self.open_error = None

        def factory(**kwargs):
            if self.factory_error:
                raise self.factory_error
            driver = Driver(**kwargs)
            if self.open_error:
                driver.open = lambda: (_ for _ in ()).throw(self.open_error)
            self.handles.append(driver)
            return driver

        self.module = SimpleNamespace(Serial=factory, SerialTimeoutException=DriverTimeout)
        self.module_patch = patch("uart_gateway.serial_backend._serial_module", return_value=self.module)
        self.inventory_patch = patch("uart_gateway.serial_backend.list_adapters", side_effect=lambda: self.devices)
        self.module_patch.start()
        self.inventory_patch.start()

    def tearDown(self):
        for driver in self.handles:
            driver.close_error = False
        for backend in self.backends:
            backend.close()
        self.inventory_patch.stop()
        self.module_patch.stop()

    def backend(self, selector=None, settings=None):
        backend = PySerialBackend(selector or AdapterSelector(port="COM3"), settings or SerialSettings())
        self.backends.append(backend)
        return backend

    def test_open_is_deferred_and_control_lines_are_set_before_open(self):
        settings = SerialSettings(baudrate=57600, bytesize=7, parity="even", stopbits=2,
                                  flow_control="xonxoff", read_timeout_ms=50, write_timeout_ms=2000)
        backend = self.backend(settings=settings)
        self.assertFalse(self.handles)
        backend.open()
        driver = self.handles[0]
        self.assertIsNone(driver.kwargs["port"])
        self.assertEqual(driver.open_snapshot, ("COM3", False, False))
        self.assertEqual(driver.kwargs["timeout"], 0.05)
        self.assertEqual(driver.kwargs["write_timeout"], 2)
        self.assertEqual(driver.kwargs["parity"], "E")
        self.assertTrue(driver.kwargs["xonxoff"])
        self.assertEqual(backend.describe().serial_number, "adapter-1")

    def test_duplicate_endpoint_alias_cannot_be_opened_twice(self):
        first = self.backend()
        first.open()
        alias = self.backend(AdapterSelector(port="\\\\.\\com3"))
        with self.assertRaises(SerialBackendError) as error:
            alias.open()
        self.assertEqual(error.exception.code, "PORT_BUSY")
        self.assertEqual(len(self.handles), 1)
        first.close()
        alias.open()

    def test_ambiguous_or_mismatched_identity_does_not_open(self):
        self.devices.append(replace(self.devices[0], port="COM4", serial_number="adapter-2"))
        for selector, code in ((AdapterSelector(vid=0x0403, pid=0x6001), "AMBIGUOUS_ADAPTER"),
                               (AdapterSelector(port="COM3", serial_number="wrong"), "DEVICE_MISSING")):
            with self.subTest(selector=selector), self.assertRaises(SerialBackendError) as error:
                self.backend(selector).open()
            self.assertEqual(error.exception.code, code)
        self.assertFalse(self.handles)

    def test_missing_metadata_allows_only_an_explicit_port(self):
        self.devices.clear()
        backend = self.backend()
        backend.open()
        self.assertEqual(backend.describe().match_status, "unverified")
        self.assertEqual(backend.describe().port, "COM3")

    def test_open_error_classification_and_cleanup(self):
        backend = self.backend()
        cases = ((PermissionError(errno.EACCES, "denied"), "PORT_BUSY"),
                 (OSError("could not open port: PermissionError(13, 'denied', None, 5)"), "PORT_BUSY"),
                 (FileNotFoundError(errno.ENOENT, "missing"), "DEVICE_MISSING"),
                 (OSError("could not open port: FileNotFoundError(2, 'missing')"), "DEVICE_MISSING"),
                 (ValueError("unsupported baud"), "SERIAL_IO_FAILED"))
        for failure, code in cases:
            with self.subTest(code=code):
                self.open_error = failure
                with self.assertRaises(SerialBackendError) as error:
                    backend.open()
                self.assertEqual(error.exception.code, code)
        self.open_error = None
        backend.open()
        self.assertTrue(self.handles[-1].opened)

    def test_constructor_failure_releases_endpoint_reservation(self):
        backend = self.backend()
        self.factory_error = ValueError("不支援參數")
        with self.assertRaises(SerialBackendError):
            backend.open()
        self.factory_error = None
        backend.open()

    def test_close_failure_keeps_endpoint_reserved_until_retry(self):
        first, second = self.backend(), self.backend()
        first.open()
        self.handles[0].close_error = True
        with self.assertRaises(SerialBackendError):
            first.close()
        with self.assertRaises(SerialBackendError) as error:
            second.open()
        self.assertEqual(error.exception.code, "PORT_BUSY")
        self.handles[0].close_error = False
        first.close()
        second.open()

    def test_partial_and_timeout_writes_are_not_retried(self):
        backend = self.backend()
        backend.open()
        driver = self.handles[0]
        driver.write_result = 2
        self.assertEqual(backend.write(b"hello"), 2)
        driver.write_error = DriverTimeout("模擬 Driver 逾時")
        with self.assertRaises(TimeoutError):
            backend.write(b"hello")
        self.assertEqual(driver.write_count, 2)

    def test_read_error_marks_device_missing(self):
        backend = self.backend()
        backend.open()
        self.handles[0].close()
        with self.assertRaises(SerialBackendError) as error:
            backend.read(4096)
        self.assertEqual(error.exception.code, "DEVICE_MISSING")
        self.assertEqual(backend.describe().match_status, "missing")

    def test_selector_and_profile_validate_without_touching_device(self):
        for kwargs in ({}, {"port": ""}, {"vid": 10}, {"vid": True, "pid": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AdapterSelector(**kwargs)
        for kwargs in ({"baudrate": True}, {"baudrate": 0}, {"stopbits": True},
                       {"dtr": 0}, {"write_timeout_ms": 0}, {"parity": "bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SerialSettings(**kwargs)
        self.assertEqual(port_key("COM03"), port_key("\\\\.\\com3"))

    def test_reopen_rejects_changed_or_missing_known_identity_before_constructing_driver(self):
        backend = self.backend()
        backend.open()
        backend.close()
        previous = self.devices[0]
        for changed in (replace(previous, serial_number="different"), replace(previous, serial_number=None),
                        replace(previous, vid=123), replace(previous, pid=None)):
            self.devices = [changed]
            with self.subTest(changed=changed), self.assertRaises(SerialBackendError) as error:
                backend.reopen_rx()
            self.assertEqual(error.exception.code, "DEVICE_MISSING")
            self.assertEqual(len(self.handles), 1)
        self.devices = [replace(previous, location="another-usb-socket")]
        backend.reopen_rx()
        self.assertEqual(len(self.handles), 2)
        self.assertEqual(self.handles[-1].open_snapshot, ("COM3", False, False))

    def test_reopen_without_serial_checks_known_location(self):
        previous = replace(self.devices[0], serial_number=None)
        self.devices = [previous]
        backend = self.backend()
        backend.open()
        backend.close()
        self.devices = [replace(previous, location="different")]
        with self.assertRaises(SerialBackendError):
            backend.reopen_rx()
        self.assertEqual(len(self.handles), 1)

    def test_failed_open_does_not_pin_an_unopened_adapter(self):
        backend = self.backend()
        self.open_error = PermissionError(errno.EACCES, "占用")
        with self.assertRaises(SerialBackendError):
            backend.open()
        self.open_error = None
        self.devices = [replace(self.devices[0], serial_number="replacement-before-first-success")]
        backend.open()
        self.assertTrue(self.handles[-1].opened)

    def test_normal_open_for_explicit_rebind_confirm_keeps_original_selector_semantics(self):
        backend = self.backend()
        backend.open()
        backend.close()
        self.devices = [replace(self.devices[0], serial_number="manually-confirmed-new-adapter")]
        backend.open()
        self.assertEqual(backend.describe().serial_number, "manually-confirmed-new-adapter")
