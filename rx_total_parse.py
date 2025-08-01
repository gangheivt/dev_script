import csv
import re
import sys
import math
from dataclasses import dataclass
from collections import defaultdict
from typing import List, Optional, Union
from tabulate import tabulate

afh_group=0
afh_group_count=0
afh_error_rate=0.0
afh_cnt_delta =0
channel_score_hist=[]

DEFAULT_RX_OK_RATE=0.4
DEFAULT_TTL=3

class error_rate_cls:
    def __init__(self, rssi, error_rate, cnt):
        self.rssi = rssi
        self.error_rate = error_rate
        self.cnt=cnt
    
    def __lt__(self, other):
        return self.rssi < other.rssi
        
def update_average_dbm(existing_avg_dbm: float, existing_count: int, 
                       new_avg_dbm: float, new_count: int) -> float:
    """
    将两组dBm平均值合并为一个新的平均值
    
    参数:
        existing_avg_dbm (float): 现有数据的平均dBm
        existing_count (int): 现有数据的样本数
        new_avg_dbm (float): 新数据的平均dBm
        new_count (int): 新数据的样本数
    
    返回:
        float: 合并后的新平均dBm
    """
    if existing_count <= 0:
        return new_avg_dbm  # 如果没有现有数据，直接返回新平均值
    
    # 分别计算两组的总功率 (mW)
    existing_total_mw = (10 ** (existing_avg_dbm / 10)) * existing_count
    new_total_mw = (10 ** (new_avg_dbm / 10)) * new_count
    
    # 合并总功率和总样本数
    combined_total_mw = existing_total_mw + new_total_mw
    combined_count = existing_count + new_count
    
    # 计算合并后的新平均功率并转换回dBm
    combined_avg_mw = combined_total_mw / combined_count
    combined_avg_dbm = 10 * math.log10(combined_avg_mw)
    
    return combined_avg_dbm

def parse_afh_log_line(log_line):
    # 修改正则表达式模式，匹配0000-0020:之后的所有十六进制数据
    pattern = r'0000-0020:\s+((?:[0-9A-F]{2}\s+)+)'
    match = re.search(pattern, log_line)
    
    if match:
        # 提取匹配到的十六进制字符串
        hex_str = match.group(1).strip()
        # 将十六进制字符串分割成单个十六进制值
        hex_values = hex_str.split()
        # 转换为整数数组
        result = [int(value, 16) for value in hex_values]
        return result
    else:
        print("未找到匹配的0000-0020数据模式")
        return []

def parse_afh_map(bytes_array: list) -> list:
    """
    解析蓝牙AFH map字节数组，返回可用信道列表
    
    参数:
    bytes_array (list): 包含AFH map的字节数组，如 [0xBB, 0x76, 0xA4, 0x00, ...]
    
    返回:
    list: 可用信道号码列表（从0开始）
    """
    # 初始化AFH映射数组
    afh_map = []
    
    # 遍历每个字节，转换为8位二进制数组（低位在前）
    for byte in bytes_array:
        for i in range(8):  # 从低位到高位处理每个位
            afh_map.append((byte >> i) & 1)  # 提取第i位的值
    
    # 提取所有可用信道的号码（值为1的索引）
    used_channels = [i for i, bit in enumerate(afh_map) if bit == 1]
    
    return used_channels

def print_afh_channels(used_channels: list, group_size: int = 40) -> None:
    """
    格式化打印AFH map中使用的信道号码
    
    参数:
    used_channels (list): 可用信道列表
    group_size (int): 每组显示的信道数量，默认为20
    """
    print("AFH map中使用的信道号码：")
    for i in range(0, len(used_channels), group_size):
        # 计算当前组的起始和结束序号（从1开始计数）
        start = i // group_size * group_size + 1
        end = min(start + group_size - 1, len(used_channels))
        print(f"信道 {start}-{end}: {used_channels[i:i+group_size]}")
    
    # 统计并打印摘要
    total_channels = len(used_channels)
    print(f"总可用信道数：{total_channels}\n")

