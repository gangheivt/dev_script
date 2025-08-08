import struct
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.ticker import MultipleLocator
from typing import List, Union, Optional
import tkinter as tk
from tkinter import simpledialog

class RSSISuccessTracker:
    def __init__(
        self,
        byte_arrays: List[Union[bytes, bytearray]],
        num_channels: int = 80,
        int_format: str = 'b',
        db_min: int = -100,
        db_max: int = -30,
        db_step: int = 5,
        delta_min: int = -20,
        delta_max: int = 20,
        delta_step: int = 5,
        count_min: int = 0,
        count_max: Optional[int] = None,
        min_count_max: int = 40,
        subplot_heights: List[float] = [0.3, 0.3, 0.4],
        update_interval: int = 1000,
        start_frame: int = 0,
        rx_hist_max: int = 320
    ):
        self.byte_arrays = byte_arrays
        self.num_channels = num_channels
        self.int_format = int_format
        self.db_min = db_min
        self.db_max = db_max
        self.db_step = db_step
        self.delta_min = delta_min
        self.delta_max = delta_max
        self.delta_step = delta_step
        self.count_min = count_min
        self.count_max = count_max
        self.min_count_max = min_count_max
        self.subplot_heights = subplot_heights
        self.update_interval = update_interval
        self.start_frame = start_frame
        self.rx_hist_max=rx_hist_max
        
        # Animation controls
        self.animation_running = True
        self.current_frame = 0
        self.play_direction = 1
        self.animation = None

        # Tkinter setup
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        
        # Process data
        self.rssi_data, self.act_rssi_data, self.success_data, self.failure_data, self.afh_ch_maps, self.rssi_hist = self._process_data()
        self.delta_data = self.act_rssi_data - self.rssi_data if self.act_rssi_data is not None else None
        self.total_samples = len(self.rssi_data) if self.rssi_data is not None else 0
        
        if self.total_samples > 0:
            self._initialize_plot()
            self.set_current_frame(self.start_frame)
        else:
            print("No valid data to visualize")

    def _process_data(self):
        if not isinstance(self.byte_arrays, list):
            print("Error: Input must be a list of byte arrays")
            return None, None, None, None, None, None
            
        if len(self.byte_arrays) == 0:
            print("Error: Byte array list is empty")
            return None, None, None, None, None, None

        total_values = self.num_channels * 5 + self.rx_hist_max
        
        bytes_per_value = struct.calcsize(self.int_format)
        required_length = total_values * bytes_per_value
        
        rssi_data, act_rssi_data, success_data, failure_data, afh_ch_maps, rx_hist = [], [], [], [], [], []
        for i, arr in enumerate(self.byte_arrays):
            if not isinstance(arr, (bytes, bytearray)):
                print(f"Warning: Element {i+1} is not a byte array - skipping")
                continue
                
            if len(arr) != required_length:
                print(f"Warning: Byte array {i+1} has invalid length {len(arr)} (expected {required_length}) - skipping")
                continue
                
            try:
                all_values = struct.unpack(f'{total_values}{self.int_format}', arr)
                rssi_data.append(all_values[:self.num_channels])
                act_rssi_data.append(all_values[self.num_channels:2*self.num_channels])
                success_data.append(all_values[2*self.num_channels:3*self.num_channels])
                failure_data.append(all_values[3*self.num_channels:4*self.num_channels])
                afh_ch_maps.append(all_values[4*self.num_channels:5*self.num_channels])
                rx_hist.append(all_values[5*self.num_channels:])
            except Exception as e:
                print(f"Error unpacking byte array {i+1}: {e} - skipping")
                continue
        
        if not rssi_data:
            print("Error: No valid RSSI data processed")
            return None, None, None, None, None, None
        
        return (
            np.array(rssi_data),
            np.array(act_rssi_data),
            np.array(success_data),
            np.array(failure_data),
            np.array(afh_ch_maps),
            np.array(rx_hist)
        )

    def _initialize_plot(self):
        self.fig, (self.ax_scan_rssi, self.ax_delta, self.ax_success) = plt.subplots(
            3, 1, figsize=(16, 16), sharex=True,
            gridspec_kw={'height_ratios': self.subplot_heights}
        )
        self.fig.subplots_adjust(top=0.95, hspace=0.2)
        
        try:
            self.fig.canvas.manager.set_window_title('RSSI Tracker')
        except AttributeError:
            pass
        
        # CRITICAL FIX: Disable matplotlib's default 's' save shortcut
        self.fig.canvas.mpl_disconnect(self.fig.canvas.manager.key_press_handler_id)
        
        # Connect our custom event handler
        self.fig.canvas.mpl_connect('button_press_event', self._on_click)
        self.fig.canvas.mpl_connect('key_press_event', self._on_key_press)
        
        self.channels = np.arange(1, self.num_channels + 1)
        
        # Calculate count max
        if self.count_max is None:
            max_success = self.success_data.max() if len(self.success_data) else 0
            max_failure = self.failure_data.max() if len(self.failure_data) else 0
            self.count_max = max(max_success + max_failure, self.min_count_max)
        else:
            self.count_max = max(self.count_max, self.min_count_max)
        
        # 1. Scanned RSSI Subplot
        self.scan_rssi_bars = self.ax_scan_rssi.bar(
            self.channels, np.zeros(self.num_channels), 
            color='blue', alpha=0.8, label='Scanned RSSI (dBm)'
        )
        self.ax_scan_rssi.set_ylim(self.db_min, self.db_max)
        self.ax_scan_rssi.yaxis.set_major_locator(MultipleLocator(self.db_step))
        self.ax_scan_rssi.grid(axis='y', linestyle='-', alpha=0.7)
        self.ax_scan_rssi.set_ylabel('RSSI (dBm)', fontweight='bold')
        self.ax_scan_rssi.legend(loc='upper right')
        self.ax_scan_rssi.set_title('Scanned RSSI by Channel', fontweight='bold')
        
        # 2. Delta Subplot
        self.delta_bars = self.ax_delta.bar(
            self.channels, np.zeros(self.num_channels), 
            alpha=0.8, label='Delta (Actual - Scanned)'
        )
        self.ax_delta.set_ylim(self.delta_min, self.delta_max)
        self.ax_delta.yaxis.set_major_locator(MultipleLocator(self.delta_step))
        self.ax_delta.grid(axis='y', linestyle='-', alpha=0.7)
        self.ax_delta.axhline(y=0, color='black', linestyle='-', alpha=0.5)
        self.ax_delta.set_ylabel('Delta (dBm)', fontweight='bold')
        self.ax_delta.legend(loc='upper right')
        self.ax_delta.set_title('RSSI Difference', fontweight='bold')
        
        # 3. Success/Failure Subplot
        self.success_container = None
        self.failure_container = None
        self.ax_success.set_ylim(self.count_min, self.count_max)
        self.ax_success.yaxis.set_major_locator(MultipleLocator(5))
        self.ax_success.grid(axis='y', linestyle='-', alpha=0.7)
        self.ax_success.set_xlabel('Channel Number', fontweight='bold')
        self.ax_success.set_ylabel('Count', fontweight='bold')
        self.ax_success.set_xticks(self.channels[::5])
        self.ax_success.set_title('Success/Failure Counts', fontweight='bold')
        self.ax_success.bar([], [], color='#4CAF50', label='Success')
        self.ax_success.bar([], [], color='#F44336', label='Failure')
        self.ax_success.legend(loc='upper right')
        
        # Main title and instructions
        self.main_title = self.fig.suptitle(
            self._get_status_text(), fontsize=16, fontweight='bold'
        )
        self.instruction_text = self.fig.text(
            0.5, 0.01,
            "Click: Pause/Resume | →: Forward | ←: Reverse | s: Set current frame",
            ha='center', style='italic'
        )

    def _get_status_text(self):
        status = "Running" if self.animation_running else "Paused"
        direction = "Forward" if self.play_direction == 1 else "Reverse"
        return f'Frame {self.current_frame + 1}/{self.total_samples} | {status} | {direction}'

    def _on_click(self, event):
        if event.inaxes is None:
            return
        self.animation_running = not self.animation_running
        if self.animation:
            (self.animation.event_source.start() if self.animation_running 
             else self.animation.event_source.stop())
        self.main_title.set_text(self._get_status_text())
        self.fig.canvas.draw_idle()

    def _on_key_press(self, event):
        # Explicitly handle only our keys
        if event.key in ['right', 'f']:
            self.play_direction = 1
        elif event.key in ['left', 'r']:
            self.play_direction = -1
        elif event.key == 's':  # Only set current frame
            self.set_current_frame()
            return  # Prevent any other handling of 's'
        self.main_title.set_text(self._get_status_text())
        self.fig.canvas.draw_idle()

    def set_current_frame(self, frame_num: Optional[int] = None):
        """Set current frame with dialog - NO SAVE FUNCTIONALITY"""
        if self.total_samples == 0:
            return

        if frame_num is None:
            # Only show frame input dialog
            user_input = simpledialog.askstring(
                title="Set Current Frame",
                prompt=f"Enter frame (1 to {self.total_samples}, current: {self.current_frame + 1}):",
                parent=self.tk_root
            )
            if not user_input:  # User canceled or empty input
                return
            try:
                frame_num = int(user_input) - 1  # Convert to 0-based index
            except ValueError:
                simpledialog.showerror(
                    title="Invalid Input",
                    message="Please enter a valid integer.",
                    parent=self.tk_root
                )
                return

        # Validate frame range
        if not (0 <= frame_num < self.total_samples):
            simpledialog.showerror(
                title="Invalid Frame",
                message=f"Frame must be between 1 and {self.total_samples}.",
                parent=self.tk_root
            )
            return

        # Update frame and refresh plot
        self.current_frame = frame_num
        self._update_plot(None)
        self.main_title.set_text(self._get_status_text())
        self.fig.canvas.draw_idle()

    def _update_plot(self, frame):
        self.current_frame = np.clip(
            self.current_frame + self.play_direction,
            0, self.total_samples - 1
        )
        
        # Update RSSI bars
        current_rssi = self.rssi_data[self.current_frame]
        current_afh = self.afh_ch_maps[self.current_frame]
        for i, (bar, val) in enumerate(zip(self.scan_rssi_bars, current_rssi)):
            bar.set_height(val)
            bar.set_color('purple' if current_afh[i] == 1 else 'blue')
        
        # Update delta bars
        current_delta = self.delta_data[self.current_frame]
        current_success = self.success_data[self.current_frame]
        current_failure = self.failure_data[self.current_frame]
        for i, delta in enumerate(current_delta):
            if current_success[i] > 0 or current_failure[i] > 0:
                self.delta_bars[i].set_height(delta)
                self.delta_bars[i].set_color(
                    '#4CAF50' if delta > 0 else '#F44336' if delta < 0 else '#9E9E9E'
                )
                self.delta_bars[i].set_alpha(0.8)
            else:
                self.delta_bars[i].set_height(0)
                self.delta_bars[i].set_alpha(0)
        
        # Update success/failure bars
        if self.success_container:
            for bar in self.success_container + self.failure_container:
                bar.remove()
        self.success_container = self.ax_success.bar(
            self.channels, current_success, color='#4CAF50', alpha=0.8
        )
        self.failure_container = self.ax_success.bar(
            self.channels, current_failure, color='#F44336', alpha=0.8, bottom=current_success
        )
        
        self.main_title.set_text(self._get_status_text())
        return (list(self.scan_rssi_bars) + list(self.delta_bars) +
                list(self.success_container) + list(self.failure_container))

    def start_visualization(self):
        if self.total_samples == 0:
            return
        self.animation = FuncAnimation(
            self.fig, self._update_plot,
            interval=self.update_interval, blit=False, repeat=True,
            cache_frame_data=False
        )
        plt.show()

# Example usage
if __name__ == "__main__":
    import random
    
    def generate_sample_data(num_samples=15, num_channels=80):
        sample_arrays = []
        for _ in range(num_samples):
            rssi = [random.randint(-95, -30) for _ in range(num_channels)]
            act_rssi = [x + random.randint(-15, 15) for x in rssi]
            successes = [random.randint(0, 35) for _ in range(num_channels)]
            failures = [random.randint(0, 15) for _ in range(num_channels)]
            afh_map = [random.randint(0, 1) for _ in range(num_channels)]
            all_vals = rssi + act_rssi + successes + failures + afh_map
            sample_arrays.append(struct.pack(f'{len(all_vals)}b', *all_vals))
        return sample_arrays
    
    tracker = RSSISuccessTracker(
        generate_sample_data(),
        min_count_max=80,
        update_interval=800,
        start_frame=4
    )
    tracker.start_visualization()