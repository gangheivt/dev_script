import csv
import re
import sys
import math
from dataclasses import dataclass
from collections import defaultdict
from typing import List, Optional, Union
import tabulate

afh_group=0
afh_group_count=0

DEFAULT_RX_OK_RATE=0.6
DEFAULT_TTL=4

def parse_file(input_txt, output_csv):
    # 匹配地址模式：xxxx-yyyy:
    addr_pattern = re.compile(r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}:', re.IGNORECASE)
    # 匹配十六进制字节
    byte_pattern = re.compile(r'[0-9a-fA-F]{2}', re.IGNORECASE)
    
    time_pattern = re.compile(r'[0-9]{2}\:[0-9]{2}:[0-9]{2}\:[0-9]{3}', re.IGNORECASE)
    
    global group_counter, afh_group, afh_group_count
    # 状态管理
    active_block = False    # 是否在数据块中
    total_groups = 0        # 预期的总组数
    collected_bytes = []    # 收集到的所有字节
    group_counter = 1       # 当前分组计数
    with open(input_txt, 'r') as infile, open(output_csv, 'w', newline='') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(['index', 'afh_group' , 'index_range', 'time', 'channel', 'freq', 'rssi', 'is_auio', 'rx_ok', 'sync_err', 'hec_err', 'guard_err', 'crc_err', 'others'])  # CSV头部
        
        for line_number, line in enumerate(infile, start = 1):
            # 检测块开始：行中包含"D/HEX sco rssi:"
            if "D/HEX rx total:" in line:
                # 结束前一个块（如果未完成）
                print(line_number, "Evaluate Above--------------------^^")
                afh_group_count=0;
                afh_group = afh_group + 1
                if active_block and len(collected_bytes) >= 2:
                    process_block(collected_bytes, total_groups, writer, timestr_in_line)
                
                # 开始新数据块
                active_block = True
                total_groups = 0
                collected_bytes = []
                
                # 查找第一个地址模式
                addr_match = addr_pattern.search(line)
                if not addr_match:
                    active_block = False
                    continue
                
                # 获取时间
                timestr_in_line = time_pattern.findall(line)[0]
                
                # 提取地址模式后的所有字节
                byte_str = line[addr_match.end():]
                bytes_in_line = byte_pattern.findall(byte_str)
                
                # 前2个字节表示总组数
                if len(bytes_in_line) >= 2:
                    total_groups = ((int(bytes_in_line[1], 16) << 8) | int(bytes_in_line[0], 16))

                    # 添加所有字节到集合
                    collected_bytes.extend(bytes_in_line)
                else:
                    active_block = False
                continue
            
            # 处理块内数据行
            if active_block:
                # 查找地址模式
                addr_match = addr_pattern.search(line)
                if not addr_match:
                    # 结束当前块并处理
                    process_block(collected_bytes, total_groups, writer, timestr_in_line)
                    active_block = False
                    continue
                
                # 提取地址模式后的所有字节
                byte_str = line[addr_match.end():]
                bytes_in_line = byte_pattern.findall(byte_str)
                collected_bytes.extend(bytes_in_line)
        
        # 处理文件末尾的数据块
        if active_block and len(collected_bytes) >= 2:
            process_block(collected_bytes, total_groups, writer, timestr_in_line)

@dataclass
class channel_assess:
    channel: int
    afh_group: int
    timestr_in_line: str
    rssi: int
    is_audio: int
    rx_ok: int
    sync_err: int
    hec_err: int
    guard_err: int
    crc_err: int
    other_err: int

