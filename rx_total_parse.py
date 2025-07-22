import csv
import re
import sys
import math


def parse_file(input_txt, output_csv):
    # 匹配地址模式：xxxx-yyyy:
    addr_pattern = re.compile(r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}:', re.IGNORECASE)
    # 匹配十六进制字节
    byte_pattern = re.compile(r'[0-9a-fA-F]{2}', re.IGNORECASE)
    
    time_pattern = re.compile(r'[0-9]{2}\:[0-9]{2}:[0-9]{2}\:[0-9]{3}', re.IGNORECASE)
    
    global group_counter
    # 状态管理
    active_block = False    # 是否在数据块中
    total_groups = 0        # 预期的总组数
    collected_bytes = []    # 收集到的所有字节
    group_counter = 1       # 当前分组计数

    with open(input_txt, 'r') as infile, open(output_csv, 'w', newline='') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(['index', 'index_range', 'time', 'channel', 'freq', 'rssi', 'is_auio', 'rx_ok', 'sync_err', 'hec_err', 'guard_err', 'crc_err', 'others'])  # CSV头部
        
        for line in infile:
            # 检测块开始：行中包含"D/HEX sco rssi:"
            if "D/HEX rx total:" in line:
                # 结束前一个块（如果未完成）
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

def process_block(bytes_list, total_groups, writer, timestr_in_line):
    """处理一个完整数据块并写入CSV"""
    # 验证数据有效性
    global group_counter
    if total_groups == 0 or len(bytes_list) < 2:
        return
    
    # 计算预期总字节数 = 2(组数字节) + total_groups * 4
    expected_bytes = 2 + total_groups * 4

    if len(bytes_list) < expected_bytes:
        return  # 数据不完整
    

    # 跳过前2个组数字节，从第3个字节开始
    data_bytes = bytes_list[2:expected_bytes]
    
    # 验证数据长度是4的倍数
    if len(data_bytes) % 4 != 0:
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
        
        writer.writerow([
            group_counter, 
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
        group_counter += 1
                


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