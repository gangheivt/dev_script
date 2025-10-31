import time
import random
import threading
from datetime import datetime, timedelta

class Node:
    """通信节点基类"""
    def __init__(self, node_id, hop_interval=22.5, time_ratio=1/3, peer=None):
        self.node_id = node_id
        self.base_hop_interval = hop_interval  # 基础跳频间隔(ms，实际时间)
        self.time_ratio = time_ratio  # 时间加速比例（仿真时间/实际时间）
        self.hop_interval = hop_interval * time_ratio  # 仿真跳频间隔(ms)
        self.tables_consistent = True
        self.current_table_id = 0
        self.running = False
        self.lock = threading.Lock()
        self.last_connection_time = datetime.now()
        self.last_successful_communication = datetime.now()
        self.connected = True
        self.disconnect_events = []  # (建联时间, 断联时间, 持续时长)
        self.peer = peer
        
    def set_peer(self, peer):
        self.peer = peer
    
    def record_successful_communication(self):
        with self.lock:
            now = datetime.now()
            self.last_successful_communication = now
            
            if not self.connected:
                self.connected = True
                self.last_connection_time = now
                print(f"{self.node_id} 恢复连接")
                
                if self.peer and not self.peer.connected:
                    self.peer.force_reconnect(now)
    
    def force_disconnect(self, disconnect_time=None):
        with self.lock:
            if self.connected:
                if disconnect_time is None:
                    disconnect_time = datetime.now()
                duration = (disconnect_time - self.last_connection_time).total_seconds()
                self.disconnect_events.append((self.last_connection_time, disconnect_time, duration))
                self.connected = False
                print(f"{self.node_id} 因对端断线同步断开，仿真连接持续 {duration:.3f}秒 (实际等效 {duration/self.time_ratio:.2f}秒)")
    
    def force_reconnect(self, connect_time=None):
        with self.lock:
            if not self.connected:
                if connect_time is None:
                    connect_time = datetime.now()
                self.connected = True
                self.last_connection_time = connect_time
                self.last_successful_communication = connect_time
                print(f"{self.node_id} 因对端重连同步恢复连接")
    
    def check_disconnection(self):
        """断线检测阈值：实际4秒，仿真时间=4*time_ratio秒"""
        if not self.running:
            return
            
        with self.lock:
            now = datetime.now()
            time_since_last = (now - self.last_successful_communication).total_seconds()
            disconnect_threshold = 4 * self.time_ratio  # 仿真断线阈值
            
            if time_since_last >= disconnect_threshold and self.connected:
                duration = (now - self.last_connection_time).total_seconds()
                self.disconnect_events.append((self.last_connection_time, now, duration))
                self.connected = False
                print(f"{self.node_id} 检测到断线，仿真连接持续 {duration:.3f}秒 (实际等效 {duration/self.time_ratio:.2f}秒)，无通信 {time_since_last:.2f}秒")
                
                if self.peer and self.peer.connected:
                    self.peer.force_disconnect(now)
    
    def get_average_disconnect_duration(self):
        with self.lock:
            total = 0.0
            count = len(self.disconnect_events)
            
            for _, _, duration in self.disconnect_events:
                total += duration
            
            if self.connected:
                current_duration = (datetime.now() - self.last_connection_time).total_seconds()
                total += current_duration
                count += 1
                
            avg_sim = total / count if count > 0 else 0.0
            avg_real = avg_sim / self.time_ratio  # 转换为实际时间
            return (avg_sim, avg_real)