class ChannelStatsArray:
    """基于信道编号索引的固定大小统计数组"""
    
    def __init__(self, max_channel: int):
        """
        初始化固定大小的信道统计数组
        
        Args:
            max_channel: 最大信道编号（决定数组大小）
        """
        self._max_channel = max_channel
        self._array = [self._create_default_stats(channel) for channel in range(max_channel + 1)]

    def __iter__(self):
        """使对象可迭代，返回所有有数据的信道统计"""
        return iter(self.get_all_channels())
    
    def items(self):
        """返回 (channel, stats) 形式的迭代器"""
        for stats in self.get_all_channels():
            yield stats["channel"], stats
        
    def _create_default_stats(self, channel: int) -> dict:
        """创建默认的统计数据结构"""
        return {
            "channel": channel,
            "rssi": 0,
            "valid_rssi_cnt": 0,
            "inv_rssi_cnt": 0,
            "rx_ok": 0,
            "rx_error": 0,
            "total": 0,
            "ttl":DEFAULT_TTL
        }
    
    def update(self, item: 'channel_assess') -> None:
        """更新指定信道的统计数据"""
        channel = item.channel
        if 0 <= channel <= self._max_channel:
            stats = self._array[channel]
            # 更新 RSSI 统计
            if item.sync_err==0 :  
                stats["rssi"] += item.rssi
                stats["valid_rssi_cnt"] += 1
            else:
                stats["inv_rssi_cnt"] += 1
            
            # 更新接收状态统计
            stats["rx_ok"] += item.rx_ok
            stats["rx_error"] += (item.hec_err + 
                                 item.guard_err + item.crc_err + 
                                 item.other_err)
            stats["total"] += 1                     
        else:
            raise IndexError(f"Channel {channel} out of range [0, {self._max_channel}]")
        self._sorted_array = sorted(
                [stats for stats in self._array if stats["total"] > 0],
                key=lambda x: self.get_rx_ok_rate(x["channel"]),
                reverse=True
            )
            
    def get_channel_stats(self, channel: int) -> dict:
        """获取指定信道的统计信息"""
        if 0 <= channel <= self._max_channel:
            return self._array[channel]
        else:
            raise IndexError(f"Channel {channel} out of range [0, {self._max_channel}]")
            
    def get(self, channel: int) -> dict:
        """获取指定信道的统计数据"""
        self._check_channel(channel)
        return self._array[channel]
    
    def get_average_rssi(self, channel: int) -> float:
        """计算指定信道的平均 RSSI"""
        stats = self.get(channel)
        count = stats["valid_rssi_cnt"]
        return stats["rssi"] / count if count > 0 else 0

    def get_rx_ok_rate(self, channel: int) -> float:
        """计算指定信道的接收成功率"""
        stats = self.get_channel_stats(channel)
        if stats["total"] > 0:
            return stats["rx_ok"] / stats["total"]
        else:
            return DEFAULT_RX_OK_RATE
        return 0
        
    def get_all_channels(self) -> list[dict]:
        """获取所有信道的统计数据"""
        return [stats for stats in self._array if stats["valid_rssi_cnt"] > 0 or stats["inv_rssi_cnt"] > 0]

    def clear_low_access_channels(self) -> int:
        """
        清空总访问次数为1的信道统计数据
        
        Returns:
            被清空的信道数量
        """
        cleared_count = 0
        
        for channel in range(self._max_channel + 1):
            stats = self.get(channel)
            if stats["total"] == 1:
                self.clear(channel)
                cleared_count += 1
        
        return cleared_count    
        
    def sort_by(self, field: str, reverse: bool = False) -> list[dict]:
        """
        按指定字段对信道统计数据进行排序
        
        Args:
            field: 排序字段，支持 'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error'
            reverse: 是否降序排列，默认为升序
        
        Returns:
            排序后的统计数据列表
        """
        # 检查字段是否有效
        valid_fields = {'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error', 'average_rssi', 'rx_ok_rate'}
        if field not in valid_fields:
            raise ValueError(f"Invalid sort field: {field}. Valid fields are {valid_fields}")
        
        # 获取所有有数据的信道
        channels = self.get_all_channels()
        
        # 根据不同字段进行排序
        if field == 'average_rssi':
            # 特殊处理：按平均 RSSI 排序
            return sorted(channels, 
                         key=lambda x: x['rssi'] / x['valid_rssi_cnt'] if x['valid_rssi_cnt'] > 0 else 0, 
                         reverse=reverse)
        elif field == 'rx_ok_rate':    
            return sorted(channels, 
                         key=lambda x: x['rx_ok'] / x['total'] if x['total'] > 0 else DEFAULT_RX_OK_RATE, 
                         reverse=reverse)            
        else:
            # 按普通字段排序
            return sorted(channels, key=lambda x: x[field], reverse=reverse)
    
    def clear(self, channel: int) -> None:
        """清空指定信道的统计数据"""
        self._check_channel(channel)
        self._array[channel] = self._create_default_stats(channel)
    
    def clear_all(self) -> None:
        """清空所有统计数据"""
        self._array = [self._create_default_stats(channel) for channel in range(self._max_channel + 1)]
    
    def _check_channel(self, channel: int) -> None:
        """检查信道是否越界"""
        if not (0 <= channel <= self._max_channel):
            raise IndexError(f"Channel {channel} out of range [0, {self._max_channel}]")
            
    def get_active_channels(self) -> set:
        """获取所有有数据的信道编号集合"""
        return {stats["channel"] for stats in self.get_all_channels()}
    
    def compare(self, other: 'ChannelStatsArray') -> tuple:
        """
        比较两个 ChannelStatsArray，返回新增、移除和保留的信道
        
        Args:
            other: 另一个 ChannelStatsArray 对象
        
        Returns:
            元组 (added_channels, removed_channels, kept_channels)
                - added_channels: 新增的信道集合（other 有而 self 没有）
                - removed_channels: 移除的信道集合（self 有而 other 没有）
                - kept_channels: 保留的信道集合（两者都有）
        """
        if not isinstance(other, ChannelStatsArray):
            raise TypeError("Comparison must be between two ChannelStatsArray objects")
        
        current_channels = self.get_active_channels()
        other_channels = other.get_active_channels()
        
        added = other_channels - current_channels  # other 比 self 多的信道
        removed = current_channels - other_channels  # self 比 other 多的信道
        kept = current_channels & other_channels  # 两者共有的信道
        
        return added, removed, kept
        
    def print_channel_numbers(self, sort: bool = True) -> None:
        """
        仅打印有数据的信道编号
        
        Args:
            sort: 是否按信道编号排序，默认为 True
        """
        channels = self.get_active_channels()
        
        if not channels:
            print("No active channels found.")
            return
        
        if sort:
            channels = sorted(channels)
        
        print("Active Channels:", end=" ")
        print(*channels, sep=", ")
        
    def update_from_history(self, history: 'ChannelStatsArray', overwrite_all: bool = False) -> None:
        """
        将历史统计数据合并到当前实例，累加所有统计值而非覆盖
        
        Args:
            history: 历史记录的 ChannelStatisticsManager 对象
            overwrite_all: 是否更新所有信道，即使历史数据中不存在，默认为 False
        """        
        if self._max_channel != history._max_channel:
            raise ValueError("Cannot update from history with different max_channel")
        
        if overwrite_all:
            # 合并所有信道数据
            for i in range(self._max_channel + 1):
                self._merge_channel_stats(i, history._array[i])
        else:
            # 仅合并历史中存在的活跃信道
            for channel_stats in history._array:
                if channel_stats["total"] > 0:  # 只处理有数据的信道
                    self._merge_channel_stats(channel_stats["channel"], channel_stats)
                    
            # 重置所有历史中不存在的信道
            for channel in range(self._max_channel + 1):
                current = self._array[channel]
                if current['channel'] not in history.get_active_channels():
                    if (current['ttl']>0):
                        current['ttl']=current['ttl']-1
                    else:
                        self._array[channel] = self._create_default_stats(channel)                    
                
        self._sort_required = True  # 合并后需要重新排序

    def _merge_channel_stats(self, channel: int, history_stats: dict) -> None:
        """合并单个信道的统计数据"""
        current = self._array[channel]
        current["rssi"] += history_stats["rssi"]
        current["valid_rssi_cnt"] += history_stats["valid_rssi_cnt"]
        current["inv_rssi_cnt"] += history_stats["inv_rssi_cnt"]
        current["rx_ok"] += history_stats["rx_ok"]
        current["rx_error"] += history_stats["rx_error"]
        current["total"] += history_stats["total"]
        current["ttl"] = history_stats["ttl"]

    def print_stats(self, format: str = 'table', detailed: bool = False, sort_by: str = 'rx_ok_rate') -> None:
        """
        打印信道统计信息
        
        Args:
            format: 输出格式，支持 'table' (表格), 'csv' (逗号分隔值), 'json' (JSON格式)
            detailed: 是否显示详细信息，默认为 False
            sort_by: 排序字段，支持 'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error', 'average_rssi', 'rx_ok_rate'
        """
        channels = self.sort_by(sort_by)
        
        if not channels:
            print("No channel statistics available.")
            return
        
        if format == 'table':
            self._print_table(channels, detailed)
        elif format == 'csv':
            self._print_csv(channels, detailed)
        elif format == 'json':
            self._print_json(channels, detailed)
        else:
            raise ValueError(f"Unsupported format: {format}. Valid formats are 'table', 'csv', 'json'.")
    
    def _print_table(self, channels: list[dict], detailed: bool) -> None:
        """以表格形式打印统计信息"""
        from tabulate import tabulate
        
        headers = ["Channel", "Avg RSSI (dBm)", "Rx OK", "Valid RSSI", "Invalid RSSI", "Rx Error", "Total"]
        if detailed:
            headers.extend(["RSSI Sum", "Success Rate"])
        
        table = []
        for stats in channels:
            avg_rssi = self.get_average_rssi(stats["channel"])
            success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
            
            row = [
                stats["channel"],
                f"{avg_rssi:.2f}",
                stats["rx_ok"],
                stats["valid_rssi_cnt"],
                stats["inv_rssi_cnt"],
                stats["rx_error"],
                stats["total"]
            ]
            
            if detailed:
                row.extend([stats["rssi"], f"{success_rate:.2f}%"])
            
            table.append(row)
        
        print(tabulate(table, headers=headers, tablefmt="grid"))
    
    def _print_csv(self, channels: list[dict], detailed: bool) -> None:
        """以CSV格式打印统计信息"""
        import csv
        import sys
        
        headers = ["channel", "average_rssi", "valid_rssi_cnt", "inv_rssi_cnt", "rx_ok", "rx_error", "total"]
        if detailed:
            headers.extend(["rssi_sum", "success_rate"])
        
        writer = csv.DictWriter(sys.stdout, fieldnames=headers)
        writer.writeheader()
        
        for stats in channels:
            row = {
                "channel": stats["channel"],
                "average_rssi": self.get_average_rssi(stats["channel"]),
                "valid_rssi_cnt": stats["valid_rssi_cnt"],
                "inv_rssi_cnt": stats["inv_rssi_cnt"],
                "rx_ok": stats["rx_ok"],
                "rx_error": stats["rx_error"],
                "total": stats["total"]
            }
            
            if detailed:
                success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
                row["rssi_sum"] = stats["rssi"]
                row["success_rate"] = success_rate
            
            writer.writerow(row)
    
    def _print_json(self, channels: list[dict], detailed: bool) -> None:
        """以JSON格式打印统计信息"""
        import json
        
        output = []
        for stats in channels:
            channel_data = {
                "channel": stats["channel"],
                "average_rssi": self.get_average_rssi(stats["channel"]),
                "valid_rssi_cnt": stats["valid_rssi_cnt"],
                "inv_rssi_cnt": stats["inv_rssi_cnt"],
                "rx_ok": stats["rx_ok"],
                "rx_error": stats["rx_error"],
                "total": stats["total"]
            }
            
            if detailed:
                success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
                channel_data["rssi_sum"] = stats["rssi"]
                channel_data["success_rate"] = success_rate
            
            output.append(channel_data)
        
        print(json.dumps(output, indent=2))    
                
    def print_all_with_selected(self, selected_channels: list, format: str = 'table', detailed: bool = False, sort_by: str = 'rx_ok_rate') -> None:
        """
        打印所有有数据的信道，并标记选中的信道
        
        Args:
            selected_channels: 需要标记的选中信道列表
            format: 输出格式，支持 'table' (表格), 'csv' (逗号分隔值), 'json' (JSON格式)
            detailed: 是否显示详细信息，默认为 False
            sort_by: 排序字段，支持 'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error', 'average_rssi'
        """
        # 获取所有有数据的信道并排序
        all_channels = self.sort_by(sort_by)
        
        if not all_channels:
            print("No channel statistics available.")
            return
        
        # 验证选中的信道是否有效
        valid_selected = [ch for ch in selected_channels if 0 <= ch <= self._max_channel]
        invalid_selected = [ch for ch in selected_channels if not (0 <= ch <= self._max_channel)]
        
        if invalid_selected:
            print(f"Warning: Invalid selected channel(s) (out of range [0, {self._max_channel}]): {invalid_selected}")
        
        # 根据格式处理输出
        if format == 'table':
            self._print_table_with_mark(all_channels, valid_selected, detailed)
        elif format == 'csv':
            self._print_csv_with_mark(all_channels, valid_selected, detailed)
        elif format == 'json':
            self._print_json_with_mark(all_channels, valid_selected, detailed)
        else:
            raise ValueError(f"Unsupported format: {format}. Valid formats are 'table', 'csv', 'json'.")

    def _print_table_with_mark(self, channels: list[dict], selected: list, detailed: bool) -> None:
        """带选中标记的表格打印（内部方法）"""
        # 表头添加标记列
        headers = ["Selected", "Channel", "Avg RSSI (dBm)", "Valid RSSI", "Invalid RSSI", "Rx OK", "Rx Error", "Total", "ttl"]
        if detailed:
            headers.extend(["RSSI Sum", "Success Rate"])
        
        table = []
        for stats in channels:
            # 判断是否为选中信道（添加标记）
            mark = "*" if stats["channel"] in selected else " "
            
            avg_rssi = self.get_average_rssi(stats["channel"])
            success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
            
            row = [
                mark,  # 选中标记列
                stats["channel"],
                f"{avg_rssi:.2f}",
                stats["valid_rssi_cnt"],
                stats["inv_rssi_cnt"],
                stats["rx_ok"],
                stats["rx_error"],
                stats["total"],
                stats["ttl"]
            ]
            
            if detailed:
                row.extend([stats["rssi"], f"{success_rate:.2f}%"])
            
            table.append(row)
        
        # 尝试使用tabulate打印，否则使用纯Python实现
        try:
            from tabulate import tabulate
            print(tabulate(table, headers=headers, tablefmt="grid"))
        except ImportError:
            # 纯Python表格实现（带标记）
            column_widths = [max(len(str(row[i])) for row in [headers] + table) for i in range(len(headers))]
            separator = "+" + "+".join("-" * (w + 2) for w in column_widths) + "+"
            
            print(separator)
            print("| " + " | ".join(f"{h:{w}}" for h, w in zip(headers, column_widths)) + " |")
            print(separator)
            
            for row in table:
                print("| " + " | ".join(f"{str(cell):{w}}" for cell, w in zip(row, column_widths)) + " |")
            
            print(separator)
            print("* Indicates selected channels")  # 标记说明

    def _print_csv_with_mark(self, channels: list[dict], selected: list, detailed: bool) -> None:
        """带选中标记的CSV打印（内部方法）"""
        import csv
        import sys
        
        headers = ["is_selected", "channel", "average_rssi", "valid_rssi_cnt", "inv_rssi_cnt", "rx_ok", "rx_error", "total"]
        if detailed:
            headers.extend(["rssi_sum", "success_rate"])
        
        writer = csv.DictWriter(sys.stdout, fieldnames=headers)
        writer.writeheader()
        
        for stats in channels:
            row = {
                "is_selected": "TRUE" if stats["channel"] in selected else "FALSE",
                "channel": stats["channel"],
                "average_rssi": self.get_average_rssi(stats["channel"]),
                "valid_rssi_cnt": stats["valid_rssi_cnt"],
                "inv_rssi_cnt": stats["inv_rssi_cnt"],
                "rx_ok": stats["rx_ok"],
                "rx_error": stats["rx_error"],
                "total": stats["total"]
            }
            
            if detailed:
                success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
                row["rssi_sum"] = stats["rssi"]
                row["success_rate"] = success_rate
            
            writer.writerow(row)

    def _print_json_with_mark(self, channels: list[dict], selected: list, detailed: bool) -> None:
        """带选中标记的JSON打印（内部方法）"""
        import json
        
        output = []
        for stats in channels:
            channel_data = {
                "is_selected": stats["channel"] in selected,
                "channel": stats["channel"],
                "average_rssi": self.get_average_rssi(stats["channel"]),
                "valid_rssi_cnt": stats["valid_rssi_cnt"],
                "inv_rssi_cnt": stats["inv_rssi_cnt"],
                "rx_ok": stats["rx_ok"],
                "rx_error": stats["rx_error"],
                "total": stats["total"]
            }
            
            if detailed:
                success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
                channel_data["rssi_sum"] = stats["rssi"]
                channel_data["success_rate"] = success_rate
            
            output.append(channel_data)
        
        print(json.dumps(output, indent=2))
            
