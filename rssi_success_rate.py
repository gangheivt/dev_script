import struct
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.ticker import MultipleLocator
from typing import List, Union, Optional

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
        subplot_heights: List[float] = [0.3, 0.3, 0.4],  # [Scanned, Delta, Success/Failure]
        update_interval: int = 1000
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
        
        # Process input data
        self.rssi_data, self.act_rssi_data, self.success_data, self.failure_data = self._process_data()
        # Calculate delta (actual - scanned)
        self.delta_data = self.act_rssi_data - self.rssi_data if self.act_rssi_data is not None else None
        self.total_samples = len(self.rssi_data) if self.rssi_data is not None else 0
        
        if self.total_samples > 0:
            self._initialize_plot()
        else:
            print("No valid data to visualize")

    def _process_data(self):
        """Process byte arrays into structured data arrays"""
        if not isinstance(self.byte_arrays, list):
            print("Error: Input must be a list of byte arrays")
            return None, None, None, None
            
        if len(self.byte_arrays) == 0:
            print("Error: The list of byte arrays is empty")
            return None, None, None, None

        total_values = self.num_channels * 4
        bytes_per_value = struct.calcsize(self.int_format)
        required_length = total_values * bytes_per_value
        
        rssi_data = []
        act_rssi_data = []
        success_data = []
        failure_data = []
        
        for i, arr in enumerate(self.byte_arrays):
            if not isinstance(arr, (bytes, bytearray)):
                print(f"Warning: Element {i+1} is not a byte array - skipping")
                continue
                
            if len(arr) != required_length:
                print(f"Warning: Byte array {i+1} has incorrect length {len(arr)} (expected {required_length}) - skipping")
                continue
                
            try:
                all_values = struct.unpack(f'{total_values}{self.int_format}', arr)
                rssi = all_values[:self.num_channels]
                act_rssi = all_values[self.num_channels:2*self.num_channels]
                successes = all_values[2*self.num_channels:3*self.num_channels]
                failures = all_values[3*self.num_channels:]
                
                act_rssi_data.append(act_rssi)
                rssi_data.append(rssi)
                success_data.append(successes)
                failure_data.append(failures)
                
            except Exception as e:
                print(f"Error unpacking byte array {i+1}: {str(e)} - skipping")
                continue
        
        if not rssi_data or not act_rssi_data or not success_data or not failure_data:
            print("Error: No valid data to process")
            return None, None, None, None
            
        return (
            np.array(rssi_data),
            np.array(act_rssi_data),
            np.array(success_data),
            np.array(failure_data)
        )

    def _initialize_plot(self):
        """Initialize the visualization with three subplots"""
        # Create 3 subplots with shared x-axis
        self.fig, (self.ax_scan_rssi, self.ax_delta, self.ax_success) = plt.subplots(
            3, 1, figsize=(16, 16), sharex=True,
            gridspec_kw={'height_ratios': self.subplot_heights}
        )
        self.fig.subplots_adjust(top=0.95, hspace=0.2)
        self.fig.canvas.manager.set_window_title('RSSI Delta Tracker')
        
        self.channels = np.arange(1, self.num_channels + 1)
        
        # Calculate max count for success/failure axis
        if self.count_max is None:
            max_success = self.success_data.max()
            max_failure = self.failure_data.max()
            total_max = max(max_success + max_failure, 5)
            self.count_max = max(total_max, self.min_count_max)
        else:
            self.count_max = max(self.count_max, self.min_count_max)
        
        # --------------------------
        # 1. Scanned RSSI Subplot (Top)
        # --------------------------
        self.scan_rssi_bars = self.ax_scan_rssi.bar(
            self.channels, 
            np.zeros(self.num_channels), 
            color='blue', 
            alpha=0.8, 
            label='Scanned RSSI (dBm)'
        )
        
        self.ax_scan_rssi.set_ylim(self.db_min, self.db_max)
        self.ax_scan_rssi.yaxis.set_major_locator(MultipleLocator(self.db_step))
        self.ax_scan_rssi.yaxis.set_minor_locator(MultipleLocator(self.db_step / 2))
        self.ax_scan_rssi.grid(axis='y', which='major', linestyle='-', alpha=0.7)
        self.ax_scan_rssi.grid(axis='y', which='minor', linestyle='--', alpha=0.3)
        self.ax_scan_rssi.set_ylabel('RSSI (dBm)', fontsize=12, fontweight='bold')
        self.ax_scan_rssi.legend(loc='upper right')
        self.ax_scan_rssi.set_title('Scanned RSSI by Channel', fontsize=14, fontweight='bold')
        
        # --------------------------
        # 2. Delta Subplot (Middle) - Actual RSSI - Scanned RSSI
        # --------------------------
        self.delta_bars = self.ax_delta.bar(
            self.channels, 
            np.zeros(self.num_channels), 
            alpha=0.8, 
            label='Delta (Actual - Scanned)'
        )
        
        self.ax_delta.set_ylim(self.delta_min, self.delta_max)
        self.ax_delta.yaxis.set_major_locator(MultipleLocator(self.delta_step))
        self.ax_delta.yaxis.set_minor_locator(MultipleLocator(self.delta_step / 2))
        self.ax_delta.grid(axis='y', which='major', linestyle='-', alpha=0.7)
        self.ax_delta.grid(axis='y', which='minor', linestyle='--', alpha=0.3)
        self.ax_delta.axhline(y=0, color='black', linestyle='-', alpha=0.5)  # Zero reference line
        self.ax_delta.set_ylabel('Delta (dBm)', fontsize=12, fontweight='bold')
        self.ax_delta.legend(loc='upper right')
        self.ax_delta.set_title('RSSI Difference (Actual - Scanned) - Only with Activity', fontsize=14, fontweight='bold')
        
        # --------------------------
        # 3. Success/Failure Subplot (Bottom)
        # --------------------------
        self.success_container = None
        self.failure_container = None
        
        self.ax_success.set_ylim(self.count_min, self.count_max)
        self.ax_success.yaxis.set_major_locator(MultipleLocator(5))
        self.ax_success.yaxis.set_minor_locator(MultipleLocator(1))
        self.ax_success.grid(axis='y', which='major', linestyle='-', alpha=0.7)
        self.ax_success.grid(axis='y', which='minor', linestyle='--', alpha=0.3)
        self.ax_success.set_xlabel('Channel Number', fontsize=12, fontweight='bold')
        self.ax_success.set_ylabel('Count', fontsize=12, fontweight='bold')
        self.ax_success.set_xticks(self.channels[::5])
        self.ax_success.set_xticklabels(self.channels[::5], fontsize=10)
        self.ax_success.set_title('Success/Failure Counts by Channel', fontsize=14, fontweight='bold')
        self.ax_success.legend(loc='upper right')
        
        # Main title with sample information
        self.main_title = self.fig.suptitle(
            f'Sample 1/{self.total_samples}',
            fontsize=20,
            fontweight='bold',
            y=0.99
        )

    def _update_plot(self, frame):
        """Update function for animation"""
        # Update Scanned RSSI bars
        current_scan_rssi = self.rssi_data[frame]
        for bar, value in zip(self.scan_rssi_bars, current_scan_rssi):
            bar.set_height(value)
            bar.set_color('purple' if value < self.db_min or value > self.db_max else 'blue')
        
        # Get current frame data
        current_delta = self.delta_data[frame]
        current_success = self.success_data[frame]
        current_failure = self.failure_data[frame]
        
        # Update Delta bars - ONLY SHOW when success OR failure > 0
        for i, (delta, success, failure) in enumerate(zip(
            current_delta, current_success, current_failure
        )):
            if success > 0 or failure > 0:
                # Show delta with color coding when there's activity
                self.delta_bars[i].set_height(delta)
                if delta > 0:
                    self.delta_bars[i].set_color('#4CAF50')  # Green for positive delta
                elif delta < 0:
                    self.delta_bars[i].set_color('#F44336')  # Red for negative delta
                else:
                    self.delta_bars[i].set_color('#9E9E9E')  # Gray for zero delta
                self.delta_bars[i].set_alpha(0.8)
            else:
                # Hide delta when no activity (success and failure are zero)
                self.delta_bars[i].set_height(0)
                self.delta_bars[i].set_alpha(0)  # Make completely transparent
        
        # Update Success/Failure bars (Bottom subplot)
        # Remove old bars
        if self.success_container:
            for bar in self.success_container:
                bar.remove()
        if self.failure_container:
            for bar in self.failure_container:
                bar.remove()
        
        # Create new success bars
        self.success_container = self.ax_success.bar(
            self.channels, 
            current_success, 
            color='#4CAF50', 
            alpha=0.8, 
            label='Success' if frame == 0 else ""
        )
        
        # Create new failure bars (stacked on top of success)
        self.failure_container = self.ax_success.bar(
            self.channels, 
            current_failure, 
            color='#F44336', 
            alpha=0.8, 
            bottom=current_success, 
            label='Failure' if frame == 0 else ""
        )
        
        # Update sample counter title
        self.main_title.set_text(f'Sample {frame + 1}/{self.total_samples}')
        
        self.fig.canvas.draw_idle()
        # Return all bars for animation
        return (
            list(self.scan_rssi_bars) + 
            list(self.delta_bars) +
            list(self.success_container) + 
            list(self.failure_container)
        )

    def start_visualization(self):
        """Start the animation visualization"""
        if self.total_samples == 0:
            return
            
        self.animation = FuncAnimation(
            self.fig,
            self._update_plot,
            frames=self.total_samples,
            interval=self.update_interval,
            blit=False,
            repeat=False
        )
        
        plt.show()