class Master(Node):
    """主节点"""
    def __init__(self, hop_interval=22.5, 
                 initial_loss_rate=0.5, 
                 max_loss_rate=0.7, 
                 inconsistent_loss_rate=0.95,
                 time_ratio=1/3,
                 peer=None):
        super().__init__("Master", hop_interval, time_ratio, peer)
        self.pending_table_change = False  # 是否有等待发送的跳频表
        self.pending_table_id = 0
        self.change_activate_time = None
        self.ack_received = False
        self.base_loss_rate = initial_loss_rate  # 基础丢包率（表一致时）
        self.max_loss_rate = max_loss_rate
        self.inconsistent_loss_rate = inconsistent_loss_rate  # 表不一致时丢包率
        self.table_notify_retry_count = 0  # 跳频表重试计数器
        self.data_retry_count = 0  # 数据重试计数器
        self.pending_data = None  # 等待重发的数据
        self.is_sending_data = False  # 标记是否正在发送数据
        self.transmission_lock = threading.Lock()  # 传输互斥锁
        self.table_send_event = threading.Event()  # 跳频表发送事件
        self.base_activate_delay = 200  # 基础生效延迟(ms，实际时间)
        self.base_table_interval = 1  # 跳频表发送间隔(秒，实际时间)
    
    # 新增receive_ack方法，处理从节点发送的ACK
    def receive_ack(self, table_id):
        """接收从节点发送的ACK确认"""
        if table_id == self.pending_table_id and self.connected:
            self.ack_received = True
            self.record_successful_communication()
            print(f"{self.node_id} 收到跳频表 #{table_id} 的 ACK 确认")
        
    def start(self):
        self.running = True
        self.current_table_id = 1
        self.last_connection_time = datetime.now()
        print(f"{self.node_id} 启动，初始跳频表 #{self.current_table_id}，初始建联时间: {self.last_connection_time.strftime('%H:%M:%S.%f')[:-3]}")
        # 单一传输线程：处理所有发送和重发操作
        threading.Thread(target=self.transmission_thread, daemon=True).start()
        # 跳频表定时发送线程
        threading.Thread(target=self.schedule_table_thread, daemon=True).start()
        threading.Thread(target=self.check_activate_change_thread, daemon=True).start()
        threading.Thread(target=self.disconnection_monitor_thread, daemon=True).start()
        
    def schedule_table_change(self):
        """生成新的跳频表并等待发送"""
        if not self.connected or self.pending_table_change:
            return
            
        self.pending_table_id = self.current_table_id + 1
        activate_delay = self.base_activate_delay * self.time_ratio
        self.change_activate_time = datetime.now() + timedelta(milliseconds=activate_delay)
        self.ack_received = False
        self.table_notify_retry_count = 0
        self.pending_table_change = True
        
        # 50%概率模拟正在发送数据的场景
        is_sending = random.random() < 0.5
        print(f"\n{self.node_id} 准备发送跳频表 #{self.pending_table_id} (50%概率正在发送数据: {'是' if is_sending else '否'})")
        print(f"跳频表 #{self.pending_table_id} 将于 {self.change_activate_time.strftime('%H:%M:%S.%f')[:-3]} 生效 (仿真延迟 {activate_delay:.1f}ms)")
        
        self.table_send_event.set()
    
    def send_table_change(self):
        """发送跳频表变更通知，使用当前丢包率，无限重试直到成功"""
        if not self.pending_table_change or not self.connected:
            return False
            
        # 确定当前丢包率：与数据发送使用相同逻辑
        loss_rate = self.base_loss_rate if self.tables_consistent else self.inconsistent_loss_rate
        
        self.table_notify_retry_count += 1
        success = random.random() > loss_rate
        
        if success and self.peer:
            self.peer.receive_table_change(
                self.change_activate_time, 
                self.inconsistent_loss_rate,
                self.pending_table_id
            )
            self.record_successful_communication()
            print(f"{self.node_id} 跳频表 #{self.pending_table_id} 发送成功 (第{self.table_notify_retry_count}次尝试，丢包率{loss_rate:.0%})")
            return True
        else:
            print(f"{self.node_id} 跳频表 #{self.pending_table_id} 发送失败 (第{self.table_notify_retry_count}次尝试，丢包率{loss_rate:.0%})，将继续重试")
            return False
    
    def send_user_data(self, data=None):
        """发送用户数据包，使用当前丢包率，无限重试直到成功"""
        if not self.peer or not self.connected:
            return False
            
        # 确定当前丢包率
        loss_rate = self.base_loss_rate if self.tables_consistent else self.inconsistent_loss_rate
        
        # 如果没有指定数据，则生成新数据
        if data is None:
            data = f"数据_{datetime.now().strftime('%f')[:3]}"
        
        success = random.random() > loss_rate
        
        if success:
            self.peer.receive_data(data)
            self.record_successful_communication()
            print(f"{self.node_id} 数据 '{data}' 发送成功 (第{self.data_retry_count+1}次尝试，丢包率{loss_rate:.0%})")
            self.pending_data = None  # 清空待重发数据
            self.data_retry_count = 0
            return True
        else:
            print(f"{self.node_id} 数据 '{data}' 发送失败 (第{self.data_retry_count+1}次尝试，丢包率{loss_rate:.0%})，将继续重试")
            self.pending_data = data  # 保存待重发数据
            self.data_retry_count += 1
            return False
    
    def transmission_thread(self):
        """单一传输线程：处理所有发送和重发操作，确保串行执行"""
        while self.running:
            if self.connected:
                # 优先处理待重发的数据
                if self.pending_data is not None:
                    with self.transmission_lock:
                        self.is_sending_data = True
                        # 持续重试直到成功
                        while self.running and self.connected and self.pending_data:
                            success = self.send_user_data(self.pending_data)
                            if success:
                                break
                            # 等待一个跳频间隔后重试
                            time.sleep(self.hop_interval / 1000)
                        self.is_sending_data = False
                        
                # 其次处理待发送的跳频表
                elif self.pending_table_change and self.table_send_event.is_set():
                    with self.transmission_lock:
                        # 检查是否需要等待数据发送完成
                        if self.is_sending_data:
                            print(f"{self.node_id} 正在发送数据，跳频表 #{self.pending_table_id} 等待中...")
                            # 等待当前数据发送完成
                            time.sleep(self.hop_interval / 1000)
                            
                        # 持续重试直到成功
                        self.is_sending_data = False
                        while self.running and self.connected and self.pending_table_change:
                            success = self.send_table_change()
                            if success:
                                self.table_send_event.clear()
                                break
                            # 等待一个跳频间隔后重试
                            time.sleep(self.hop_interval / 1000)
                
                # 没有重发任务时，发送新数据
                else:
                    with self.transmission_lock:
                        self.is_sending_data = True
                        self.send_user_data()
                        self.is_sending_data = False
            
            # 按照跳频间隔等待
            time.sleep(self.hop_interval / 1000)
    
    def schedule_table_thread(self):
        """定时发送跳频表（实际每1秒一次）"""
        while self.running:
            interval = self.base_table_interval * self.time_ratio
            time.sleep(interval)
            if self.connected and (not self.pending_table_change or self.ack_received):
                self.schedule_table_change()
                # 调整基础丢包率
                self.base_loss_rate = min(self.max_loss_rate, self.base_loss_rate + 0.02)
                print(f"{self.node_id} 基础丢包率调整为: {self.base_loss_rate:.2f}")
    
    def check_activate_change_thread(self):
        while self.running:
            if self.pending_table_change and datetime.now() >= self.change_activate_time:
                if self.ack_received:
                    self.current_table_id = self.pending_table_id
                    self.tables_consistent = True
                    print(f"{self.node_id} 已启用跳频表 #{self.current_table_id}，表状态一致")
                else:
                    print(f"{self.node_id} 跳频表 #{self.pending_table_id} 未同步，保持原表 #{self.current_table_id}")
                
                self.pending_table_change = False
                self.change_activate_time = None
            time.sleep(0.0001)
    
    def disconnection_monitor_thread(self):
        monitor_interval = 0.1 * self.time_ratio
        while self.running:
            self.check_disconnection()
            time.sleep(monitor_interval)

