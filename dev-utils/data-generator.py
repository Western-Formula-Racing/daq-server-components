import os
import csv
import random
from datetime import datetime, timedelta

OUTPUT_DIR = "./generated_days"
DAYS = 5
SESSIONS_PER_DAY = 3
SESSION_LENGTH_MIN = 2               # minutes
FREQ_HZ = 40                         # messages per second per CAN ID
START_DATE = datetime(2025, 1, 1)
PROTOCOL = "CAN"

# CSV layout expected by startup-data-loader/load_data.py
CSV_HEADER = ["relative_ms", "protocol", "can_id"] + [f"byte{i}" for i in range(8)]

# CAN IDs from installer/startup-data-loader/example.dbc
ID_DRIVETRAIN = 256
ID_BATTERY = 512

os.makedirs(OUTPUT_DIR, exist_ok=True)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def encode_unsigned(value, scale, offset, bits):
    raw = int((value - offset) / scale)
    return raw & ((1 << bits) - 1)


def encode_signed(value, scale, offset, bits):
    raw = int((value - offset) / scale)
    if raw < 0:
        raw = (1 << bits) + raw
    return raw & ((1 << bits) - 1)


def to_le_bytes(value, length):
    """Return little-endian byte list of the provided raw value."""
    return list(value.to_bytes(length, byteorder="little"))


def gen_drivetrain_frame():
    """Generate an 8-byte drivetrain frame that matches example.dbc."""
    engine = clamp(random.gauss(3000, 400), 600, 7200)           # rpm
    throttle = clamp(random.gauss(30, 10), 0, 100)               # %
    oil = clamp(random.gauss(100, 20), 20, 180)                  # kPa
    steer = clamp(random.gauss(0, 20), -120, 120)                # deg

    eng_raw = encode_unsigned(engine, 0.125, 0, 16)
    thr_raw = encode_unsigned(throttle, 0.5, 0, 8)
    oil_raw = encode_unsigned(oil, 1, 0, 8)
    steer_raw = encode_signed(steer, 0.1, 0, 16)

    data = (
        to_le_bytes(eng_raw, 2) +
        [thr_raw] +
        [oil_raw] +
        to_le_bytes(steer_raw, 2) +
        [0, 0]
    )
    return data


def gen_battery_frame():
    """Generate an 8-byte battery frame that matches example.dbc."""
    voltage = clamp(random.gauss(420, 8), 360, 450)              # V
    current = clamp(random.gauss(0, 50), -250, 250)              # A
    soc = clamp(random.gauss(80, 4), 40, 100)                    # %
    temp = clamp(random.gauss(45, 5), 10, 70)                    # C

    v_raw = encode_unsigned(voltage, 0.1, 0, 16)
    i_raw = encode_signed(current, 0.1, 0, 16)
    soc_raw = encode_unsigned(soc, 0.5, 0, 8)
    t_raw = encode_unsigned(temp, 1, -40, 8)

    data = (
        to_le_bytes(v_raw, 2) +
        to_le_bytes(i_raw, 2) +
        [soc_raw] +
        [t_raw] +
        [0, 0]
    )
    return data


def generate_session_csv(session_start, output_dir):
    """Write a CSV file for a single driving session."""
    session_name = session_start.strftime("%Y-%m-%d-%H-%M-%S")
    fname = os.path.join(output_dir, f"{session_name}.csv")

    duration_ms = SESSION_LENGTH_MIN * 60 * 1000
    frame_interval_ms = max(1, int(round(1000 / FREQ_HZ)))
    battery_offset_ms = max(1, min(frame_interval_ms - 1, int(round(frame_interval_ms / 2))))

    with open(fname, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)

        rel_ms = 0
        while rel_ms <= duration_ms:
            drivetrain_row = [rel_ms, PROTOCOL, ID_DRIVETRAIN] + gen_drivetrain_frame()
            writer.writerow(drivetrain_row)

            battery_ms = rel_ms + battery_offset_ms
            if battery_ms <= duration_ms:
                battery_row = [battery_ms, PROTOCOL, ID_BATTERY] + gen_battery_frame()
                writer.writerow(battery_row)

            rel_ms += frame_interval_ms

    print(f"Generated: {fname}")


def main():
    for day in range(DAYS):
        day_date = START_DATE + timedelta(days=day)

        for session in range(SESSIONS_PER_DAY):
            # Stagger sessions through the day so filenames look realistic
            minutes_offset = session * (SESSION_LENGTH_MIN + 5) + random.randint(5, 15)
            session_start = day_date + timedelta(minutes=minutes_offset)
            generate_session_csv(session_start, OUTPUT_DIR)

    print("Done!")


if __name__ == "__main__":
    main()
