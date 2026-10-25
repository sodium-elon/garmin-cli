"""Sanitized, synthetic FIT bytes exercise the actual fitdecode read loop."""
import io
import struct
import unittest
from datetime import datetime, timezone
from zipfile import ZipFile

import fitdecode


def synthetic_fit(messages):
    """Encode native FIT fields: (global message number, [(field, base type, bytes)])."""
    payload = bytearray()
    for message in messages:
        number, fields = message[:2]
        developer = message[2] if len(message) == 3 else []
        payload += struct.pack('<BBBHB', 0x60 if developer else 0x40, 0, 0, number, len(fields))
        for field, base_type, value in fields:
            payload += bytes((field, len(value), base_type))
        if developer:
            payload += bytes((len(developer),))
            for field, index, value in developer:
                payload += bytes((field, len(value), index))
        payload += b'\x00' + b''.join(value for _, _, value in fields)
        payload += b''.join(value for _, _, value in developer)
    header = struct.pack('<BBHI4s', 14, 0x20, 2100, len(payload), b'.FIT')
    header += struct.pack('<H', fitdecode.utils.compute_crc(header))
    data = header + payload
    return bytes(data + struct.pack('<H', fitdecode.utils.compute_crc(data)))


def u32(field, value):
    return field, 0x86, struct.pack('<I', value)


def u16(field, value):
    return field, 0x84, struct.pack('<H', value)


def u8(field, value):
    return field, 0x02, bytes((value,))