class Slave(Node):
    """从节点"""
    def __init__(self, hop_interval=22.5, time_ratio=1/3, peer=None):
        super().__init__("Slave", hop_interval, time_ratio, peer)
        self.pending_table_change = False
        self.pending_table_id = 0
        self.change_activate_time = None
        self.inconsistent_loss_rate = 0.95
        
    def start(self):
        self.running = True
        self.current_table_id = 1
        self.last_connection_time = datetime.now()
        print(f"{self.node_id} 启动，初始跳频表 #{self.current_table_id}，初始建联时间: {self.last_connection_time.strftime('%H:%M:%S.%f')[:-3]}")
        threading.Thread(target=self.check_activate_change_thread, daemon=True).start()
        threading.Thread(target=self.disconnection_monitor_thread, daemon=True).start()
    
    def receive_table_change(self, activate_time, inconsistent_loss_rate, table_id):
        if not self.connected:
            return
            
        self.pending_table_change = True
        self.pending_table_id = table_id
        self.change_activate_time = activate_time
        self.inconsistent_loss_rate = inconsistent_loss_rate
        print(f"{self.node_id} 收到跳频表 #{table_id} 变更通知，将于 {self.change_activate_time.strftime('%H:%M:%S.%f')[:-3]} 生效")
        self.send_ack(table_id)
        self.record_successful_communication()
    
    def send_ack(self, table_id):
        """发送ACK确认，使用与数据/跳频表相同的丢包率，无限重试直到成功"""
        if self.peer and self.connected:
            retry_count = 0
            while self.running and self.connected:
                # ACK使用与当前通信相同的丢包率
                tables_consistent = (self.current_table_id == self.peer.current_table_id)
                loss_rate = self.peer.base_loss_rate if tables_consistent else self.inconsistent_loss_rate
                
                retry_count += 1
                success = random.random() > loss_rate
                if success:
                    print(f"{self.node_id} 发送跳频表 #{table_id} 的 ACK (第{retry_count}次尝试，丢包率{loss_rate:.0%})")
                    self.peer.receive_ack(table_id)
                    return
                else:
                    print(f"{self.node_id} ACK发送失败 (第{retry_count}次尝试，丢包率{loss_rate:.0%})，将继续重试")
                    time.sleep(self.hop_interval / 1000)  # 等待重发
    
    def receive_data(self, data):
        if not self.connected:
            return
            
        tables_consistent = (self.current_table_id == self.peer.current_table_id)
        loss_rate = self.peer.base_loss_rate if tables_consistent else self.inconsistent_loss_rate
        
        if random.random() > loss_rate:
            self.record_successful_communication()
            if int(data.split('_')[1]) % 200 < 20:
                print(f"{self.node_id} 接收成功: {data} (表{'' if tables_consistent else '不'}一致，当前表 #{self.current_table_id})")
        else:
            if not tables_consistent and random.random() < 0.1:
                print(f"{self.node_id} 数据包丢失 (表不一致，主表 #{self.peer.current_table_id}, 从表 #{self.current_table_id})")
    
    def check_activate_change_thread(self):
        while self.running:
            if self.pending_table_change and datetime.now() >= self.change_activate_time:
                self.current_table_id = self.pending_table_id
                self.tables_consistent = True
                print(f"{self.node_id} 已同步跳频表 #{self.current_table_id}，表状态一致")
                self.pending_table_change = False
                self.change_activate_time = None
            time.sleep(0.0001)
    
    def disconnection_monitor_thread(self):
        monitor_interval = 0.1 * self.time_ratio
        while self.running:
            self.check_disconnection()
            time.sleep(monitor_interval)

