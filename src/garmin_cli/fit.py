"""Decode original activity FIT into Garmin staging splits and HR zones."""
from io import BytesIO
from zipfile import ZipFile

import fitdecode


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FIT_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBERS = 32


def decode_activity_fit(archive: bytes, activity_id: str | int) -> dict:
    """Return staging fragments. Invalid input raises a sanitized ``ValueError``.

    Native ``averagePower``/``maxPower`` never contain developer power. Optional
    ``developerPower`` retains the original field name, units, value and identity;
    consumers must explicitly choose its source. No missing metric is zero-filled.
    """
    try:
        return _decode_activity_fit(_fit_payload(archive), activity_id)
    except Exception:
        # Parser and ZIP errors can contain private values, names or byte offsets.
        raise ValueError('Invalid activity FIT archive') from None


def _fit_payload(archive: bytes) -> bytes:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError
    if archive.startswith(b'PK'):
        with ZipFile(BytesIO(archive)) as zipped:
            members = zipped.infolist()
            if (len(members) > MAX_ZIP_MEMBERS
                    or sum(info.file_size for info in members) > MAX_FIT_BYTES):
                raise ValueError
            fits = [info for info in members
                    if not info.is_dir() and info.filename.lower().endswith('.fit')]
            if len(fits) != 1:
                raise ValueError
            name = fits[0].filename.replace('\\', '/')
            if name.startswith('/') or '..' in name.split('/'):
                raise ValueError
            if fits[0].file_size > MAX_FIT_BYTES:
                raise ValueError
            archive = zipped.read(fits[0])
    if len(archive) > MAX_FIT_BYTES:
        raise ValueError
    if len(archive) < 14 or archive[0] not in (12, 14) or archive[8:12] != b'.FIT':
        raise ValueError
    expected = archive[0] + int.from_bytes(archive[4:8], 'little') + 2
    if len(archive) != expected:
        raise ValueError
    return archive


def _decode_activity_fit(archive: bytes, activity_id: str | int) -> dict:
    laps = []
    zones = []
    applications = {}
    session_count = 0
    session_zone_seen = False
    with fitdecode.FitReader(BytesIO(archive), check_crc=fitdecode.CrcCheck.RAISE,
                             error_handling=fitdecode.ErrorHandling.RAISE) as reader:
        for message in reader:
            if not isinstance(message, fitdecode.FitDataMessage):
                continue
            values = {f.name: f.value for f in message.fields
                      if f.value is not None and not (f.field_def and f.field_def.is_dev)}
            if message.name == 'session':
                session_count += 1
            if message.name == 'developer_data_id':
                app = values.get('application_id')
                if app is not None:
                    applications[values['developer_data_index']] = bytes(app).hex()
            if message.name == 'time_in_zone' and values.get('reference_mesg') in ('session', 18):
                if session_zone_seen or values.get('reference_index', 0) != 0:
                    raise ValueError
                session_zone_seen = True
                times = values.get('time_in_hr_zone', ())
                highs = values.get('hr_zone_high_boundary', ())
                if not isinstance(times, (list, tuple)):
                    times = (times,)
                if not isinstance(highs, (list, tuple)):
                    highs = (highs,)
                for index, seconds in enumerate(times):
                    # Garmin's staging contract exposes zones 1–5, not FIT's
                    # underflow (0) or any overflow zone. FIT highs are inclusive.
                    if (1 <= index <= 5 and seconds is not None
                            and index <= len(highs) and highs[index - 1] is not None):
                        zones.append({'zoneNumber': index, 'secsInZone': seconds,
                                      'zoneLowBoundary': highs[index - 1] + 1})
            if message.name != 'lap':
                continue
            lap = {'lapIndex': len(laps) + 1}
            for source, target in [('total_distance', 'distance'), ('total_timer_time', 'duration'),
                                   ('avg_speed', 'averageSpeed')]:
                if source in values:
                    lap[target] = values[source]
            for source, target in [
                ('total_moving_time', 'movingDuration'), ('max_speed', 'maxSpeed'),
                ('enhanced_avg_speed', 'averageSpeed'), ('enhanced_max_speed', 'maxSpeed'),
                ('avg_heart_rate', 'averageHR'), ('max_heart_rate', 'maxHR'),
                ('avg_power', 'averagePower'), ('max_power', 'maxPower'),
                ('avg_stance_time', 'groundContactTime'), ('avg_vertical_ratio', 'verticalRatio'),
                ('total_ascent', 'elevationGain'), ('total_descent', 'elevationLoss')]:
                if source in values:
                    lap[target] = values[source]
            for source, target in [('avg_step_length', 'strideLength'),
                                   ('avg_vertical_oscillation', 'verticalOscillation')]:
                if source in values:
                    lap[target] = values[source] / 10
            for prefix, target in [('avg', 'averageRunCadence'), ('max', 'maxRunCadence')]:
                source = prefix + '_running_cadence'
                if source in values:
                    lap[target] = 2 * (values[source] + values.get(prefix + '_fractional_cadence', 0))
            for source, target in [('start_position_lat', 'startLat'), ('start_position_long', 'startLng'),
                                   ('end_position_lat', 'endLat'), ('end_position_long', 'endLng')]:
                if source in values:
                    lap[target] = values[source] * 180 / 2**31
            if 'intensity' in values:
                lap['intensityType'] = values['intensity'].upper()
            if 'start_time' in values:
                lap['startTimeGMT'] = values['start_time'].isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            developer_power = []
            for field in message.fields:
                definition = field.field_def
                if (definition and definition.is_dev and field.value is not None
                        and 'power' in field.name.lower()
                        and (field.units or '').lower() in ('w', 'watt', 'watts')):
                    item = {'developerDataIndex': definition.dev_data_index,
                            'fieldDefinitionNumber': field.def_num, 'fieldName': field.name,
                            'units': field.units, 'value': field.value}
                    if definition.dev_data_index in applications:
                        item['applicationId'] = applications[definition.dev_data_index]
                    developer_power.append(item)
            if developer_power:
                lap['developerPower'] = developer_power
            if not {'startTimeGMT', 'distance', 'duration', 'averageSpeed'} <= lap.keys():
                raise ValueError
            laps.append(lap)
    if session_count != 1:
        raise ValueError
    return {'splits': {'activityId': int(activity_id), 'lapDTOs': laps}, 'hrZones': zones}
