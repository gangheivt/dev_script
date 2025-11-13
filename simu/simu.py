import time
import random
import argparse
from datetime import datetime

class MasterSlaveSimulator:
	def __init__(self, initial_error_rate, max_error_rate, merge_success_rate, algorithm=1, speedup=5):
		# 基础时间参数
		base_connection_interval = 0.0225  # 通信事件间隔（原始时间）
		base_channel_update_interval = 1.5
		self.base_timeout_duration = 4.0  # 基础超时时间（4秒，未加速）
		
		# 加速后的参数
		self.speedup = speedup
		self.connection_interval = base_connection_interval / speedup  # 通信事件间隔（加速后）
		self.channel_update_interval = base_channel_update_interval / speedup
		self.channel_activation_delay = (20 * base_connection_interval) / speedup  # 更新包发出到激活的延迟
		self.timeout_duration = self.base_timeout_duration / speedup  # 加速后的超时时间
		
		# 算法选择 (1: 定时激活, 2: ACK确认后激活)
		self.algorithm = algorithm
		print(f"使用算法 {algorithm}: {'定时激活' if algorithm == 1 else 'ACK确认后激活'}")
		print(f"核心特性: 两种算法均为每个channel map update生成唯一且固定的激活时间（生成后永不改变）")
		print(f"算法1: 到达激活时间强制更新；算法2: 激活时间前收到ACK2则激活，超时则失效")
		print(f"误包率规则: 激活时间点未更新且主从信道一致时，误包率保持在max_error rate")
		print(f"空包特性: EMPTY_PACKET不会重传，即使丢失也不重传")
		print(f"回退特性: 回退channel map且主从信道一致时，误包率保持在max_error rate")
		
		# 可配置参数
		self.initial_error_rate = initial_error_rate
		self.max_error_rate = max_error_rate
		self.merge_success_rate = merge_success_rate
		
		# 信道状态变量
		self.master_channel = 0
		self.slave_channel = 0
		self.last_master_channel = 0
		self.last_channel_update_time = time.time()
		self.last_channel_activation_time = None
		self.is_backed_off = False  # 标记是否处于回退状态
		self.activation_time_missed = False  # 新增：标记是否错过激活时间点且未更新
		
		# 所有算法通用：存储每个更新包的专属激活时间（生成后永不改变）
		self.scheduled_updates = []  # 格式: (激活时间, 目标信道, 更新ID, 是否回退)
		self.processed_updates = []  # 存储已处理的更新包，用于查询历史记录
		self.slave_scheduled_channel = None  # Slave收到更新后计划切换的信道
		
		# 发送队列与重传控制
		self.master_send_queue = []
		self.slave_send_queue = []
		self.master_pending_packet = None  # 已发送但未收到ACK1的数据包（非空包）
		self.pending_packet_id = None	  # 待确认的数据包编号（用于ACK1编号匹配）
		self.retransmit_needed = False	 # 标记是否需要在下一个通信事件重传（仅用于非空包）
		self.retransmit_count = 0		  # 重传次数计数器（仅用于非空包）
		self.connection_event_counter = 0  # 通信事件计数器
		
		# 数据包编号
		self.master_packet_id = 0
		self.slave_packet_id = 0
		self.ack1_counter = 0			  # ACK1基础计数器
		self.ack2_packet_id = 0
		self.channel_update_id = 0		 # 信道更新包编号
		
		# 事件ID
		self.event_id = 0
		
		self.current_error_rate = self.initial_error_rate
		self.running = True
		
		# 断线检测
		self.master_last_receive_time = time.time()  # Master最后收到任何数据/ACK1的时间
		self.slave_last_receive_time = time.time()   # Slave最后收到任何数据的时间
		self.disconnected = False
		self.disconnect_time = None

	def random_packet_loss(self):
		return random.random() < self.current_error_rate

	def update_error_rate(self):
		current_time = time.time()
		
		# 优先判断：激活时间点未更新且主从信道一致，保持最大误包率
		if self.activation_time_missed and self.master_channel == self.slave_channel:
			self.current_error_rate = self.max_error_rate
			return
			
		# 其次判断：回退状态且主从信道一致，保持最大误包率
		if self.is_backed_off and self.master_channel == self.slave_channel:
			self.current_error_rate = self.max_error_rate
			return
				
		# 正常状态下的误包率计算
		if self.master_channel == self.slave_channel:
			if not self.last_channel_activation_time:
				self.current_error_rate = self.initial_error_rate
				self.last_channel_activation_time=current_time
			if (self.current_error_rate<self.max_error_rate) :
				time_since_activation = current_time - self.last_channel_activation_time
				time_to_next_update = self.channel_update_interval - time_since_activation
				ratio = 1 - (time_to_next_update / self.channel_update_interval) if time_to_next_update > 0 else 1
				
				self.current_error_rate = self.initial_error_rate + \
										(self.max_error_rate - self.initial_error_rate) * ratio
				self.current_error_rate = max(0.0, min(self.current_error_rate, 1.0))
		else:
			self.current_error_rate = 1 - (1 - self.max_error_rate) * self.merge_success_rate
			self.current_error_rate = max(0.0, min(self.current_error_rate, 1.0))

	def master_generate_data(self):
		# 只有没有 pending 数据包时才生成新数据
		if self.master_pending_packet is None and random.random() < 0.3:
			data = f"Master_Data_{self.master_packet_id}"
			self.master_send_queue.append(data)
			self.master_packet_id += 1
			return True
		return False

	def slave_generate_data(self):
		if random.random() < 0.3:
			data = f"Slave_Data_{self.slave_packet_id}"
			self.slave_send_queue.append(data)
			self.slave_packet_id += 1
			return True
		return False

	def process_channel_update(self):
		if self.disconnected:
			return
			
		current_time = time.time()
		
		# 检查是否需要生成新的信道更新
		if current_time - self.last_channel_update_time >= self.channel_update_interval:
			# 清除队列中旧的信道更新包
			self.master_send_queue = [pkg for pkg in self.master_send_queue 
									if not pkg.startswith("CHANNEL_UPDATE_")]
			
			is_backed_off = False  # 标记本次更新是否为回退
			if self.current_error_rate > self.max_error_rate:
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 误包率过高 ({self.current_error_rate:.2f})，回退到信道 {self.last_master_channel}")
				new_channel = self.last_master_channel
				is_backed_off = True
				self.is_backed_off = True  # 设置回退状态
			else:
				self.last_master_channel = self.master_channel
				new_channel = (self.master_channel + 1) % 10
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 生成新信道配置 {new_channel} (当前Master信道: {self.master_channel})")
				self.is_backed_off = False  # 清除回退状态
				self.activation_time_missed = False  # 新更新生成时清除未更新标记
			
			# 两种算法均为当前更新包生成唯一且固定的激活时间
			update_id = self.channel_update_id
			activation_time = current_time + self.channel_activation_delay
			self.scheduled_updates.append((activation_time, new_channel, update_id, is_backed_off))
			
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 为更新包 #{update_id} 确定激活时间: {datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]} (此时间生成后永不改变)")
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - {'算法1: 到达时间强制激活' if self.algorithm == 1 else '算法2: 需在激活时间前收到ACK2才会激活'}")
			
			# 生成带编号的信道更新包
			channel_update_pkg = f"CHANNEL_UPDATE_{new_channel}_{update_id}"
			self.master_send_queue.insert(0, channel_update_pkg)
			self.last_channel_update_time = current_time
			self.channel_update_id += 1

	def check_channel_activation(self):
		if self.disconnected or not self.scheduled_updates:
			return
			
		current_time = time.time()
		self.activation_time_missed = False  # 默认为未错过激活时间
		
		# 算法1: 检查所有计划中的更新是否到达激活时间（到点强制激活）
		if self.algorithm == 1:
			to_activate = [update for update in self.scheduled_updates if current_time >= update[0]]
			
			if to_activate:
				# 按激活时间排序，确保先处理最早到期的更新
				to_activate.sort(key=lambda x: x[0])
				activation_time, new_channel, update_id, is_backed_off = to_activate[0]
				
				# 更新状态标记
				self.is_backed_off = is_backed_off
				old_master_channel = self.master_channel
				old_slave_channel = self.slave_channel
				
				# Master强制切换到计划信道
				self.master_channel = new_channel
				
				# Slave如果收到更新则切换，否则保持原信道
				if self.slave_scheduled_channel == new_channel:
					self.slave_channel = new_channel
					self.slave_scheduled_channel = None
				
				# 判断是否成功更新
				update_successful = (old_master_channel != self.master_channel) or (old_slave_channel != self.slave_channel)
				
				# 打印激活信息
				backoff_info = "（回退状态）" if is_backed_off else ""
				print(f"\n[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 算法1信道激活时间到达 (更新包 #{update_id}) {backoff_info}:")
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 激活时间: {datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]} (生成后未改变)")
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - Master信道变更: {old_master_channel} → {self.master_channel}")
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - Slave信道变更: {old_slave_channel} → {self.slave_channel}")
				
				# 关键逻辑：如果未成功更新且主从信道一致，设置未更新标记
				if not update_successful and self.master_channel == self.slave_channel:
					self.activation_time_missed = True
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 激活时间点未更新信道且主从信道一致，误包率将保持在 {self.max_error_rate}")
				elif is_backed_off and self.master_channel == self.slave_channel:
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 回退状态且信道一致，误包率将保持在 {self.max_error_rate}")
				print("")
				
				# 移除已激活的更新并添加到历史记录
				for update in to_activate:
					self.processed_updates.append(update)
					self.scheduled_updates.remove(update)
					
				self.last_channel_activation_time = current_time
		
		# 算法2: 检查已过期未确认的更新（超过激活时间仍未收到ACK2则失效）
		elif self.algorithm == 2:
			expired_updates = [update for update in self.scheduled_updates if current_time >= update[0]]
			
			if expired_updates:
				for update in expired_updates:
					activation_time, new_channel, update_id, is_backed_off = update
					# 检查是否因未更新导致过期（主从信道仍一致且未切换）
					channels_unchanged = (self.master_channel != new_channel) or (self.slave_channel != new_channel)
					self.activation_time_missed = channels_unchanged and (self.master_channel == self.slave_channel)
					
					print(f"\n[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 算法2更新包 #{update_id} 已过期:")
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 激活时间: {datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]} (生成后未改变)")
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 未在激活时间前收到ACK2，更新失效")
					
					# 关键逻辑：如果未更新且主从信道一致，提示误包率保持最大
					if self.activation_time_missed:
						print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 激活时间点未更新信道且主从信道一致，误包率将保持在 {self.max_error_rate}")
					print("")
					
					self.processed_updates.append(update)
					self.scheduled_updates.remove(update)
					
					# 清除相关等待状态
					if hasattr(self, '_waiting_for_ack1') and self._waiting_for_ack1 and self._master_pending_channel == new_channel:
						self._waiting_for_ack1 = False
						self._master_pending_channel = None
				self.last_channel_activation_time = current_time
	# 算法2所需变量
	@property
	def waiting_for_ack1(self):
		return hasattr(self, '_waiting_for_ack1') and self._waiting_for_ack1

	@waiting_for_ack1.setter
	def waiting_for_ack1(self, value):
		self._waiting_for_ack1 = value

	@property
	def master_pending_channel(self):
		return hasattr(self, '_master_pending_channel') and self._master_pending_channel

	@master_pending_channel.setter
	def master_pending_channel(self, value):
		self._master_pending_channel = value

	@property
	def slave_pending_channel(self):
		return hasattr(self, '_slave_pending_channel') and self._slave_pending_channel

	@slave_pending_channel.setter
	def slave_pending_channel(self, value):
		self._slave_pending_channel = value

	@property
	def waiting_for_ack2(self):
		return hasattr(self, '_waiting_for_ack2') and self._waiting_for_ack2

	@waiting_for_ack2.setter
	def waiting_for_ack2(self, value):
		self._waiting_for_ack2 = value

	def check_disconnection(self):
		if self.disconnected:
			return
			
		current_time = time.time()
		
		# Master超时判断：超过4秒（原始时间）未收到任何数据或ACK1
		master_timeout = current_time - self.master_last_receive_time > self.timeout_duration
		# Slave超时判断：超过4秒（原始时间）未收到任何数据
		slave_timeout = current_time - self.slave_last_receive_time > self.timeout_duration
		
		if master_timeout or slave_timeout:
			self.disconnected = True
			self.disconnect_time = current_time
			reason = (f"Master超过{self.base_timeout_duration}秒未收到数据或ACK1" 
					 if master_timeout else f"Slave超过{self.base_timeout_duration}秒未收到数据")
			print(f"\n[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 断线! 原因: {reason}")
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 断线时信道: Master={self.master_channel}, Slave={self.slave_channel}")
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 断线时间: {datetime.fromtimestamp(self.disconnect_time).strftime('%H:%M:%S.%f')[:-3]}")

	def process_communication_event(self):
		"""处理单个通信事件"""
		if self.disconnected:
			return
			
		# 递增通信事件计数器
		self.connection_event_counter += 1
		current_event_id = self.event_id
		self.event_id += 1
		current_time = time.time()
			
		master_sent = None
		is_channel_update = False
		update_id = None
		is_empty_packet = False  # 标记当前发送的是否为空包
		received_packet_id = None  # 用于记录收到的数据包编号（匹配ACK1）
		
		# 重传逻辑：仅对非空包重传
		if self.master_pending_packet is not None and self.retransmit_needed:
			master_sent = self.master_pending_packet
			self.retransmit_count += 1
			is_channel_update = master_sent.startswith("CHANNEL_UPDATE_")
			
			# 提取重传包的编号
			if is_channel_update:
				update_id = int(master_sent.split("_")[3])
				received_packet_id = update_id  # 信道更新包使用其update_id作为编号
			elif "Master_Data_" in master_sent:
				received_packet_id = int(master_sent.split("_")[2])  # 提取数据编号
			
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master重传: {master_sent} (重传次数: {self.retransmit_count}, 编号: {received_packet_id}, 误包率: {self.current_error_rate:.2f}, 当前信道: Master={self.master_channel}, Slave={self.slave_channel})")
			
			if self.random_packet_loss():
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 重传数据包丢失，将在下一个通信事件再次尝试")
				self.retransmit_needed = True
				self.check_disconnection()
				return
			else:
				self.retransmit_needed = False
		
		# 没有pending包或不需要重传时，发送新数据（或空包）
		else:
			# 重置非空包的pending状态
			self.master_pending_packet = None
			self.pending_packet_id = None
			self.retransmit_count = 0
			
			# 有数据则发送数据，否则发送空包
			if self.master_send_queue:
				master_sent = self.master_send_queue.pop(0)
				is_channel_update = master_sent.startswith("CHANNEL_UPDATE_")
				
				# 提取新发送包的编号
				if is_channel_update:
					update_id = int(master_sent.split("_")[3])
					received_packet_id = update_id  # 信道更新包使用update_id作为编号
				elif "Master_Data_" in master_sent:
					received_packet_id = int(master_sent.split("_")[2])  # 提取数据编号
				
				# 标记为pending并记录编号
				self.master_pending_packet = master_sent
				self.pending_packet_id = received_packet_id
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master发送: {master_sent} (编号: {received_packet_id}, 误包率: {self.current_error_rate:.2f}, 当前信道: Master={self.master_channel}, Slave={self.slave_channel})")
			else:
				# 发送空包（空包不进入pending状态，不重传）
				master_sent = "EMPTY_PACKET"
				is_empty_packet = True
				received_packet_id = self.ack1_counter  # 空包使用基础计数器作为编号
				self.ack1_counter += 1
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master无数据，发送空包 (编号: {received_packet_id}, 空包不重传，当前信道: Master={self.master_channel}, Slave={self.slave_channel})")
			
			# 检查数据包是否丢失
			if self.random_packet_loss():
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - {'空包丢失，不重传' if is_empty_packet else '数据包丢失，将在下一个通信事件重传'} (编号: {received_packet_id})")
				if not is_empty_packet:
					self.retransmit_needed = True
				self.check_disconnection()
				return
			else:
				self.retransmit_needed = False
		
		if master_sent:
			# Slave收到数据，重置Slave超时计时器
			self.slave_last_receive_time = current_time
			
			# 处理信道更新包（两种算法均检查激活时间是否过期）
			if is_channel_update and update_id is not None:
				parts = master_sent.split("_")
				new_channel = int(parts[2])
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave收到信道更新 ({new_channel}, 编号: {received_packet_id}) (当前信道: Slave={self.slave_channel})")
				
				# 查找该更新包对应的激活时间和回退状态
				activation_time = None
				is_backed_off = False
				# 先检查未处理的更新
				for update in self.scheduled_updates:
					if update[2] == update_id:  # 匹配更新ID
						activation_time = update[0]
						is_backed_off = update[3]
						break
				# 再检查已处理的历史更新
				if not activation_time:
					for update in self.processed_updates:
						if update[2] == update_id:
							activation_time = update[0]
							is_backed_off = update[3]
							break
				
				# 检查是否已过激活时间
				is_expired = False
				if activation_time:
					is_expired = current_time > activation_time
					activation_status = f"已过激活时间({datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]})" if is_expired else f"激活时间未到({datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]})"
					backoff_status = "（回退更新）" if is_backed_off else ""
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 信道更新检查: {activation_status} {backoff_status} (时间生成后未改变)")
				
				# 处理逻辑：已过期则仅发送ACK1不更新
				if is_expired:
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 信道更新已过期，Slave仅发送ACK1，不更新配置")
				else:
					# 算法1：记录计划信道等待激活时间
					if self.algorithm == 1:
						self.slave_scheduled_channel = new_channel
						activation_time_str = datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]
						print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave将在 {activation_time_str} 激活信道 {new_channel} (算法1定时激活)")
					# 算法2：等待ACK2确认
					elif self.algorithm == 2:
						self.slave_pending_channel = new_channel
						self.waiting_for_ack2 = True
						activation_time_str = datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]
						print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave等待ACK2确认，需在 {activation_time_str} 前完成 (算法2)")
			
			elif is_empty_packet:
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave收到空包 (编号: {received_packet_id}) (当前信道: Slave={self.slave_channel})")
			else:
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave收到数据 (编号: {received_packet_id}) (当前信道: Slave={self.slave_channel})")
			
			# 生成Slave响应（无论是否过期都发送ACK1）
			self.slave_generate_data()
			slave_data = None
			
			# ACK1编号与收到的Master数据包编号统一
			if is_channel_update:
				ack1_str = f"ACK1_CHN_{received_packet_id}"  # 信道更新包的特殊ACK1标记
			else:
				ack1_str = f"ACK1_{received_packet_id}"	  # 普通ACK1，编号与数据包一致
			
			if self.slave_send_queue:
				slave_data = self.slave_send_queue.pop(0)
			
			response_info = f"{slave_data} ({ack1_str})" if slave_data else f"({ack1_str})"
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave发送响应: {response_info}")
			
			# 检查Slave响应是否丢失
			if self.random_packet_loss():
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave响应丢失 (丢失ACK: {ack1_str})")
				if slave_data:
					self.slave_send_queue.insert(0, slave_data)
				if not is_empty_packet:
					self.retransmit_needed = True
				self.check_disconnection()
				return
			
			# Master收到响应，重置超时计时器
			self.master_last_receive_time = current_time
			ack2_sent = False
			
			# 仅在收到channel map的ACK1时才发送ACK2（算法2）
			if is_channel_update and self.algorithm == 2 and not is_expired:
				ack2_id = received_packet_id  # ACK2编号也与原始数据包编号一致
				self.ack2_packet_id = max(self.ack2_packet_id, ack2_id + 1)  # 确保计数器同步
				print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master收到{ack1_str}，发送ACK2_{ack2_id}")
				ack2_sent = True
				
				if not self.waiting_for_ack1:
					self.master_pending_channel = int(master_sent.split("_")[2])
					self.waiting_for_ack1 = True
					activation_time_str = datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3] if activation_time else "N/A"
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master等待更新信道，需在 {activation_time_str} 前完成ACK2确认 (算法2)")
			
			# 非信道更新包或算法1不发送ACK2
			else:
				if is_channel_update and self.algorithm == 2 and is_expired:
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 信道更新已过期，不发送ACK2 (算法2)")
				else:
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master收到{ack1_str}，{'算法1不使用ACK2' if self.algorithm == 1 else '不发送ACK2'}")
			
			# 处理信道更新的ACK2（算法2）
			if is_channel_update and self.algorithm == 2 and ack2_sent and self.waiting_for_ack1 and not is_expired:
				if self.waiting_for_ack2:
					# 查找该更新包的回退状态
					is_backed_off = False
					for update in self.scheduled_updates:
						if update[2] == update_id:
							is_backed_off = update[3]
							break
					
					old_master_channel = self.master_channel
					old_slave_channel = self.slave_channel
					self.master_channel = self.master_pending_channel
					self.slave_channel = self.slave_pending_channel
					self.waiting_for_ack1 = False
					self.waiting_for_ack2 = False
					self.activation_time_missed = False  # 成功更新后清除未更新标记
					
					# 从计划列表中移除已激活的更新
					for update in self.scheduled_updates[:]:
						if update[2] == update_id:
							self.processed_updates.append(update)
							self.scheduled_updates.remove(update)
							break
					
					backoff_info = "（回退状态）" if is_backed_off else ""
					print(f"\n[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 算法2信道激活完成 {backoff_info}:")
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 激活时间: {datetime.fromtimestamp(activation_time).strftime('%H:%M:%S.%f')[:-3]} (生成后未改变)")
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Master信道变更: {old_master_channel} → {self.master_channel}")
					print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - Slave信道变更: {old_slave_channel} → {self.slave_channel}")
					if is_backed_off and self.master_channel == self.slave_channel:
						print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] Event ID: N/A - 回退状态且信道一致，误包率将保持在 {self.max_error_rate}")
					print("")
			
			# 清除非空包的pending状态
			if not is_empty_packet:
				self.master_pending_packet = None
				self.pending_packet_id = None
			self.retransmit_needed = False
			print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 通信事件 #{self.connection_event_counter} | Event ID: {current_event_id} - 完成本次通信事件处理\n")

	def run_simulation(self, max_duration=60):
		print("启动主从通信模拟...")
		print(f"初始信道状态: Master={self.master_channel}, Slave={self.slave_channel}")
		print(f"参数: 初始误包率={self.initial_error_rate}, 最大误包率={self.max_error_rate}, 合并成功率={self.merge_success_rate}")
		print(f"超时设置: Master/Slave {self.base_timeout_duration}秒(原始时间)未收到数据则断线")
		print(f"加速倍数: {self.speedup}x\n")
		
		actual_max_duration = max_duration / self.speedup
		start_time = time.time()
		
		while self.running and time.time() - start_time < actual_max_duration and not self.disconnected:
			self.update_error_rate()
			self.process_channel_update()
			self.check_channel_activation()  # 检查激活或过期
			self.master_generate_data()
			self.process_communication_event()  # 每个循环处理一个通信事件
			time.sleep(self.connection_interval)  # 等待下一个通信事件周期
		
		print("\n模拟结束")
		print(f"最终信道状态: Master={self.master_channel}, Slave={self.slave_channel}")
		print(f"总通信事件数: {self.connection_event_counter}")
		print(f"处理的通信事件总数: {self.event_id}")
		if self.disconnected:
			original_disconnect_time = start_time + (self.disconnect_time - start_time) * self.speedup
			print(f"因断线提前结束。原始时间尺度断线时间: {datetime.fromtimestamp(original_disconnect_time).strftime('%H:%M:%S.%f')[:-3]}")
			return self.connection_event_counter
		else:
			print(f"正常结束。原始时间尺度总时长: {max_duration}s, 实际运行时间: {time.time() - start_time:.2f}s")
			return self.connection_event_counter

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='激活时间未更新时保持最大误包率的主从通信模拟器')
	parser.add_argument('--initial-error', type=float, default=0.1, help='初始误包率 (0-1)')
	parser.add_argument('--max-error', type=float, default=0.5, help='最大误包率 (0-1)')
	parser.add_argument('--merge-success', type=float, default=0.5, help='信道合并成功率 (0-1)')
	parser.add_argument('--duration', type=int, default=60, help='原始时间尺度模拟时长 (秒)')
	parser.add_argument('--algorithm', type=int, default=1, choices=[1, 2], 
					  help='1: 定时激活（强制更新）; 2: ACK确认后激活 (默认1)')
	
	args = parser.parse_args()
	
	# 参数验证
	if not (0 <= args.initial_error <= 1):
		raise ValueError("初始误包率必须在0-1之间")
	if not (0 <= args.max_error <= 1):
		raise ValueError("最大误包率必须在0-1之间")
	if not (0 <= args.merge_success <= 1):
		raise ValueError("合并成功率必须在0-1之间")
	if args.duration <= 0:
		raise ValueError("模拟时长必须为正数")
	
	simulator = MasterSlaveSimulator(
		initial_error_rate=args.initial_error,
		max_error_rate=args.max_error,
		merge_success_rate=args.merge_success,
		algorithm=args.algorithm,
		speedup=5
	)
	print(simulator.run_simulation(max_duration=args.duration))