if __name__ == "__main__":
    # 核心参数配置
    TIME_RATIO = 1/3  # 时间加速比例
    INITIAL_LOSS_RATE = 0.7
    MAX_LOSS_RATE = 0.9
    INCONSISTENT_LOSS_RATE = 0.99
    BASE_RUN_DURATION = 120  # 实际运行时间（秒）
    sim_run_duration = BASE_RUN_DURATION * TIME_RATIO
    
    # 创建节点并建立双向引用
    master = Master(
        initial_loss_rate=INITIAL_LOSS_RATE,
        max_loss_rate=MAX_LOSS_RATE,
        inconsistent_loss_rate=INCONSISTENT_LOSS_RATE,
        time_ratio=TIME_RATIO
    )
    slave = Slave(time_ratio=TIME_RATIO)
    master.set_peer(slave)
    slave.set_peer(master)
    
    print(f"开始通信仿真（时间加速比例: {TIME_RATIO}）...")
    print("核心特性：")
    print("- 所有传输（数据、跳频表、ACK）使用相同的丢包率逻辑")
    print("- 表一致时使用基础丢包率，表不一致时使用高丢包率")
    print("- 所有类型的传输失败后都会无限重试，直到成功")
    print("- 跳频表固定每1秒发送一次（实际时间），发送前可能需要等待数据传输完成")
    print("----------------------------------------")
    
    # 启动节点
    master.start()
    slave.start()
    
    # 运行仿真
    try:
        time.sleep(sim_run_duration)
    except KeyboardInterrupt:
        pass
    finally:
        master.running = False
        slave.running = False
        print("\n----------------------------------------")
        print("仿真结束统计:")
        print(f"仿真运行时间: {sim_run_duration:.1f}秒（实际等效 {BASE_RUN_DURATION}秒）")
        print(f"最终跳频表编号 - Master: {master.current_table_id}, Slave: {slave.current_table_id}")
        
        master_sim_avg, master_real_avg = master.get_average_disconnect_duration()
        slave_sim_avg, slave_real_avg = slave.get_average_disconnect_duration()
        
        print(f"Master 断线次数: {len(master.disconnect_events)}, 平均连接时长: {master_sim_avg:.3f}秒（实际等效 {master_real_avg:.2f}秒）")
        print(f"Slave 断线次数: {len(slave.disconnect_events)}, 平均连接时长: {slave_sim_avg:.3f}秒（实际等效 {slave_real_avg:.2f}秒）")