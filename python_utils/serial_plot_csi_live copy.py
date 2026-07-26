import sys
import matplotlib
import matplotlib.pyplot as plt
import math
import numpy as np
import collections
from wait_timer import WaitTimer
from read_stdin import readline, print_until_first_csi_line

# Set subcarrier to plot
subcarrier = 10

# Wait Timers. Change these values to increase or decrease the rate of `print_stats` and `render_plot`.
print_stats_wait_timer = WaitTimer(1.0)
render_plot_wait_timer = WaitTimer(0.2)

# Deque definition
perm_amp = collections.deque(maxlen=100)
perm_phase = collections.deque(maxlen=100)

# Variables to store CSI statistics
packet_count = 0
total_packet_counts = 0

baseline_value = None
calibration_packets = 100

# The baseline can adapt very slowly, but only while the signal is quiet.
adaptive_baseline = True
baseline_learning_rate = 0.001
quiet_threshold_percent = 3.0

# Create figure for plotting
plt.ion()
fig = plt.figure()
ax = fig.add_subplot(111)
fig.canvas.draw()
plt.show(block=False)


def carrier_plot(amp):
    global baseline_value

    plt.clf()

    # Shape:
    # rows = received Wi-Fi packets
    # columns = CSI subcarrier amplitudes
    data = np.asarray(amp, dtype=np.float64)

    if data.ndim != 2 or data.shape[0] < 3:
        return

    # Use a broad group of CSI subcarriers rather than only one.
    selected = data[:, 10:54]

    # Combine the selected subcarriers into one value per packet.
    # Median is less affected by unusually noisy subcarriers.
    packet_signal = np.median(selected, axis=1)

    # Remove isolated packet-level spikes using a short median filter.
    median_window = 5
    half_window = median_window // 2
    median_filtered = np.empty_like(packet_signal)

    for i in range(len(packet_signal)):
        left = max(0, i - half_window)
        right = min(len(packet_signal), i + half_window + 1)

        median_filtered[i] = np.median(
            packet_signal[left:right]
        )

    # Smooth the signal over multiple packets.
    if len(median_filtered) >= smoothing_window:
        kernel = np.ones(smoothing_window) / smoothing_window

        smooth_signal = np.convolve(
            median_filtered,
            kernel,
            mode="valid"
        )
    else:
        smooth_signal = median_filtered

    # Establish the empty-room baseline only once.
    if baseline_value is None:
        if len(data) < calibration_packets:
            plt.text(
                0.5,
                0.60,
                "CALIBRATING EMPTY BASELINE",
                horizontalalignment="center",
                verticalalignment="center",
                transform=plt.gca().transAxes,
                fontsize=17,
                fontweight="bold"
            )

            plt.text(
                0.5,
                0.43,
                f"{len(data)}/{calibration_packets} packets\n"
                "Keep people and objects away from the link",
                horizontalalignment="center",
                verticalalignment="center",
                transform=plt.gca().transAxes,
                fontsize=12
            )

            plt.xlim(0, 100)
            plt.ylim(-40, 40)
            plt.xlabel("Recent packets")
            plt.ylabel("Change from baseline (%)")
            plt.title("CSI calibration")
            plt.grid(True, alpha=0.25)
            plt.tight_layout()

            fig.canvas.flush_events()
            plt.show()
            return

        # Robust initial baseline from the empty calibration period.
        baseline_value = np.median(smooth_signal)

        print(
            f"Empty-room baseline fixed at: "
            f"{baseline_value:.4f}"
        )

    # Convert amplitude into percentage change from baseline.
    percent_change = (
        100.0
        * (smooth_signal - baseline_value)
        / baseline_value
    )

    latest_change = percent_change[-1]

    # Slowly correct baseline drift only when close to the baseline.
    if (
        adaptive_baseline
        and abs(latest_change) < quiet_threshold_percent
    ):
        latest_amplitude = smooth_signal[-1]

        baseline_value = (
            (1.0 - baseline_learning_rate) * baseline_value
            + baseline_learning_rate * latest_amplitude
        )

        # Recalculate after the small baseline adjustment.
        percent_change = (
            100.0
            * (smooth_signal - baseline_value)
            / baseline_value
        )

    # Display the available filtered samples.
    display_signal = percent_change[-100:]
    x = np.arange(len(display_signal))

    plt.plot(
        x,
        display_signal,
        linewidth=2.5,
        label="Filtered CSI change"
    )

    # Empty-room reference.
    plt.axhline(
        0,
        linewidth=1.8,
        linestyle="--",
        label="Empty baseline"
    )

    # Normal-noise guides.
    plt.axhline(3, linewidth=0.9, linestyle=":")
    plt.axhline(-3, linewidth=0.9, linestyle=":")

    # Larger response guides.
    plt.axhline(10, linewidth=0.9, linestyle=":")
    plt.axhline(-10, linewidth=0.9, linestyle=":")

    plt.fill_between(
        x,
        -3,
        3,
        alpha=0.10,
        label="Approximate quiet zone"
    )

    plt.xlabel("Recent filtered samples")
    plt.ylabel("Change from empty baseline (%)")

    plt.title(
        "Live CSI response — "
        f"current change: {display_signal[-1]:+.1f}%"
    )

    plt.xlim(0, 100)

    # Fixed ±40% y-axis.
    plt.ylim(-40, 40)

    plt.yticks(
        [-40, -30, -20, -10, -5, 0,
          5, 10, 20, 30, 40],
        [
            "-40%", "-30%", "-20%", "-10%", "-5%",
            "0%",
            "+5%", "+10%", "+20%", "+30%", "+40%"
        ]
    )

    plt.grid(True, alpha=0.25)
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()

    fig.canvas.flush_events()
    plt.show()

def process(res):
    # Parser
    all_data = res.split(',')
    csi_data = all_data[25].split(" ")
    csi_data[0] = csi_data[0].replace("[", "")
    csi_data[-1] = csi_data[-1].replace("]", "")

    csi_data.pop()
    csi_data = [int(c) for c in csi_data if c]
    imaginary = []
    real = []
    for i, val in enumerate(csi_data):
        if i % 2 == 0:
            imaginary.append(val)
        else:
            real.append(val)

    csi_size = len(csi_data)
    amplitudes = []
    phases = []
    if len(imaginary) > 0 and len(real) > 0:
        for j in range(int(csi_size / 2)):
            amplitude_calc = math.sqrt(imaginary[j] ** 2 + real[j] ** 2)
            phase_calc = math.atan2(imaginary[j], real[j])
            amplitudes.append(amplitude_calc)
            phases.append(phase_calc)

        perm_phase.append(phases)
        perm_amp.append(amplitudes)

print_until_first_csi_line()

while True:
    line = readline()
    if "CSI_DATA" in line:
        process(line)
        packet_count += 1
        total_packet_counts += 1

        if print_stats_wait_timer.check():
            print_stats_wait_timer.update()
            print("Packet Count:", packet_count, "per second.", "Total Count:", total_packet_counts)
            packet_count = 0

        if render_plot_wait_timer.check() and len(perm_amp) > 2:
            render_plot_wait_timer.update()
            carrier_plot(perm_amp)
