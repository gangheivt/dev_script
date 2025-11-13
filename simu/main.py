import subprocess
import csv
import time
from datetime import datetime

def run_simulation(initial_error, max_error, algorithm, duration=120, speedup=5):
    """运行单次模拟并返回通信事件计数器结果"""
    try:
        # 使用独立的Python脚本作为入口，避免导入问题
        result = subprocess.run(
            [
                "python", "-c", 
                "import sys; "
                "from simu import MasterSlaveSimulator; "  # 直接从simulator.py导入
                "ie = float(sys.argv[1]); "
                "me = float(sys.argv[2]); "
                "alg = int(sys.argv[3]); "
                "dur = int(sys.argv[4]); "
                "sp = int(sys.argv[5]); "
                "sim = MasterSlaveSimulator(initial_error_rate=ie, max_error_rate=me, "
                "merge_success_rate=0.5, algorithm=alg, speedup=sp); "
                "print(sim.run_simulation(max_duration=dur))",
                str(initial_error),
                str(max_error),
                str(algorithm),
                str(duration),
                str(speedup)
            ],
            capture_output=True,
            text=True,
            check=True
        )
        
        output_lines = result.stdout.strip().split('\n')
        # 查找最后一个整数输出（通信事件计数器）
        for line in reversed(output_lines):
            if line.isdigit():
                return int(line)
        return None
    except Exception as e:
        print(f"❌ 模拟失败: {str(e)}")
        # 打印错误详情以便调试
        print(f"错误输出: {result.stderr if 'result' in locals() else '无'}")
        return None

def print_scenario_header(initial_error, max_error, algorithm, scenario_num, total_scenarios):
    print("\n" + "="*60)
    print(f"场景 {scenario_num}/{total_scenarios}")
    print(f"初始误包率: {initial_error:.2f} | 最大误包率: {max_error:.2f} | 算法: {algorithm}")
    print("-"*60)

def print_run_result(run_num, total_runs, event_count):
    if event_count is not None:
        print(f"  运行 {run_num}/{total_runs} 完成 | 通信事件数: {event_count}")
    else:
        print(f"  运行 {run_num}/{total_runs} 失败 ❌")

def print_scenario_stats(results):
    print("-"*60)
    if results:
        valid_results = [r for r in results if r is not None]
        if valid_results:
            avg = sum(valid_results) / len(valid_results)
            min_val = min(valid_results)
            max_val = max(valid_results)
            print(f"  统计结果:")
            print(f"  平均值: {avg:.2f} | 最小值: {min_val} | 最大值: {max_val}")
        else:
            print(f"  无有效运行结果")
    else:
        print(f"  无运行结果")
    print("="*60 + "\n")

def generate_range(start, end, step):
    """可靠的浮点数范围生成器，避免浮点数精度问题"""
    values = []
    current = start
    while current <= end + 1e-9:  # 增加微小误差容忍
        values.append(round(current, 2))
        current += step
    return values

def main():
    # 生成参数列表
    initial_errors = generate_range(0.5, 0.7, 0.05)  # 0.5, 0.55, 0.6, 0.65, 0.7
    max_errors = generate_range(0.7, 0.9, 0.05)      # 0.7, 0.75, 0.8, 0.85, 0.9
    algorithms = [1, 2]
    runs_per_scenario = 3
    simulation_duration = 120
    speedup = 5
    
    # 验证生成的参数
    print("生成的初始误包率列表:", initial_errors)
    print("生成的最大误包率列表:", max_errors)
    
    # 计算有效场景数
    valid_scenarios = 0
    for ie in initial_errors:
        for me in max_errors:
            if ie <= me:
                valid_scenarios += len(algorithms)
    
    # 输出CSV文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"algorithm_comparison_{timestamp}.csv"
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            '初始误包率', '最大误包率', '算法', 
            '第1次通信事件数', '第2次通信事件数', '第3次通信事件数', 
            '平均值', '最小值', '最大值'
        ])
    
    current_scenario = 0
    start_time = time.time()
    
    # 遍历所有参数组合
    for initial_error in initial_errors:
        for max_error in max_errors:
            if initial_error > max_error:
                continue
                
            for algorithm in algorithms:
                current_scenario += 1
                print_scenario_header(initial_error, max_error, algorithm, current_scenario, valid_scenarios)
                
                results = []
                for run in range(1, runs_per_scenario + 1):
                    event_count = run_simulation(
                        initial_error=initial_error,
                        max_error=max_error,
                        algorithm=algorithm,
                        duration=simulation_duration,
                        speedup=speedup
                    )
                    results.append(event_count)
                    print_run_result(run, runs_per_scenario, event_count)
                    time.sleep(0.5)  # 短暂延迟，避免资源占用过高
                
                print_scenario_stats(results)
                
                # 计算统计值
                valid_results = [r for r in results if r is not None]
                avg = sum(valid_results)/len(valid_results) if valid_results else None
                min_val = min(valid_results) if valid_results else None
                max_val = max(valid_results) if valid_results else None
                
                while len(results) < runs_per_scenario:
                    results.append(None)
                
                # 写入CSV
                with open(output_file, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        initial_error, max_error, algorithm,
                        results[0], results[1], results[2],
                        round(avg, 2) if avg else None,
                        min_val,
                        max_val
                    ])
    
    total_time = time.time() - start_time
    print(f"\n所有模拟完成！总耗时: {total_time:.2f}秒")
    print(f"结果已保存至: {output_file}")

if __name__ == "__main__":
    main()