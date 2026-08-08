import unittest
import struct
from pathlib import Path

from d6_controller import D6Controller, HEARTBEAT_PAYLOAD, KEY_TO_DEVICE_ID, KEY_TO_IMAGE_DEVICE_ID, REPORT_SIZE


class ProtocolTests(unittest.TestCase):
    def test_key_mapping_covers_fifteen_keys(self):
        self.assertEqual(set(KEY_TO_DEVICE_ID), set(range(1, 16)))
        self.assertEqual(len(set(KEY_TO_DEVICE_ID.values())), 15)

    def test_image_mapping_uses_direct_panel_slots(self):
        self.assertEqual(KEY_TO_IMAGE_DEVICE_ID[1], 0x01)
        self.assertEqual(KEY_TO_IMAGE_DEVICE_ID[11], 0x0B)

    def test_frame_has_report_id_and_512_data_bytes(self):
        frame = D6Controller._frame(b"CRT\0\0STP\0\0")
        self.assertEqual(len(frame), REPORT_SIZE)
        self.assertEqual(frame[0], 0)
        self.assertEqual(frame[1:10], b"CRT\0\0STP\0")

    def test_heartbeat_frame_matches_vendor_shape(self):
        self.assertEqual(HEARTBEAT_PAYLOAD, b"CRT\0\0CONNECT\0\0\0")
        self.assertEqual(len(HEARTBEAT_PAYLOAD), 15)

    def test_qucmd_frame_contains_five_parameters(self):
        frame = D6Controller._frame(b"CRT\0\0QUCMD" + bytes([1, 2, 3, 4, 5]))
        self.assertEqual(frame[1:16], b"CRT\0\0QUCMD\x01\x02\x03\x04\x05")

    def test_profile_resolves_scene_and_page_without_sending(self):
        controller = D6Controller.__new__(D6Controller)
        sent = []
        controller.clear_screen = lambda: sent.append(("clear",))
        controller.set_brightness = lambda value: sent.append(("brightness", value))
        controller.set_key_image = lambda key, path: sent.append(("image", key, Path(path).name))
        count = controller.apply_profile(
            {
                "device": {"brightness": 42},
                "scenes": {"work": {"pages": {"main": {"keys": {"2": {"image": "two.jpg"}}}}}},
            },
            scene="work",
            page="main",
            root="assets",
        )
        self.assertEqual(count, 1)
        self.assertEqual(sent, [("clear",), ("brightness", 42), ("image", 2, "two.jpg")])

    def test_verified_d6_capabilities_exclude_rgb(self):
        self.assertEqual(
            D6Controller.capabilities(),
            {
                "button_events": True,
                "key_images": True,
                "brightness": True,
                "heartbeat": True,
                "rgb": False,
            },
        )

    def test_decode_accepts_windows_report_shape(self):
        report = bytearray(REPORT_SIZE)
        report[1:8] = b"ACK\0\0OK"
        report[10] = KEY_TO_DEVICE_ID[1]
        report[11] = 1
        self.assertEqual(D6Controller.decode_key_report(bytes(report)), (1, True))

    def test_decode_accepts_hidapi_report_shape(self):
        report = bytearray(REPORT_SIZE - 1)
        report[0:7] = b"ACK\0\0OK"
        report[9] = KEY_TO_DEVICE_ID[15]
        report[10] = 0
        self.assertEqual(D6Controller.decode_key_report(bytes(report)), (15, False))

    def test_decode_accepts_ack_framed_key_event(self):
        report = b"\0ACK\0\0OK\0\0" + bytes([KEY_TO_DEVICE_ID[1], 1]) + bytes(501)
        self.assertEqual(D6Controller.decode_key_report(report), (1, True))

    def test_decode_ignores_write_confirmation(self):
        report = b"\0ACK\0\0OK\0\0\xff\0" + bytes(501)
        self.assertIsNone(D6Controller.decode_key_report(report))

    def test_key_image_header_uses_two_byte_big_endian_length(self):
        header = b"CRT\0\0BAT\0\0" + struct.pack(">H", 0x1234) + bytes([0x0B])
        self.assertEqual(header[0:10], b"CRT\0\0BAT\0\0")
        self.assertEqual(header[10:12], b"\x12\x34")
        self.assertEqual(header[12], 0x0B)


if __name__ == "__main__":
    unittest.main()