# Example usage with sample data generation
if __name__ == "__main__":
    import random
    
    def generate_sample_data(num_samples=10, num_channels=80):
        """Generate sample data with some inactive channels"""
        sample_arrays = []
        int_format = 'b'  # Using signed byte format
        
        for _ in range(num_samples):
            # Generate scanned RSSI values (-95 to -30 dBm)
            rssi = [random.randint(-95, -30) for _ in range(num_channels)]
            
            # Generate actual RSSI with variations from scanned values
            act_rssi = [x + random.randint(-15, 15) for x in rssi]
            
            # Generate success counts with some zeros (inactive channels)
            successes = []
            for _ in range(num_channels):
                # 30% chance of inactivity
                if random.random() < 0.3:
                    successes.append(0)
                else:
                    successes.append(random.randint(1, 35))
            
            # Generate failure counts (only non-zero when there's success)
            failures = []
            for s in successes:
                if s == 0:
                    failures.append(0)
                else:
                    failures.append(random.randint(0, 15))
            
            # Combine all values and pack into byte array
            all_values = rssi + act_rssi + successes + failures
            byte_array = struct.pack(f'{len(all_values)}{int_format}', *all_values)
            sample_arrays.append(byte_array)
            
        return sample_arrays
    
    # Generate test data
    sample_data = generate_sample_data(num_samples=15, num_channels=80)
    
    # Create and start the tracker
    tracker = RSSISuccessTracker(
        sample_data,
        min_count_max=80,
        db_min=-100,
        db_max=-30,
        delta_min=-40,
        delta_max=40,
        update_interval=800  # Update every 800ms
    )
    tracker.start_visualization()
    