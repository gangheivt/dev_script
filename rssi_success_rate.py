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
        int_format: str = 'h',
        db_min: int = -100,
        db_max: int = -30,
        db_step: int = 5,
        count_max: Optional[int] = None
    ):
        self.byte_arrays = byte_arrays
        self.num_channels = num_channels
        self.int_format = int_format
        self.db_min = db_min
        self.db_max = db_max
        self.db_step = db_step
        self.count_max = count_max
        
        self.rssi_data, self.success_data, self.failure_data = self._process_data()
        self.total_samples = len(self.rssi_data) if self.rssi_data is not None else 0
        
        if self.total_samples > 0:
            self._initialize_plot()

    def _process_data(self):
        if not isinstance(self.byte_arrays, list):
            print("Error: Input must be a list of byte arrays")
            return None, None, None
            
        if len(self.byte_arrays) == 0:
            print("Error: The list of byte arrays is empty")
            return None, None, None

        total_values = self.num_channels * 3
        bytes_per_value = struct.calcsize(self.int_format)
        required_length = total_values * bytes_per_value
        
        rssi_data = []
        success_data = []
        failure_data = []
        
        for i, arr in enumerate(self.byte_arrays):
            if not isinstance(arr, (bytes, bytearray)):
                print(f"Warning: Element {i+1} is not a byte array - skipping")
                continue
                
            if len(arr) != required_length:
                print(f"Warning: Byte array {i+1} has incorrect length - skipping")
                continue
                
            try:
                all_values = struct.unpack(f'{total_values}{self.int_format}', arr)
                rssi = all_values[:self.num_channels]
                successes = all_values[self.num_channels:2*self.num_channels]
                failures = all_values[2*self.num_channels:]
                
                rssi_data.append(rssi)
                success_data.append(successes)
                failure_data.append(failures)
                
            except Exception as e:
                print(f"Error unpacking byte array {i+1}: {str(e)} - skipping")
                continue
        
        if not rssi_data or not success_data or not failure_data:
            print("Error: No valid data to process")
            return None, None, None
            
        return (
            np.array(rssi_data),
            np.array(success_data),
            np.array(failure_data)
        )

    def _initialize_plot(self):
        self.fig, (self.ax_rssi, self.ax_success) = plt.subplots(
            2, 1, figsize=(16, 14), sharex=True
        )
        self.fig.subplots_adjust(top=0.95, hspace=0.1)
        self.fig.canvas.manager.set_window_title('RSSI & Success/Failure Tracker')
        
        self.channels = np.arange(1, self.num_channels + 1)
        
        if self.count_max is None:
            max_success = self.success_data.max()
            max_failure = self.failure_data.max()
            self.count_max = max(max_success + max_failure, 5)
        
        # Initialize RSSI bars
        self.rssi_bars = self.ax_rssi.bar(
            self.channels, 
            np.zeros(self.num_channels), 
            color='blue', 
            alpha=0.8, 
            label='RSSI (dBm)'
        )
        
        # Configure RSSI axis
        self.ax_rssi.set_ylim(self.db_min, self.db_max)
        self.ax_rssi.yaxis.set_major_locator(MultipleLocator(self.db_step))
        self.ax_rssi.yaxis.set_minor_locator(MultipleLocator(self.db_step / 2))
        self.ax_rssi.grid(axis='y', which='major', linestyle='-', alpha=0.7)
        self.ax_rssi.grid(axis='y', which='minor', linestyle='--', alpha=0.3)
        self.ax_rssi.set_ylabel('RSSI (dBm)', fontsize=12, fontweight='bold')
        self.ax_rssi.legend(loc='upper right')
        
        # For success/failure, we'll store references to the bar containers
        self.success_container = None
        self.failure_container = None
        
        # Configure success/failure axis
        self.ax_success.set_ylim(0, self.count_max)
        self.ax_success.yaxis.set_major_locator(MultipleLocator(5))
        self.ax_success.yaxis.set_minor_locator(MultipleLocator(1))
        self.ax_success.grid(axis='y', which='major', linestyle='-', alpha=0.7)
        self.ax_success.grid(axis='y', which='minor', linestyle='--', alpha=0.3)
        self.ax_success.set_xlabel('Channel Number', fontsize=12, fontweight='bold')
        self.ax_success.set_ylabel('Count', fontsize=12, fontweight='bold')
        self.ax_success.set_xticks(self.channels[::5])
        self.ax_success.set_xticklabels(self.channels[::5], fontsize=10)
        self.ax_success.legend(loc='upper right')
        
        # Main title
        self.main_title = self.fig.suptitle(
            f'Sample 1/{self.total_samples}',
            fontsize=20,
            fontweight='bold',
            y=0.99
        )

    def _update_plot(self, frame):
        # Update RSSI bars
        current_rssi = self.rssi_data[frame]
        for bar, value in zip(self.rssi_bars, current_rssi):
            bar.set_height(value)
            if value < self.db_min or value > self.db_max:
                bar.set_color('purple')
            else:
                bar.set_color('blue')
        
        # Update success/failure bars using the compatible method
        current_success = self.success_data[frame]
        current_failure = self.failure_data[frame]
        
        # Remove old bars if they exist
        if self.success_container:
            for bar in self.success_container:
                bar.remove()
        if self.failure_container:
            for bar in self.failure_container:
                bar.remove()
        
        # Create new success bars (bottom layer)
        self.success_container = self.ax_success.bar(
            self.channels, 
            current_success, 
            color='#4CAF50', 
            alpha=0.8, 
            label='Success' if frame == 0 else ""  # Only show legend once
        )
        
        # Create new failure bars (top layer, stacked)
        self.failure_container = self.ax_success.bar(
            self.channels, 
            current_failure, 
            color='#F44336', 
            alpha=0.8, 
            bottom=current_success, 
            label='Failure' if frame == 0 else ""  # Only show legend once
        )
        
        # Update title
        self.main_title.set_text(f'Sample {frame + 1}/{self.total_samples}')
        
        self.fig.canvas.draw_idle()
        # Return all bars for animation
        return list(self.rssi_bars) + list(self.success_container) + list(self.failure_container)

    def start_visualization(self):
        if self.total_samples == 0:
            print("No valid data to visualize")
            return
            
        self.animation = FuncAnimation(
            self.fig,
            self._update_plot,
            frames=self.total_samples,
            interval=1000,
            blit=False,
            repeat=False
        )
        
        plt.show()

# Example usage with sample data
if __name__ == "__main__":
    import random
    
    def generate_sample_data(num_samples=5, num_channels=80):
        sample_arrays = []
        int_format = 'h'
        
        for _ in range(num_samples):
            rssi = [random.randint(-95, -30) for _ in range(num_channels)]
            successes = [random.randint(0, 15) for _ in range(num_channels)]
            failures = [random.randint(0, 5) for _ in range(num_channels)]
            
            all_values = rssi + successes + failures
            byte_array = struct.pack(f'{len(all_values)}{int_format}', *all_values)
            sample_arrays.append(byte_array)
            
        return sample_arrays
    
    sample_data = generate_sample_data(num_samples=10)
    tracker = RSSISuccessTracker(sample_data)
    tracker.start_visualization()