def process_block(bytes_list, total_groups, writer, timestr_in_line):
    """处理一个完整数据块并写入CSV"""
    # 验证数据有效性
    channels=[]
    global group_counter, afh_group, afh_group_count
    global last_array, hist_array
    
    if total_groups == 0 or len(bytes_list) < 2:
        print("Invalid total_groups or bytes_list")
        return
    
    # 计算预期总字节数 = 2(组数字节) + total_groups * 4
    expected_bytes = 2 + total_groups * 4

    if len(bytes_list) < expected_bytes:
        print("Not enought data,", len(bytes_list), "<", expected_bytes)
        expected_bytes=len(bytes_list)
        #return  # 数据不完整    

    # 跳过前2个组数字节，从第3个字节开始
    data_bytes = bytes_list[2:expected_bytes]
    
    # 验证数据长度是4的倍数
    if len(data_bytes) % 4 != 0:
        print("Length error")
        return
    

    # 每4字节一组写入CSV
    for i in range(0, len(data_bytes), 4):
        if i + 4 > len(data_bytes):
            break
            
        index_range = math.floor(group_counter/10000)
        channel = int(data_bytes[i+2], 16) & 0x7F;
        freq = 2402 + channel
        is_audio = (int(data_bytes[i+2], 16)>>7) & 0x1;
        rssi = int(data_bytes[i], 16) - 255
        rx_state = int(data_bytes[i+1], 16);
        rx_ok = 0
        sync_err = 0
        hec_err = 0
        guard_err = 0
        crc_err = 0
        other_err = 0
        if (rx_state & 0x1) != 0:
            sync_err = 1
        elif (rx_state & 0x2) != 0:
            hec_err = 1
        elif (rx_state & 0x4) != 0:
            crc_err = 1
        elif (rx_state & 0x80) != 0:
            guard_err = 1
        elif (rx_state & 0x10) != 0:
            rx_ok = 1
        elif(rx_state & 0x68) != 0:
            other_err = 1
        else:
            rx_ok = 1
        afh_group_count=afh_group_count+1
        writer.writerow([
            group_counter,
            afh_group,    
            index_range,
            timestr_in_line,
            channel,
            freq,
            rssi,
            is_audio,
            rx_ok,
            sync_err,
            hec_err,
            guard_err,
            crc_err,
            other_err,
        ])
        channels.append(channel_assess(
            channel,
            afh_group,
            timestr_in_line,
            rssi,
            is_audio,
            rx_ok,
            sync_err,
            hec_err,
            guard_err,
            crc_err,
            other_err,               
        ))
        group_counter += 1
    
    stats_array = ChannelStatsArray(max_channel=79)    
    for i in channels:
        stats_array.update(i)
    stats_array.clear_low_access_channels()
   
    added_array, removed_array, kept_array=last_array.compare(stats_array)    
    added_array = sorted(added_array)
    removed_array = sorted(removed_array)
    print("Removed ", end="")
    print(removed_array)
    print("Channel up to date history")
    hist_array.print_all_with_selected(added_array, detailed=True)
    print("Added: ", end="")
    print(added_array)
    kept_array = sorted(kept_array)
    print("kept_array ", end="")
    print(kept_array)
    print("=======================================================================================")    
    
    hist_array.update_from_history(stats_array)
    last_array=stats_array    
    stats_array.print_stats(detailed=True)
    
            
last_array = ChannelStatsArray(max_channel=79)
hist_array = ChannelStatsArray(max_channel=79)

if __name__ == "__main__":
    
    input_file = sys.argv[1]  # 替换为你的输入文件路径
    print("input file:", input_file)
    num = len(sys.argv)
    if (num >= 3):
        output_file = sys.argv[2]
    else:
        output_file = "result2.csv"  # 替换为你想要的输出文件路径
    parse_file(input_file, output_file)
    print(f"处理完成，结果已保存到 {output_file}")