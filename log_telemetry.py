import os
import re
import subprocess
import time

timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
LOG_FILE = os.getenv("NETWORK_TELEMETRY_CSV", f"network_telemetry_{timestamp}.csv")

# Monitored server ports
RTMP_PORT = "1935"
RTSP_PORT = "8554"


def parse_endpoint(addr_str):
    """Safely extracts IP and port from IPv4, IPv6, and bracketed addresses."""
    addr_str = addr_str.strip()
    if ":" in addr_str:
        ip, port = addr_str.rsplit(":", 1)
        return ip.strip("[]"), port
    return addr_str, ""


def parse_rtt_value(raw_val, unit_str):
    """Converts rtt values to float milliseconds handling 'us' and 'ms'."""
    try:
        val = float(raw_val)
        if unit_str == "us":
            return val / 1000.0
        return val
    except ValueError:
        return 0.0


# Initialize CSV File Header
with open(LOG_FILE, "w", buffering=1, encoding="utf-8") as f:
    f.write("timestamp,sport,dport,peer_ip,recv_q_bytes,send_q_bytes,rtt_ms,srtt_ms\n")

print(f"[*] Logging server-side raw socket telemetry to {LOG_FILE}")
print(
    f"[*] Polling server ports {RTMP_PORT} (RTMP) and {RTSP_PORT} (RTSP) every 1s. Press Ctrl+C to stop...\n"
)

no_conn_counter = 0

try:
    with open(LOG_FILE, "a", buffering=1, encoding="utf-8") as log_f:
        while True:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            recorded_count = 0

            try:
                # FIX 1: Filter strictly by SERVER SOURCE PORT (sport) to prevent
                # double-counting loopback connections on 127.0.0.1
                cmd = [
                    "ss",
                    "-Htinp",
                    f"( sport = :{RTMP_PORT} or sport = :{RTSP_PORT} )",
                ]
                raw_output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
                output = raw_output.decode("utf-8", errors="ignore")
                lines = [l.strip() for l in output.strip().split("\n") if l.strip()]

                i = 0
                while i < len(lines):
                    line = lines[i]
                    if line.startswith("ESTAB"):
                        parts = line.split()
                        if len(parts) >= 5:
                            recv_q = parts[1]
                            send_q = parts[2]
                            local_addr = parts[3]
                            peer_addr = parts[4]

                            _, sport = parse_endpoint(local_addr)
                            peer_ip, dport = parse_endpoint(peer_addr)

                            srtt_val = 0.0
                            minrtt_val = 0.0

                            # Consume all metric lines belonging to this socket block
                            i += 1
                            while i < len(lines) and not lines[i].startswith("ESTAB"):
                                metric_line = lines[i]

                                # FIX 2: Unit-aware RTT extraction (handles 'us', 'ms', and raw floats)
                                rtt_match = re.search(
                                    r"rtt:([\d\.]+)(us|ms)?", metric_line
                                )
                                if rtt_match:
                                    raw_val = rtt_match.group(1)
                                    unit = rtt_match.group(2) or "ms"
                                    srtt_val = parse_rtt_value(raw_val, unit)

                                minrtt_match = re.search(
                                    r"minrtt:([\d\.]+)(us|ms)?", metric_line
                                )
                                if minrtt_match:
                                    raw_val = minrtt_match.group(1)
                                    unit = minrtt_match.group(2) or "ms"
                                    minrtt_val = parse_rtt_value(raw_val, unit)

                                i += 1

                            # If minrtt is missing, fall back to srtt
                            rtt_out = minrtt_val if minrtt_val > 0.0 else srtt_val

                            log_f.write(
                                f"{timestamp},{sport},{dport},{peer_ip},{recv_q},{send_q},{rtt_out:.3f},{srtt_val:.3f}\n"
                            )
                            recorded_count += 1
                            continue

                    i += 1

                if recorded_count > 0:
                    no_conn_counter = 0
                    print(
                        f"[{timestamp}] Logged {recorded_count} active stream socket(s) to {LOG_FILE}"
                    )
                else:
                    no_conn_counter += 1
                    if no_conn_counter % 5 == 1:
                        print(
                            f"[{timestamp}] Waiting for active streams on port {RTMP_PORT}/{RTSP_PORT}..."
                        )

            except FileNotFoundError:
                print("\n[-] ERROR: 'ss' command not found on host system.")
                break
            except subprocess.CalledProcessError as e:
                print(f"[!] 'ss' execution failed: {e.output.decode('utf-8', errors='ignore').strip()}")
            except Exception as e:
                print(f"[!] Telemetry error: {e}")

            time.sleep(1)

except KeyboardInterrupt:
    print("\n[+] Network telemetry logging stopped cleanly.")