class FitTests(unittest.TestCase):
    def decode(self, messages):
        # Metric-focused fixtures get valid required measurements by default.
        messages = [(number, self.lap_fields(fields), *rest) if number == 19
                    else (number, fields, *rest) for number, fields, *rest in messages]
        from garmin_cli.fit import decode_activity_fit
        return decode_activity_fit(synthetic_fit([(18, [u16(254, 0)])] + messages), '123')

    @staticmethod
    def lap_fields(fields=()):
        defaults = [u32(2, 1000000000), u32(8, 1000), u32(9, 100), u16(13, 1000)]
        return [f for f in defaults if f[0] not in {v[0] for v in fields}] + list(fields)

    def test_required_lap_measurements_cannot_be_missing_or_invalid(self):
        from garmin_cli.fit import decode_activity_fit
        for missing in (2, 8, 9, 13):
            for invalid in (False, True):
                fields = [(n, t, b'\xff' * len(v) if invalid and n == missing else v)
                          for n, t, v in self.lap_fields() if invalid or n != missing]
                with self.subTest(field=missing, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, '^Invalid activity FIT archive$'):
                        decode_activity_fit(synthetic_fit([(18, []), (19, fields)]), 123)

    def test_requires_exactly_one_session_and_no_duplicate_session_zones(self):
        from garmin_cli.fit import decode_activity_fit
        zone = (216, [u16(0, 18), u16(1, 0), u32(2, 1000), u8(6, 99)])
        for messages in [[], [(18, []), (18, [])], [(18, []), zone, zone]]:
            with self.subTest(case=len(messages)):
                with self.assertRaisesRegex(ValueError, '^Invalid activity FIT archive$'):
                    decode_activity_fit(synthetic_fit(messages), 123)


    def test_raw_fit_lap_contract_uses_one_based_index_timer_seconds_and_utc(self):
        result = self.decode([(19, [u16(254, 0), u32(2, 1000000000), u32(7, 65000),
                                     u32(8, 60000), u32(9, 20000), u16(13, 3333)])])
        self.assertEqual(result, {'splits': {'activityId': 123, 'lapDTOs': [{
            'lapIndex': 1, 'startTimeGMT': '2021-09-08T01:46:40.000Z',
            'distance': 200.0, 'duration': 60.0, 'averageSpeed': 3.333,
        }]}, 'hrZones': []})

    def test_lap_metrics_preserve_units_and_enhanced_speed_precedence(self):
        lap = self.decode([(19, [u8(25, 1), u8(15, 140), u8(16, 170),
            u8(17, 80), u8(18, 95), u8(80, 64), u8(81, 32),
            u16(13, 3000), u32(110, 3200), u16(14, 4000), u32(111, 4500),
            u32(52, 58000), u16(19, 250), u16(20, 400),
            u16(77, 855), u16(79, 2400), u16(118, 740), u16(120, 11000),
            u8(23, 4), u16(21, 5), u16(22, 3),
            (3, 0x85, struct.pack('<i', 1073741824)),
            (4, 0x85, struct.pack('<i', -1073741824)),
            (5, 0x85, struct.pack('<i', 536870912)),
            (6, 0x85, struct.pack('<i', -536870912)),
        ])])['splits']['lapDTOs'][0]
        self.assertEqual(lap, {'lapIndex': 1, 'averageHR': 140, 'maxHR': 170,
            'startTimeGMT': '2021-09-08T01:46:40.000Z', 'distance': 1.0, 'duration': 1.0,
            'averageRunCadence': 161.0, 'maxRunCadence': 190.5,
            'averageSpeed': 3.2, 'maxSpeed': 4.5, 'movingDuration': 58.0,
            'averagePower': 250, 'maxPower': 400,
            'verticalOscillation': 8.55, 'groundContactTime': 240.0,
            'verticalRatio': 7.4, 'strideLength': 110.0, 'intensityType': 'RECOVERY',
            'elevationGain': 5, 'elevationLoss': 3,
            'startLat': 90.0, 'startLng': -90.0, 'endLat': 45.0, 'endLng': -45.0})

    def test_hr_zones_omit_fit_underflow_and_overflow_for_legacy_five_zone_contract(self):
        times = (2, 0x86, struct.pack('<7I', 500, 1000, 2000, 3000, 4000, 5000, 0))
        zones = self.decode([(216, [u16(0, 18), u16(1, 0), times,
                                    (6, 0x02, bytes((99, 119, 139, 159, 179, 199)))])])['hrZones']
        self.assertEqual([z['zoneNumber'] for z in zones], [1, 2, 3, 4, 5])
        self.assertEqual([z['zoneLowBoundary'] for z in zones], [100, 120, 140, 160, 180])

    def test_hr_zones_select_session_only_and_use_inclusive_high_boundaries(self):
        times = lambda vals: (2, 0x86, struct.pack('<' + 'I' * len(vals), *vals))
        boundaries = (6, 0x02, bytes((99, 119, 139)))
        zones = self.decode([
            (216, [u16(0, 19), u16(1, 0), times((9000, 9000, 9000, 9000)), boundaries]),
            (216, [u16(0, 18), u16(1, 0), times((500, 1200, 2300, 0)), boundaries]),
        ])['hrZones']
        self.assertEqual(zones, [
            {'zoneNumber': 1, 'secsInZone': 1.2, 'zoneLowBoundary': 100},
            {'zoneNumber': 2, 'secsInZone': 2.3, 'zoneLowBoundary': 120},
            {'zoneNumber': 3, 'secsInZone': 0.0, 'zoneLowBoundary': 140},
        ])

    def test_developer_lap_power_retains_application_identity_without_native_alias(self):
        messages = [
            (207, [u8(3, 0), (1, 0x0D, bytes(range(16)))]),
            (206, [u8(0, 0), u8(1, 10), u8(2, 0x84),
                   (3, 0x07, b'Lap Power\x00'), (8, 0x07, b'Watts\x00'), u16(14, 19)]),
            (19, [], [(10, 0, struct.pack('<H', 225))]),
            (19, [u16(19, 250)], [(10, 0, struct.pack('<H', 230))]),
        ]
        laps = self.decode(messages)['splits']['lapDTOs']
        self.assertNotIn('averagePower', laps[0])
        self.assertEqual(laps[1]['averagePower'], 250)
        self.assertEqual(laps[0]['developerPower'], [{
            'developerDataIndex': 0, 'applicationId': bytes(range(16)).hex(),
            'fieldDefinitionNumber': 10, 'fieldName': 'Lap Power',
            'units': 'Watts', 'value': 225,
        }])

    def test_original_zip_reads_exactly_one_fit_in_memory(self):
        from garmin_cli.fit import decode_activity_fit
        raw = synthetic_fit([(18, []), (19, self.lap_fields([u32(8, 1000)]))])
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('original/ACTIVITY.FIT', raw)
            archive.writestr('notes.txt', 'not activity data')
        self.assertEqual(decode_activity_fit(buffer.getvalue(), 123), decode_activity_fit(raw, 123))

    def test_invalid_archives_fail_with_sanitized_value_error(self):
        from garmin_cli.fit import decode_activity_fit
        raw = synthetic_fit([(18, []), (19, self.lap_fields([u32(8, 1000)]))])
        def zipped(names):
            buf = io.BytesIO()
            with ZipFile(buf, 'w') as z:
                for name in names:
                    z.writestr(name, raw)
            return buf.getvalue()
        invalid = [b'', b'private-secret-not-fit', b'PKprivate-secret', raw[:-1],
                   raw[:-1] + bytes((raw[-1] ^ 1,)), raw + raw,
                   zipped(['private-secret.txt']), zipped(['one.fit', 'two.fit']),
                   zipped(['../private-secret.fit']), zipped(['/private-secret.fit'])]
        for data in invalid:
            with self.subTest(kind=data[:2]):
                with self.assertRaisesRegex(ValueError, '^Invalid activity FIT archive$'):
                    decode_activity_fit(data, 123)

    def test_zip_member_count_and_total_expanded_size_are_bounded(self):
        from unittest.mock import patch
        from garmin_cli.fit import decode_activity_fit
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as z:
            z.writestr('activity.fit', synthetic_fit([(19, self.lap_fields([u32(8, 1000)]))]))
            z.writestr('unused.txt', b'x' * 1000)
        for limit, value in [('MAX_ZIP_MEMBERS', 1), ('MAX_FIT_BYTES', 100)]:
            with self.subTest(limit=limit), patch('garmin_cli.fit.' + limit, value):
                with patch.object(ZipFile, 'open') as opened:
                    with self.assertRaisesRegex(ValueError, '^Invalid activity FIT archive$'):
                        decode_activity_fit(buf.getvalue(), 123)
                    opened.assert_not_called()

    def test_resource_limits_reject_oversized_input_and_zip_before_decompression(self):
        from unittest.mock import patch
        from garmin_cli.fit import decode_activity_fit
        from zipfile import ZIP_DEFLATED
        raw = synthetic_fit([(18, []), (19, self.lap_fields([u32(8, 1000)]))])
        with patch('garmin_cli.fit.MAX_ARCHIVE_BYTES', len(raw) - 1, create=True):
            with self.assertRaisesRegex(ValueError, 'Invalid activity FIT archive'):
                decode_activity_fit(raw, 123)
        buf = io.BytesIO()
        with ZipFile(buf, 'w', compression=ZIP_DEFLATED) as z:
            z.writestr('activity.fit', raw * 100)
        with patch('garmin_cli.fit.MAX_FIT_BYTES', len(raw), create=True):
            with patch.object(ZipFile, 'open', side_effect=AssertionError('must not decompress')) as opened:
                with self.assertRaisesRegex(ValueError, 'Invalid activity FIT archive'):
                    decode_activity_fit(buf.getvalue(), 123)
                opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