def parse_channel_quality(byte_array):
    """
    Parses a byte array where each byte represents 4 channels (LSB-first).
    Each channel is 2 bits:
    - 00 (0) = unknown
    - 01 (1) = good
    - 11 (3) = bad
    """
    result = []
    for byte in byte_array:
        # Extract 4 channels from the byte (2 bits each), LSB-first
        for i in range(4):
            # Shift to isolate each 2-bit channel (LSB to MSB)
            channel_bits = (byte >> (i * 2)) & 0b11
            result.append(channel_bits)
    
    groups = {
    "good": [],    # 1 对应 "good"
    "bad": [],     # 3 对应 "bad"
    "unknown": []  # 0 对应 "unknown"
    }
    
    # 遍历数组，按状态分组并记录索引（注意：索引从 0 开始）
    for index, quality in enumerate(result):
        if quality == 1:
            groups["good"].append(2*index)
            groups["good"].append(2*index+1)
        elif quality == 3:
            groups["bad"].append(2*index)
            groups["bad"].append(2*index+1)
        elif quality == 0:
            groups["unknown"].append(2*index)
            groups["unknown"].append(2*index+1)
            
    return groups["good"], groups["bad"], groups ["unknown"]
       
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
    
    current_total=0
    current_error=0
    last_total=0;
    last_error=0;
    with open(input_txt, 'r') as infile, open(output_csv, 'w', newline='') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(['index', 'afh_group' , 'index_range', 'time', 'channel', 'freq', 'rssi', 'is_auio', 'rx_ok', 'sync_err', 'hec_err', 'guard_err', 'crc_err', 'others'])  # CSV头部
        
        for line_number, line in enumerate(infile, start = 1):
            if "D/HEX afh_ch_map:" in line:
                print("AFH map: ", end="")
                afh_info=parse_afh_log_line(line)
                afh_map=afh_info[4:14]
                afh_suggest=afh_info[14:24]
                used_channels=parse_afh_map(afh_map)
                print_afh_channels(used_channels)
                print("Remote：", end="")
                good, bad, unknown=parse_channel_quality(afh_suggest)
                print("Good channels (indexes):", good)
                print("Bad channels (indexes):", bad)
                print("Unknown channels (indexes):", unknown)
                continue
                
            if "afh_sco_data_stats" in line:
                global afh_error_rate, afh_cnt_delta
                words = re.split(r'[,\s]+', line)
                current_total = int(words[3])
                current_error = int(words[4])
                if ((current_total-last_total)==0):
                    print("afh_sco_data_stats: line",line_number, current_total, last_total )
                else:
                    afh_error_rate=float(current_error-last_error)/float(current_total-last_total)
                print("afh_error_rate: ", afh_error_rate*100, current_total-last_total)
                afh_cnt_delta=current_total-last_total
                last_total=current_total
                last_error=current_error                
            # 检测块开始：行中包含"D/HEX sco rssi:"
            if "D/HEX" in line:
                # 结束前一个块（如果未完成）
                if "D/HEX rx total:" in line:
                    tag=1
                    afh_group_count=0;
                    afh_group = afh_group + 1
                elif "D/HEX ch_hist:" in line:
                    print("Read channel history at line", line_number)
                    tag=2
                elif "D/HEX si_ch_ass:" in line:
                    tag=3
                else:
                    tag=3
                #print("Processing block ", line_number, tag)
                
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
                    process_block(collected_bytes, total_groups, writer, timestr_in_line, tag)
                    active_block = False
                    continue
                
                # 提取地址模式后的所有字节
                byte_str = line[addr_match.end():]
                bytes_in_line = byte_pattern.findall(byte_str)
                collected_bytes.extend(bytes_in_line)
                
        # 处理文件末尾的数据块
        if active_block and len(collected_bytes) >= 2:
            process_block(collected_bytes, total_groups, writer, timestr_in_line, tag)

def parse_file2(input_txt, output_csv):
    # 匹配地址模式：xxxx-yyyy:
    addr_pattern = re.compile(r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}:', re.IGNORECASE)
    # 匹配十六进制字节
    byte_pattern = re.compile(r'[0-9a-fA-F]{2}', re.IGNORECASE)
    
    time_pattern = re.compile(r'[0-9]{2}\:[0-9]{2}:[0-9]{2}\:[0-9]{3}', re.IGNORECASE)
    
    with open(input_txt, 'r') as infile:
        for line_number, line in enumerate(infile, start = 1):
            words=line.split(re.split(r'[,\s]+', line))
            
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
            "rx_audio_ok": 0,
            "rx_error": 0,
            "score": 0,
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
                if (stats["rssi"]==0):
                    stats["rssi"] = item.rssi
                else:
                    stats["rssi"] = update_average_dbm(stats["rssi"], stats["valid_rssi_cnt"], item.rssi, 1)
                stats["valid_rssi_cnt"] += 1
            else:
                stats["inv_rssi_cnt"] += 1
            
            # 更新接收状态统计
            if (item.rx_ok==1):
                stats["score"] += 1
            elif (item.sync_err==0 and item.rssi >= -95):
                stats["score"] -= 1
            stats["rx_ok"] += item.rx_ok
            if (item.rx_ok>0 and item.is_audio>0):
                stats["rx_audio_ok"] += item.rx_ok                
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
        if (channel<0):
            total_mw=0
            total_cnt=0
            for i in self._array:
                total_mw += (10 ** (i["rssi"] / 10)) * i["valid_rssi_cnt"]
                total_cnt += i["valid_rssi_cnt"]
            if (total_cnt>0):
                combined_avg_mw = total_mw / total_cnt
                combined_avg_dbm = 10 * math.log10(combined_avg_mw)
                return combined_avg_dbm
            else:
                return -70
        else:
            stats = self.get(channel)
            return stats["rssi"]

    def get_rx_ok_rate(self, channel: int) -> float:
        """计算指定信道的接收成功率"""
        stats = self.get_channel_stats(channel)
        if stats["ttl"]==0:
            return DEFAULT_RX_OK_RATE
        elif stats["total"] > 0:
            return stats["rx_ok"] / stats["total"]
        else:
            return DEFAULT_RX_OK_RATE
        return 0

    def get_rx_audio_ok_rate(self, channel: int) -> float:
        """计算指定信道的接收成功率"""
        stats = self.get_channel_stats(channel)
        if stats["ttl"]==0:
            return DEFAULT_RX_OK_RATE
        elif stats["total"] > 0:
            return stats["rx_audio_ok"] / stats["total"]
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
        valid_fields = {'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error', 'rx_ok_rate', 'rx_audio_ok_rate'}
        if field not in valid_fields:
            raise ValueError(f"Invalid sort field: {field}. Valid fields are {valid_fields}")
        
        # 获取所有有数据的信道
        channels = self.get_all_channels()
        
        # 根据不同字段进行排序
        if field == 'rx_ok_rate':    
            return sorted(channels, 
                         key=lambda x: self.get_rx_ok_rate(x["channel"]), 
                         reverse=reverse)            
        elif field == 'rx_audio_ok_rate':    
            return sorted(channels, 
                         key=lambda x: self.get_rx_audio_ok_rate(x["channel"]), 
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

    def _merge_channel_stats(self, channel: int, history_stats: dict, overwrite: bool = True) -> None:
        """合并单个信道的统计数据"""
        current = self._array[channel]
        if (overwrite==True):
            current["rssi"] = history_stats['rssi']
            current["valid_rssi_cnt"] = history_stats["valid_rssi_cnt"]
            current["inv_rssi_cnt"] = history_stats["inv_rssi_cnt"]
            current["rx_ok"] = history_stats["rx_ok"]
            current["rx_audio_ok"] = history_stats["rx_audio_ok"]
            current["rx_error"] = history_stats["rx_error"]
            current["score"] = history_stats["score"]
            current["total"] = history_stats["total"]
        else:
            current["rssi"] = update_average_dbm(current["rssi"],current["valid_rssi_cnt"],history_stats['rssi'],history_stats["valid_rssi_cnt"])
            current["valid_rssi_cnt"] += history_stats["valid_rssi_cnt"]
            current["inv_rssi_cnt"] += history_stats["inv_rssi_cnt"]
            current["rx_ok"] += history_stats["rx_ok"]
            current["rx_audio_ok"] += history_stats["rx_audio_ok"]
            current["rx_error"] += history_stats["rx_error"]
            current["score"] = (history_stats["score"]+current["score"])/2
            current["total"] += history_stats["total"]
        current["ttl"] = history_stats["ttl"]

    def print_stats(self, format: str = 'table', detailed: bool = False, sort_by: str = 'rx_audio_ok_rate') -> None:
        """
        打印信道统计信息
        
        Args:
            format: 输出格式，支持 'table' (表格), 'csv' (逗号分隔值), 'json' (JSON格式)
            detailed: 是否显示详细信息，默认为 False
            sort_by: 排序字段，支持 'channel', 'rssi', 'valid_rssi_cnt', 'inv_rssi_cnt', 'rx_ok', 'rx_error', 'average_rssi', 'rx_ok_rate', 'rx_audio_ok_rate'
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
        headers = ["Channel", "Avg RSSI (dBm)", "Rx OK", "Rx Error", "Score",  "Actual Score", "Invalid RSSI","Total" ]
        if detailed:
            headers.extend(["Success Rate", "Audio Success rate"])
        
        table = []
        for stats in channels:
            avg_rssi = self.get_average_rssi(stats["channel"])
            success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
            audio_success_rate = stats["rx_audio_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0            
            try:
                row = [
                    stats["channel"],
                    f"{avg_rssi:.2f}",
                    stats["rx_ok"],
                    stats["rx_error"],
                    stats["score"],
                    channel_score_hist[stats["channel"]].score,
                    stats["inv_rssi_cnt"],
                    stats["total"]
                ]
            except:
                row = [
                    stats["channel"],
                    f"{avg_rssi:.2f}",
                    stats["rx_ok"],
                    stats["inv_rssi_cnt"],
                    stats["rx_error"],
                    stats["score"],
                    -1000,
                    stats["inv_rssi_cnt"],                    
                    stats["total"]
                ]
                
            if detailed:
                row.extend([f"{success_rate:.2f}%", f"{audio_success_rate:.2f}%"])
            
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
                
    def print_all_with_selected(self, selected_channels: list, title: str="Removed", format: str = 'table', detailed: bool = False, sort_by: str = 'rx_audio_ok_rate') -> None:
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
            self._print_table_with_mark(all_channels, title, valid_selected, detailed)
        elif format == 'csv':
            self._print_csv_with_mark(all_channels, title, valid_selected, detailed)
        elif format == 'json':
            self._print_json_with_mark(all_channels, title, valid_selected, detailed)
        else:
            raise ValueError(f"Unsupported format: {format}. Valid formats are 'table', 'csv', 'json'.")

    def _print_table_with_mark(self, channels: list[dict], title:str, selected: list, detailed: bool) -> None:
        """带选中标记的表格打印（内部方法）"""
        # 表头添加标记列
        headers = [title, "Channel", "Avg RSSI (dBm)", "Invalid RSSI", "Rx OK", "Rx Error", "Score", "Total", "ttl"]
        if detailed:
            headers.extend(["Success Rate", "Audio Success Rate"])
        
        table = []
        for stats in channels:
            # 判断是否为选中信道（添加标记）
            mark = "*" if stats["channel"] in selected else " "
            
            avg_rssi = self.get_average_rssi(stats["channel"])
            success_rate = stats["rx_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
            audio_success_rate = stats["rx_audio_ok"] / stats["total"] * 100 if stats["total"] > 0 else 0
            if (stats["ttl"]==0):
                audio_success_rate = DEFAULT_RX_OK_RATE*100
                success_rate = DEFAULT_RX_OK_RATE*100
            row = [
                mark,  # 选中标记列
                stats["channel"],
                f"{avg_rssi:.2f}",
                stats["inv_rssi_cnt"],
                stats["rx_ok"],
                stats["rx_error"],
                stats["score"],
                stats["total"],
                stats["ttl"]
            ]
            
            if detailed:
                row.extend([f"{success_rate:.2f}%", f"{audio_success_rate:.2f}%"])
            
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

    def _print_csv_with_mark(self, channels: list[dict], title:str, selected: list, detailed: bool) -> None:
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

    def _print_json_with_mark(self, channels: list[dict], title:str, selected: list, detailed: bool) -> None:
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
        
def process_rx_total(data_bytes, writer, timestr_in_line):
    channels=[]
    global group_counter, afh_group, afh_group_count
    global last_array, hist_array, last_removed
    
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
    kept_array = sorted(kept_array)
    print("Evaluate Previous block as Below--------------------")
    last_array.print_all_with_selected(removed_array, "Removed", detailed=True)

    print("Evaluate Current block as Below--------------------")
    stats_array.print_stats(detailed=True)

    print("Removed ", end="")
    print(removed_array)    
    print("Added with history below: ", end="")
    print(added_array)
    hist_array.print_all_with_selected(added_array, "Added", detailed=True)
    print("=======================================================================================")    
    
    global error_rate_stat
    if (afh_cnt_delta<2000):
        error_rate_stat += [error_rate_cls(stats_array.get_average_rssi(-1),afh_error_rate, afh_cnt_delta)]
    
    hist_array.update_from_history(stats_array)
    last_array=stats_array    
    last_removed=removed_array

@dataclass
class channel_hist:
    channel: int
    score: int
    ttl: int
    
def hex_to_signed_int(hex_str):
    unsigned = int(hex_str, 16)
    bits = len(hex_str) * 4  # 4 bits per hex digit
    if unsigned >= (1 << (bits - 1)):  # Check sign bit
        return unsigned - (1 << bits)
    return unsigned
    
def process_ch_hist(data_bytes):
    global channel_score_hist
    channel_score_hist=[]
    chan=0
    for i in range(0, len(data_bytes), 8):
        if i + 8 > len(data_bytes):
            break
        chan=chan+1
        channel_score_hist +=  [channel_hist(chan, hex_to_signed_int(data_bytes[i+4]), hex_to_signed_int(data_bytes[i+5]))]    

def process_block(bytes_list, total_groups, writer, timestr_in_line, tag=1):
    """处理一个完整数据块并写入CSV"""
    
    # 计算预期总字节数 = 2(组数字节) + total_groups * 4
    if (tag==1 or tag==3):
        expected_bytes = 2 + total_groups * 4
        # 跳过前2个组数字节，从第3个字节开始
        data_bytes = bytes_list[2:expected_bytes]
    elif (tag==2):
        expected_bytes = 79 * 8
        data_bytes = bytes_list
        
    if len(bytes_list) < expected_bytes:
        print("Not enought data,", len(bytes_list), "<", expected_bytes)
        expected_bytes=len(bytes_list)
        #return  # 数据不完整    
    
    # 验证数据长度是4的倍数
    if len(data_bytes) % 4 != 0:
        print("Length error")
        return
    
    if (tag==1):
        process_rx_total(data_bytes,writer, timestr_in_line)
    elif (tag==2):
        process_ch_hist(data_bytes)

            
last_array = ChannelStatsArray(max_channel=79)
hist_array = ChannelStatsArray(max_channel=79)
last_removed = []
error_rate_stat = []

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
    error_rate_sorted = sorted(error_rate_stat, key=lambda p: p.rssi)
    # 转换为表格数据
    table_data = [
        [f"{item.rssi:.2f}", f"{item.error_rate:.2%}", f"{item.cnt}"]
            for item in error_rate_sorted
    ]

    # 使用 tabulate 打印表格
    print(tabulate(
        table_data,
        headers=["RSSI (dBm)", "Error Rate", "cnt"],
        tablefmt="pretty",  # 可选: "plain", "simple", "grid", "fancy_grid", "pipe" 等
        floatfmt=".2f"
    ))
    
    total_error_rate=0
    total_cnt=0
    total_mw=0
    for i in error_rate_sorted:
        if (i.rssi<-65 and i.rssi>-85):
            total_error_rate+=(i.error_rate*i.cnt)
            total_mw += (10 ** (i.rssi / 10)) * i.cnt
            total_cnt+=i.cnt
    print("Average error rate %.4f%%" %(total_error_rate/total_cnt*100.0))
    combined_avg_mw = total_mw / total_cnt
    combined_avg_dbm = 10 * math.log10(combined_avg_mw)
    print("Average RSSI %.4fdbm" %(combined_avg_dbm))
    
